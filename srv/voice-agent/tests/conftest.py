"""
Test configuration and fixtures for Voice Agent tests.

Provides both mock fixtures for unit tests (audio processing, etc.) and
real auth fixtures for integration tests via busibox_common.testing.
"""

import asyncio
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from main import app

# ---------------------------------------------------------------------------
# Shared testing library (for integration tests)
# ---------------------------------------------------------------------------
_has_shared_testing = False
try:
    from busibox_common.testing.auth import AuthTestClient, auth_client  # noqa: F401
    from busibox_common.testing.environment import (
        load_env_files,
        create_service_auth_fixture,
    )

    load_env_files(Path(__file__).parent.parent)
    set_auth_env = create_service_auth_fixture("voice-agent")
    _has_shared_testing = True
    pytest_plugins = ["busibox_common.testing.pytest_failed_filter"]
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Integration-test auth fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_headers():
    """Get Authorization + X-Test-Mode headers for integration tests.

    Requires a running authz service and busibox_common.testing installed.
    """
    if not _has_shared_testing:
        pytest.skip("busibox_common.testing not available")

    client = AuthTestClient()
    return client.get_auth_header(audience="voice-agent-api")


# ---------------------------------------------------------------------------
# App / client fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def client():
    """Create test client for API tests."""
    return TestClient(app)


@pytest.fixture
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# Audio fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_audio_chunk():
    """Create a sample audio chunk for testing."""
    import numpy as np

    sample_rate = 16000
    duration = 1.0
    samples = int(sample_rate * duration)
    audio = np.zeros(samples, dtype=np.float32)

    return {
        "data": audio,
        "sample_rate": sample_rate,
        "duration_ms": int(duration * 1000),
    }


@pytest.fixture
def sample_speech_audio():
    """Create sample speech-like audio for testing."""
    import numpy as np

    sample_rate = 16000
    duration = 2.0
    samples = int(sample_rate * duration)

    t = np.linspace(0, duration, samples, dtype=np.float32)
    frequency = 440  # A4 note
    audio = 0.5 * np.sin(2 * np.pi * frequency * t)

    return {
        "data": audio,
        "sample_rate": sample_rate,
        "duration_ms": int(duration * 1000),
    }
