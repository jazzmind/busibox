from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Central application settings, loaded from environment variables.
    """

    app_name: str = "agent-server"
    environment: str = Field("development", description="environment name (development/test/prod)")
    debug: bool = False
    log_level: str = Field("INFO", description="Logging level (DEBUG/INFO/WARNING/ERROR)")

    # Model/provider configuration
    default_model: str = Field(
        "agent",
        description="Default model purpose for LiteLLM (e.g., agent, fast, frontier, etc.)",
    )
    litellm_base_url: AnyHttpUrl = Field(
        "http://10.96.200.207:4000/v1",
        description="Base URL for LiteLLM proxy (OpenAI-compatible endpoint)",
    )
    litellm_api_key: Optional[str] = Field(
        None,
        description="API key for LiteLLM proxy (if authentication is enabled)",
    )

    # Busibox service endpoints
    search_api_url: AnyHttpUrl = Field(
        "http://10.96.200.204:8003",
        description="Base URL for Busibox search API",
    )
    ingest_api_url: AnyHttpUrl = Field(
        "http://10.96.200.206:8002",
        description="Base URL for Busibox ingest API",
    )
    rag_api_url: AnyHttpUrl = Field(
        "http://10.96.200.204:8003",
        description="Base URL for RAG/vector database API",
    )
    
    # Milvus configuration (for insights)
    milvus_host: str = Field(
        "10.96.200.204",
        description="Milvus host for insights storage",
    )
    milvus_port: int = Field(
        19530,
        description="Milvus port",
    )

    # Auth configuration
    auth_issuer: Optional[str] = Field(
        None, description="Expected issuer for Busibox JWT tokens (string identifier, not URL)"
    )
    auth_audience: Optional[str] = Field(
        None, description="Expected audience for Busibox JWT tokens"
    )
    auth_jwks_url: Optional[AnyHttpUrl] = Field(
        None, description="JWKS endpoint for Busibox auth (authz)"
    )
    auth_token_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010/oauth/token",
        description="Token endpoint for OAuth2 token exchange",
    )
    auth_client_id: str = Field("test-client-id", description="Client ID for token exchange")
    auth_client_secret: str = Field("test-client-secret", description="Client secret for token exchange")

    # Database configuration
    database_url: str = Field(
        "postgresql+asyncpg://agent_server:agent_server@localhost:5432/agent_server",
        description="SQLAlchemy connection URL",
    )

    # Redis/background tasks
    redis_url: str = Field("redis://localhost:6379/0", description="Redis URL for queues/locks")

    # Web Search Provider Configuration
    search_duckduckgo_enabled: bool = Field(True, description="Enable DuckDuckGo search (free)")
    search_tavily_enabled: bool = Field(False, description="Enable Tavily search")
    tavily_api_key: Optional[str] = Field(None, description="Tavily API key")
    search_perplexity_enabled: bool = Field(False, description="Enable Perplexity search")
    perplexity_api_key: Optional[str] = Field(None, description="Perplexity API key")
    search_brave_enabled: bool = Field(False, description="Enable Brave search")
    brave_api_key: Optional[str] = Field(None, description="Brave API key")

    # CORS
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    # OpenTelemetry configuration
    otlp_endpoint: Optional[AnyHttpUrl] = Field(
        None,
        description="OTLP endpoint for trace export (e.g., http://localhost:4317)",
    )
    otel_service_name: Optional[str] = Field(
        None,
        description="Override service name for traces (defaults to app_name)",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"  # Ignore extra fields from .env that aren't in the model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
