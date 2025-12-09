import jwt
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_token_issuance_and_audit(authz_app):
    app, authz, audit_log = authz_app

    body = {
        "userId": "11111111-1111-1111-1111-111111111111",
        "roles": [
            {"id": "aaaaaaa1-bbbb-cccc-dddd-eeeeeeee0001", "name": "r1", "permissions": ["read", "update"]},
            {"id": "aaaaaaa2-bbbb-cccc-dddd-eeeeeeee0002", "name": "r2", "permissions": ["read"]},
        ],
        "audience": "test-audience",
    }

    async with AsyncClient(app=app, base_url="http://test") as client:
        resp = await client.post("/authz/token", json=body)

    assert resp.status_code == 200
    data = resp.json()
    token = data["token"]
    decoded = jwt.decode(token, "test-secret", algorithms=["HS256"], audience="test-audience")
    assert decoded["sub"] == body["userId"]
    assert decoded["roles"] == body["roles"]
    assert decoded["iss"] == "authz-test"

    assert len(audit_log) == 1
    audit = audit_log[0]
    assert audit["action"] == "authz.token.issued"
    assert audit["resource_type"] == "authz_token"
    assert audit["details"]["role_count"] == 2
    # audit should carry caller context as the actor
    assert audit["user_id"] == body["userId"]
    assert audit["role_ids"] == []


@pytest.mark.asyncio
async def test_audit_uses_caller_context_from_bearer(authz_app):
    app, authz, audit_log = authz_app

    caller_roles = [{"id": "role-123", "name": "finance", "permissions": ["read"]}]
    caller_token = jwt.encode(
        {"sub": "caller-uid", "roles": caller_roles, "aud": "test-audience", "iss": "authz-test"},
        "test-secret",
        algorithm="HS256",
    )

    body = {
        "actorId": "actor-x",
        "action": "doc.move",
        "resourceType": "document",
        "resourceId": "abcd-ef01",
        "details": {"from": "libA", "to": "libB"},
    }

    headers = {"Authorization": f"Bearer {caller_token}"}

    async with AsyncClient(app=app, base_url="http://test", headers=headers) as client:
        resp = await client.post("/authz/audit", json=body)

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    assert len(audit_log) == 1
    audit = audit_log[0]
    # payload values
    assert audit["actor_id"] == body["actorId"]
    assert audit["action"] == body["action"]
    assert audit["resource_type"] == body["resourceType"]
    assert audit["resource_id"] == body["resourceId"]
    # caller context propagated to RLS vars
    assert audit["user_id"] == "caller-uid"
    assert audit["role_ids"] == ["role-123"]


@pytest.mark.asyncio
async def test_validation_errors(authz_app):
    app, authz, audit_log = authz_app

    async with AsyncClient(app=app, base_url="http://test") as client:
        # missing userId
        resp = await client.post("/authz/token", json={"roles": []})
        assert resp.status_code == 400
        # bad roles type
        resp = await client.post("/authz/token", json={"userId": "x", "roles": {}})
        assert resp.status_code == 400

    assert audit_log == []
