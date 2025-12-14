import json

import jwt
import pytest
import httpx
from fastapi import FastAPI

from oauth.client_auth import hash_client_secret


class FakePG:
    def __init__(self, *_, **__):
        self.audit_log = []
        self.clients = {}
        self.keys = {}  # kid -> {kid, alg, private_key_pem, public_jwk}
        self.users = {}  # user_id -> {email, role_ids}
        self.roles = {}  # role_id -> {id,name}

    async def connect(self):
        return None

    async def ensure_schema(self):
        return None

    async def insert_audit(self, actor_id, action, resource_type, resource_id, details, user_id, role_ids):
        self.audit_log.append(
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

    async def get_oauth_client(self, client_id: str):
        return self.clients.get(client_id)

    async def upsert_oauth_client(self, *, client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active=True):
        self.clients[client_id] = {
            "client_id": client_id,
            "client_secret_hash": client_secret_hash,
            "allowed_audiences": allowed_audiences,
            "allowed_scopes": allowed_scopes,
            "is_active": is_active,
        }

    async def get_active_signing_key(self):
        if not self.keys:
            return None
        # return last inserted
        return list(self.keys.values())[-1]

    async def insert_signing_key(self, *, kid, alg, private_key_pem, public_jwk, is_active=True):
        self.keys[kid] = {"kid": kid, "alg": alg, "private_key_pem": private_key_pem, "public_jwk": public_jwk}

    async def list_public_jwks(self):
        return [v["public_jwk"] for v in self.keys.values()]

    async def upsert_roles(self, roles):
        for r in roles:
            self.roles[r["id"]] = r

    async def upsert_user_and_roles(
        self,
        *,
        user_id,
        email,
        status,
        idp_provider,
        idp_tenant_id,
        idp_object_id,
        idp_roles,
        idp_groups,
        user_role_ids,
    ):
        self.users[user_id] = {"email": email, "role_ids": list(user_role_ids)}

    async def get_user_roles(self, user_id: str):
        u = self.users.get(user_id)
        if not u:
            return []
        out = []
        for rid in u["role_ids"]:
            r = self.roles.get(rid)
            if r:
                out.append({"id": r["id"], "name": r["name"]})
        return out


@pytest.fixture
def authz_app(reload_authz, monkeypatch):
    import routes.oauth as oauth
    import routes.internal as internal
    import routes.authz as authz

    fake = FakePG()

    # Patch module-level PostgresService instances
    monkeypatch.setattr(oauth, "_pg", fake)
    monkeypatch.setattr(internal, "pg", fake)
    # Patch audit route to use fake DB (it otherwise tries to connect to localhost)
    monkeypatch.setattr(authz, "PostgresService", lambda *_args, **_kwargs: fake)

    app = FastAPI()
    app.include_router(oauth.router)
    app.include_router(internal.router)
    app.include_router(authz.router)
    return app, oauth, internal, authz, fake


def _decode_with_jwks(token: str, jwk: dict, *, issuer: str, audience: str | None):
    public = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    return jwt.decode(
        token,
        public,
        algorithms=["RS256"],
        issuer=issuer,
        audience=audience,
        options={"require": ["exp", "iat", "sub", "iss", "aud", "jti"]},
    )


@pytest.mark.asyncio
async def test_jwks_endpoint_bootstraps_key_and_client(authz_app):
    app, oauth, internal, authz, fake = authz_app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/.well-known/jwks.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "keys" in data
        assert len(data["keys"]) == 1
        assert data["keys"][0]["kty"] == "RSA"
        assert data["keys"][0]["kid"]

    # client should be bootstrapped by env vars
    assert "test-client" in fake.clients


@pytest.mark.asyncio
async def test_token_exchange_issues_service_scoped_token(authz_app):
    app, oauth, internal, authz, fake = authz_app

    # Sync a user + roles first (mimics ai-portal server-to-server sync)
    user_id = "11111111-1111-1111-1111-111111111111"
    role_id = "aaaaaaa1-bbbb-cccc-dddd-eeeeeeee0001"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # bootstrap key + client
        await client.get("/.well-known/jwks.json")
        sync = await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "user@example.com",
                "roles": [{"id": role_id, "name": "Editors"}],
                "user_role_ids": [role_id],
            },
        )
        assert sync.status_code == 200

        resp = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "test-audience",
                "scope": "search.read",
                "requested_subject": user_id,
                "requested_purpose": "unit-test",
            },
        )

    assert resp.status_code == 200
    token = resp.json()["access_token"]
    assert token

    jwk = (await fake.list_public_jwks())[0]
    decoded = _decode_with_jwks(token, jwk, issuer="authz-test", audience="test-audience")
    assert decoded["sub"] == user_id
    assert decoded["aud"] == "test-audience"
    assert decoded["iss"] == "authz-test"
    assert decoded["scope"] == "search.read"
    assert decoded["typ"] == "access"
    assert decoded["roles"][0]["id"] == role_id

    # audit log should include issuance entry
    assert len(fake.audit_log) == 1
    assert fake.audit_log[0]["action"] == "oauth.token.issued"


@pytest.mark.asyncio
async def test_audit_uses_caller_context_from_bearer(authz_app):
    app, oauth, internal, authz, fake = authz_app

    # Create a token for caller
    user_id = "22222222-2222-2222-2222-222222222222"
    role_id = "aaaaaaa2-bbbb-cccc-dddd-eeeeeeee0002"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # bootstrap key + client
        await client.get("/.well-known/jwks.json")
        await client.post(
            "/internal/sync/user",
            json={
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "user_id": user_id,
                "email": "caller@example.com",
                "roles": [{"id": role_id, "name": "Finance"}],
                "user_role_ids": [role_id],
            },
        )
        tok = await client.post(
            "/oauth/token",
            json={
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": "test-client",
                "client_secret": "test-client-secret",
                "audience": "test-audience",
                "scope": "search.read",
                "requested_subject": user_id,
            },
        )
        access_token = tok.json()["access_token"]

        headers = {"Authorization": f"Bearer {access_token}"}
        resp = await client.post(
            "/authz/audit",
            headers=headers,
            json={
                "actorId": user_id,
                "action": "doc.move",
                "resourceType": "document",
                "resourceId": None,
                "details": {"from": "libA", "to": "libB"},
            },
        )

    assert resp.status_code == 200
    # second audit record (first is oauth.token.issued)
    assert len(fake.audit_log) >= 2
    audit = fake.audit_log[-1]
    assert audit["user_id"] == user_id
    assert audit["role_ids"] == [role_id]






