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
    postgres_host: str = os.getenv("POSTGRES_HOST", "postgres")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "5432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "busibox")
    postgres_user: str = os.getenv("POSTGRES_USER", "app_user")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")
    
    # Test mode configuration
    # When enabled, requests with X-Test-Mode: true header will use test database
    test_mode_enabled: bool = os.getenv("SEARCH_TEST_MODE_ENABLED", "false").lower() == "true"
    test_postgres_db: str = os.getenv("TEST_DB_NAME", "test_files")
    test_postgres_user: str = os.getenv("TEST_DB_USER", "busibox_test_user")
    test_postgres_password: str = os.getenv("TEST_DB_PASSWORD", "testpassword")
    
    # Redis (optional caching)
    redis_host: Optional[str] = os.getenv("REDIS_HOST", None)
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_password: Optional[str] = os.getenv("REDIS_PASSWORD", None)
    enable_caching: bool = os.getenv("ENABLE_CACHING", "false").lower() == "true"
    cache_ttl: int = 300  # 5 minutes
    
    # Embedding API (dedicated embedding service - no auth required)
    embedding_api_url: str = os.getenv("EMBEDDING_API_URL", "http://embedding-api:8005")
    # Note: This is for logging only - actual model is determined by embedding-api service
    embedding_model: str = os.getenv("FASTEMBED_MODEL", "BAAI/bge-large-en-v1.5").split("/")[-1]
    embedding_dim: int = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
    
    # LiteLLM (for LLM calls)
    litellm_base_url: str = os.getenv("LITELLM_BASE_URL", "http://litellm:4000")
    litellm_api_key: str = os.getenv("LITELLM_API_KEY", "")
    
    # Reranking mode: "none" (skip), "vllm" or "qwen3-gpu" (GPU), "local" or "baai-cpu" (CPU, slow startup)
    # This determines the default reranker used by hybrid search
    reranking_mode: str = os.getenv("RERANKING_MODE", "none")
    
    # Legacy enable_reranking bool - superseded by reranking_mode
    # If reranking_mode is "none", reranking is disabled regardless of this setting
    enable_reranking: bool = os.getenv("ENABLE_RERANKING", "true").lower() == "true"
    
    # Reranking (local model for RerankingService)
    reranker_model: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    reranker_device: str = "cpu"  # or "cuda"
    
    # vLLM Reranker (for hybrid search via MilvusSearchService)
    vllm_reranker_url: str = os.getenv("VLLM_RERANKER_URL", "http://vllm-reranker:8002/v1")
    vllm_reranker_model: str = os.getenv("VLLM_RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B")
    
    # AuthZ JWT Validation
    authz_jwks_url: str = os.getenv("AUTHZ_JWKS_URL", "http://authz-api:8010/.well-known/jwks.json")
    authz_issuer: str = os.getenv("AUTHZ_ISSUER", "busibox-authz")
    authz_audience: str = os.getenv("AUTHZ_AUDIENCE", "search-api")
    jwt_algorithms: str = os.getenv("JWT_ALGORITHMS", "RS256")
    
    # AuthZ Token URL (for token exchange)
    authz_token_url: str = os.getenv("AUTHZ_TOKEN_URL", "http://authz-api:8010/oauth/token")
    
    # Service-to-service OAuth client (api-service)
    # Used for token exchange when calling other services (ingest, agent, etc.)
    api_service_client_id: str = os.getenv("API_SERVICE_CLIENT_ID", "api-service")
    api_service_client_secret: str = os.getenv("API_SERVICE_CLIENT_SECRET", "")
    
    # Bootstrap client credentials (for PVT tests)
    authz_bootstrap_client_id: str = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
    authz_bootstrap_client_secret: str = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
    
    # Test user (for integration tests)
    test_user_id: Optional[str] = os.getenv("TEST_USER_ID", None)
    
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

