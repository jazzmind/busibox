#!/usr/bin/env python3
"""
Busibox File Ingestion Worker

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
import signal
import socket
import uuid
from datetime import datetime
from typing import List, Optional

import structlog
import redis as redis_sync
from redis.exceptions import RedisError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from shared.config import Config
from services.file_service import FileService
from services.postgres_service import PostgresService
from services.milvus_service import MilvusService
from processors.text_extractor import TextExtractor, ExtractionResult
from processors.chunker import Chunker, Chunk
from processors.embedder import Embedder
from processors.classifier import DocumentClassifier
from processors.metadata_extractor import MetadataExtractor
from processors.colpali import ColPaliEmbedder
from processors.llm_cleanup import LLMCleanup

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


class IngestWorker:
    """File ingestion worker that processes jobs from Redis Streams."""
    
    def __init__(self, config: dict = None):
        """Initialize worker with configuration."""
        if config is None:
            config = Config().to_dict()
        self.config = config
        self.worker_id = config.get("worker_id", socket.gethostname())
        self.stream_name = config.get("stream_name", "jobs:ingestion")
        self.consumer_group = config.get("consumer_group", "workers")
        self.consumer_name = f"{self.worker_id}-{os.getpid()}"
        self.running = False
        
        # Services (initialized in connect())
        self.redis_client: Optional[redis_sync.Redis] = None
        self.file_service: Optional[FileService] = None
        self.postgres_service: Optional[PostgresService] = None
        self.milvus_service: Optional[MilvusService] = None
        
        # Processors (initialized in connect())
        self.text_extractor: Optional[TextExtractor] = None
        self.chunker: Optional[Chunker] = None
        self.embedder: Optional[Embedder] = None
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
        self.milvus_service = MilvusService(self.config)
        self.milvus_service.connect()
        
        # Initialize processors
        self.text_extractor = TextExtractor(self.config)
        self.chunker = Chunker(self.config)
        self.embedder = Embedder(self.config)
        self.classifier = DocumentClassifier(self.config)
        self.metadata_extractor = MetadataExtractor(self.config)
        self.colpali = ColPaliEmbedder(self.config)
        self.llm_cleanup = LLMCleanup(self.config)
        
        logger.info("All services connected")
    
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
    
    def _get_timeout_seconds(self, page_count: int) -> int:
        """Calculate timeout based on document size."""
        if page_count < 10:
            return self.config.get("timeout_small", 300)  # 5 minutes
        elif page_count <= 50:
            return self.config.get("timeout_medium", 600)  # 10 minutes
        else:
            return self.config.get("timeout_large", 1200)  # 20 minutes
    
    def _is_transient_error(self, error: Exception) -> bool:
        """
        Determine if error is transient (should retry) or permanent.
        
        Transient errors:
        - Network timeouts
        - Service unavailable (503, 502)
        - Connection errors
        - Rate limiting (429)
        - Temporary service failures
        
        Permanent errors:
        - Corrupted files
        - Unsupported formats
        - Invalid data
        - Authentication failures (401)
        - Permission errors (403)
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Transient errors
        transient_indicators = [
            "timeout",
            "connection",
            "unavailable",
            "temporary",
            "retry",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
            "network",
            "socket",
            "refused",
        ]
        
        if any(indicator in error_str for indicator in transient_indicators):
            return True
        
        # Permanent errors
        permanent_indicators = [
            "corrupted",
            "invalid",
            "unsupported",
            "format",
            "malformed",
            "parse error",
            "401",
            "403",
            "404",  # File not found is permanent
            "valueerror",
            "typeerror",
        ]
        
        if any(indicator in error_str for indicator in permanent_indicators):
            return False
        
        # Check error type
        transient_types = (
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
            redis_sync.ConnectionError,
            redis_sync.TimeoutError,
        )
        
        if isinstance(error, transient_types):
            return True
        
        # Default: assume transient for unknown errors (safer to retry)
        return True
    
    def _check_duplicate(self, file_id: str, content_hash: str) -> bool:
        """Check if file with same content_hash already processed."""
        try:
            conn = self.postgres_service._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT file_id, chunk_count, vector_count
                        FROM ingestion_files
                        WHERE content_hash = %s
                        AND file_id IN (
                            SELECT file_id FROM ingestion_status
                            WHERE stage = 'completed'
                        )
                        ORDER BY created_at DESC
                        LIMIT 1
                    """, (content_hash,))
                    
                    existing = cur.fetchone()
                    if existing:
                        existing_file_id, chunk_count, vector_count = existing
                        
                        # Update current file to completed status
                        self.postgres_service.update_status(
                            file_id=file_id,
                            stage="completed",
                            progress=100,
                            chunks_processed=chunk_count,
                            total_chunks=chunk_count,
                        )
                        
                        # Update file metadata
                        self.postgres_service.update_file_metadata(
                            file_id=file_id,
                            chunk_count=chunk_count,
                            vector_count=vector_count,
                            processing_duration_seconds=0,  # Instant for duplicates
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
    
    def process_job(self, job_id: str, job_data: dict, trace_id: str):
        """
        Process a single ingestion job.
        
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
        
        start_time = time.time()
        temp_file_path = None
        
        try:
            logger.info(
                "Processing job",
                job_id=job_id,
                file_id=file_id,
                user_id=user_id,
                trace_id=trace_id,
            )
            
            # Get content_hash from database
            conn = self.postgres_service._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content_hash FROM ingestion_files WHERE file_id = %s",
                        (file_id,),  # Pass string directly, not UUID object
                    )
                    result = cur.fetchone()
                    if not result:
                        raise ValueError(f"File {file_id} not found in database")
                    content_hash = result[0]
            finally:
                self.postgres_service._return_connection(conn)
            
            # Check for duplicate
            if self._check_duplicate(file_id, content_hash):
                logger.info(
                    "Job completed (duplicate)",
                    file_id=file_id,
                    processing_time_seconds=time.time() - start_time,
                )
                return
            
            # Stage 1: Parsing
            logger.info(
                "Stage 1: Starting text extraction",
                file_id=file_id,
                mime_type=mime_type,
                filename=original_filename,
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="parsing",
                progress=10,
            )
            
            logger.debug("Downloading file from storage", file_id=file_id, storage_path=storage_path)
            temp_file_path = self.file_service.download(storage_path)
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
            
            extraction_result: ExtractionResult = self.text_extractor.extract(
                temp_file_path,
                mime_type,
            )
            
            # Restore original marker_enabled setting
            if processing_config and "marker_enabled" in processing_config:
                self.text_extractor.marker_enabled = original_marker_enabled
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
                progress=20,
                total_pages=page_count,
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
                progress=30,
            )
            
            metadata = self.metadata_extractor.extract(
                temp_file_path,
                mime_type,
                extraction_result.text,
            )
            
            # Update file metadata
            self.postgres_service.update_file_metadata(
                file_id=file_id,
                document_type=document_type,
                primary_language=primary_language,
                detected_languages=detected_languages,
                extracted_title=metadata.get("title"),
                extracted_author=metadata.get("author"),
                extracted_date=metadata.get("date"),
                extracted_keywords=metadata.get("keywords", []),
            )
            
            # Stage 4: Chunking
            logger.info(
                "Stage 4: Starting text chunking",
                file_id=file_id,
                text_length=len(extraction_result.text),
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="chunking",
                progress=40,
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
            
            chunks: List[Chunk] = self.chunker.chunk(
                extraction_result.text,
                page_number=None,  # Will be set per chunk if available
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
            
            self.postgres_service.update_status(
                file_id=file_id,
                stage="chunking",
                progress=45,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
            )
            
            # Stage 4.5: LLM Cleanup (optional)
            # Check if LLM cleanup is enabled via config override or default setting
            llm_cleanup_enabled = processing_config.get("llm_cleanup_enabled", False) if processing_config else False
            llm_cleanup_enabled = llm_cleanup_enabled or (self.llm_cleanup and self.llm_cleanup.enabled)
            
            if llm_cleanup_enabled and self.llm_cleanup:
                logger.info(
                    "Stage 4.5: Starting LLM cleanup",
                    file_id=file_id,
                    chunk_count=total_chunks,
                )
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="cleanup",
                    progress=47,
                    chunks_processed=0,
                    total_chunks=total_chunks,
                )
                
                # Run async cleanup in sync context
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    chunks = loop.run_until_complete(
                        self.llm_cleanup.cleanup_chunks(chunks)
                    )
                    logger.info(
                        "LLM cleanup complete",
                        file_id=file_id,
                        chunk_count=len(chunks),
                    )
                finally:
                    loop.close()
                
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="cleanup",
                    progress=50,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                )
            else:
                logger.debug("LLM cleanup disabled, skipping", file_id=file_id)
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="chunking",
                    progress=50,
                    chunks_processed=total_chunks,
                    total_chunks=total_chunks,
                )
            
            # Store chunks in PostgreSQL (after cleanup)
            chunk_dicts = [c.to_dict() for c in chunks]
            self.postgres_service.insert_chunks(file_id, chunk_dicts)
            
            # Stage 5: Embedding
            logger.info(
                "Stage 5: Starting embedding generation",
                file_id=file_id,
                chunk_count=total_chunks,
            )
            self.postgres_service.update_status(
                file_id=file_id,
                stage="embedding",
                progress=60,
                chunks_processed=0,
                total_chunks=total_chunks,
            )
            
            # Generate dense embeddings
            chunk_texts = [c.text for c in chunks]
            
            logger.debug("Generating dense embeddings", file_id=file_id, chunk_count=len(chunk_texts))
            # Run async embedding in sync context
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                embeddings = loop.run_until_complete(
                    self.embedder.embed_chunks(chunk_texts)
                )
                logger.info(
                    "Dense embeddings generated",
                    file_id=file_id,
                    embedding_count=len(embeddings),
                )
            finally:
                loop.close()
            
            self.postgres_service.update_status(
                file_id=file_id,
                stage="embedding",
                progress=80,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
            )
            
            # Generate ColPali embeddings for PDF pages (if available)
            page_embeddings = None
            if extraction_result.page_images and mime_type == "application/pdf":
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
                except Exception as e:
                    logger.warning(
                        "ColPali embedding generation failed",
                        file_id=file_id,
                        error=str(e),
                    )
            
            # Stage 6: Indexing
            self.postgres_service.update_status(
                file_id=file_id,
                stage="indexing",
                progress=90,
                chunks_processed=total_chunks,
                total_chunks=total_chunks,
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
            
            vector_count = self.milvus_service.insert_text_chunks(
                file_id=file_id,
                user_id=user_id,
                chunks=chunk_dicts_for_milvus,
                embeddings=embeddings,
                content_hash=content_hash,
            )
            
            # Insert page images if available
            if page_embeddings and extraction_result.page_images:
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
                )
                
                vector_count += page_vector_count
            
            # Update final metadata
            processing_duration = int(time.time() - start_time)
            
            self.postgres_service.update_file_metadata(
                file_id=file_id,
                chunk_count=total_chunks,
                vector_count=vector_count,
                processing_duration_seconds=processing_duration,
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
                    
                    multi_flow = MultiFlowProcessor(
                        config=self.config,
                        text_extractor=self.text_extractor,
                        chunker=self.chunker,
                        embedder=self.embedder,
                        classifier=self.classifier,
                        colpali_embedder=self.colpali,
                    )
                    
                    # Process with multiple strategies for comparison
                    max_strategies = processing_config.get("max_parallel_strategies", 3)
                    results = multi_flow.process_with_strategies(
                        file_path=temp_file_path,
                        mime_type=mime_type,
                        file_id=file_id,
                        user_id=user_id,
                        max_strategies=max_strategies,
                        marker_enabled=processing_config.get("marker_enabled", False),
                        colpali_enabled=processing_config.get("colpali_enabled", True),
                    )
                    
                    logger.info(
                        "Multi-flow comparison completed",
                        file_id=file_id,
                        strategies_run=len(results),
                        results_summary={
                            strategy.value: {
                                "success": result.success,
                                "chunk_count": len(result.chunks) if result.success else 0,
                                "processing_time": result.processing_time_seconds,
                            }
                            for strategy, result in results.items()
                        },
                    )
                    
                    # Record strategy results in database
                    try:
                        conn = self.postgres_service._get_connection()
                        try:
                            with conn.cursor() as cur:
                                for strategy, result in results.items():
                                    # Calculate metrics
                                    text_length = len(result.text) if result.text else 0
                                    chunk_count = len(result.chunks) if result.chunks else 0
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
                                        strategy.value,
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
        
        except asyncio.TimeoutError:
            processing_duration = int(time.time() - start_time)
            error_msg = f"Processing exceeded {timeout_seconds}s timeout for {page_count}-page document"
            
            # Timeout is considered permanent (document too large)
            self.postgres_service.update_status(
                file_id=file_id,
                stage="failed",
                progress=0,
                error_message=error_msg,
            )
            
            logger.error(
                "Job processing timeout",
                file_id=file_id,
                timeout_seconds=timeout_seconds,
                processing_time_seconds=processing_duration,
            )
            raise
        
        except Exception as e:
            error_msg = str(e)
            is_transient = self._is_transient_error(e)
            
            # Get current retry count
            conn = self.postgres_service._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT retry_count FROM ingestion_status WHERE file_id = %s",
                        (file_id,),  # Pass string directly, not UUID object
                    )
                    result = cur.fetchone()
                    retry_count = result[0] if result else 0
            finally:
                self.postgres_service._return_connection(conn)
            
            max_retries = 3
            
            if is_transient and retry_count < max_retries:
                # Transient error - will retry
                new_retry_count = retry_count + 1
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="queued",  # Back to queued for retry
                    progress=0,
                    error_message=f"Transient error (retry {new_retry_count}/{max_retries}): {error_msg}",
                    retry_count=new_retry_count,
                )
                
                logger.warning(
                    "Job processing failed (transient, will retry)",
                    job_id=job_id,
                    file_id=file_id,
                    retry_count=new_retry_count,
                    max_retries=max_retries,
                    error=error_msg,
                    exc_info=True,
                )
                
                # Re-queue job in Redis for retry
                try:
                    self.redis_client.xadd(
                        self.stream_name,
                        {
                            "job_id": job_id,
                            "file_id": file_id,
                            "user_id": user_id,
                            "storage_path": storage_path,
                            "mime_type": mime_type,
                            "original_filename": original_filename,
                            "trace_id": trace_id,
                            "retry_count": str(new_retry_count),
                        },
                    )
                    logger.info("Job re-queued for retry", file_id=file_id, retry_count=new_retry_count)
                except Exception as requeue_error:
                    logger.error("Failed to re-queue job", file_id=file_id, error=str(requeue_error))
                
                # Don't raise - allow Redis to handle retry
                return
            
            else:
                # Permanent error or max retries exceeded
                self.postgres_service.update_status(
                    file_id=file_id,
                    stage="failed",
                    progress=0,
                    error_message=error_msg if not is_transient else f"Max retries ({max_retries}) exceeded: {error_msg}",
                    retry_count=retry_count,
                )
                
                logger.error(
                    "Job processing failed (permanent or max retries)",
                    job_id=job_id,
                    file_id=file_id,
                    retry_count=retry_count,
                    is_transient=is_transient,
                    error=error_msg,
                    exc_info=True,
                )
                raise
        
        finally:
            # Cleanup temporary file
            if temp_file_path:
                self.file_service.cleanup_temp_file(temp_file_path)
    
    def run(self):
        """Main worker loop - consume and process jobs."""
        self.running = True
        logger.info("Worker started", worker_id=self.worker_id)
        
        while self.running:
            try:
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
                            
                        except Exception as e:
                            logger.error(
                                "Job processing failed",
                                job_id=job_id,
                                trace_id=trace_id,
                                error=str(e),
                                exc_info=True,
                            )
                            # Don't ack - message will be retried by Redis
                
            except RedisError as e:
                logger.error("Redis error", error=str(e), exc_info=True)
                time.sleep(5)  # Backoff before retry
            
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
    """Handle shutdown signals."""
    logger.info("Received signal", signal=signum)
    sys.exit(0)


def main():
    """Main entry point."""
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Load configuration
    config = Config().to_dict()
    
    # Create and start worker
    worker = IngestWorker(config)
    
    try:
        worker.connect()
        worker.run()
    except Exception as e:
        logger.error("Worker failed", error=str(e), exc_info=True)
        sys.exit(1)
    finally:
        worker.disconnect()


if __name__ == "__main__":
    main()
