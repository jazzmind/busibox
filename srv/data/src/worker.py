#!/usr/bin/env python3
"""
Busibox File Data Worker

Consumes jobs from Redis Streams and processes uploaded files:
1. Check for duplicate content (content_hash)
2. Download file from MinIO
3. Extract text (Marker, TATR, page images)
4. Classify document and detect languages
5. Extract metadata
6. Chunk text (400-800 tokens, semantic boundaries)
7. Generate embeddings (dense via liteLLM, ColPali for pages)
8. Store vectors in Milvus (dense, sparse BM25, multi-vector)
9. Store metadata in PostgreSQL
10. Update job status with NOTIFY

Environment Variables:
    REDIS_HOST: Redis server host
    REDIS_PORT: Redis server port
    POSTGRES_HOST: PostgreSQL server host
    POSTGRES_PORT: PostgreSQL server port
    MINIO_ENDPOINT: MinIO server endpoint
    MILVUS_HOST: Milvus server host
    LITELLM_BASE_URL: liteLLM API base URL
    WORKER_ID: Unique worker identifier (default: hostname)
"""

import asyncio
import json
import os
import sys
import time
import traceback
import signal
import socket
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

import structlog
import redis as redis_sync
from redis.exceptions import RedisError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# IMPORTANT: Patch transformers before any marker/surya imports
# This fixes "Cannot copy out of meta tensor" error with transformers 4.56+
# by disabling lazy loading of model weights
def _patch_transformers_loading():
    """Patch transformers to disable low_cpu_mem_usage which causes meta tensor issues on GPU."""
    try:
        from transformers import PreTrainedModel
        original_from_pretrained = PreTrainedModel.from_pretrained.__func__
        
        def patched_from_pretrained(cls, *args, **kwargs):
            # Disable meta tensor loading - we have plenty of RAM
            kwargs['low_cpu_mem_usage'] = False
            return original_from_pretrained(cls, *args, **kwargs)
        
        PreTrainedModel.from_pretrained = classmethod(patched_from_pretrained)
    except Exception:
        pass  # If patch fails, continue anyway

_patch_transformers_loading()

# Early logging setup for debugging import issues
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger_early = logging.getLogger(__name__)
logger_early.info("=" * 80)
logger_early.info("WORKER MODULE LOADING - Starting imports")
logger_early.info("=" * 80)

from shared.config import Config
from services.file_service import FileService
from services.postgres_service import PostgresService
from services.milvus_service import MilvusService
from services.processing_history_service import ProcessingHistoryService
from processors.text_extractor import TextExtractor, ExtractionResult
from processors.chunker import Chunker, Chunk
from services.embedding_client import EmbeddingClient
from processors.classifier import DocumentClassifier
from processors.metadata_extractor import MetadataExtractor
# ColPali import is deferred to avoid ONNX initialization at import time
# It will be imported conditionally in the worker's connect() method
from processors.llm_cleanup import LLMCleanup
from processors.markdown_generator import MarkdownGenerator
from processors.image_extractor import ImageExtractor
from worker import ErrorHandler, HistoryLogger
from worker.pipeline import PipelineMixin
from worker.triggers import TriggerMixin

# Import WorkerRLSContext for setting RLS context on database operations
from api.middleware.jwt_auth import WorkerRLSContext

logger_early.info("All imports successful")
logger_early.info("=" * 80)

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


