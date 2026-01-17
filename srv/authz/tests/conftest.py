import os
import sys
import importlib
import pytest

# Enable pytest plugin for failed test filter generation (if available)
try:
    pytest_plugins = ["testing.pytest_failed_filter"]
except ImportError:
    pass  # Plugin not available in authz (no shared testing lib)


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("AUTHZ_ISSUER", "authz-test")
    monkeypatch.setenv("AUTHZ_ACCESS_TOKEN_TTL", "600")
    monkeypatch.setenv("AUTHZ_SIGNING_ALG", "RS256")
    monkeypatch.setenv("AUTHZ_RSA_KEY_SIZE", "2048")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "test-client")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES", "test-audience,ingest-api,search-api,agent-api")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_ALLOWED_SCOPES", "search.read,ingest.write,ingest.read,agent.execute")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")
    monkeypatch.setenv("POSTGRES_DB", "test_db")
    yield


@pytest.fixture(autouse=True)
def add_src_to_path():
    """
    Ensure `srv/authz/src` is importable for all tests.
    Some tests import modules directly (e.g. oauth.contracts) without using the
    `reload_authz` fixture.
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
    # ensure src is on path
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


# Note: All tests now use real PostgreSQL database via test_real_auth_integration.py
# Mock-based fixtures have been removed in favor of real integration testing





