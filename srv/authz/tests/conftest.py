import os
import sys
import importlib
import pytest
import pytest_asyncio
import httpx
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("AUTHZ_ISSUER", "authz-test")
    monkeypatch.setenv("AUTHZ_ACCESS_TOKEN_TTL", "600")
    monkeypatch.setenv("AUTHZ_SIGNING_ALG", "RS256")
    monkeypatch.setenv("AUTHZ_RSA_KEY_SIZE", "2048")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "test-client")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES", "test-audience,ingest-api,search-api,agent-api")
    monkeypatch.setenv("AUTHZ_BOOTSTRAP_ALLOWED_SCOPES", "search.read,ingest.write")
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
    modules = ["config", "routes.authz", "routes.oauth", "routes.internal"]
    for m in modules:
        if m in sys.modules:
            importlib.reload(sys.modules[m])
    import config  # noqa
    import routes.authz as authz  # noqa
    importlib.reload(config)
    importlib.reload(authz)
    yield authz
    for m in modules:
        sys.modules.pop(m, None)
    sys.path.remove(root)


@pytest.fixture
def authz_app(reload_authz, monkeypatch):
    authz = reload_authz

    audit_log = []

    class FakePG:
        def __init__(self, *_, **__):
            pass

        async def connect(self):
            return None

        async def insert_audit(self, actor_id, action, resource_type, resource_id, details, user_id, role_ids):
            audit_log.append(
                {
                    "actor_id": actor_id,
                    "action": action,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "details": details,
                    "user_id": user_id,
                    "role_ids": role_ids,
                }
            )

    monkeypatch.setattr(authz, "PostgresService", FakePG)

    app = FastAPI()
    app.include_router(authz.router)
    return app, authz, audit_log


@pytest_asyncio.fixture
async def authz_client(authz_app):
    app, authz, audit_log = authz_app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, audit_log





