"""
Signal Bot Configuration

Loads settings from environment variables.
"""

from functools import lru_cache
from typing import List, Optional

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Signal bot settings, loaded from environment variables.
    """

    app_name: str = "signal-bot"
    environment: str = Field("development", description="Environment name")
    debug: bool = False
    log_level: str = Field("INFO", description="Logging level")

    # Signal CLI REST API configuration
    signal_cli_url: AnyHttpUrl = Field(
        "http://localhost:8080",
        description="Base URL for signal-cli-rest-api",
    )
    signal_phone_number: str = Field(
        ...,
        description="Phone number registered with Signal (E.164 format)",
    )

    # Agent API configuration
    agent_api_url: AnyHttpUrl = Field(
        "http://10.96.200.202:8000",
        description="Base URL for Busibox Agent API",
    )

    # Auth configuration for Agent API
    # Service account credentials for the bot
    auth_token_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010/oauth/token",
        description="Token endpoint for OAuth2",
    )
    auth_client_id: str = Field(
        "signal-bot-client",
        description="OAuth client ID for Signal bot",
    )
    auth_client_secret: str = Field(
        ...,
        description="OAuth client secret for Signal bot",
    )
    service_user_id: str = Field(
        "signal-bot-service",
        description="Service user ID for agent API calls",
    )

    # Bot behavior
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

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
