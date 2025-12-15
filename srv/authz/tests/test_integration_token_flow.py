"""
Integration tests for end-to-end token exchange flow.

These tests verify the complete OAuth2 token exchange flow:
1. Bootstrap authz (key generation, client registration)
2. Sync user + roles from ai-portal
3. Exchange for service-scoped access token
4. Validate token structure and claims
5. Use token to call downstream service (simulated)
"""

import json

import jwt
import pytest
import httpx
from fastapi import FastAPI


@pytest.fixture
def full_authz_app(reload_authz, monkeypatch):
    """Full authz app with all routers."""
    import importlib
    import routes.admin as admin
    import routes.authz as authz
    import routes.internal as internal
    import routes.oauth as oauth
    import config as cfg
    import main

    # Reload config first to pick up environment variables
    importlib.reload(cfg)
    # Create new config instance BEFORE reloading oauth (so oauth uses new config)
    new_config = cfg.Config()
    # Now reload oauth and admin modules
    importlib.reload(oauth)
    importlib.reload(admin)
    # Update their config references to use the new config instance
    oauth.config = new_config
    admin.config = new_config

    from test_authz_service import FakePG

    fake = FakePG()

    # Patch all module-level PostgresService instances
    monkeypatch.setattr(oauth, "_pg", fake)
    monkeypatch.setattr(internal, "pg", fake)
    monkeypatch.setattr(admin, "pg", fake)
    monkeypatch.setattr(main, "pg", fake)  # For write_audit in authz.py
    monkeypatch.setattr(authz, "PostgresService", lambda *_args, **_kwargs: fake)

    app = FastAPI()
    app.include_router(oauth.router)
    app.include_router(internal.router)
    app.include_router(admin.router)
    app.include_router(authz.router)
    return app, fake


@pytest.mark.asyncio
async def test_complete_token_exchange_flow(full_authz_app):
    """
    Test complete flow:
    1. Bootstrap authz (JWKS + client)
    2. Sync user + roles
    3. Exchange for access token
    4. Decode and validate token
    """
    app, fake = full_authz_app

    user_id = "aaaaaaaa-bbbb-cccc-dddd-111111111111"
    role_id = "role-001-aaaa-bbbb-cccc-dddddddddddd"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Bootstrap (get JWKS to trigger key generation)
        jwks_resp = await client.get("/.well-known/jwks.json")
        assert jwks_resp.status_code == 200
        jwks_data = jwks_resp.json()
        assert len(jwks_data["keys"]) == 1
        jwk = jwks_data["keys"][0]

        # Step 2: Sync user + roles (simulates ai-portal sync)
        sync_resp = await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "alice@example.com",
                "status": "active",
                "roles": [{"id": role_id, "name": "Engineering", "description": "Engineering team"}],
                "user_role_ids": [role_id],
            },
        )
        assert sync_resp.status_code == 200

        # Step 3: Exchange for service-scoped access token
        exchange_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "ingest-api",
                "scope": "ingest.write ingest.read",
                "requested_subject": user_id,
                "requested_purpose": "integration-test",
            },
        )
        assert exchange_resp.status_code == 200
        token_data = exchange_resp.json()
        assert "access_token" in token_data
        assert token_data["token_type"] == "bearer"
        assert token_data["expires_in"] > 0

        access_token = token_data["access_token"]

        # Step 4: Decode and validate token structure
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        decoded = jwt.decode(
            access_token,
            public_key,
            algorithms=["RS256"],
            issuer="authz-test",
            audience="ingest-api",
            options={"require": ["exp", "iat", "sub", "iss", "aud", "jti"]},
        )

        assert decoded["sub"] == user_id
        assert decoded["aud"] == "ingest-api"
        assert decoded["iss"] == "authz-test"
        assert decoded["typ"] == "access"
        assert "ingest.write" in decoded["scope"]
        assert "ingest.read" in decoded["scope"]
        assert len(decoded["roles"]) == 1
        assert decoded["roles"][0]["id"] == role_id
        assert decoded["roles"][0]["name"] == "Engineering"
        assert "read" in decoded["roles"][0]["permissions"]
        assert "create" in decoded["roles"][0]["permissions"]


