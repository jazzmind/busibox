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

    # Model/provider configuration
    default_model: str = Field(
        "anthropic:claude-3-5-sonnet",
        description="Default model identifier passed to Pydantic AI Agent",
    )

    # Busibox service endpoints
    search_api_url: AnyHttpUrl = Field(
        "http://localhost:8002",
        description="Base URL for Busibox search API",
    )
    ingest_api_url: AnyHttpUrl = Field(
        "http://localhost:8001",
        description="Base URL for Busibox ingest API",
    )
    rag_api_url: AnyHttpUrl = Field(
        "http://localhost:8003",
        description="Base URL for RAG/vector database API",
    )

    # Auth configuration
    auth_issuer: Optional[AnyHttpUrl] = Field(
        None, description="Expected issuer for Busibox JWT tokens"
    )
    auth_audience: Optional[str] = Field(
        None, description="Expected audience for Busibox JWT tokens"
    )
    auth_jwks_url: Optional[AnyHttpUrl] = Field(
        None, description="JWKS endpoint for Busibox auth"
    )
    auth_token_url: AnyHttpUrl = Field(
        "http://localhost:8080/oauth/token",
        description="Token endpoint for OAuth2 client-credentials exchange",
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

    # CORS
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
