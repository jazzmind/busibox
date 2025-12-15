"""
OAuth2 endpoints for authz.

- GET /.well-known/jwks.json
- POST /oauth/token
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

import jwt
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from config import Config
from oauth.claims import AccessTokenClaims
from oauth.client_auth import verify_client_secret
from oauth.contracts import OAuthTokenRequest, OAuthTokenResponse, TOKEN_EXCHANGE_GRANT
from oauth.keys import generate_rsa_signing_key, load_private_key

logger = structlog.get_logger()
router = APIRouter()

config = Config()

# PostgresService instance - will be set by main.py
_pg = None

def set_pg_service(pg_service):
    """Set the shared PostgresService instance."""
    global _pg
    _pg = pg_service


async def _ensure_bootstrap() -> None:
    """
    Ensure authz has at least one active signing key and (optionally) a bootstrap OAuth client.
    """
    await _pg.connect()

    # 1) signing key
    active_key = await _pg.get_active_signing_key()
    if not active_key:
        if config.signing_alg != "RS256":
            raise RuntimeError(f"Unsupported signing alg for bootstrap: {config.signing_alg}")
        sk = generate_rsa_signing_key(
            key_size=config.rsa_key_size,
            alg=config.signing_alg,
            passphrase=config.key_encryption_passphrase,
        )
        await _pg.insert_signing_key(
            kid=sk.kid,
            alg=sk.alg,
            private_key_pem=sk.private_key_pem,
            public_jwk=sk.public_jwk,
            is_active=True,
        )
        logger.info("Generated initial authz signing key", kid=sk.kid, alg=sk.alg)

    # 2) bootstrap client (optional)
    if config.bootstrap_client_id and config.bootstrap_client_secret:
        existing = await _pg.get_oauth_client(config.bootstrap_client_id)
        from oauth.client_auth import hash_client_secret

        desired_hash = hash_client_secret(config.bootstrap_client_secret)
        if not existing:
            await _pg.upsert_oauth_client(
                client_id=config.bootstrap_client_id,
                client_secret_hash=desired_hash,
                allowed_audiences=config.bootstrap_client_allowed_audiences,
                allowed_scopes=config.bootstrap_client_allowed_scopes,
                is_active=True,
            )
            logger.info("Bootstrapped authz OAuth client", client_id=config.bootstrap_client_id)


async def _require_client(client_id: str, client_secret: str) -> dict:
    await _pg.connect()
    client = await _pg.get_oauth_client(client_id)
    if not client or not client.get("is_active"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    if not verify_client_secret(client_secret, client["client_secret_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_client")
    return client


def _enforce_audience(client: dict, audience: str) -> None:
    allowed = client.get("allowed_audiences") or []
    if audience not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_audience")


def _enforce_scopes(client: dict, scope_str: str) -> str:
    requested = [s for s in scope_str.split(" ") if s]
    allowed = client.get("allowed_scopes") or []
    if not requested:
        return ""
    if not allowed:
        # no scopes allowed
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_scope")
    out: List[str] = [s for s in requested if s in allowed]
    if len(out) != len(requested):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="unauthorized_client_scope")
    return " ".join(out)


async def _sign_access_token(claims: dict) -> str:
    await _pg.connect()
    row = await _pg.get_active_signing_key()
    if not row:
        raise RuntimeError("no active signing key configured")
    kid = row["kid"]
    alg = row["alg"]
    private_pem = row["private_key_pem"]
    key_obj = load_private_key(private_pem, config.key_encryption_passphrase)
    # PyJWT supports cryptography key objects directly.
    token = jwt.encode(claims, key_obj, algorithm=alg, headers={"kid": kid, "typ": "JWT"})
    return token


@router.get("/.well-known/jwks.json")
async def jwks():
    await _ensure_bootstrap()
    keys = await _pg.list_public_jwks()
    return {"keys": keys}


@router.post("/oauth/token")
async def token(request: Request):
    """
    OAuth2 token endpoint.

    Supports:
    - grant_type=client_credentials
    - grant_type=urn:ietf:params:oauth:grant-type:token-exchange

    Accepts both application/x-www-form-urlencoded and JSON bodies.
    """
    await _ensure_bootstrap()

    content_type = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        form = await request.form()
        body = dict(form)
    else:
        body = await request.json()

    try:
        token_req = OAuthTokenRequest.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_request") from e

    client = await _require_client(token_req.client_id, token_req.client_secret)

    if token_req.grant_type == "client_credentials":
        if not token_req.audience:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audience_required")
        _enforce_audience(client, token_req.audience)
        scope = _enforce_scopes(client, token_req.scope)

        now = int(time.time())
        exp = now + config.access_token_ttl
        claims = AccessTokenClaims(
            iss=config.issuer,
            sub=token_req.client_id,
            aud=token_req.audience,
            iat=now,
            nbf=now,
            exp=exp,
            jti=str(uuid.uuid4()),
            scope=scope,
            roles=[],
        ).model_dump()
        access_token = await _sign_access_token(claims)
        return OAuthTokenResponse(
            access_token=access_token,
            expires_in=config.access_token_ttl,
            scope=scope,
            issued_token_type="urn:ietf:params:oauth:token-type:access_token",
        ).model_dump()

    if token_req.grant_type == TOKEN_EXCHANGE_GRANT:
        if not token_req.audience:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audience_required")
        if not token_req.requested_subject:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="requested_subject_required")

        # Validate requested_subject is a valid UUID format
        try:
            uuid.UUID(token_req.requested_subject)
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="invalid_subject_format"
            )

        _enforce_audience(client, token_req.audience)
        scope = _enforce_scopes(client, token_req.scope)

        # Pull RBAC from authz DB (synced from ai-portal initially).
        # First check if user exists (get_user_roles returns empty list for non-existent users)
        await _pg.connect()
        if not await _pg.user_exists(token_req.requested_subject):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unknown_subject")
        
        roles = await _pg.get_user_roles(token_req.requested_subject)

        # Current compatibility behavior: treat role membership as full CRUD for that role.
        role_claims = [
            {"id": r["id"], "name": r["name"], "permissions": ["read", "create", "update", "delete"]}
            for r in roles
        ]

        now = int(time.time())
        exp = now + config.access_token_ttl
        claims = AccessTokenClaims(
            iss=config.issuer,
            sub=token_req.requested_subject,
            aud=token_req.audience,
            iat=now,
            nbf=now,
            exp=exp,
            jti=str(uuid.uuid4()),
            scope=scope,
            roles=role_claims,
        ).model_dump()

        access_token = await _sign_access_token(claims)

        # Audit (best-effort): actor is the subject, caller is the OAuth client.
        await _pg.insert_audit(
            actor_id=token_req.requested_subject,
            action="oauth.token.issued",
            resource_type="oauth_token",
            resource_id=None,
            details={
                "grant_type": TOKEN_EXCHANGE_GRANT,
                "client_id": token_req.client_id,
                "audience": token_req.audience,
                "scope": scope,
                "purpose": token_req.requested_purpose,
            },
            user_id=token_req.requested_subject,
            role_ids=[r["id"] for r in roles],
        )

        return OAuthTokenResponse(
            access_token=access_token,
            expires_in=config.access_token_ttl,
            scope=scope,
            issued_token_type="urn:ietf:params:oauth:token-type:access_token",
        ).model_dump()

    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported_grant_type")

