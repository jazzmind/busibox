"""
Bridge Service Configuration

Loads settings from environment variables for all communication channels:
- Signal bot (polling loop)
- Email sending (SMTP / Resend)
- HTTP API server (FastAPI)
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Bridge service settings, loaded from environment variables.
    Covers Signal bot, email channel, and the HTTP API server.
    """

    @field_validator(
        "smtp_port",
        "smtp_host",
        "smtp_user",
        "smtp_password",
        "email_from",
        "resend_api_key",
        mode="before",
    )
    @classmethod
    def empty_str_to_none(cls, v: object) -> object:
        """Convert empty strings to None for optional fields.

        Docker Compose sets unset env vars to '' rather than leaving them
        unset, which breaks Pydantic's Optional[int] parsing.
        """
        if isinstance(v, str) and v.strip() == "":
            return None
        return v

    app_name: str = "bridge"
    environment: str = Field("development", description="Environment name")
    debug: bool = False
    log_level: str = Field("INFO", description="Logging level")

    # -------------------------------------------------------------------------
    # HTTP API Server
    # -------------------------------------------------------------------------
    bridge_api_port: int = Field(8081, description="Port for Bridge HTTP API")

    # -------------------------------------------------------------------------
    # Signal Channel
    # -------------------------------------------------------------------------
    signal_enabled: bool = Field(True, description="Enable Signal channel")

    # Signal CLI REST API configuration
    signal_cli_url: AnyHttpUrl = Field(
        "http://localhost:8080",
        description="Base URL for signal-cli-rest-api",
    )
    signal_phone_number: str = Field(
        "",
        description="Phone number registered with Signal (E.164 format)",
    )

    # Agent API configuration
    agent_api_url: AnyHttpUrl = Field(
        "http://10.96.200.202:8000",
        description="Base URL for Busibox Agent API",
    )

    # Auth configuration for Agent API (Zero Trust)
    auth_token_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010/oauth/token",
        description="Token endpoint for OAuth2 token exchange",
    )
    delegation_token: str = Field(
        "",
        description="Pre-issued delegation token for signal-bot service account",
    )

    # Bot behavior (Signal-specific)
    enable_web_search: bool = Field(True, description="Enable web search for queries")
    enable_doc_search: bool = Field(False, description="Enable document search")
    default_model: str = Field("auto", description="Default model selection")
    max_message_length: int = Field(4000, description="Max message length for Signal")

    # Rate limiting
    rate_limit_messages: int = Field(30, description="Max messages per window")
    rate_limit_window: int = Field(60, description="Rate limit window in seconds")

    # Polling configuration
    poll_interval: float = Field(1.0, description="Seconds between message polls")

    # Allowed phone numbers (empty = allow all)
    allowed_phone_numbers: List[str] = Field(
        default_factory=list,
        description="List of allowed phone numbers (empty = all)",
    )

    # -------------------------------------------------------------------------
    # Email Channel
    # -------------------------------------------------------------------------
    email_enabled: bool = Field(False, description="Enable email channel")
    smtp_host: Optional[str] = Field(None, description="SMTP server host")
    smtp_port: Optional[int] = Field(None, description="SMTP server port")
    smtp_user: Optional[str] = Field(None, description="SMTP username")
    smtp_password: Optional[str] = Field(None, description="SMTP password")
    smtp_secure: bool = Field(False, description="Use SSL/TLS for SMTP")
    email_from: Optional[str] = Field(None, description="From address for emails")
    resend_api_key: Optional[str] = Field(None, description="Resend API key (alternative to SMTP)")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def clear_settings_cache() -> None:
    """Clear the cached settings to force re-read from env."""
    get_settings.cache_clear()
