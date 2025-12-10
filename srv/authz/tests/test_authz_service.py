import jwt
import pytest
import httpx


@pytest.mark.asyncio
async def test_token_issuance_and_audit(authz_client):
    client, audit_log = authz_client

    body = {
        "userId": "11111111-1111-1111-1111-111111111111",
        "roles": [
            {"id": "aaaaaaa1-bbbb-cccc-dddd-eeeeeeee0001", "name": "r1", "permissions": ["read", "update"]},
            {"id": "aaaaaaa2-bbbb-cccc-dddd-eeeeeeee0002", "name": "r2", "permissions": ["read"]},
        ],
        "audience": "test-audience",
    }

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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
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

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # missing userId
        resp = await client.post("/authz/token", json={"roles": []})
        assert resp.status_code == 400
        # bad roles type
        resp = await client.post("/authz/token", json={"userId": "x", "roles": {}})
        assert resp.status_code == 400

    assert audit_log == []


@pytest.mark.asyncio
async def test_token_issuance_with_empty_roles(authz_client):
    client, audit_log = authz_client

    body = {
        "userId": "22222222-2222-2222-2222-222222222222",
        "roles": [],
        "audience": "test-audience",
    }

    resp = await client.post("/authz/token", json=body)
    assert resp.status_code == 200
    data = resp.json()
    token = data["token"]
    decoded = jwt.decode(token, "test-secret", algorithms=["HS256"], audience="test-audience")
    assert decoded["roles"] == []

    assert len(audit_log) == 1
    audit = audit_log[0]
    assert audit["details"]["role_count"] == 0


@pytest.mark.asyncio
async def test_audit_falls_back_to_actor_when_no_bearer(authz_client):
    client, audit_log = authz_client

    body = {
        "actorId": "actor-no-bearer",
        "action": "doc.update",
        "resourceType": "document",
        "resourceId": "r-123",
        "details": {"field": "x"},
    }

    resp = await client.post("/authz/audit", json=body)
    assert resp.status_code == 200
    audit = audit_log[0]
    assert audit["user_id"] == "actor-no-bearer"
    assert audit["role_ids"] == []


@pytest.mark.asyncio
async def test_audit_ignores_invalid_bearer(authz_app):
    app, authz, audit_log = authz_app

    headers = {"Authorization": "Bearer not-a-jwt"}
    body = {
        "actorId": "actor-x",
        "action": "doc.move",
        "resourceType": "document",
        "resourceId": "abcd-ef01",
        "details": {"from": "libA", "to": "libB"},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        resp = await client.post("/authz/audit", json=body)

    assert resp.status_code == 200
    audit = audit_log[0]
    assert audit["user_id"] == body["actorId"]
    assert audit["role_ids"] == []


@pytest.mark.asyncio
async def test_audit_missing_required_fields(authz_app):
    app, authz, audit_log = authz_app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/authz/audit", json={"actorId": "x"})
        assert resp.status_code == 400
        resp = await client.post("/authz/audit", json={"action": "x"})
        assert resp.status_code == 400

    assert audit_log == []

