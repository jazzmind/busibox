"""
Voice Agent Service Configuration.

Environment-based configuration for the voice agent service.
"""

import os
from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Voice Agent service settings."""

    # Service identity
    service_name: str = "voice-agent"
    service_version: str = "0.1.0"

    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8005
    debug: bool = False

    # PostgreSQL configuration
    postgres_host: str = "10.96.200.203"
    postgres_port: int = 5432
    postgres_db: str = "voice_agent"
    postgres_user: str = "busibox_user"
    postgres_password: str = ""

    # Test mode configuration
    test_mode_enabled: bool = False
    test_db_name: str = "test_voice_agent"
    test_db_user: str = "busibox_test_user"
    test_db_password: str = "testpassword"

    # FreeSWITCH ESL configuration
    freeswitch_host: str = "127.0.0.1"
    freeswitch_port: int = 8021
    freeswitch_password: str = "ClueCon"

    # SIP Trunk configuration (Telnyx)
    sip_trunk_host: str = "sip.telnyx.com"
    sip_trunk_username: str = ""
    sip_trunk_password: str = ""
    sip_trunk_caller_id: str = ""

    # Audio processing configuration
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    audio_chunk_duration_ms: int = 100  # 100ms chunks for real-time

    # Whisper STT configuration
    whisper_model: str = "base.en"  # Options: tiny, base, small, medium, large
    whisper_device: str = "auto"  # auto, cpu, cuda
    whisper_compute_type: str = "int8"  # float16, int8, int8_float16

    # VAD configuration (Phase 2)
    vad_threshold: float = 0.5
    vad_min_speech_duration_ms: int = 250
    vad_min_silence_duration_ms: int = 500

    # TTS configuration (Phase 4)
    piper_model: str = "en_US-amy-medium"
    piper_speaker: int = 0

    # LiteLLM configuration
    litellm_base_url: str = "http://10.96.200.207:4000"
    litellm_api_key: str = ""
    default_model: str = "gpt-4o-mini"

    # AuthZ configuration
    authz_url: str = "http://10.96.200.210:8010"
    jwt_issuer: str = "busibox-authz"
    jwt_audience: str = "voice-agent"

    # Data API configuration (for transcript storage)
    data_api_url: str = "http://10.96.200.206:8001"

    # Call configuration
    max_parallel_calls: int = 3
    backup_call_delay_minutes: int = 5
    max_call_duration_minutes: int = 120

    class Config:
        env_prefix = "VOICE_"
        env_file = ".env"
        extra = "ignore"

    @property
    def database_url(self) -> str:
        """Get the database connection URL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def test_database_url(self) -> str:
        """Get the test database connection URL."""
        return (
            f"postgresql://{self.test_db_user}:{self.test_db_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.test_db_name}"
        )


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
