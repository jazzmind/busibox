#!/usr/bin/env python3
"""
Busibox File Ingestion Worker

Consumes jobs from Redis Streams and processes uploaded files:
1. Download file from MinIO
2. Extract text (PDF, DOCX, TXT)
3. Chunk text (spaCy, 512 tokens, 50 overlap)
4. Generate embeddings (via liteLLM)
5. Store embeddings in Milvus
6. Store chunk metadata in PostgreSQL
7. Update job status

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

import os
import sys
import time
import signal
import socket
from typing import Optional

import structlog
import redis
from redis.exceptions import RedisError

from services.file_service import FileService
from services.postgres_service import PostgresService
from services.milvus_service import MilvusService
from processors.text_extractor import TextExtractor
from processors.chunker import Chunker
from processors.embedder import Embedder
from utils.config import load_config

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
    
    def __init__(self, config: dict):
        """Initialize worker with configuration."""
        self.config = config
        self.worker_id = config.get("worker_id", socket.gethostname())
        self.stream_name = config.get("stream_name", "jobs:ingestion")
        self.consumer_group = config.get("consumer_group", "workers")
        self.consumer_name = f"{self.worker_id}-{os.getpid()}"
        self.running = False
        
        # Services (initialized in connect())
        self.redis_client: Optional[redis.Redis] = None
        self.file_service: Optional[FileService] = None
        self.postgres_service: Optional[PostgresService] = None
        self.milvus_service: Optional[MilvusService] = None
        
        # Processors (initialized in connect())
        self.text_extractor: Optional[TextExtractor] = None
        self.chunker: Optional[Chunker] = None
        self.embedder: Optional[Embedder] = None
        
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
        self.redis_client = redis.Redis(
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
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.info("Consumer group already exists", group=self.consumer_group)
            else:
                raise
        
        # Initialize services
        self.file_service = FileService(self.config)
        self.postgres_service = PostgresService(self.config)
        self.milvus_service = MilvusService(self.config)
        
        # Initialize processors
        self.text_extractor = TextExtractor()
        self.chunker = Chunker(self.config)
        self.embedder = Embedder(self.config)
        
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
    
    def process_job(self, job_id: str, job_data: dict, trace_id: str):
        """
        Process a single ingestion job.
        
        Args:
            job_id: Job ID (UUID)
            job_data: Job data from Redis stream
            trace_id: Trace ID for observability
        """
        file_id = job_data.get("file_id")
        
        logger.info(
            "Processing job",
            job_id=job_id,
            file_id=file_id,
            trace_id=trace_id,
        )
        
        # TODO: Implement job processing logic
        # 1. Download file from MinIO (file_service.download)
        # 2. Extract text (text_extractor.extract)
        # 3. Chunk text (chunker.chunk)
        # 4. Generate embeddings (embedder.embed_chunks)
        # 5. Store in Milvus (milvus_service.insert)
        # 6. Store metadata in PostgreSQL (postgres_service.insert_chunks)
        # 7. Update job status (postgres_service.update_job_status)
        
        logger.info(
            "Job processing complete (stub)",
            job_id=job_id,
            file_id=file_id,
            trace_id=trace_id,
        )
    
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
                        trace_id = message_data.get("trace_id", "unknown")
                        
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
                            # Don't ack - message will be retried
                
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
    config = load_config()
    
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

