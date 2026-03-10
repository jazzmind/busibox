"""
Pytest configuration for AuthZ service tests.

Unit tests use the reload_authz fixture and monkeypatched env vars.
Integration tests (test_pvt.py, test_real_auth_integration.py) use real
services and the shared busibox_common.testing.AuthTestClient for token
management.
"""
import os
import sys
import importlib

import pytest

# ---------------------------------------------------------------------------
# Shared testing library (available when busibox_common is on PYTHONPATH)
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
    set_auth_env = create_service_auth_fixture("authz")
    _has_shared_testing = True
except ImportError:
    pass  # Shared library not available (e.g. minimal unit-test run)

# Enable pytest plugin for failed test filter generation (if available)
if _has_shared_testing:
    pytest_plugins = ["busibox_common.testing.pytest_failed_filter"]


# ---------------------------------------------------------------------------
# Unit-test env vars (monkeypatched so they don't leak)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("AUTHZ_ISSUER", "authz-test")
    monkeypatch.setenv("AUTHZ_ACCESS_TOKEN_TTL", "600")
    monkeypatch.setenv("AUTHZ_SIGNING_ALG", "RS256")
    monkeypatch.setenv("AUTHZ_RSA_KEY_SIZE", "2048")
    # Zero Trust: allowed audiences for token exchange (no client auth)
    monkeypatch.setenv("AUTHZ_ALLOWED_AUDIENCES", "test-audience,data-api,search-api,agent-api")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")
    monkeypatch.setenv("POSTGRES_DB", "test_db")
    yield


@pytest.fixture(autouse=True)
def add_src_to_path():
    """
    Ensure ``srv/authz/src`` is importable for all tests.
    Some tests import modules directly (e.g. oauth.contracts) without using the
    ``reload_authz`` fixture.
    """
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    sys.path.insert(0, root)
    try:
        yield
    finally:
        if root in sys.path:
            sys.path.remove(root)


@pytest.fixture
def reload_authz(monkeypatch):
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    sys.path.insert(0, root)
    modules = ["config", "routes.audit", "routes.oauth", "routes.internal"]
    for m in modules:
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    import config  # noqa
    import routes.audit as audit  # noqa
    importlib.reload(config)
    importlib.reload(audit)
    yield audit
    for m in modules:
        sys.modules.pop(m, None)
    sys.path.remove(root)


# ---------------------------------------------------------------------------
# Integration-test auth fixtures (require running authz + test DB)
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_headers():
    """Get Authorization + X-Test-Mode headers for integration tests.

    Requires a running authz service and busibox_common.testing installed.
    Falls back to skip if not available.
    """
    if not _has_shared_testing:
        pytest.skip("busibox_common.testing not available")

    client = AuthTestClient()
    client._bootstrap_admin_in_authz_db()
    return client.get_auth_header(audience="authz-api")
