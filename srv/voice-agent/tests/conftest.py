"""
Test configuration and fixtures for Voice Agent tests.
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


@pytest.fixture
def sample_audio_chunk():
    """Create a sample audio chunk for testing."""
    import numpy as np
    
    # Generate 1 second of silence at 16kHz
    sample_rate = 16000
    duration = 1.0
    samples = int(sample_rate * duration)
    
    # Silent audio
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
    
    # Generate simple sine wave as "speech"
    t = np.linspace(0, duration, samples, dtype=np.float32)
    frequency = 440  # A4 note
    audio = 0.5 * np.sin(2 * np.pi * frequency * t)
    
    return {
        "data": audio,
        "sample_rate": sample_rate,
        "duration_ms": int(duration * 1000),
    }
