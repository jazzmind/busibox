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
    data_api_url: AnyHttpUrl = Field(
        "http://10.96.200.206:8002",
        description="Base URL for Busibox data API",
    )
    rag_api_url: AnyHttpUrl = Field(
        "http://10.96.200.204:8003",
        description="Base URL for RAG/vector database API",
    )
    
    # Embedding API (dedicated embedding service - no auth required)
    embedding_api_url: str = Field(
        "http://embedding-api:8005",
        description="Dedicated embedding service URL (port 8005). No auth required for internal services.",
    )
    
    # Milvus configuration (for insights)
    milvus_host: str = Field(
        "milvus",
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

    # Database configuration
    # Default credentials match docker-compose.yml (busibox_user:devpassword)
    database_url: str = Field(
        "postgresql+asyncpg://busibox_user:devpassword@localhost:5432/agent",
        description="SQLAlchemy connection URL",
    )
    
    # LiteLLM database for spend tracking queries (read-only access)
    # Uses asyncpg directly (not SQLAlchemy) for raw queries against LiteLLM's Prisma schema
    litellm_database_url: Optional[str] = Field(
        None,
        description="PostgreSQL connection URL for LiteLLM spend database (e.g., postgresql://user:pass@host:5432/litellm)",
    )
    
    # Test mode configuration
    # When enabled, requests with X-Test-Mode: true header will use test database
    test_mode_enabled: bool = Field(
        False,
        description="Enable test mode support (routes test requests to test database)",
    )
    test_database_url: str = Field(
        "postgresql+asyncpg://busibox_test_user:testpassword@localhost:5432/test_agent",
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
    portal_name: str = Field(
        "Busibox",
        description="Display name used in notification subjects (e.g. 'Dredging News from Busibox')",
    )
    
    # Email configuration
    # Bridge API is the preferred email provider (handles SMTP/Resend internally)
    bridge_api_url: Optional[str] = Field(
        None,
        description="Bridge API URL for sending emails (e.g., http://bridge-api:8081). Preferred over direct SMTP.",
    )
    
    # Legacy SMTP configuration (used only if bridge_api_url is not set)
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

    # Skills system (AgentSkills-compatible SKILL.md loader)
    skills_enabled: bool = Field(
        False,
        description="Enable loading SKILL.md skills and injecting them into agent prompts",
    )
    skills_dirs: str = Field(
        "/srv/skills,~/.openclaw/skills,/skills",
        description="Comma-separated directories to scan recursively for SKILL.md files",
    )
    skills_cache_ttl_seconds: int = Field(
        60,
        description="Seconds to cache loaded skills before re-scan",
    )
    skills_allowed_roles: str = Field(
        "",
        description="Optional comma-separated RBAC roles allowed to use skills globally",
    )
    skills_clawhub_enabled: bool = Field(
        False,
        description="Enable ClawHub integration hints for loaded skills",
    )

    def get_skill_dirs(self) -> List[str]:
        raw = self.skills_dirs or ""
        if not raw.strip():
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    def get_skills_allowed_roles(self) -> List[str]:
        raw = self.skills_allowed_roles or ""
        if not raw.strip():
            return []
        return [role.strip().lower() for role in raw.split(",") if role.strip()]

    model_config = ConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Ignore extra fields from .env that aren't in the model
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
