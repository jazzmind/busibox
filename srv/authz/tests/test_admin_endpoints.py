"""
Tests for admin RBAC endpoints.
"""

import pytest
import httpx
from fastapi import FastAPI


@pytest.fixture
def admin_app(reload_authz, monkeypatch):
    import routes.admin as admin
    import routes.oauth as oauth

    from test_authz_service import FakePG

    fake = FakePG()

    # Patch module-level PostgresService instances
    monkeypatch.setattr(admin, "pg", fake)
    monkeypatch.setattr(oauth, "_pg", fake)

    app = FastAPI()
    app.include_router(admin.router)
    app.include_router(oauth.router)
    return app, admin, fake


@pytest.mark.asyncio
async def test_create_role_with_admin_token(admin_app, monkeypatch):
    app, admin, fake = admin_app

    # Set admin token
    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "Engineering", "description": "Engineering team"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Engineering"
    assert data["description"] == "Engineering team"
    assert "id" in data


@pytest.mark.asyncio
async def test_create_role_with_oauth_client(admin_app):
    app, admin, fake = admin_app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap key + client
        await client.get("/.well-known/jwks.json")

        resp = await client.post(
            "/admin/roles",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "name": "Finance",
                "description": "Finance department",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Finance"


@pytest.mark.asyncio
async def test_list_roles(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a few roles
        await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "Role1"},
        )
        await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "Role2"},
        )

        # List roles
        resp = await client.get(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert any(r["name"] == "Role1" for r in data)
    assert any(r["name"] == "Role2" for r in data)


@pytest.mark.asyncio
async def test_get_role(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a role
        create_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "TestRole", "description": "Test description"},
        )
        role_id = create_resp.json()["id"]

        # Get the role
        resp = await client.get(
            f"/admin/roles/{role_id}",
            headers={"Authorization": "Bearer test-admin-token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == role_id
    assert data["name"] == "TestRole"
    assert data["description"] == "Test description"


@pytest.mark.asyncio
async def test_update_role(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a role
        create_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "OldName"},
        )
        role_id = create_resp.json()["id"]

        # Update the role
        resp = await client.put(
            f"/admin/roles/{role_id}",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "NewName", "description": "Updated description"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "NewName"
    assert data["description"] == "Updated description"


@pytest.mark.asyncio
async def test_delete_role(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a role
        create_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "ToDelete"},
        )
        role_id = create_resp.json()["id"]

        # Delete the role
        resp = await client.delete(
            f"/admin/roles/{role_id}",
            headers={"Authorization": "Bearer test-admin-token"},
        )

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_add_user_role(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    user_id = "11111111-1111-1111-1111-111111111111"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a role
        create_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "TestRole"},
        )
        role_id = create_resp.json()["id"]

        # Add user-role binding
        resp = await client.post(
            "/admin/user-roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"user_id": user_id, "role_id": role_id},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == user_id
    assert data["role_id"] == role_id


@pytest.mark.asyncio
async def test_remove_user_role(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    user_id = "11111111-1111-1111-1111-111111111111"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a role
        create_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "TestRole"},
        )
        role_id = create_resp.json()["id"]

        # Add user-role binding
        await client.post(
            "/admin/user-roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"user_id": user_id, "role_id": role_id},
        )

        # Remove user-role binding
        resp = await client.delete(
            "/admin/user-roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"user_id": user_id, "role_id": role_id},
        )

    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


@pytest.mark.asyncio
async def test_get_user_roles(admin_app, monkeypatch):
    app, admin, fake = admin_app

    monkeypatch.setenv("AUTHZ_ADMIN_TOKEN", "test-admin-token")
    import importlib
    import config as cfg

    importlib.reload(cfg)
    monkeypatch.setattr(admin, "config", cfg.Config())

    user_id = "11111111-1111-1111-1111-111111111111"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create roles
        role1_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "Role1"},
        )
        role1_id = role1_resp.json()["id"]

        role2_resp = await client.post(
            "/admin/roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"name": "Role2"},
        )
        role2_id = role2_resp.json()["id"]

        # Add user-role bindings
        await client.post(
            "/admin/user-roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"user_id": user_id, "role_id": role1_id},
        )
        await client.post(
            "/admin/user-roles",
            headers={"Authorization": "Bearer test-admin-token"},
            json={"user_id": user_id, "role_id": role2_id},
        )

        # Get user roles
        resp = await client.get(
            f"/admin/users/{user_id}/roles",
            headers={"Authorization": "Bearer test-admin-token"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert any(r["name"] == "Role1" for r in data)
    assert any(r["name"] == "Role2" for r in data)


@pytest.mark.asyncio
async def test_unauthorized_without_auth(admin_app):
    app, admin, fake = admin_app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/roles",
            json={"name": "TestRole"},
        )

    assert resp.status_code == 401