class IngestWorker(PipelineMixin, TriggerMixin):
    """File data worker that processes jobs from Redis Streams."""
    
    def __init__(self, config: dict = None):
        """Initialize worker with configuration."""
        if config is None:
            config = Config().to_dict()
        self.config = config
        self.worker_id = config.get("worker_id", socket.gethostname())
        self.stream_name = config.get("stream_name", "jobs:data")
        self.consumer_group = config.get("consumer_group", "workers")
        self.consumer_name = f"{self.worker_id}-{os.getpid()}"
        self.running = False
        
        # Current RLS context for database operations
        # Set at start of process_job(), cleared at end
        self._current_rls_context: Optional[WorkerRLSContext] = None
        
        # Services (initialized in connect())
        self.redis_client: Optional[redis_sync.Redis] = None
        self.file_service: Optional[FileService] = None
        self.postgres_service: Optional[PostgresService] = None
        self.milvus_service: Optional[MilvusService] = None
        self.history_service: Optional[ProcessingHistoryService] = None
        self.error_handler: Optional[ErrorHandler] = None
        self.history: Optional[HistoryLogger] = None
        
        # Processors (initialized in connect())
        self.text_extractor: Optional[TextExtractor] = None
        self.chunker: Optional[Chunker] = None
        self.embedder: Optional[EmbeddingClient] = None
        self.classifier: Optional[DocumentClassifier] = None
        self.metadata_extractor: Optional[MetadataExtractor] = None
        self.colpali: Optional[ColPaliEmbedder] = None
        self.llm_cleanup: Optional[LLMCleanup] = None
        
        logger.info(
            "Worker initialized",
            worker_id=self.worker_id,
            consumer_name=self.consumer_name,
            stream=self.stream_name,
        )
    
    def connect(self):
        """Connect to all required services."""
        logger.info("Connecting to services")
        
        # Redis
        self.redis_client = redis_sync.Redis(
            host=self.config["redis_host"],
            port=self.config["redis_port"],
            decode_responses=True,
        )
        self.redis_client.ping()
        
        # Create consumer group if it doesn't exist
        try:
            self.redis_client.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id='0',
                mkstream=True
            )
            logger.info("Consumer group created", group=self.consumer_group)
        except redis_sync.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info("Consumer group already exists", group=self.consumer_group)
            else:
                raise
        
        # Initialize services
        self.file_service = FileService(self.config)
        self.postgres_service = PostgresService(self.config)
        self.postgres_service.connect()
        
        self._run_schema_migrations()
        
        self.milvus_service = MilvusService(self.config)
        self.milvus_service.connect()
        self.history_service = ProcessingHistoryService(self.config)
        self.history_service.connect()
        self.error_handler = ErrorHandler(self.config, self.postgres_service, self.redis_client)
        self.history = HistoryLogger(self.history_service)
        
        # Initialize processors
        self.text_extractor = TextExtractor(self.config)
        self.chunker = Chunker(self.config)
        self.embedder = EmbeddingClient(self.config)
        self.classifier = DocumentClassifier(self.config)
        self.metadata_extractor = MetadataExtractor(self.config)
        
        # Initialize ColPali only if enabled (requires GPU/ONNX)
        # Import is deferred to avoid ONNX initialization at module import time
        # Wrap in try-except to handle missing GPU gracefully
        try:
            colpali_enabled = self.config.get("colpali_enabled", False)
            if colpali_enabled:
                logger.info("ColPali enabled, importing ColPaliEmbedder...")
                from processors.colpali import ColPaliEmbedder
                self.colpali = ColPaliEmbedder(self.config)
                logger.info("ColPali embedder initialized")
            else:
                logger.info("ColPali disabled, skipping initialization")
                self.colpali = None
        except Exception as e:
            logger.warning(
                "Failed to initialize ColPali (GPU/ONNX not available), visual embeddings disabled",
                error=str(e),
                error_type=type(e).__name__
            )
            self.colpali = None
        
        self.llm_cleanup = LLMCleanup(self.config)
        self.markdown_generator = MarkdownGenerator()
        self.image_extractor = ImageExtractor()
        
        logger.info("All services connected")
    
    def _run_schema_migrations(self):
        """Run idempotent schema migrations on startup via sync psycopg2 connection."""
        try:
            from schema import apply_schema_sync
            conn = self.postgres_service._get_connection()
            try:
                apply_schema_sync(conn)
            finally:
                self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.error("Failed to apply schema migrations", error=str(e), exc_info=True)
            raise
    
    def disconnect(self):
        """Disconnect from all services."""
        logger.info("Disconnecting from services")
        
        if self.postgres_service:
            self.postgres_service.close()
        
        if self.milvus_service:
            self.milvus_service.close()
        
        if self.redis_client:
            self.redis_client.close()
        
        logger.info("All services disconnected")

    def _reconnect_redis(self):
        """Reconnect Redis client after transport-level failures."""
        try:
            if self.redis_client:
                try:
                    self.redis_client.close()
                except Exception:
                    pass

            self.redis_client = redis_sync.Redis(
                host=self.config["redis_host"],
                port=self.config["redis_port"],
                decode_responses=True,
            )
            self.redis_client.ping()
            logger.info("Reconnected to Redis")
        except Exception as reconnect_error:
            logger.error(
                "Failed to reconnect Redis",
                error=str(reconnect_error),
                exc_info=True,
            )
    
    # Removed _log_step - now using self.history.log_step() directly
    # Removed _is_transient_error - now using self.error_handler.is_transient_error()
    
    def _exchange_delegation_token(
        self,
        delegation_token: str,
        user_id: str,
        file_id: str,
    ) -> Optional[str]:
        """
        Exchange a delegation token for a data-api scoped access token.
        
        Uses Zero Trust token exchange: the delegation token (created at upload
        time) is exchanged for a short-lived data-api token. This ensures the
        worker operates with proper user identity for RLS, decryption, etc.
        
        Args:
            delegation_token: Delegation JWT from the job data
            user_id: User ID for logging
            file_id: File ID for logging
            
        Returns:
            Access token string for data-api audience, or None if exchange fails
        """
        from busibox_common.auth import exchange_token_zero_trust
        
        authz_token_url = self.config.get("authz_token_url") or os.environ.get(
            "AUTHZ_TOKEN_URL", ""
        )
        
        if not authz_token_url:
            logger.warning(
                "AUTHZ_TOKEN_URL not configured, cannot exchange delegation token",
                file_id=file_id,
            )
            return None
        
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(
                    exchange_token_zero_trust(
                        subject_token=delegation_token,
                        target_audience="data-api",
                        user_id=user_id,
                        authz_url=authz_token_url,
                        use_cache=False,  # Don't cache worker tokens
                    )
                )
                
                if result:
                    logger.info(
                        "Delegation token exchanged for data-api token",
                        file_id=file_id,
                        user_id=user_id,
                        expires_in=result.expires_in,
                    )
                    return result.access_token
                else:
                    logger.warning(
                        "Delegation token exchange returned no result",
                        file_id=file_id,
                        user_id=user_id,
                    )
                    return None
            finally:
                loop.close()
                
        except Exception as e:
            logger.warning(
                "Failed to exchange delegation token (non-fatal, will process without user token)",
                file_id=file_id,
                user_id=user_id,
                error=str(e),
            )
            return None
    
    def _get_timeout_seconds(self, page_count: int) -> int:
        """Calculate timeout based on document size."""
        if page_count < 10:
            return self.config.get("timeout_small", 300)  # 5 minutes
        elif page_count <= 50:
            return self.config.get("timeout_medium", 600)  # 10 minutes
        else:
            return self.config.get("timeout_large", 1200)  # 20 minutes
    
    # Removed _is_transient_error - moved to ErrorHandler class
    
    def _check_duplicate(self, file_id: str, content_hash: str) -> bool:
        """Check if file with same content_hash already processed."""
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT file_id, chunk_count, vector_count
                        FROM data_files
                        WHERE content_hash = %s
                        AND file_id IN (
                            SELECT file_id FROM data_status
                            WHERE stage = 'completed'
                        )
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (content_hash,))
                    
                    existing = cur.fetchone()
                    if existing:
                        existing_file_id, chunk_count, vector_count = existing
                        
                        self.postgres_service.update_status(
                            file_id=file_id,
                            stage="completed",
                            progress=100,
                            chunks_processed=chunk_count,
                            total_chunks=chunk_count,
                            status_message="Processing complete (duplicate detected)",
                        )
                        
                        # Update file metadata (pass RLS context)
                        self.postgres_service.update_file_metadata(
                            file_id=file_id,
                            chunk_count=chunk_count,
                            vector_count=vector_count,
                            processing_duration_seconds=0,  # Instant for duplicates
                            request=self._current_rls_context,
                        )
                        
                        logger.info(
                            "Duplicate detected, vectors reused",
                            file_id=file_id,
                            existing_file_id=existing_file_id,
                            content_hash=content_hash,
                        )
                        
                        return True
            finally:
                self.postgres_service._return_connection(conn)
        
        except Exception as e:
            logger.error("Duplicate check failed", file_id=file_id, error=str(e), exc_info=True)
            # Continue processing even if duplicate check fails
        
        return False
    
    def _load_chunks_from_db(self, file_id: str) -> Optional[List]:
        """Load existing chunks from database for partial reprocessing."""
        try:
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT chunk_index, text, char_offset, token_count, page_number, section_heading, metadata
                        FROM data_chunks
                        WHERE file_id = %s
                        ORDER BY chunk_index
                        """,
                        (file_id,),
                    )
                    
                    rows = cur.fetchall()
                    if not rows:
                        return None
                    
                    # Convert to Chunk objects
                    chunks = []
                    for chunk_index, text, char_offset, token_count, page_number, section_heading, metadata in rows:
                        metadata_dict = metadata or {}
                        if isinstance(metadata_dict, str):
                            try:
                                metadata_dict = json.loads(metadata_dict)
                            except Exception:
                                metadata_dict = {}
                        chunk = Chunk(
                            text=text or "",
                            chunk_index=chunk_index,
                            token_count=token_count or 0,
                            char_offset=char_offset or 0,
                            page_number=page_number,
                            section_heading=section_heading,
                            language=metadata_dict.get("language"),
                        )
                        # Keep compatibility with existing fast-path code expecting these attrs.
                        chunk.index = chunk_index
                        chunk.metadata = metadata_dict
                        chunks.append(chunk)
                    
                    return chunks
            finally:
                self.postgres_service._return_connection(conn)
        
        except Exception as e:
            logger.error("Failed to load chunks from DB", file_id=file_id, error=str(e), exc_info=True)
            return None

    def process_job(self, job_id: str, job_data: dict, trace_id: str):
        """
        Process a single data job.
        
        Args:
            job_id: Job ID (UUID)
            job_data: Job data from Redis stream
            trace_id: Trace ID for observability
        """
        file_id = job_data.get("file_id")
        user_id = job_data.get("user_id")
        storage_path = job_data.get("storage_path")
        mime_type = job_data.get("mime_type")
        original_filename = job_data.get("original_filename", "unknown")
        
        # Get retry count from job data (used when DB record doesn't exist)
        job_retry_count = int(job_data.get("retry_count", 0))
        
        # Extract visibility and role_ids for partition routing
        visibility = job_data.get("visibility", "personal")
        role_ids_str = job_data.get("role_ids")
        role_ids: Optional[List[str]] = None
        if role_ids_str:
            try:
                role_ids = json.loads(role_ids_str)
            except json.JSONDecodeError:
                logger.warning(
                    "Failed to parse role_ids, using None",
                    file_id=file_id,
                    role_ids_str=role_ids_str,
                )
        
        # Extract delegation token for Zero Trust token exchange
        # The delegation token was created at upload time so the worker can
        # perform authenticated operations (decryption, etc.) on behalf of the user
        delegation_token = job_data.get("delegation_token")
        
        # Parse processing configuration if provided
        processing_config = {}
        processing_config_str = job_data.get("processing_config")
        if processing_config_str:
            try:
                processing_config = json.loads(processing_config_str)
                logger.info(
                    "Using custom processing configuration",
                    file_id=file_id,
                    llm_cleanup=processing_config.get("llm_cleanup_enabled"),
                    multi_flow=processing_config.get("multi_flow_enabled"),
                    marker=processing_config.get("marker_enabled"),
                    colpali=processing_config.get("colpali_enabled"),
                )
            except json.JSONDecodeError as e:
                logger.warning(
                    "Failed to parse processing config, using defaults",
                    file_id=file_id,
                    error=str(e),
                )
        
        # Get start_stage for partial reprocessing
        # Stages in order: parsing, chunking, cleanup, markdown, embedding, indexing
        # (entity_extraction removed -- now schema-driven and on-demand via /extract API)
        start_stage = job_data.get("start_stage", "parsing")
        stage_order = ["parsing", "chunking", "cleanup", "markdown", "embedding", "indexing"]
        start_stage_idx = stage_order.index(start_stage) if start_stage in stage_order else 0
        
        if start_stage != "parsing":
            logger.info(
                "Partial reprocessing - starting from stage",
                file_id=file_id,
                start_stage=start_stage,
                skipping_stages=stage_order[:start_stage_idx],
            )
        
        start_time = time.time()
        temp_file_path = None
        
        # Create RLS context for database operations
        # This ensures the worker can access the user's data via RLS
        # Store on instance so helper methods can access it
        self._current_rls_context = WorkerRLSContext(user_id=user_id, role_ids=role_ids)
        
        # Exchange delegation token for a data-api scoped user token
        # This user_token can be used for decryption and other authenticated operations
        user_token = None
        if delegation_token:
            user_token = self._exchange_delegation_token(
                delegation_token=delegation_token,
                user_id=user_id,
                file_id=file_id,
            )
        
        try:
            logger.info(
                "Processing job",
                job_id=job_id,
                file_id=file_id,
                user_id=user_id,
                trace_id=trace_id,
                visibility=visibility,
                role_count=len(role_ids) if role_ids else 0,
                has_user_token=user_token is not None,
            )
            
            # Hard cap: refuse to process if retry count from job data is too high.
            # This prevents infinite retry loops when DB tracking fails.
            if job_retry_count >= 5:
                logger.error(
                    "Job exceeded hard retry cap, dropping permanently",
                    file_id=file_id,
                    job_retry_count=job_retry_count,
                )
                return

            # Get content_hash from database (with RLS context)
            conn = self.postgres_service._get_connection(self._current_rls_context)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content_hash FROM data_files WHERE file_id = %s",
                        (file_id,),
                    )
                    result = cur.fetchone()
                    if not result:
                        logger.error(
                            "File not found in database — orphaned job, skipping permanently",
                            file_id=file_id,
                            user_id=user_id,
                        )
                        return
                    content_hash = result[0]
            finally:
                self.postgres_service._return_connection(conn)
            
            # Check for duplicate (skip if force_reprocess requested)
            force_reprocess = processing_config.get("force_reprocess", False)
            if not force_reprocess and self._check_duplicate(file_id, content_hash):
                logger.info(
                    "Job completed (duplicate)",
                    file_id=file_id,
                    processing_time_seconds=time.time() - start_time,
                )
                return
            
            if force_reprocess:
                logger.info(
                    "Force reprocess enabled, skipping duplicate check",
                    file_id=file_id,
                )
            
            # For partial reprocessing, load existing data if starting from later stage.
            # These early-return paths must NOT reset the document status/counts.
            existing_chunks = None
            if start_stage == "entity_extraction":
                # Entity extraction only: load chunks for text, run entity extraction
                # User explicitly selected this reprocess option - always run extraction
                existing_chunks = self._load_chunks_from_db(file_id)
                if existing_chunks:
                    self.postgres_service.update_status(
                        file_id=file_id,
                        stage="entity_extraction",
                        progress=50,
                        chunks_processed=len(existing_chunks),
                        total_chunks=len(existing_chunks),
                        status_message="Extracting entities for knowledge graph",
                    )
                    try:
                        from processors.entity_extractor import EntityExtractor
                        from services.graph_service import GraphService
                        text_from_chunks = "\n\n".join(c.text for c in existing_chunks if c.text)
                        if text_from_chunks:
                            library_id = None
                            reprocess_filename = original_filename
                            conn = self.postgres_service._get_connection(self._current_rls_context)
                            try:
                                cur = conn.cursor()
                                cur.execute(
                                    "SELECT library_id, filename FROM data_files WHERE file_id = %s",
                                    (file_id,),
                                )
                                row = cur.fetchone()
                                if row:
                                    library_id = str(row[0])
                                    if row[1]:
                                        reprocess_filename = row[1]
                            finally:
                                self.postgres_service._return_connection(conn)
                            entity_extractor = EntityExtractor(
                                litellm_base_url=os.getenv("LITELLM_BASE_URL", "http://litellm:4000"),
                                litellm_api_key=os.getenv("LITELLM_API_KEY", ""),
                            )
                            graph = GraphService()
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            try:
                                connected = loop.run_until_complete(graph.connect())
                                if connected:
                                    loop.run_until_complete(
                                        entity_extractor.extract_and_store_graph(
                                            text=text_from_chunks,
                                            file_id=file_id,
                                            filename=reprocess_filename,
                                            owner_id=user_id,
                                            visibility=visibility,
                                            graph_service=graph,
                                            library_id=library_id,
                                        )
                                    )
                            finally:
                                loop.run_until_complete(graph.disconnect())
                                loop.close()
                    except Exception as e:
                        logger.warning(
                            "Entity extraction reprocess failed (non-blocking)",
                            file_id=file_id,
                            error=str(e),
                        )
                    self.postgres_service.update_status(
                        file_id=file_id,
                        stage="completed",
                        progress=100,
                        chunks_processed=len(existing_chunks),
                        status_message="Processing complete",
                        total_chunks=len(existing_chunks),
                    )
                    logger.info(
                        "Entity extraction reprocess complete",
                        file_id=file_id,
                    )
                    return
                else:
                    logger.warning(
                        "No existing chunks found for entity extraction, starting from parsing",
                        file_id=file_id,
                    )
                    start_stage = "parsing"
                    start_stage_idx = 0
            elif start_stage in ["embedding", "indexing"]:
                # Fast path: just re-embed or re-index existing chunks
                existing_chunks = self._load_chunks_from_db(file_id)
                if existing_chunks:
                    logger.info(
                        "Fast path: partial reprocess from existing chunks",
                        file_id=file_id,
                        chunk_count=len(existing_chunks),
                        start_stage=start_stage,
                    )
                    # Run the fast path and return
                    self._fast_reprocess(
                        file_id=file_id,
                        user_id=user_id,
                        chunks=existing_chunks,
                        start_stage=start_stage,
                        visibility=visibility,
                        role_ids=role_ids,
                        processing_config=processing_config,
                        start_time=start_time,
                    )
                    return
                else:
                    logger.warning(
                        "No existing chunks found, starting from parsing",
                        file_id=file_id,
                        requested_start_stage=start_stage,
                    )
                    start_stage = "parsing"
                    start_stage_idx = 0
            
            # Stage 1: Parsing
            parsing_start = self.history.log_stage_start(
                file_id, "parsing",
                f"Starting text extraction for {mime_type}",
                metadata={"mime_type": mime_type, "filename": original_filename}
            )
            
            logger.info(
                "Stage 1: Starting text extraction",
                file_id=file_id,
                mime_type=mime_type,
                filename=original_filename,
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="parsing",
                progress=5,
                status_message="Downloading file",
            )
            
            logger.debug("Downloading file from storage", file_id=file_id, storage_path=storage_path)
            download_start = time.time()
            temp_file_path = self.file_service.download(
                storage_path,
                file_id=file_id,
                user_token=user_token,
                role_ids=role_ids,
                user_id=user_id,
            )
            self.history.log_substep(
                file_id, "parsing", "download_from_minio",
                f"Downloaded file from {storage_path}",
                started_at=download_start
            )
            logger.debug("File downloaded", file_id=file_id, temp_path=temp_file_path)
            
            logger.info("Extracting text and images", file_id=file_id, mime_type=mime_type)
            
            # Override marker_enabled from processing_config if provided
            original_marker_enabled = self.text_extractor.marker_enabled
            if processing_config and "marker_enabled" in processing_config:
                self.text_extractor.marker_enabled = processing_config["marker_enabled"]
                logger.info(
                    "Overriding marker_enabled from processing_config",
                    file_id=file_id,
                    marker_enabled=self.text_extractor.marker_enabled,
                )
            
            def _page_progress(page_num: int, total_pages: int):
                progress = 5 + int((page_num / total_pages) * 15)  # 5-20% range
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="parsing",
                    progress=progress,
                    pages_processed=page_num,
                    total_pages=total_pages,
                    status_message=f"Parsing page {page_num} of {total_pages}",
                )
            
            extract_start = time.time()
            extraction_result: ExtractionResult = self.text_extractor.extract(
                temp_file_path,
                mime_type,
                progress_callback=_page_progress,
            )
            
            # Restore original marker_enabled setting
            if processing_config and "marker_enabled" in processing_config:
                self.text_extractor.marker_enabled = original_marker_enabled
            
            extraction_method = extraction_result.metadata.get("extraction_method", "unknown")
            method_display = {
                "marker": "✓ Marker (high quality)",
                "remote_marker": "✓ Remote Marker (production)",
                "pdfplumber": "⚠ pdfplumber fallback",
                "remote_marker_failed": "⚠ Remote Marker failed → pdfplumber",
                "remote_marker_error": "⚠ Remote Marker error → pdfplumber",
            }.get(extraction_method, extraction_method)
            
            self.history.log_substep(
                file_id, "parsing", "text_extraction",
                f"Extracted {len(extraction_result.text)} chars, {extraction_result.page_count} pages ({method_display})",
                metadata={
                    "text_length": len(extraction_result.text),
                    "page_count": extraction_result.page_count,
                    "table_count": len(extraction_result.tables) if extraction_result.tables else 0,
                    "method": extraction_method,
                    "marker_used": extraction_method in ("marker", "remote_marker"),
                },
                started_at=extract_start
            )
            
            logger.info(
                "Text extraction complete",
                file_id=file_id,
                text_length=len(extraction_result.text),
                page_count=extraction_result.page_count,
                table_count=len(extraction_result.tables) if extraction_result.tables else 0,
            )
            
            page_count = extraction_result.page_count or 1
            
            # Calculate timeout based on page count
            timeout_seconds = self._get_timeout_seconds(page_count)
            logger.info(
                "Processing with timeout",
                file_id=file_id,
                page_count=page_count,
                timeout_seconds=timeout_seconds,
            )
            
            # Stage 2: Classifying
            self.postgres_service.update_status(
                file_id=file_id,
                stage="classifying",
                progress=22,
                total_pages=page_count,
                status_message="Classifying document type",
            )
            
            document_type, confidence = self.classifier.classify(
                extraction_result.text,
                original_filename,
                mime_type,
            )
            
            primary_language, detected_languages = self.classifier.detect_languages(
                extraction_result.text,
            )
            
            # Stage 3: Extracting Metadata
            self.postgres_service.update_status(
                file_id=file_id,
                stage="extracting_metadata",
                progress=28,
                status_message="Extracting metadata",
            )
            
            metadata = self.metadata_extractor.extract(
                temp_file_path,
                mime_type,
                extraction_result.text,
            )
            
            # Update file metadata (pass RLS context)
            self.postgres_service.update_file_metadata(
                file_id=file_id,
                document_type=document_type,
                primary_language=primary_language,
                detected_languages=detected_languages,
                extracted_title=metadata.get("title"),
                extracted_author=metadata.get("author"),
                extracted_date=metadata.get("date"),
                extracted_keywords=metadata.get("keywords", []),
                request=self._current_rls_context,
            )
            
            # Update metadata JSON with page_count and word_count
            try:
                conn = self.postgres_service._get_connection(self._current_rls_context)
                with conn.cursor() as cur:
                    # Calculate word count from extracted text
                    word_count = len(extraction_result.text.split())
                    
                    # Update metadata JSON field
                    cur.execute("""
                        UPDATE data_files
                        SET metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb
                        WHERE file_id = %s
                    """, (
                        json.dumps({"page_count": extraction_result.page_count, "word_count": word_count}),
                        file_id
                    ))
                    conn.commit()
                    logger.debug(
                        "Updated metadata with page_count and word_count",
                        file_id=file_id,
                        page_count=extraction_result.page_count,
                        word_count=word_count
                    )
                self.postgres_service._return_connection(conn)
            except Exception as e:
                logger.warning(
                    "Failed to update metadata JSON",
                    file_id=file_id,
                    error=str(e)
                )
            
            # Stage 4: Chunking
            chunking_start = self.history.log_stage_start(
                file_id, "chunking",
                f"Starting chunking with detected languages: {detected_languages}",
                metadata={"text_length": len(extraction_result.text), "detected_languages": detected_languages}
            )
            logger.info(
                "Stage 4: Starting text chunking",
                file_id=file_id,
                text_length=len(extraction_result.text),
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="chunking",
                progress=32,
                status_message="Creating semantic chunks",
            )
            
            # Apply custom chunking config if provided
            if processing_config:
                chunk_size_min = processing_config.get("chunk_size_min")
                chunk_size_max = processing_config.get("chunk_size_max")
                chunk_overlap_pct = processing_config.get("chunk_overlap_pct")
                
                # Temporarily override chunker config
                if chunk_size_min is not None:
                    original_min = self.chunker.min_tokens
                    self.chunker.min_tokens = chunk_size_min
                    logger.info(f"Using custom chunk_size_min: {chunk_size_min}")
                
                if chunk_size_max is not None:
                    original_max = self.chunker.max_tokens
                    self.chunker.max_tokens = chunk_size_max
                    logger.info(f"Using custom chunk_size_max: {chunk_size_max}")
                
                if chunk_overlap_pct is not None:
                    original_overlap = self.chunker.overlap_pct
                    self.chunker.overlap_pct = chunk_overlap_pct
                    logger.info(f"Using custom chunk_overlap_pct: {chunk_overlap_pct}")
            
            if extraction_result.markdown:
                logger.info(
                    "Using markdown-aware chunking",
                    file_id=file_id,
                    markdown_length=len(extraction_result.markdown),
                )
                chunks: List[Chunk] = self.chunker.chunk_markdown(
                    extraction_result.markdown,
                    detected_languages=detected_languages,
                )
            else:
                chunks: List[Chunk] = self.chunker.chunk(
                    extraction_result.text,
                    page_number=None,
                    detected_languages=detected_languages,
                )
            
            # Restore original chunker config
            if processing_config:
                if chunk_size_min is not None:
                    self.chunker.min_tokens = original_min
                if chunk_size_max is not None:
                    self.chunker.max_tokens = original_max
                if chunk_overlap_pct is not None:
                    self.chunker.overlap_pct = original_overlap
            
            total_chunks = len(chunks)
            logger.info(
                "Chunking complete",
                file_id=file_id,
                chunk_count=total_chunks,
            )
            
            self.history.log_stage_complete(
                file_id, "chunking",
                f"Created {total_chunks} chunks from {len(extraction_result.text)} characters",
                metadata={"chunk_count": total_chunks, "text_length": len(extraction_result.text)},
                started_at=chunking_start
            )
            
            self.postgres_service.update_status(
                file_id=file_id,
                stage="chunking",
                progress=38,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
                status_message=f"Created {total_chunks} chunks",
            )
            
            # Stage 4.5: LLM Cleanup (optional)
            # Check if LLM cleanup is enabled via config override or default setting
            llm_cleanup_enabled = processing_config.get("llm_cleanup_enabled", False) if processing_config else False
            llm_cleanup_enabled = llm_cleanup_enabled or (self.llm_cleanup and self.llm_cleanup.enabled)
            
            if llm_cleanup_enabled and self.llm_cleanup:
                cleanup_start = self.history.log_stage_start(
                    file_id, "cleanup",
                    f"Starting LLM cleanup on {total_chunks} chunks",
                    metadata={"original_chunk_count": total_chunks}
                )
                logger.info(
                    "Stage 4.5: Starting LLM cleanup",
                    file_id=file_id,
                    chunk_count=total_chunks,
                )
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="cleanup",
                    progress=40,
                    chunks_processed=0,
                    total_chunks=total_chunks,
                    status_message=f"Cleaning up {total_chunks} chunks",
                )
                
                # Run async cleanup in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    original_chunk_count = len(chunks)
                    chunks = loop.run_until_complete(
                        self.llm_cleanup.cleanup_chunks(chunks)
                    )
                    logger.info(
                        "LLM cleanup complete",
                        file_id=file_id,
                        chunk_count=len(chunks),
                    )
                    
                    self.history.log_stage_complete(
                        file_id, "cleanup",
                        f"LLM cleanup: {original_chunk_count} → {len(chunks)} chunks",
                        metadata={"original_count": original_chunk_count, "final_count": len(chunks)},
                        started_at=cleanup_start
                    )
                finally:
                    loop.close()
                
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="cleanup",
                    progress=48,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                    status_message="Cleanup complete",
                )
            else:
                logger.debug("LLM cleanup disabled, skipping", file_id=file_id)
                self.history.log_skip(
                    file_id, "cleanup", "llm_cleanup",
                    "LLM cleanup was disabled",
                    metadata={"chunk_count": total_chunks}
                )
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="chunking",
                    progress=48,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                    status_message="Chunks ready for embedding",
                )
            
            # Store chunks in PostgreSQL (after cleanup)
            chunk_dicts = [c.to_dict() for c in chunks]
            self.postgres_service.insert_chunks(file_id, chunk_dicts)
            
            # Stage 4.55: Entity Extraction for Knowledge Graph (LEGACY - DISABLED)
            # Entity extraction is now schema-driven and runs on-demand via
            # the /extract API after document processing completes.
            # The config flag is ignored; this block is kept for reference only.
            entity_extraction_enabled = False
            if entity_extraction_enabled:
                try:
                    from processors.entity_extractor import EntityExtractor
                    from services.graph_service import GraphService
                    
                    logger.info(
                        "Stage 4.55: Starting entity extraction for knowledge graph",
                        file_id=file_id,
                    )
                    self.postgres_service.update_status(
                        file_id=file_id,
                        stage="entity_extraction",
                        progress=51,
                        status_message="Extracting entities for knowledge graph",
                    )
                    
                    # Get library_id for graph filtering (Document nodes store library_id)
                    library_id = None
                    conn = self.postgres_service._get_connection(self._current_rls_context)
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT library_id FROM data_files WHERE file_id = %s",
                            (file_id,),
                        )
                        row = cur.fetchone()
                        if row:
                            library_id = str(row[0])
                    finally:
                        self.postgres_service._return_connection(conn)
                    
                    entity_extractor = EntityExtractor(
                        litellm_base_url=os.getenv("LITELLM_BASE_URL", "http://litellm:4000"),
                        litellm_api_key=os.getenv("LITELLM_API_KEY", ""),
                    )
                    
                    graph = GraphService()
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        connected = loop.run_until_complete(graph.connect())
                        if connected:
                            entity_count = loop.run_until_complete(
                                entity_extractor.extract_and_store_graph(
                                    text=extraction_result.text,
                                    file_id=file_id,
                                    filename=original_filename,
                                    owner_id=user_id,
                                    visibility=visibility,
                                    graph_service=graph,
                                    library_id=library_id,
                                )
                            )
                            logger.info(
                                "Entity extraction complete",
                                file_id=file_id,
                                entity_count=entity_count,
                            )
                        else:
                            logger.debug("Neo4j not available, skipping entity extraction", file_id=file_id)
                    finally:
                        loop.run_until_complete(graph.disconnect())
                        loop.close()
                except Exception as e:
                    logger.warning(
                        "Entity extraction failed (non-blocking)",
                        file_id=file_id,
                        error=str(e),
                    )
            else:
                logger.debug("Entity extraction disabled, skipping", file_id=file_id)
            
            # Stage 4.6: Markdown and Image Generation
            markdown_start = time.time()
            try:
                self.history.log_stage_start(
                    file_id, "markdown_generation",
                    "Starting markdown and image generation",
                    metadata={"text_length": len(extraction_result.text)}
                )
                logger.info(
                    "Stage 4.6: Starting markdown and image generation",
                    file_id=file_id
                )
                
                # Extract images from the original file
                images_metadata = []
                images_data = []
                try:
                    self.history.log_substep(
                        file_id, "markdown_generation", "image_extraction",
                        "Extracting images from document"
                    )
                    images_metadata, images_data = self.image_extractor.extract(
                        temp_file_path, 
                        mime_type=mime_type
                    )
                    logger.info(
                        "Image extraction complete",
                        file_id=file_id,
                        image_count=len(images_data)
                    )
                except Exception as img_err:
                    logger.warning(
                        "Image extraction failed (non-fatal)",
                        file_id=file_id,
                        error=str(img_err),
                        exc_info=True
                    )
                    self.history.log_substep(
                        file_id, "markdown_generation", "image_extraction_failed",
                        f"Image extraction failed: {str(img_err)}",
                        metadata={"error": str(img_err)}
                    )
                
                # Generate markdown
                try:
                    self.history.log_substep(
                        file_id, "markdown_generation", "markdown_generation",
                        "Generating markdown from extracted text"
                    )
                    
                    # Get extraction method from metadata
                    extraction_method = extraction_result.metadata.get("extraction_method", "simple")
                    
                    # Prepare image references (HTMLRenderer will convert to API URLs)
                    image_refs = []
                    for i, img_meta in enumerate(images_metadata):
                        # Use relative path - HTMLRenderer converts to API URLs
                        image_refs.append({
                            'path': f'images/image_{i}.png',
                            'caption': f'Image {i+1}'
                        })
                    
                    # Use existing markdown from Marker if available, otherwise generate
                    if extraction_result.markdown:
                        logger.info(
                            "Using markdown from extraction",
                            file_id=file_id,
                            extraction_method=extraction_method,
                            markdown_length=len(extraction_result.markdown)
                        )
                        markdown_content = extraction_result.markdown
                        
                        # Insert image references if we have images
                        if image_refs:
                            markdown_content = self.markdown_generator._insert_images_by_position(
                                markdown_content, 
                                image_refs
                            )
                        
                        # Extract metadata
                        md_metadata = self.markdown_generator._extract_metadata(markdown_content)
                    else:
                        # Generate markdown from text (for simple extraction or when Marker didn't produce markdown)
                        logger.info(
                            "Generating markdown from text",
                            file_id=file_id,
                            extraction_method=extraction_method
                        )
                        markdown_content, md_metadata = self.markdown_generator.generate(
                            extraction_result.text,
                            extraction_method=extraction_method,
                            images=image_refs if image_refs else None
                        )
                    
                    logger.info(
                        "Markdown generation complete",
                        file_id=file_id,
                        markdown_length=len(markdown_content),
                        heading_count=md_metadata.get('heading_count', 0),
                        extraction_method=extraction_method
                    )
                except Exception as md_err:
                    logger.warning(
                        "Markdown generation failed (non-fatal)",
                        file_id=file_id,
                        error=str(md_err),
                        exc_info=True
                    )
                    self.history.log_substep(
                        file_id, "markdown_generation", "markdown_generation_failed",
                        f"Markdown generation failed: {str(md_err)}",
                        metadata={"error": str(md_err)}
                    )
                    markdown_content = None
                
                # Upload markdown and images to MinIO
                markdown_path = None
                images_path = None
                image_count = 0
                
                if markdown_content:
                    try:
                        self.history.log_substep(
                            file_id, "markdown_generation", "upload_markdown",
                            "Uploading markdown to MinIO"
                        )
                        # Use same path prefix as original file storage
                        # storage_path format: {prefix}/{file_id}/{filename}
                        # Extract prefix by removing file_id and filename parts
                        path_parts = storage_path.rsplit("/", 2)
                        if len(path_parts) >= 2:
                            base_path = path_parts[0] + "/" + file_id
                        else:
                            # Fallback to legacy format
                            base_path = f"{user_id}/{file_id}"
                        markdown_path = f"{base_path}/content.md"
                        
                        # Upload markdown to MinIO
                        import io
                        markdown_bytes = markdown_content.encode('utf-8')
                        self.file_service.client.put_object(
                            bucket_name=self.file_service.bucket,
                            object_name=markdown_path,
                            data=io.BytesIO(markdown_bytes),
                            length=len(markdown_bytes),
                            content_type='text/markdown'
                        )
                        
                        logger.info(
                            "Markdown uploaded to MinIO",
                            file_id=file_id,
                            path=markdown_path
                        )
                    except Exception as upload_err:
                        logger.warning(
                            "Markdown upload failed (non-fatal)",
                            file_id=file_id,
                            error=str(upload_err),
                            exc_info=True
                        )
                        markdown_path = None
                
                # Upload images
                if images_data:
                    try:
                        self.history.log_substep(
                            file_id, "markdown_generation", "upload_images",
                            f"Uploading {len(images_data)} images to MinIO"
                        )
                        # Use same path prefix as original file storage
                        path_parts = storage_path.rsplit("/", 2)
                        if len(path_parts) >= 2:
                            base_path = path_parts[0] + "/" + file_id
                        else:
                            base_path = f"{user_id}/{file_id}"
                        images_path = f"{base_path}/images"
                        
                        import io
                        for i, (img_data, img_meta) in enumerate(zip(images_data, images_metadata)):
                            image_path = f"{images_path}/image_{i}.png"
                            self.file_service.client.put_object(
                                bucket_name=self.file_service.bucket,
                                object_name=image_path,
                                data=io.BytesIO(img_data),
                                length=len(img_data),
                                content_type='image/png'
                            )
                        
                        image_count = len(images_data)

                        # Save image metadata JSON for duplicate/decorative filtering
                        if images_metadata:
                            import json as json_mod
                            meta_json = json_mod.dumps(images_metadata, default=str).encode("utf-8")
                            meta_path = f"{images_path}/metadata.json"
                            self.file_service.client.put_object(
                                bucket_name=self.file_service.bucket,
                                object_name=meta_path,
                                data=io.BytesIO(meta_json),
                                length=len(meta_json),
                                content_type='application/json'
                            )

                        logger.info(
                            "Images uploaded to MinIO",
                            file_id=file_id,
                            image_count=image_count,
                            path=images_path
                        )
                    except Exception as upload_err:
                        logger.warning(
                            "Image upload failed (non-fatal)",
                            file_id=file_id,
                            error=str(upload_err),
                            exc_info=True
                        )
                        images_path = None
                        image_count = 0
                
                # Update database with markdown/image paths
                # IMPORTANT: Use RLS context to ensure we can update the file record
                try:
                    conn = self.postgres_service._get_connection(self._current_rls_context)
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            """UPDATE data_files 
                               SET markdown_path = %s, 
                                   has_markdown = %s, 
                                   images_path = %s, 
                                   image_count = %s
                               WHERE file_id = %s""",
                            (markdown_path,
                             markdown_path is not None,
                             images_path,
                             image_count,
                             file_id)
                        )
                        rows_updated = cur.rowcount
                        conn.commit()
                        cur.close()
                        if rows_updated == 0:
                            logger.warning(
                                "Markdown update affected 0 rows - RLS may be blocking",
                                file_id=file_id,
                                user_id=getattr(self._current_rls_context.state, "user_id", None) if self._current_rls_context else None
                            )
                    finally:
                        self.postgres_service._return_connection(conn)
                    logger.info(
                        "Database updated with markdown/image paths",
                        file_id=file_id,
                        has_markdown=markdown_path is not None,
                        image_count=image_count,
                        rows_updated=rows_updated
                    )
                except Exception as db_err:
                    logger.error(
                        "Failed to update database with markdown paths",
                        file_id=file_id,
                        error=str(db_err),
                        exc_info=True
                    )
                
                self.history.log_stage_complete(
                    file_id=file_id,
                    stage="markdown_generation",
                    message="Markdown and image generation complete",
                    metadata={
                        "has_markdown": markdown_path is not None,
                        "image_count": image_count,
                        "markdown_length": len(markdown_content) if markdown_content else 0
                    },
                    started_at=markdown_start
                )
                
            except Exception as e:
                logger.warning(
                    "Markdown/image generation stage failed (non-fatal)",
                    file_id=file_id,
                    error=str(e),
                    exc_info=True
                )
                self.history.log_step(
                    file_id=file_id,
                    stage="markdown_generation",
                    step_name="markdown_generation_error",
                    status="failed",
                    error_message=f"Markdown/image generation failed: {str(e)}",
                    metadata={"error": str(e), "stack_trace": traceback.format_exc()},
                    started_at=markdown_start
                )
            
            # Stage 5: Embedding
            embedding_start = self.history.log_stage_start(
                file_id, "embedding",
                f"Starting embedding generation for {len(chunks)} chunks",
                metadata={"chunk_count": len(chunks)}
            )
            logger.info(
                "Stage 5: Starting embedding generation",
                file_id=file_id,
                chunk_count=total_chunks,
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="embedding",
                progress=50,
                chunks_processed=0,
                total_chunks=total_chunks,
                status_message=f"Embedding {total_chunks} chunks",
            )
            
            # Generate dense embeddings
            chunk_texts = [c.text for c in chunks]
            
            text_embed_start = time.time()
            logger.debug("Generating dense embeddings via embedding-api", file_id=file_id, chunk_count=len(chunk_texts))
            # Use sync embedding client (no need for async loop)
            embeddings = self.embedder.embed_chunks_sync(chunk_texts)
            logger.info(
                "Dense embeddings generated via embedding-api",
                file_id=file_id,
                embedding_count=len(embeddings),
            )
            embedding_dimension = self.embedder.get_embedding_dimension()
            self.history.log_substep(
                file_id, "embedding", "text_embeddings",
                f"Generated {len(embeddings)} text embeddings via embedding-api ({embedding_dimension}-d)",
                metadata={"embedding_count": len(embeddings), "model": "bge-large-en-v1.5", "dimension": embedding_dimension},
                started_at=text_embed_start
            )
            
            self.postgres_service.update_status(
                file_id=file_id,
                stage="embedding",
                progress=78,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
                status_message="Embeddings generated",
            )
            
            # Generate ColPali embeddings for PDF pages (if available and enabled)
            # Check per-job processing_config first, then fall back to worker-level setting
            colpali_enabled_for_job = (
                processing_config.get("colpali_enabled", self.colpali is not None)
                if processing_config else (self.colpali is not None)
            )
            page_embeddings = None
            if extraction_result.page_images and mime_type == "application/pdf" and self.colpali is not None and colpali_enabled_for_job:
                colpali_start = time.time()
                logger.info(
                    "Generating ColPali visual embeddings",
                    file_id=file_id,
                    page_count=len(extraction_result.page_images),
                )
                try:
                    page_image_dicts = [
                        {"page_number": i + 1, "image_path": path}
                        for i, path in enumerate(extraction_result.page_images)
                    ]
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        page_embeddings = loop.run_until_complete(
                            self.colpali.embed_pages(extraction_result.page_images)
                        )
                    finally:
                        loop.close()
                    
                    if page_embeddings:
                        logger.info(
                            "ColPali embeddings generated",
                            file_id=file_id,
                            page_count=len(page_embeddings),
                        )
                        self.history.log_substep(
                            file_id, "embedding", "colpali_embeddings",
                            f"Generated {len(page_embeddings)} ColPali page embeddings (128-d pooled)",
                            metadata={"page_count": len(page_embeddings), "model": "vidore/colpali", "dimension": 128},
                            started_at=colpali_start
                        )
                except Exception as e:
                    logger.warning(
                        "ColPali embedding generation failed",
                        file_id=file_id,
                        error=str(e),
                    )
                    self.history.log_error(
                        file_id, "embedding", "colpali_embeddings", e,
                        metadata={"page_count": len(extraction_result.page_images)},
                        started_at=colpali_start
                    )
            
            # Complete embedding stage
            self.history.log_stage_complete(
                file_id, "embedding",
                f"Completed embedding generation: {len(embeddings)} text, {len(page_embeddings) if page_embeddings else 0} visual",
                metadata={
                    "text_embedding_count": len(embeddings),
                    "visual_embedding_count": len(page_embeddings) if page_embeddings else 0
                },
                started_at=embedding_start
            )
            
            # Stage 6: Indexing
            indexing_start = self.history.log_stage_start(
                file_id, "indexing",
                f"Starting Milvus indexing for {len(chunks)} chunks",
                metadata={"chunk_count": len(chunks), "has_visual": bool(page_embeddings)}
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="indexing",
                progress=85,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
                status_message="Indexing in vector database",
            )
            
            # Insert text chunks into Milvus
            chunk_dicts_for_milvus = [
                {
                    "text": c.text,
                    "chunk_index": c.chunk_index,
                    "page_number": c.page_number,
                    "char_offset": c.char_offset,
                    "section_heading": c.section_heading,
                    "language": c.language,
                }
                for c in chunks
            ]
            
            milvus_text_start = time.time()
            vector_count = self.milvus_service.insert_text_chunks(
                file_id=file_id,
                user_id=user_id,
                chunks=chunk_dicts_for_milvus,
                embeddings=embeddings,
                content_hash=content_hash,
                visibility=visibility,
                role_ids=role_ids,
            )
            self.history.log_substep(
                file_id, "indexing", "milvus_text_insert",
                f"Inserted {vector_count} text vectors into Milvus",
                metadata={"vector_count": vector_count},
                started_at=milvus_text_start
            )
            
            # Insert page images if available
            if page_embeddings and extraction_result.page_images:
                milvus_visual_start = time.time()
                page_image_dicts = [
                    {"page_number": i + 1, "image_path": path}
                    for i, path in enumerate(extraction_result.page_images)
                ]
                
                page_vector_count = self.milvus_service.insert_page_images(
                    file_id=file_id,
                    user_id=user_id,
                    page_images=page_image_dicts,
                    page_embeddings=page_embeddings,
                    content_hash=content_hash,
                    visibility=visibility,
                    role_ids=role_ids,
                )
                
                vector_count += page_vector_count
                self.history.log_substep(
                    file_id, "indexing", "milvus_visual_insert",
                    f"Inserted {page_vector_count} visual vectors into Milvus",
                    metadata={"page_vector_count": page_vector_count},
                    started_at=milvus_visual_start
                )
            
            # Complete indexing stage
            self.history.log_stage_complete(
                file_id, "indexing",
                f"Completed Milvus indexing: {vector_count} total vectors",
                metadata={"total_vectors": vector_count, "text_chunks": len(chunks), "page_vectors": len(page_embeddings) if page_embeddings else 0},
                started_at=indexing_start
            )
            
            # Update final metadata (pass RLS context)
            processing_duration = int(time.time() - start_time)
            
            self.postgres_service.update_file_metadata(
                file_id=file_id,
                chunk_count=total_chunks,
                vector_count=vector_count,
                processing_duration_seconds=processing_duration,
                request=self._current_rls_context,
            )
            
            # Log final completion
            self.history.log_stage_complete(
                file_id, "completed",
                f"Processing complete: {total_chunks} chunks, {vector_count} vectors in {processing_duration}s",
                metadata={
                    "total_chunks": total_chunks,
                    "total_vectors": vector_count,
                    "processing_duration_seconds": processing_duration,
                    "stages": ["parsing", "chunking", "cleanup" if llm_cleanup_enabled else None, "embedding", "indexing"],
                },
                started_at=start_time
            )
            
            # Stage 7: Multi-Flow Processing (optional, non-blocking)
            if processing_config and processing_config.get("multi_flow_enabled", False):
                logger.info(
                    "Stage 7: Starting multi-flow comparison",
                    file_id=file_id,
                    marker_enabled=processing_config.get("marker_enabled", False),
                    colpali_enabled=processing_config.get("colpali_enabled", True),
                )
                
                try:
                    from processors.multi_flow_processor import MultiFlowProcessor
                    # asyncio is already imported at module level
                    
                    # Build config with multi-flow settings
                    multi_flow_config = {
                        **self.config,
                        "max_parallel_strategies": processing_config.get("max_parallel_strategies", 3),
                        "marker_enabled": processing_config.get("marker_enabled", False),
                        "colpali_enabled": processing_config.get("colpali_enabled", True),
                    }
                    
                    multi_flow = MultiFlowProcessor(config=multi_flow_config)
                    
                    # Process document with multiple strategies
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        results = loop.run_until_complete(
                            multi_flow.process_document(
                                file_path=temp_file_path,
                                mime_type=mime_type,
                                file_id=file_id,
                                original_filename=original_filename,
                            )
                        )
                    finally:
                        loop.close()
                    
                    logger.info(
                        "Multi-flow comparison completed",
                        file_id=file_id,
                        strategies_run=len(results),
                        results_summary={
                            strategy_name: {
                                "success": result.success,
                                "text_length": len(result.text) if result.text else 0,
                                "processing_time": result.processing_time_seconds,
                            }
                            for strategy_name, result in results.items()
                        },
                    )
                    
                    # Record strategy results in database
                    try:
                        conn = self.postgres_service._get_connection(self._current_rls_context)
                        try:
                            with conn.cursor() as cur:
                                for strategy_name, result in results.items():
                                    # Calculate metrics
                                    text_length = len(result.text) if result.text else 0
                                    # Chunk count from metadata or embedding count
                                    chunk_count = result.metadata.get("chunk_count", 0) if result.metadata else 0
                                    embedding_count = len(result.embeddings) if result.embeddings else 0
                                    visual_embedding_count = len(result.visual_embeddings) if result.visual_embeddings else 0
                                    
                                    # Insert or update strategy result
                                    cur.execute("""
                                        INSERT INTO processing_strategy_results (
                                            file_id, processing_strategy, success,
                                            text_length, chunk_count, embedding_count, visual_embedding_count,
                                            processing_time_seconds, error_message, metadata
                                        ) VALUES (
                                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                                        )
                                        ON CONFLICT (file_id, processing_strategy)
                                        DO UPDATE SET
                                            success = EXCLUDED.success,
                                            text_length = EXCLUDED.text_length,
                                            chunk_count = EXCLUDED.chunk_count,
                                            embedding_count = EXCLUDED.embedding_count,
                                            visual_embedding_count = EXCLUDED.visual_embedding_count,
                                            processing_time_seconds = EXCLUDED.processing_time_seconds,
                                            error_message = EXCLUDED.error_message,
                                            metadata = EXCLUDED.metadata,
                                            created_at = NOW()
                                    """, (
                                        file_id,
                                        strategy_name,  # Already a string
                                        result.success,
                                        text_length,
                                        chunk_count,
                                        embedding_count,
                                        visual_embedding_count,
                                        result.processing_time_seconds,
                                        result.error if not result.success else None,
                                        json.dumps(result.metadata) if result.metadata else '{}',
                                    ))
                                conn.commit()
                                logger.info(
                                    "Recorded processing strategy results",
                                    file_id=file_id,
                                    strategies_recorded=len(results),
                                )
                        finally:
                            self.postgres_service._return_connection(conn)
                    except Exception as db_error:
                        logger.warning(
                            "Failed to record strategy results (non-fatal)",
                            file_id=file_id,
                            error=str(db_error),
                        )
                    
                except ImportError as e:
                    logger.warning(
                        "Multi-flow requested but MultiFlowProcessor not available",
                        error=str(e),
                    )
                except Exception as e:
                    logger.error(
                        "Multi-flow comparison failed (non-fatal)",
                        file_id=file_id,
                        error=str(e),
                        exc_info=True,
                    )
            
            # Stage 8: Completed
            self.postgres_service.update_status(
                file_id=file_id,
                stage="completed",
                progress=100,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
                pages_processed=page_count,
                total_pages=page_count,
                status_message="Processing complete",
            )
            
            logger.info(
                "Job processing complete",
                job_id=job_id,
                file_id=file_id,
                processing_time_seconds=processing_duration,
                chunk_count=total_chunks,
                vector_count=vector_count,
                multi_flow_enabled=processing_config.get("multi_flow_enabled", False) if processing_config else False,
            )
            
            # Stage 9a: Check library classification rules (non-blocking)
            self._check_library_classification(
                file_id=file_id,
                user_id=user_id,
            )
            
            # Stage 9b: Check library triggers (non-blocking)
            self._check_library_triggers(
                file_id=file_id,
                user_id=user_id,
                delegation_token=delegation_token,
            )
        
        except asyncio.TimeoutError as e:
            processing_duration = int(time.time() - start_time)
            error_msg = f"Processing exceeded {timeout_seconds}s timeout for {page_count}-page document"
            
            self.history.log_error(
                file_id, "failed", "timeout_error", e,
                metadata={
                    "timeout_seconds": timeout_seconds,
                    "processing_duration": processing_duration,
                    "page_count": page_count,
                },
                started_at=start_time
            )
            
            # Timeout is considered permanent (document too large)
            self.error_handler.mark_failed(file_id, e)
            
            logger.error(
                "Job processing timeout",
                file_id=file_id,
                timeout_seconds=timeout_seconds,
                processing_time_seconds=processing_duration,
            )
            raise
        
        except Exception as e:
            error_msg = str(e)
            
            # Log error to history
            self.history.log_error(
                file_id, "failed", "processing_error", e,
                metadata={"is_transient": self.error_handler.is_transient_error(e)},
                started_at=start_time
            )
            
            # Build job_data dict for error handler
            job_data_for_retry = {
                "job_id": job_id,
                "file_id": file_id,
                "user_id": user_id,
                "storage_path": storage_path,
                "mime_type": mime_type,
                "original_filename": original_filename,
                "trace_id": trace_id,
            }
            
            # Use ErrorHandler to manage retry logic
            # Pass job_retry_count to prevent infinite loops when file doesn't exist in DB
            if self.error_handler.should_retry(file_id, e, job_retry_count):
                # Transient error - requeue for retry
                self.error_handler.requeue_job(job_id, file_id, job_data_for_retry, e)
                logger.warning(
                    "Job processing failed (transient, will retry)",
                    job_id=job_id,
                    file_id=file_id,
                    error=error_msg,
                )
                # Don't raise - allow Redis to handle retry
                return
            else:
                # Permanent error or max retries exceeded
                self.error_handler.mark_failed(file_id, e)
                logger.error(
                    "Job processing failed",
                    job_id=job_id,
                    file_id=file_id,
                    error=error_msg,
                    exc_info=True,
                )
                raise
        
        finally:
            # Cleanup temporary file
            if temp_file_path:
                self.file_service.cleanup_temp_file(temp_file_path)
            
            # Clear RLS context after processing
            self._current_rls_context = None
    
    def _check_file_exists(self, file_id: str) -> bool:
        """
        Check if a file exists in the database.
        
        Note: This bypasses RLS by not setting session variables, since the worker
        needs to be able to check any file's existence regardless of ownership.
        """
        try:
            conn = self.postgres_service._get_connection()  # No RLS context
            try:
                with conn.cursor() as cur:
                    # Reset any previous RLS session vars to ensure we see all files
                    cur.execute("RESET app.user_id;")
                    cur.execute("RESET app.role_ids;")
                    cur.execute(
                        "SELECT 1 FROM data_files WHERE file_id = %s LIMIT 1",
                        (file_id,)
                    )
                    result = cur.fetchone()
                    logger.debug(
                        "File existence check",
                        file_id=file_id,
                        exists=result is not None,
                    )
                    return result is not None
            finally:
                self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.warning(
                "Error checking if file exists",
                file_id=file_id,
                error=str(e),
            )
            # Assume file exists if we can't check - let processing handle it
            return True
    
    def _claim_pending_messages(self) -> int:
        """
        Claim and process pending messages that were delivered but not acknowledged.
        
        This handles the case where a previous worker instance received messages
        but crashed before acknowledging them. Messages idle for >60 seconds
        are claimed by this worker.
        
        Returns:
            Number of messages claimed
        """
        claimed_count = 0
        try:
            # Check for pending messages in the consumer group
            pending_info = self.redis_client.xpending(
                self.stream_name,
                self.consumer_group,
            )
            
            if not pending_info or pending_info['pending'] == 0:
                return 0
            
            pending_count = pending_info['pending']
            logger.info(
                "Found pending messages in consumer group",
                stream=self.stream_name,
                pending_count=pending_count,
            )
            
            # Get detailed info about pending messages
            # Only claim messages that have been idle for > 60 seconds
            pending_messages = self.redis_client.xpending_range(
                self.stream_name,
                self.consumer_group,
                min='-',
                max='+',
                count=100,  # Process up to 100 pending messages at a time
            )
            
            for msg_info in pending_messages:
                message_id = msg_info['message_id']
                idle_time_ms = msg_info['time_since_delivered']
                times_delivered = int(msg_info.get("times_delivered", 0))
                
                logger.info(
                    "Inspecting pending message",
                    message_id=message_id,
                    idle_time_seconds=round(idle_time_ms / 1000, 1),
                    times_delivered=times_delivered,
                )

                # Dead-letter poison-pill jobs that have failed too many times.
                if times_delivered > 5:
                    logger.warning(
                        "Dead-lettering pending message after too many deliveries",
                        message_id=message_id,
                        times_delivered=times_delivered,
                    )
                    self.redis_client.xack(
                        self.stream_name,
                        self.consumer_group,
                        message_id,
                    )
                    continue
                
                # Only claim messages idle for > 60 seconds (60000ms)
                if idle_time_ms > 60000:
                    try:
                        # Claim the message for this consumer
                        claimed = self.redis_client.xclaim(
                            self.stream_name,
                            self.consumer_group,
                            self.consumer_name,
                            min_idle_time=60000,  # 60 seconds
                            message_ids=[message_id],
                        )
                        
                        if claimed:
                            for msg_id, msg_data in claimed:
                                claimed_count += 1
                                file_id = msg_data.get("file_id")
                                logger.info(
                                    "Claimed pending message",
                                    message_id=msg_id,
                                    idle_time_seconds=idle_time_ms / 1000,
                                    file_id=file_id,
                                )
                                
                                # Check if file still exists in database before processing
                                file_exists = self._check_file_exists(file_id)
                                if not file_exists:
                                    logger.warning(
                                        "Acknowledging orphaned message - file no longer exists",
                                        message_id=msg_id,
                                        file_id=file_id,
                                    )
                                    # Acknowledge the orphaned message to remove it from pending
                                    self.redis_client.xack(
                                        self.stream_name,
                                        self.consumer_group,
                                        msg_id
                                    )
                                    continue
                                
                                # Process the claimed message
                                job_id = msg_data.get("job_id")
                                trace_id = msg_data.get("trace_id", f"trace-{uuid.uuid4()}")
                                
                                try:
                                    self.process_job(job_id, msg_data, trace_id)
                                    # Acknowledge the message
                                    self.redis_client.xack(
                                        self.stream_name,
                                        self.consumer_group,
                                        msg_id
                                    )
                                except ValueError as e:
                                    # File not found or similar - acknowledge to prevent infinite retry
                                    if "not found" in str(e).lower():
                                        logger.warning(
                                            "Acknowledging failed message - file not found",
                                            message_id=msg_id,
                                            file_id=file_id,
                                            error=str(e),
                                        )
                                        self.redis_client.xack(
                                            self.stream_name,
                                            self.consumer_group,
                                            msg_id
                                        )
                                    else:
                                        logger.error(
                                            "Failed to process claimed message",
                                            message_id=msg_id,
                                            error=str(e),
                                            exc_info=True,
                                        )
                                except Exception as e:
                                    logger.error(
                                        "Failed to process claimed message",
                                        message_id=msg_id,
                                        error=str(e),
                                        exc_info=True,
                                    )
                                    # Don't ack - will be retried
                    
                    except Exception as e:
                        logger.warning(
                            "Failed to claim message",
                            message_id=message_id,
                            error=str(e),
                        )
        
        except Exception as e:
            logger.error(
                "Error checking pending messages",
                error=str(e),
                exc_info=True,
            )
        
        return claimed_count
    
    def run(self):
        """Main worker loop - consume and process jobs."""
        self.running = True
        logger.info("Worker started", worker_id=self.worker_id)
        
        # On startup, claim any pending messages from crashed workers
        logger.info("Checking for pending messages from previous workers...")
        claimed = self._claim_pending_messages()
        if claimed:
            logger.info(f"Claimed and processed {claimed} pending messages")
        
        # Heartbeat counter for periodic logging
        heartbeat_counter = 0
        heartbeat_interval = 12  # Log every 60 seconds (12 * 5 second blocks)
        
        # Pending message check counter (check every 5 minutes = 60 heartbeats)
        pending_check_counter = 0
        pending_check_interval = 60
        
        while self.running:
            try:
                # Periodic heartbeat logging
                heartbeat_counter += 1
                if heartbeat_counter >= heartbeat_interval:
                    logger.info(
                        "Worker heartbeat - listening for jobs",
                        worker_id=self.worker_id,
                        stream=self.stream_name,
                        consumer_group=self.consumer_group,
                    )
                    heartbeat_counter = 0
                
                # Periodically check for pending messages (every 5 minutes)
                pending_check_counter += 1
                if pending_check_counter >= pending_check_interval:
                    claimed = self._claim_pending_messages()
                    if claimed:
                        logger.info(f"Periodic check: claimed and processed {claimed} pending messages")
                    pending_check_counter = 0
                    
                    TextExtractor.cleanup_if_idle()
                
                # Read from stream (block for 5 seconds)
                messages = self.redis_client.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={self.stream_name: '>'},
                    count=1,
                    block=5000,  # 5 second timeout
                )
                
                if not messages:
                    continue
                
                # Reset heartbeat counter when we get a message
                heartbeat_counter = 0
                logger.info("Received job from Redis stream")
                
                # Process message
                for stream, message_list in messages:
                    for message_id, message_data in message_list:
                        job_id = message_data.get("job_id")
                        trace_id = message_data.get("trace_id", f"trace-{uuid.uuid4()}")
                        
                        try:
                            self.process_job(job_id, message_data, trace_id)
                            
                            # Acknowledge message
                            self.redis_client.xack(
                                self.stream_name,
                                self.consumer_group,
                                message_id
                            )
                            
                            # Trim stream to keep only last 10000 messages (prevent memory bloat)
                            # MAXLEN ~ 10000 uses approximate trimming for better performance
                            try:
                                self.redis_client.xtrim(
                                    self.stream_name,
                                    maxlen=10000,
                                    approximate=True
                                )
                            except Exception as trim_err:
                                logger.warning(
                                    "Failed to trim Redis stream",
                                    stream=self.stream_name,
                                    error=str(trim_err)
                                )
                            
                        except Exception as e:
                            logger.error(
                                "Job processing failed",
                                job_id=job_id,
                                trace_id=trace_id,
                                error=str(e),
                                exc_info=True,
                            )
                            # process_job requeues transient errors internally.
                            # If we reached this block, the job is permanently failed.
                            try:
                                self.redis_client.xack(
                                    self.stream_name,
                                    self.consumer_group,
                                    message_id,
                                )
                            except Exception as ack_error:
                                logger.warning(
                                    "Failed to acknowledge permanently failed message",
                                    message_id=message_id,
                                    error=str(ack_error),
                                )
                
            except RedisError as e:
                logger.error("Redis error", error=str(e), exc_info=True)
                time.sleep(5)  # Backoff before retry
                try:
                    self.redis_client.ping()
                except Exception:
                    logger.warning("Redis ping failed after error, reconnecting")
                    self._reconnect_redis()
            
            except KeyboardInterrupt:
                logger.info("Received interrupt signal")
                self.stop()
            
            except Exception as e:
                logger.error("Unexpected error in worker loop", error=str(e), exc_info=True)
                time.sleep(1)
        
        logger.info("Worker stopped")
    
    def stop(self):
        """Stop the worker gracefully."""
        logger.info("Stopping worker")
        self.running = False


def signal_handler(signum, frame):
    """Handle shutdown signals with GPU cleanup."""
    logger.info("Received signal, cleaning up", signal=signum)
    
    # Clean up GPU memory before exiting
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            # Reset all CUDA memory
            for i in range(torch.cuda.device_count()):
                with torch.cuda.device(i):
                    torch.cuda.empty_cache()
            logger.info("Cleared CUDA cache on shutdown")
    except Exception as e:
        logger.warning("Failed to clear CUDA cache on shutdown", error=str(e))
    
    sys.exit(0)


def main():
    """Main entry point."""
    logger.info("=" * 80)
    logger.info("DATA WORKER STARTING")
    logger.info("=" * 80)
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger.info("Signal handlers registered")
    
    # Load configuration
    config = Config().to_dict()
    logger.info(
        "Configuration loaded",
        redis_host=config.get("redis_host"),
        redis_port=config.get("redis_port"),
        stream_name=config.get("redis_stream_name"),
        consumer_group=config.get("redis_consumer_group"),
    )
    
    # Create and start worker
    worker = IngestWorker(config)
    
    try:
        logger.info("Connecting to services...")
        worker.connect()
        logger.info("=" * 80)
        logger.info("WORKER READY - Listening for jobs")
        logger.info("=" * 80)
        worker.run()
    except Exception as e:
        logger.error("Worker failed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        logger.info("Worker shutting down...")
        worker.disconnect()
        logger.info("Worker stopped")


if __name__ == "__main__":
    main()
