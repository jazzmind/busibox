"""
Bridge Service Configuration

Loads settings from environment variables for all communication channels:
- Signal bot (polling loop)
- Email sending (SMTP / Resend)
- HTTP API server (FastAPI)
"""

from functools import lru_cache
import json
from typing import Dict, List, Optional

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
    data_api_url: AnyHttpUrl = Field(
        "http://10.96.200.206:8002",
        description="Base URL for Busibox Data API",
    )

    # Auth configuration for Agent API (Zero Trust)
    auth_token_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010/oauth/token",
        description="Token endpoint for OAuth2 token exchange",
    )
    authz_base_url: AnyHttpUrl = Field(
        "http://10.96.200.210:8010",
        description="Base URL for AuthZ API (channel linking lookup/verify)",
    )
    delegation_token: str = Field(
        "",
        description="Pre-issued delegation token for signal-bot service account",
    )

    # Bot behavior (Signal-specific)
    enable_web_search: bool = Field(True, description="Enable web search for queries")
    enable_doc_search: bool = Field(True, description="Enable document search")
    default_model: str = Field("auto", description="Default model selection")
    default_agent_id: str = Field("chat-agent", description="Default agent ID for inbound bridge messages")
    max_message_length: int = Field(4000, description="Max message length for Signal")

    # Rate limiting
    rate_limit_messages: int = Field(30, description="Max messages per window")
    rate_limit_window: int = Field(60, description="Rate limit window in seconds")

    # Polling configuration
    poll_interval: float = Field(1.0, description="Seconds between message polls")

    # Allowed phone numbers (empty = allow all)
    # Stored as str to avoid pydantic-settings v2 trying to JSON-parse List fields
    # from env vars (which fails on empty strings and plain comma-separated values).
    # Use get_allowed_phone_numbers() to get the parsed list.
    allowed_phone_numbers: str = Field(
        "",
        description="Comma-separated list of allowed phone numbers (empty = all)",
    )

    def get_allowed_phone_numbers(self) -> List[str]:
        """Parse allowed_phone_numbers string into a list."""
        if not self.allowed_phone_numbers or not self.allowed_phone_numbers.strip():
            return []
        return [n.strip() for n in self.allowed_phone_numbers.split(",") if n.strip()]

    # -------------------------------------------------------------------------
    # Telegram Channel
    # -------------------------------------------------------------------------
    telegram_enabled: bool = Field(False, description="Enable Telegram channel")
    telegram_bot_token: str = Field("", description="Telegram bot token")
    telegram_poll_interval: float = Field(1.0, description="Seconds between Telegram polls")
    telegram_poll_timeout: int = Field(25, description="Telegram getUpdates timeout in seconds")
    telegram_allowed_chat_ids: str = Field(
        "",
        description="Comma-separated Telegram chat IDs allowed to interact (empty = all)",
    )

    def get_allowed_telegram_chat_ids(self) -> List[str]:
        """Parse allowed Telegram chat IDs into a normalized list."""
        if not self.telegram_allowed_chat_ids or not self.telegram_allowed_chat_ids.strip():
            return []
        return [chat_id.strip() for chat_id in self.telegram_allowed_chat_ids.split(",") if chat_id.strip()]

    # -------------------------------------------------------------------------
    # Discord Channel
    # -------------------------------------------------------------------------
    discord_enabled: bool = Field(False, description="Enable Discord channel")
    discord_bot_token: str = Field("", description="Discord bot token")
    discord_poll_interval: float = Field(2.0, description="Seconds between Discord polls")
    discord_channel_ids: str = Field(
        "",
        description="Comma-separated Discord channel IDs to poll",
    )

    def get_discord_channel_ids(self) -> List[str]:
        """Parse configured Discord channel IDs."""
        if not self.discord_channel_ids or not self.discord_channel_ids.strip():
            return []
        return [channel_id.strip() for channel_id in self.discord_channel_ids.split(",") if channel_id.strip()]

    # -------------------------------------------------------------------------
    # WhatsApp Channel (Cloud API webhook mode)
    # -------------------------------------------------------------------------
    whatsapp_enabled: bool = Field(False, description="Enable WhatsApp channel")
    whatsapp_verify_token: str = Field("", description="Meta webhook verify token")
    whatsapp_access_token: str = Field("", description="Meta Graph API access token")
    whatsapp_phone_number_id: str = Field("", description="Meta phone number ID for sends")
    whatsapp_api_version: str = Field("v22.0", description="Meta Graph API version")
    whatsapp_allowed_phone_numbers: str = Field(
        "",
        description="Comma-separated WhatsApp phone numbers allowed (empty = all)",
    )

    def get_allowed_whatsapp_phone_numbers(self) -> List[str]:
        """Parse allowed WhatsApp phone numbers into a list."""
        if not self.whatsapp_allowed_phone_numbers or not self.whatsapp_allowed_phone_numbers.strip():
            return []
        return [number.strip() for number in self.whatsapp_allowed_phone_numbers.split(",") if number.strip()]

    # -------------------------------------------------------------------------
    # Cross-channel identity mapping
    # -------------------------------------------------------------------------
    channel_user_bindings: str = Field(
        "",
        description=(
            "Optional JSON map of external channel IDs to stable IDs, "
            "e.g. {'signal:+1555':'user-123','telegram:123':'user-123'}"
        ),
    )

    def get_channel_user_bindings(self) -> Dict[str, str]:
        """
        Parse channel_user_bindings JSON into a dict.

        Expected format:
            {"signal:+1555": "user-1", "telegram:12345": "user-1"}
        """
        raw = (self.channel_user_bindings or "").strip()
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return {}
            normalized: Dict[str, str] = {}
            for key, value in parsed.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                k = key.strip().lower()
                v = value.strip()
                if k and v:
                    normalized[k] = v
            return normalized
        except Exception:
            return {}

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

    # Inbound email (IMAP polling)
    email_inbound_enabled: bool = Field(False, description="Enable inbound email polling channel")
    imap_host: Optional[str] = Field(None, description="IMAP server host")
    imap_port: int = Field(993, description="IMAP server port")
    imap_user: Optional[str] = Field(None, description="IMAP username")
    imap_password: Optional[str] = Field(None, description="IMAP password")
    imap_use_ssl: bool = Field(True, description="Use IMAP SSL")
    imap_folder: str = Field("INBOX", description="IMAP folder to poll")
    email_inbound_poll_interval: float = Field(30.0, description="Seconds between inbound email polls")
    email_allowed_senders: str = Field(
        "",
        description="Comma-separated list of allowed sender emails (empty = all)",
    )
    email_confirmation_ttl: int = Field(
        10800,
        description="Seconds before an email confirmation session expires (default 3 hours)",
    )

    def get_email_allowed_senders(self) -> List[str]:
        """Parse allowed inbound sender addresses."""
        if not self.email_allowed_senders or not self.email_allowed_senders.strip():
            return []
        return [sender.strip().lower() for sender in self.email_allowed_senders.split(",") if sender.strip()]

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
