"""
Configuration management for Search Service.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    """Search service configuration."""
    
    # Service
    service_name: str = "search-api"
    service_port: int = 8003
    log_level: str = "INFO"
    
    # Milvus
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "documents"
    
    # PostgreSQL
    postgres_host: str = os.getenv("POSTGRES_HOST", "10.96.200.203")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "busibox")
    postgres_user: str = os.getenv("POSTGRES_USER", "app_user")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    
    # Redis (optional caching)
    redis_host: Optional[str] = os.getenv("REDIS_HOST", None)
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: Optional[str] = os.getenv("REDIS_PASSWORD", None)
    enable_caching: bool = os.getenv("ENABLE_CACHING", "false").lower() == "true"
    cache_ttl: int = 300  # 5 minutes
    
    # Embedding service (local FastEmbed on ingest-lxc)
    embedding_service_url: str = os.getenv("EMBEDDING_SERVICE_URL", "http://10.96.200.206:8002")
    embedding_model: str = "bge-large-en-v1.5"
    embedding_dim: int = 1024
    
    # LiteLLM (for LLM calls)
    litellm_base_url: str = os.getenv("LITELLM_BASE_URL", "http://10.96.200.207:4000")
    litellm_api_key: str = os.getenv("LITELLM_API_KEY", "")
    
    # Reranking (local model for RerankingService)
    reranker_model: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_device: str = "cpu"  # or "cuda"
    enable_reranking: bool = os.getenv("ENABLE_RERANKING", "true").lower() == "true"
    
    # vLLM Reranker (for hybrid search via MilvusSearchService)
    vllm_reranker_url: str = os.getenv("VLLM_RERANKER_URL", "http://10.96.200.208:8002/v1")
    vllm_reranker_model: str = os.getenv("VLLM_RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B")
    
    # Search defaults
    default_search_limit: int = 10
    default_rerank_k: int = 100
    max_search_limit: int = 100
    
    # Highlighting
    highlight_fragment_size: int = 200
    highlight_num_fragments: int = 3
    highlight_pre_tag: str = "<mark>"
    highlight_post_tag: str = "</mark>"
    
    # Performance
    enable_query_cache: bool = True
    query_cache_ttl: int = 300  # 5 minutes
    max_concurrent_searches: int = 50
    
    class Config:
        env_file = ".env"
        case_sensitive = False
    
    def to_dict(self):
        """Convert config to dictionary."""
        return self.model_dump()


# Global config instance
config = Config()