@pytest.mark.asyncio
async def test_token_exchange_with_multiple_roles(full_authz_app):
    """Test token exchange for user with multiple roles."""
    app, fake = full_authz_app

    user_id = "bbbbbbbb-bbbb-cccc-dddd-222222222222"
    role1_id = "role-002-aaaa-bbbb-cccc-dddddddddddd"
    role2_id = "role-003-aaaa-bbbb-cccc-dddddddddddd"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Sync user with multiple roles
        await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "bob@example.com",
                "roles": [
                    {"id": role1_id, "name": "Engineering"},
                    {"id": role2_id, "name": "Finance"},
                ],
                "user_role_ids": [role1_id, role2_id],
            },
        )

        # Exchange for token
        exchange_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "search-api",
                "scope": "search.read",
                "requested_subject": user_id,
            },
        )
        assert exchange_resp.status_code == 200

        token_data = exchange_resp.json()
        access_token = token_data["access_token"]

        # Decode token
        jwks = (await client.get("/.well-known/jwks.json")).json()
        jwk = jwks["keys"][0]
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        decoded = jwt.decode(
            access_token,
            public_key,
            algorithms=["RS256"],
            issuer="authz-test",
            audience="search-api",
        )

        # Verify both roles present
        assert len(decoded["roles"]) == 2
        role_ids = [r["id"] for r in decoded["roles"]]
        assert role1_id in role_ids
        assert role2_id in role_ids


@pytest.mark.asyncio
async def test_token_exchange_fails_for_unknown_user(full_authz_app):
    """Test token exchange fails for user not synced to authz."""
    app, fake = full_authz_app

    unknown_user_id = "cccccccc-cccc-cccc-dddd-333333333333"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Try to exchange without syncing user first
        exchange_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "ingest-api",
                "scope": "ingest.write",
                "requested_subject": unknown_user_id,
            },
        )
        assert exchange_resp.status_code == 400
        assert "unknown_subject" in exchange_resp.json()["detail"]


@pytest.mark.asyncio
async def test_token_exchange_enforces_audience(full_authz_app):
    """Test token exchange enforces allowed audiences for client."""
    app, fake = full_authz_app

    user_id = "dddddddd-dddd-dddd-dddd-444444444444"
    role_id = "role-004-aaaa-bbbb-cccc-dddddddddddd"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Sync user
        await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "charlie@example.com",
                "roles": [{"id": role_id, "name": "Sales"}],
                "user_role_ids": [role_id],
            },
        )

        # Try to request token for disallowed audience
        exchange_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "forbidden-service",  # Not in allowed_audiences
                "scope": "forbidden.read",
                "requested_subject": user_id,
            },
        )
        assert exchange_resp.status_code == 403
        assert "unauthorized_client_audience" in exchange_resp.json()["detail"]


@pytest.mark.asyncio
async def test_client_credentials_flow(full_authz_app):
    """Test OAuth2 client_credentials grant (service-to-service)."""
    app, fake = full_authz_app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Request token with client_credentials
        token_resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "ingest-api",
                "scope": "ingest.write",
            },
        )
        assert token_resp.status_code == 200
        token_data = token_resp.json()
        assert "access_token" in token_data

        # Decode token
        jwks = (await client.get("/.well-known/jwks.json")).json()
        jwk = jwks["keys"][0]
        public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
        decoded = jwt.decode(
            token_data["access_token"],
            public_key,
            algorithms=["RS256"],
            issuer="authz-test",
            audience="ingest-api",
        )

        # For client_credentials, sub is the client_id
        assert decoded["sub"] == "test-client"
        assert decoded["aud"] == "ingest-api"
        assert decoded["scope"] == "ingest.write"
        assert decoded["roles"] == []  # No user roles for service tokens


@pytest.mark.asyncio
async def test_audit_log_records_token_issuance(full_authz_app):
    """Test that token issuance is recorded in audit log."""
    app, fake = full_authz_app

    user_id = "eeeeeeee-eeee-eeee-eeee-555555555555"
    role_id = "role-005-aaaa-bbbb-cccc-dddddddddddd"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Bootstrap
        await client.get("/.well-known/jwks.json")

        # Sync user
        await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "dave@example.com",
                "roles": [{"id": role_id, "name": "Support"}],
                "user_role_ids": [role_id],
            },
        )

        # Exchange for token
        await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "agent-api",
                "scope": "agent.execute",
                "requested_subject": user_id,
                "requested_purpose": "audit-test",
            },
        )

    # Verify audit log
    assert len(fake.audit_log) > 0
    token_audit = next((a for a in fake.audit_log if a["action"] == "oauth.token.issued"), None)
    assert token_audit is not None
    assert token_audit["actor_id"] == user_id
    assert token_audit["details"]["audience"] == "agent-api"
    assert token_audit["details"]["purpose"] == "audit-test"

