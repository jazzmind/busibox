"""
Test OAuth2 token endpoint with form-encoded requests.

The OAuth2 spec requires support for application/x-www-form-urlencoded,
not just JSON. These tests ensure we handle both formats correctly.
"""

import pytest
import httpx
from fastapi import FastAPI


@pytest.fixture
def oauth_app_with_form(reload_authz, monkeypatch):
    """OAuth app that supports form-encoded requests."""
    import importlib
    import routes.oauth as oauth
    import routes.internal as internal
    import config as cfg
    from test_authz_service import FakePG

    # Reload config to pick up test environment variables (set by set_env fixture)
    importlib.reload(cfg)
    new_config = cfg.Config()
    importlib.reload(oauth)
    oauth.config = new_config

    fake = FakePG()
    monkeypatch.setattr(oauth, "_pg", fake)
    monkeypatch.setattr(internal, "pg", fake)

    app = FastAPI()
    app.include_router(oauth.router)
    app.include_router(internal.router)  # Needed for /internal/sync/user
    return app, fake


@pytest.mark.asyncio
async def test_token_endpoint_with_form_encoded_body(oauth_app_with_form):
    """Test that token endpoint accepts application/x-www-form-urlencoded."""
    app, fake = oauth_app_with_form

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Test form-encoded request (standard OAuth2 format)
        form_data = {
            "grant_type": "client_credentials",
            "client_id": "test-client",
            "client_secret": "test-client-secret",
            "audience": "ingest-api",  # Use allowed audience from conftest
            "scope": "ingest.write",  # Use allowed scope from conftest
        }

        resp = await client.post(
            "/oauth/token",
            data=form_data,  # This sends as application/x-www-form-urlencoded
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        assert resp.status_code == 200
        token_data = resp.json()
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"


@pytest.mark.asyncio
async def test_token_endpoint_with_json_body(oauth_app_with_form):
    """Test that token endpoint still accepts JSON (our extension)."""
    app, fake = oauth_app_with_form

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Test JSON request (our extension for convenience)
        json_data = {
            "grant_type": "client_credentials",
            "client_id": "test-client",
            "client_secret": "test-client-secret",
            "audience": "search-api",  # Use allowed audience from conftest
            "scope": "search.read",  # Use allowed scope from conftest
        }

        resp = await client.post(
            "/oauth/token",
            json=json_data,
            headers={"Content-Type": "application/json"},
        )

        assert resp.status_code == 200
        token_data = resp.json()
        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"


@pytest.mark.asyncio
async def test_token_exchange_with_form_encoding(oauth_app_with_form):
    """Test token exchange with form-encoded request."""
    app, fake = oauth_app_with_form

    # Use valid UUIDs
    user_id = "11111111-1111-1111-1111-111111111111"
    role_id = "22222222-2222-2222-2222-222222222222"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Sync a user first
        await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "test@example.com",
                "roles": [{"id": role_id, "name": "Admin"}],
                "user_role_ids": [role_id],
            },
        )

        # Test token exchange with form encoding
        form_data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": "test-client",
            "client_secret": "test-client-secret",
            "audience": "agent-api",  # Use allowed audience from conftest
            "scope": "agent.execute",  # Use allowed scope from conftest
            "requested_subject": user_id,
        }

        resp = await client.post(
            "/oauth/token",
            data=form_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if resp.status_code != 200:
            print(f"ERROR: {resp.status_code} - {resp.json()}")
        assert resp.status_code == 200
        token_data = resp.json()
        assert "access_token" in token_data

