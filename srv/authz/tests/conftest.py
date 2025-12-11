import os
import sys
import importlib
import pytest
import pytest_asyncio
import httpx
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def set_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    monkeypatch.setenv("JWT_AUDIENCE", "test-audience")
    monkeypatch.setenv("JWT_ISSUER", "authz-test")
    monkeypatch.setenv("AUTHZ_TOKEN_TTL", "600")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "test_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test_pass")
    monkeypatch.setenv("POSTGRES_DB", "test_db")
    yield


@pytest.fixture
def reload_authz(monkeypatch):
    # ensure src is on path
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    sys.path.insert(0, root)
    modules = ["config", "routes.authz"]
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


