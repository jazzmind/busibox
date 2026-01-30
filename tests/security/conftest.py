"""
Security Test Suite Configuration and Fixtures

Provides shared fixtures and configuration for all security tests.
Tests can run against local dev or test/production environments.
"""

import os
import asyncio
from typing import Optional, Dict, Any
from dataclasses import dataclass

import pytest
import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


@dataclass
class ServiceEndpoints:
    """Service endpoint configuration."""
    agent: str
    data: str
    search: str
    authz: str
    files: str
    
    @classmethod
    def from_env(cls, env: str = "test") -> "ServiceEndpoints":
        """Load endpoints based on environment."""
        if env == "local":
            return cls(
                agent=os.getenv("AGENT_API_URL", "http://localhost:8000"),
                data=os.getenv("DATA_API_URL", "http://localhost:8002"),
                search=os.getenv("SEARCH_API_URL", "http://localhost:8003"),
                authz=os.getenv("AUTHZ_API_URL", "http://localhost:8010"),
                files=os.getenv("FILES_API_URL", "http://localhost:9000"),
            )
        elif env == "test":
            return cls(
                agent="http://10.96.201.202:8000",
                data="http://10.96.201.206:8002",
                search="http://10.96.201.204:8003",
                authz="http://10.96.201.210:8010",
                files="http://10.96.201.205:9000",
            )
        else:  # production
            return cls(
                agent="http://10.96.200.202:8000",
                data="http://10.96.200.206:8002",
                search="http://10.96.200.204:8003",
                authz="http://10.96.200.210:8010",
                files="http://10.96.200.205:9000",
            )


@dataclass
class TestCredentials:
    """Test credentials for authentication."""
    valid_token: Optional[str] = None
    user_id: str = "test-security-user"
    
    @classmethod
    def from_env(cls) -> "TestCredentials":
        """Load credentials from environment."""
        return cls(
            valid_token=os.getenv("TEST_JWT_TOKEN"),
            user_id=os.getenv("TEST_USER_ID", "test-security-user"),
        )


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def test_env() -> str:
    """Get target test environment."""
    return os.getenv("SECURITY_TEST_ENV", "test")


@pytest.fixture(scope="session")
def endpoints(test_env: str) -> ServiceEndpoints:
    """Get service endpoints for target environment."""
    return ServiceEndpoints.from_env(test_env)


@pytest.fixture(scope="session")
def credentials() -> TestCredentials:
    """Get test credentials."""
    return TestCredentials.from_env()


@pytest.fixture(scope="session")
def http_client() -> httpx.Client:
    """Synchronous HTTP client for tests."""
    client = httpx.Client(timeout=30.0, follow_redirects=False)
    yield client
    client.close()


@pytest.fixture(scope="session")
async def async_http_client() -> httpx.AsyncClient:
    """Async HTTP client for concurrent tests."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        yield client


@pytest.fixture
def auth_headers(credentials: TestCredentials) -> Dict[str, str]:
    """Get authentication headers."""
    headers = {}
    if credentials.valid_token:
        headers["Authorization"] = f"Bearer {credentials.valid_token}"
    elif credentials.user_id:
        headers["X-User-Id"] = credentials.user_id
    return headers


@pytest.fixture
def admin_headers(credentials: TestCredentials) -> Dict[str, str]:
    """Get admin authentication headers."""
    headers = {}
    if credentials.admin_token:
        headers["Authorization"] = f"Bearer {credentials.admin_token}"
    return headers


# Test configuration
def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "destructive: marks tests that may modify data")
    config.addinivalue_line("markers", "auth: authentication/authorization tests")
    config.addinivalue_line("markers", "injection: injection attack tests")
    config.addinivalue_line("markers", "fuzz: fuzzing tests")
    config.addinivalue_line("markers", "rate_limit: rate limiting tests")
    config.addinivalue_line("markers", "idor: insecure direct object reference tests")


def pytest_collection_modifyitems(config, items):
    """Skip slow tests unless explicitly requested."""
    if not config.getoption("--runslow", default=False):
        skip_slow = pytest.mark.skip(reason="need --runslow option to run")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip_slow)


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--runslow", action="store_true", default=False, help="run slow tests"
    )
    parser.addoption(
        "--destructive", action="store_true", default=False, help="run destructive tests"
    )

