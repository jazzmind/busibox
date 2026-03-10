"""
Test fixtures for deployment service.

Provides both mock fixtures for unit tests and real auth fixtures for
integration tests via busibox_common.testing.
"""
import os

import pytest
from unittest.mock import AsyncMock, MagicMock

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
    from pathlib import Path

    load_env_files(Path(__file__).parent.parent)
    set_auth_env = create_service_auth_fixture("deploy")
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
    return client.get_auth_header(audience="deploy-api")


# ---------------------------------------------------------------------------
# Mock fixtures (for unit tests that don't need real services)
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ssh_command():
    """Mock SSH command execution."""
    async def mock_execute(host: str, command: str):
        return "", "", 0
    return mock_execute


@pytest.fixture
def sample_manifest():
    """Sample app manifest."""
    return {
        "name": "Test App",
        "id": "test-app",
        "version": "1.0.0",
        "description": "Test application",
        "icon": "Calculator",
        "defaultPath": "/testapp",
        "defaultPort": 3010,
        "healthEndpoint": "/api/health",
        "buildCommand": "npm run build",
        "startCommand": "npm start",
        "appMode": "prisma",
        "database": {
            "required": True,
            "preferredName": "testapp",
            "schemaManagement": "prisma"
        },
        "requiredEnvVars": ["LITELLM_API_KEY"],
        "optionalEnvVars": []
    }


@pytest.fixture
def sample_config():
    """Sample deployment config."""
    return {
        "githubRepoOwner": "test-owner",
        "githubRepoName": "test-repo",
        "githubBranch": "main",
        "environment": "staging",
        "secrets": {}
    }
