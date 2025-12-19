"""Unit tests for JWT validation and claim handling."""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt
from jose.utils import base64url_encode

from app.auth.tokens import jwks_cache, settings, validate_bearer


def build_token(secret: str, extra_claims: dict | None = None, *, kid: str = "test-key") -> str:
    now = datetime.now(timezone.utc)
    claims = {
        "sub": "user-123",
        "email": "user@example.com",
        "scope": "search.read ingest.write",
        "roles": ["user"],
        "iss": settings.auth_issuer or "https://issuer.test",
        "aud": settings.auth_audience or "https://aud.test",
        "iat": int(now.timestamp()),
        "nbf": int((now - timedelta(seconds=10)).timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if extra_claims:
        claims.update(extra_claims)
    return jwt.encode(claims, secret, algorithm="HS256", headers={"kid": kid})


def build_jwks(secret: str, *, kid: str = "test-key") -> dict:
    encoded_key = base64url_encode(secret.encode()).decode()
    return {"keys": [{"kty": "oct", "k": encoded_key, "alg": "HS256", "kid": kid}]}


@pytest.mark.asyncio
async def test_validate_bearer_success(monkeypatch):
    secret = "super-secret"
    token = build_token(secret)
    jwks = build_jwks(secret)

    async def fake_get():
        return jwks

    # Reset any cached JWKS to ensure deterministic test
    jwks_cache._jwks = None  # type: ignore[attr-defined]
    monkeypatch.setattr(jwks_cache, "get", fake_get)

    settings.auth_issuer = "https://issuer.test"
    settings.auth_audience = "https://aud.test"

    principal = await validate_bearer(token)

    assert principal.sub == "user-123"
    assert principal.email == "user@example.com"
    assert "search.read" in principal.scopes
    assert "ingest.write" in principal.scopes
    assert principal.roles == ["user"]
    assert principal.token == token


@pytest.mark.asyncio
async def test_validate_bearer_expired(monkeypatch):
    secret = "super-secret"
    past = datetime.now(timezone.utc) - timedelta(minutes=10)
    token = build_token(secret, {"exp": int(past.timestamp())})
    jwks = build_jwks(secret)

    async def fake_get():
        return jwks

    monkeypatch.setattr(jwks_cache, "get", fake_get)

    with pytest.raises(jwt.ExpiredSignatureError):
        await validate_bearer(token)


@pytest.mark.asyncio
async def test_validate_bearer_audience_mismatch(monkeypatch):
    secret = "super-secret"
    token = build_token(secret, {"aud": "https://different.test"})
    jwks = build_jwks(secret)
    settings.auth_audience = "https://expected.test"

    async def fake_get():
        return jwks

    monkeypatch.setattr(jwks_cache, "get", fake_get)

    with pytest.raises(jwt.JWTError):
        await validate_bearer(token)


@pytest.mark.asyncio
async def test_validate_bearer_signature_failure(monkeypatch):
    correct_secret = "super-secret"
    wrong_secret = "other-secret"
    token = build_token(wrong_secret)
    jwks = build_jwks(correct_secret)

    async def fake_get():
        return jwks

    monkeypatch.setattr(jwks_cache, "get", fake_get)

    with pytest.raises(jwt.JWTError):
        await validate_bearer(token)








