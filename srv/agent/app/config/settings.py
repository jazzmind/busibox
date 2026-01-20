from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field, ConfigDict
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
    fast_model: str = Field(
        "fast",
        description="Fast/cheap model for simple tasks like query optimization",
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
    auth_client_id: str = Field(
        "agent-api",
        description="Client ID for token exchange (from AUTH_CLIENT_ID env var)"
    )
    auth_client_secret: str = Field(
        "",
        description="Client secret for token exchange (from AUTH_CLIENT_SECRET env var)"
    )

    # Database configuration
    # Default credentials match docker-compose.local.yml (busibox_user:devpassword)
    database_url: str = Field(
        "postgresql+asyncpg://busibox_user:devpassword@localhost:5432/agent_server",
        description="SQLAlchemy connection URL",
    )
    
    # Test mode configuration
    # When enabled, requests with X-Test-Mode: true header will use test database
    test_mode_enabled: bool = Field(
        False,
        description="Enable test mode support (routes test requests to test database)",
    )
    test_database_url: str = Field(
        "postgresql+asyncpg://busibox_test_user:testpassword@localhost:5432/test_agent_server",
        description="SQLAlchemy connection URL for test database",
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

    # Portal/UI URLs (for notification links)
    portal_base_url: str = Field(
        "https://localhost",
        description="Base URL for portal links in notifications",
    )
    
    # Email/SMTP configuration
    smtp_host: Optional[str] = Field(None, description="SMTP server host")
    smtp_port: int = Field(587, description="SMTP server port")
    smtp_username: Optional[str] = Field(None, description="SMTP username")
    smtp_password: Optional[str] = Field(None, description="SMTP password")
    email_from: str = Field(
        "noreply@busibox.local",
        description="Default from address for emails",
    )

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

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from .env that aren't in the model
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
