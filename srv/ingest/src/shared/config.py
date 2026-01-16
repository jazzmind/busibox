"""
Shared configuration loader.

Loads configuration from environment variables for both API and worker.
"""

import os
from typing import Dict


class Config:
    """Configuration class with environment variable loading."""
    
    def __init__(self):
        """Load configuration from environment variables."""
        # Worker configuration
        self.worker_id = os.getenv("WORKER_ID", "")
        self.stream_name = os.getenv("REDIS_STREAM", "jobs:ingestion")
        self.consumer_group = os.getenv("REDIS_CONSUMER_GROUP", "workers")
        
        # Redis configuration
        self.redis_host = os.getenv("REDIS_HOST", "10.96.200.206")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        
        # PostgreSQL configuration (files database)
        self.postgres_host = os.getenv("POSTGRES_HOST", "10.96.200.203")
        self.postgres_port = int(os.getenv("POSTGRES_PORT", "5432"))
        self.postgres_db = os.getenv("POSTGRES_DB", "files")
        self.postgres_user = os.getenv("POSTGRES_USER", "busibox_user")
        self.postgres_password = os.getenv("POSTGRES_PASSWORD", "")
        
        # Test mode configuration
        # When enabled, requests with X-Test-Mode: true header will use test database
        self.test_mode_enabled = os.getenv("INGEST_TEST_MODE_ENABLED", "false").lower() == "true"
        self.test_postgres_db = os.getenv("TEST_DB_NAME", "test_files")
        self.test_postgres_user = os.getenv("TEST_DB_USER", "busibox_test_user")
        self.test_postgres_password = os.getenv("TEST_DB_PASSWORD", "testpassword")
        
        # Milvus configuration
        self.milvus_host = os.getenv("MILVUS_HOST", "10.96.200.204")
        self.milvus_port = int(os.getenv("MILVUS_PORT", "19530"))
        self.milvus_collection = os.getenv("MILVUS_COLLECTION", "documents")
        
        # MinIO configuration
        self.minio_endpoint = os.getenv("MINIO_ENDPOINT", "10.96.200.205:9000")
        # Support both MINIO_ACCESS_KEY and MINIO_USER for compatibility
        self.minio_access_key = os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_USER", "minioadmin")
        self.minio_secret_key = os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_PASS", "minioadmin")
        self.minio_secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        self.minio_bucket = os.getenv("MINIO_BUCKET", "documents")
        
        # FastEmbed configuration (local text embeddings)
        self.fastembed_model = os.getenv("FASTEMBED_MODEL", "BAAI/bge-large-en-v1.5")
        self.embedding_batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", "32"))
        
        # ColPali configuration (visual embeddings)
        self.colpali_base_url = os.getenv("COLPALI_BASE_URL", "http://10.96.200.208:9006/v1")
        self.colpali_api_key = os.getenv("COLPALI_API_KEY", "EMPTY")
        self.colpali_enabled = os.getenv("COLPALI_ENABLED", "true").lower() == "true"
        self.colpali_pooling_method = os.getenv("COLPALI_POOLING_METHOD", "mean")  # mean or max
        
        # Marker configuration (gold standard for PDF extraction, pdfplumber is fallback)
        self.marker_enabled = os.getenv("MARKER_ENABLED", "true").lower() == "true"
        self.marker_use_gpu = os.getenv("MARKER_USE_GPU", "true").lower() == "true"
        self.marker_gpu_device = os.getenv("MARKER_GPU_DEVICE", "cuda")  # cuda, cpu, or auto
        self.marker_inference_ram = os.getenv("MARKER_INFERENCE_RAM", "16")  # GB of VRAM
        self.marker_vram_per_task = os.getenv("MARKER_VRAM_PER_TASK", "3.5")  # GB per task
        # Remote Marker service URL - if set, calls remote API instead of local Marker
        # Used by test environment to leverage production Marker
        self.marker_service_url = os.getenv("MARKER_SERVICE_URL", "")
        
        # Multi-flow processing (optional - enables parallel strategy comparison)
        self.multi_flow_enabled = os.getenv("MULTI_FLOW_ENABLED", "false").lower() == "true"
        self.max_parallel_strategies = int(os.getenv("MAX_PARALLEL_STRATEGIES", "3"))
        
        # LLM cleanup configuration (fixes text quality issues)
        self.llm_cleanup_enabled = os.getenv("LLM_CLEANUP_ENABLED", "true").lower() == "true"
        
        # LiteLLM configuration (for LLM cleanup)
        self.litellm_base_url = os.getenv("LITELLM_BASE_URL", "http://10.96.200.207:4000")
        self.litellm_api_key = os.getenv("LITELLM_API_KEY", "")
        
        # Processing configuration
        self.chunk_size_min = int(os.getenv("CHUNK_SIZE_MIN", "400"))
        self.chunk_size_max = int(os.getenv("CHUNK_SIZE_MAX", "800"))
        self.chunk_overlap_pct = float(os.getenv("CHUNK_OVERLAP_PCT", "0.12"))  # 12% overlap
        
        # Timeout configuration (in seconds)
        self.timeout_small = int(os.getenv("TIMEOUT_SMALL", "300"))  # 5 minutes
        self.timeout_medium = int(os.getenv("TIMEOUT_MEDIUM", "600"))  # 10 minutes
        self.timeout_large = int(os.getenv("TIMEOUT_LARGE", "1200"))  # 20 minutes
    
    def to_dict(self) -> Dict:
        """Convert config to dictionary (for compatibility with existing code)."""
        return {
            "worker_id": self.worker_id,
            "stream_name": self.stream_name,
            "redis_stream": self.stream_name,  # Alias for API compatibility
            "consumer_group": self.consumer_group,
            "redis_consumer_group": self.consumer_group,  # Alias for API compatibility
            "redis_host": self.redis_host,
            "redis_port": self.redis_port,
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "postgres_password": self.postgres_password,
            "test_mode_enabled": self.test_mode_enabled,
            "test_postgres_db": self.test_postgres_db,
            "test_postgres_user": self.test_postgres_user,
            "test_postgres_password": self.test_postgres_password,
            "milvus_host": self.milvus_host,
            "milvus_port": self.milvus_port,
            "milvus_collection": self.milvus_collection,
            "minio_endpoint": self.minio_endpoint,
            "minio_access_key": self.minio_access_key,
            "minio_secret_key": self.minio_secret_key,
            "minio_secure": self.minio_secure,
            "minio_bucket": self.minio_bucket,
            "fastembed_model": self.fastembed_model,
            "embedding_batch_size": self.embedding_batch_size,
            "colpali_base_url": self.colpali_base_url,
            "colpali_api_key": self.colpali_api_key,
            "colpali_enabled": self.colpali_enabled,
            "colpali_pooling_method": self.colpali_pooling_method,
            "marker_enabled": self.marker_enabled,
            "marker_use_gpu": self.marker_use_gpu,
            "marker_gpu_device": self.marker_gpu_device,
            "marker_inference_ram": self.marker_inference_ram,
            "marker_vram_per_task": self.marker_vram_per_task,
            "marker_service_url": self.marker_service_url,
            "multi_flow_enabled": self.multi_flow_enabled,
            "max_parallel_strategies": self.max_parallel_strategies,
            "llm_cleanup_enabled": self.llm_cleanup_enabled,
            "litellm_base_url": self.litellm_base_url,
            "litellm_api_key": self.litellm_api_key,
            "chunk_size": self.chunk_size_max,  # For backward compatibility
            "chunk_overlap": int(self.chunk_size_max * self.chunk_overlap_pct),
            "chunk_size_min": self.chunk_size_min,
            "chunk_size_max": self.chunk_size_max,
            "chunk_overlap_pct": self.chunk_overlap_pct,
            "temp_dir": "/tmp/ingest",
        }

