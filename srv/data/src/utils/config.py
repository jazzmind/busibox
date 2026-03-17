"""Configuration loader for data worker."""

import os
from typing import Dict


def load_config() -> Dict[str, any]:
    """
    Load configuration from environment variables.
    
    Returns:
        Configuration dictionary
    """
    return {
        # Worker configuration
        "worker_id": os.getenv("WORKER_ID", ""),
        "stream_name": os.getenv("REDIS_STREAM", "jobs:data"),
        "consumer_group": os.getenv("REDIS_CONSUMER_GROUP", "workers"),
        
        # Redis configuration
        "redis_host": os.getenv("REDIS_HOST", "redis"),
        "redis_port": int(os.getenv("REDIS_PORT", "6379")),
        
        # PostgreSQL configuration
        "postgres_host": os.getenv("POSTGRES_HOST", "postgres"),
        "postgres_port": int(os.getenv("POSTGRES_PORT", "5432")),
        "postgres_db": os.getenv("POSTGRES_DB", "busibox"),
        "postgres_user": os.getenv("POSTGRES_USER", "postgres"),
        "postgres_password": os.getenv("POSTGRES_PASSWORD", ""),
        
        # Milvus configuration
        "milvus_host": os.getenv("MILVUS_HOST", "milvus"),
        "milvus_port": int(os.getenv("MILVUS_PORT", "19530")),
        "milvus_collection": os.getenv("MILVUS_COLLECTION", "document_embeddings"),
        
        # MinIO configuration
        "minio_endpoint": os.getenv("MINIO_ENDPOINT", "minio:9000"),
        "minio_access_key": os.getenv("MINIO_ACCESS_KEY", ""),
        "minio_secret_key": os.getenv("MINIO_SECRET_KEY", ""),
        "minio_secure": os.getenv("MINIO_SECURE", "false").lower() == "true",
        "minio_bucket": os.getenv("MINIO_BUCKET", "documents"),
        
        # liteLLM configuration
        "litellm_base_url": os.getenv("LITELLM_BASE_URL", "http://localhost:8000"),
        "litellm_api_key": os.getenv("LITELLM_API_KEY", ""),
        
        # Processing configuration
        "chunk_size": int(os.getenv("CHUNK_SIZE", "512")),
        "chunk_overlap": int(os.getenv("CHUNK_OVERLAP", "50")),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "bge-large-en-v1.5"),
    }

