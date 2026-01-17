"""
Authentication endpoints.

These endpoints manage:
- Sessions (create, validate, delete)
- Magic links (create, validate, use)
- TOTP codes (create, verify)
- Passkeys (WebAuthn - challenge, register, authenticate)

Session tokens are RS256-signed JWTs that can be:
1. Validated cryptographically (no DB lookup required for basic validation)
2. Used as subject_token for OAuth2 token exchange (RFC 8693)
3. Revoked via JTI tracking in authz_sessions table

Most endpoints require either:
- OAuth client credentials (client_id/client_secret), OR
- Admin token (for management operations)
"""

from __future__ import annotations

import time
import uuid as uuid_module
from typing import List, Optional
from uuid import UUID

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from config import Config
from oauth.client_auth import verify_client_secret
from oauth.keys import load_private_key

router = APIRouter()
config = Config()

# PostgresService instances - will be set by main.py
# pg is production, pg_test is test database (optional)
pg = None
pg_test = None

# Header name for test mode
TEST_MODE_HEADER = "X-Test-Mode"


def set_pg_service(pg_service, pg_test_service=None):
    """Set the shared PostgresService instances."""
    global pg, pg_test
    pg = pg_service
    pg_test = pg_test_service


def _get_pg(request: Request):
    """Get the appropriate PostgresService based on request headers.
    
    If X-Test-Mode: true header is present and test mode is enabled,
    returns the test database service. Otherwise returns production.
    """
    if pg_test and config.test_mode_enabled:
        test_mode = request.headers.get(TEST_MODE_HEADER, "").lower() == "true"
        if test_mode:
            return pg_test
    return pg


# ============================================================================
# Session JWT Signing
# ============================================================================


async def _sign_session_jwt(user_id: str, email: str, session_id: str, db=None) -> tuple[str, int]:
    """
    Sign a session JWT for a user.
    
    Returns (jwt_string, expires_at_timestamp)
    
    If db is provided, uses that PostgresService instance. Otherwise uses production.
    """
    db = db or pg
    await db.connect()
    row = await db.get_active_signing_key()
    if not row:
        raise RuntimeError("no active signing key configured")
    
    kid = row["kid"]
    alg = row["alg"]
    private_pem = row["private_key_pem"]
    key_obj = load_private_key(private_pem, config.key_encryption_passphrase)
    
    now = int(time.time())
    exp = now + config.session_token_ttl
    
    claims = {
        "iss": config.issuer,
        "sub": str(user_id),
        "aud": "ai-portal",  # Session tokens are for ai-portal
        "exp": exp,
        "iat": now,
        "nbf": now,
        "jti": str(session_id),  # Use session ID as JTI for revocation tracking
        "typ": "session",
        "email": email,
    }
    
    token = jwt.encode(claims, key_obj, algorithm=alg, headers={"kid": kid, "typ": "JWT"})
    return token, exp


# ============================================================================
# Request/Response Models
# ============================================================================


class SessionCreate(BaseModel):
    user_id: str
    token: str
    expires_at: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    token: str
    expires_at: str
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: str


class MagicLinkCreate(BaseModel):
    user_id: str
    email: str
    expires_in_seconds: int = Field(default=900, ge=60, le=86400)  # 1 min to 24 hours


class MagicLinkResponse(BaseModel):
    magic_link_id: str
    token: str
    email: str
    expires_at: str
    created_at: str


class TotpCreate(BaseModel):
    user_id: str
    email: str
    expires_in_seconds: int = Field(default=300, ge=60, le=900)  # 1-15 minutes (matches magic link)


class TotpVerify(BaseModel):
    email: str
    code: str


class PasskeyChallengeCreate(BaseModel):
    type: str = Field(..., pattern="^(registration|authentication)$")
    user_id: Optional[str] = None


class PasskeyRegister(BaseModel):
    user_id: str
    credential_id: str
    credential_public_key: str
    counter: int = 0
    device_type: str
    backed_up: bool = False
    transports: List[str] = Field(default_factory=list)
    aaguid: Optional[str] = None
    name: str


class PasskeyAuthenticate(BaseModel):
    credential_id: str
    new_counter: int


# ============================================================================
# Authentication Helpers
# ============================================================================


async def _require_client_auth(request: Request) -> None:
    """
    Require OAuth client credentials or admin token.
    Used for endpoints that sync from ai-portal or other trusted services.
    """
    # Try admin token first
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:]
        if config.admin_token and token == config.admin_token:
            return

    # Try OAuth client credentials in body
    try:
        body = await request.json()
        client_id = body.get("client_id")
        client_secret = body.get("client_secret")

        if client_id and client_secret:
            db = _get_pg(request)
            await db.connect()
            client = await db.get_oauth_client(client_id)
            if client and client.get("is_active"):
                if verify_client_secret(client_secret, client["client_secret_hash"]):
                    return
    except Exception:
        pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized: valid admin token or OAuth client credentials required",
    )


def _format_datetime(dt) -> str:
    """Format datetime for response."""
    if dt is None:
        return ""
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def _format_datetime_from_timestamp(ts: int) -> str:
    """Format a Unix timestamp as ISO datetime string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ============================================================================
# Session Endpoints
# ============================================================================


@router.post("/auth/sessions")
async def create_session(request: Request):
    """
    Create or sync a session.
    
    Used by ai-portal to sync better-auth sessions to authz.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - user_id: string (required)
    - token: string (session token, required)
    - expires_at: ISO timestamp (required)
    - ip_address: string (optional)
    - user_agent: string (optional)
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        session_data = SessionCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Validate user_id
    try:
        UUID(session_data.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()

    # Check user exists
    user = await db.get_user(session_data.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    session = await db.create_session(
        user_id=session_data.user_id,
        token=session_data.token,
        expires_at=session_data.expires_at,
        ip_address=session_data.ip_address,
        user_agent=session_data.user_agent,
    )

    return {
        "session_id": session["session_id"],
        "user_id": session["user_id"],
        "token": session["token"],
        "expires_at": _format_datetime(session["expires_at"]),
        "ip_address": session.get("ip_address"),
        "user_agent": session.get("user_agent"),
        "created_at": _format_datetime(session["created_at"]),
    }


@router.get("/auth/sessions/{token}")
async def validate_session(request: Request, token: str):
    """
    Validate a session by token.
    
    The token can be either:
    1. A JWT session token (returned by magic link, TOTP, etc.) - extracts jti to lookup session
    2. A database session token (legacy) - looks up directly
    
    Returns the session and user info if valid, 404 if not found/expired.
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()
    
    session = None
    
    # Try to decode as JWT first
    try:
        # Decode without verification to check if it's a JWT and extract jti
        unverified = jwt.decode(token, options={"verify_signature": False})
        jti = unverified.get("jti")
        
        if jti:
            # Look up session by session_id (jti)
            session = await db.get_session_by_id(jti)
    except (jwt.DecodeError, jwt.InvalidTokenError):
        # Not a JWT, try as database token
        pass
    
    # If JWT lookup didn't work, try as database token
    if not session:
        session = await db.get_session(token)

    if not session:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or expired")

    return {
        "session_id": session["session_id"],
        "user_id": session["user_id"],
        "token": session.get("token", token),  # Return original token if JWT
        "expires_at": _format_datetime(session["expires_at"]),
        "ip_address": session.get("ip_address"),
        "user_agent": session.get("user_agent"),
        "created_at": _format_datetime(session["created_at"]),
        "user": session.get("user"),
    }


@router.delete("/auth/sessions/{token}")
async def delete_session(request: Request, token: str):
    """
    Delete a session by token (logout).
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()
    deleted = await db.delete_session(token)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return {"status": "ok", "deleted": True}


@router.delete("/auth/sessions/user/{user_id}")
async def delete_user_sessions(request: Request, user_id: str):
    """
    Delete all sessions for a user (logout everywhere).
    """
    await _require_client_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()
    count = await db.delete_user_sessions(user_id)

    return {"status": "ok", "deleted_count": count}


# ============================================================================
# Magic Link Endpoints
# ============================================================================


@router.post("/auth/magic-links")
async def create_magic_link(request: Request):
    """
    Create a magic link for passwordless login.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - user_id: string (required)
    - email: string (required)
    - expires_in_seconds: int (default: 900 = 15 minutes)
    
    Returns the token to be included in the magic link URL.
    ai-portal is responsible for sending the email.
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        link_data = MagicLinkCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Validate user_id
    try:
        UUID(link_data.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()

    # Check user exists
    user = await db.get_user(link_data.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    link = await db.create_magic_link(
        user_id=link_data.user_id,
        email=link_data.email,
        expires_in_seconds=link_data.expires_in_seconds,
    )

    return {
        "magic_link_id": link["magic_link_id"],
        "token": link["token"],
        "email": link["email"],
        "expires_at": _format_datetime(link["expires_at"]),
        "created_at": _format_datetime(link["created_at"]),
    }


@router.get("/auth/magic-links/{token}")
async def validate_magic_link(request: Request, token: str):
    """
    Validate a magic link (without consuming it).
    
    Returns the magic link info if valid.
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()
    link = await db.get_magic_link(token)

    if not link:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Magic link not found")

    # Check if expired or used
    from datetime import datetime
    if link.get("used_at"):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Magic link already used")
    
    expires_at = link.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="Magic link expired")

    return {
        "magic_link_id": link["magic_link_id"],
        "user_id": link["user_id"],
        "email": link["email"],
        "expires_at": _format_datetime(link["expires_at"]),
        "created_at": _format_datetime(link["created_at"]),
    }


@router.post("/auth/magic-links/{token}/use")
async def use_magic_link(request: Request, token: str):
    """
    Use (consume) a magic link.
    
    - Marks the link as used
    - Activates the user if pending
    - Sets email_verified_at
    - Creates a new session
    
    Returns the user and a signed session JWT.
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()
    result = await db.use_magic_link(token)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Magic link not found, expired, or already used",
        )

    user = result["user"]
    session = result["session"]
    
    # Sign a session JWT
    session_jwt, expires_at = await _sign_session_jwt(
        user_id=user["user_id"],
        email=user["email"],
        session_id=session["session_id"],
        db=db,
    )

    return {
        "user": {
            "user_id": user["user_id"],
            "email": user["email"],
            "status": user["status"],
            "email_verified_at": _format_datetime(user.get("email_verified_at")),
            "roles": [
                {"id": r["id"], "name": r["name"]}
                for r in user.get("roles", [])
            ],
        },
        "session": {
            "token": session_jwt,
            "expires_at": _format_datetime_from_timestamp(expires_at),
            "token_type": "Bearer",
        },
    }


# ============================================================================
# TOTP Endpoints
# ============================================================================


@router.post("/auth/totp")
async def create_totp_code(request: Request):
    """
    Create a TOTP code for multi-device login.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - user_id: string (required)
    - email: string (required)
    - expires_in_seconds: int (default: 300 = 5 minutes)
    
    Returns the plaintext code (to be sent via email by ai-portal).
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        totp_data = TotpCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # Validate user_id
    try:
        UUID(totp_data.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()

    # Check user exists
    user = await db.get_user(totp_data.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.create_totp_code(
        user_id=totp_data.user_id,
        email=totp_data.email,
        expires_in_seconds=totp_data.expires_in_seconds,
    )

    return {
        "code": result["code"],  # Plaintext - send via email
        "expires_at": result["expires_at"],
    }


@router.post("/auth/totp/verify")
async def verify_totp_code(request: Request):
    """
    Verify a TOTP code.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - email: string (required)
    - code: string (6-digit code, required)
    
    If valid:
    - Marks the code as used
    - Creates a new session
    - Returns user and signed session JWT
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        verify_data = TotpVerify.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    result = await db.verify_totp_code(verify_data.email, verify_data.code)

    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired code",
        )

    user = result["user"]
    session = result["session"]
    
    # Sign a session JWT
    session_jwt, expires_at = await _sign_session_jwt(
        user_id=user["user_id"],
        email=user["email"],
        session_id=session["session_id"],
        db=db,
    )

    return {
        "user": {
            "user_id": user["user_id"],
            "email": user["email"],
            "status": user["status"],
            "roles": [
                {"id": r["id"], "name": r["name"]}
                for r in user.get("roles", [])
            ],
        },
        "session": {
            "token": session_jwt,
            "expires_at": _format_datetime_from_timestamp(expires_at),
            "token_type": "Bearer",
        },
    }


# ============================================================================
# Passkey (WebAuthn) Endpoints
# ============================================================================


@router.post("/auth/passkeys/challenge")
async def create_passkey_challenge(request: Request):
    """
    Create a passkey challenge for WebAuthn registration or authentication.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - type: "registration" or "authentication" (required)
    - user_id: string (optional, required for registration)
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        challenge_data = PasskeyChallengeCreate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # For registration, user_id is required
    if challenge_data.type == "registration" and not challenge_data.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="user_id is required for registration",
        )

    if challenge_data.user_id:
        try:
            UUID(challenge_data.user_id)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()

    # For registration, verify user exists
    if challenge_data.user_id:
        user = await db.get_user(challenge_data.user_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    result = await db.create_passkey_challenge(
        challenge_type=challenge_data.type,
        user_id=challenge_data.user_id,
    )

    return {
        "challenge": result["challenge"],
        "expires_at": _format_datetime(result["expires_at"]),
    }


@router.get("/auth/passkeys/challenge/{challenge}")
async def get_passkey_challenge(request: Request, challenge: str):
    """
    Get a passkey challenge (to verify it's still valid).
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()
    result = await db.get_passkey_challenge(challenge)

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found or expired")

    return {
        "challenge": result["challenge"],
        "type": result["type"],
        "user_id": result.get("user_id"),
        "expires_at": _format_datetime(result["expires_at"]),
    }


@router.post("/auth/passkeys")
async def register_passkey(request: Request):
    """
    Register a new passkey for a user.
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - user_id: string (required)
    - credential_id: string (Base64URL, required)
    - credential_public_key: string (Base64URL, required)
    - counter: int (default: 0)
    - device_type: string (required, e.g., "singleDevice" or "multiDevice")
    - backed_up: bool (default: false)
    - transports: string[] (e.g., ["internal", "hybrid"])
    - aaguid: string (optional)
    - name: string (required, user-friendly device name)
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        passkey_data = PasskeyRegister.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    try:
        UUID(passkey_data.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()

    # Check user exists
    user = await db.get_user(passkey_data.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Check if credential already exists
    existing = await db.get_passkey_by_credential_id(passkey_data.credential_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Passkey with this credential ID already exists",
        )

    result = await db.register_passkey(
        user_id=passkey_data.user_id,
        credential_id=passkey_data.credential_id,
        credential_public_key=passkey_data.credential_public_key,
        counter=passkey_data.counter,
        device_type=passkey_data.device_type,
        backed_up=passkey_data.backed_up,
        transports=passkey_data.transports,
        aaguid=passkey_data.aaguid,
        name=passkey_data.name,
    )

    return {
        "passkey_id": result["passkey_id"],
        "name": result["name"],
        "device_type": result["device_type"],
        "created_at": _format_datetime(result["created_at"]),
    }


@router.get("/auth/passkeys/user/{user_id}")
async def list_user_passkeys(request: Request, user_id: str):
    """
    List all passkeys for a user.
    """
    await _require_client_auth(request)

    try:
        UUID(user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user_id format") from e

    db = _get_pg(request)
    await db.connect()
    passkeys = await db.list_user_passkeys(user_id)

    return {
        "passkeys": [
            {
                "passkey_id": p["passkey_id"],
                "credential_id": p["credential_id"],
                "name": p["name"],
                "device_type": p["device_type"],
                "backed_up": p["backed_up"],
                "transports": p["transports"],
                "last_used_at": _format_datetime(p.get("last_used_at")),
                "created_at": _format_datetime(p["created_at"]),
            }
            for p in passkeys
        ],
    }


@router.delete("/auth/passkeys/{passkey_id}")
async def delete_passkey(request: Request, passkey_id: str):
    """
    Delete a passkey.
    """
    await _require_client_auth(request)

    try:
        UUID(passkey_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey_id format") from e

    db = _get_pg(request)
    await db.connect()
    deleted = await db.delete_passkey(passkey_id)

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found")

    return {"status": "ok", "deleted": True}


@router.post("/auth/passkeys/authenticate")
async def authenticate_with_passkey(request: Request):
    """
    Authenticate using a passkey.
    
    The caller (ai-portal) is responsible for:
    1. Getting the challenge from /auth/passkeys/challenge
    2. Calling navigator.credentials.get() in the browser
    3. Verifying the signature against the stored public key
    4. Calling this endpoint with the credential_id and new_counter
    
    This endpoint:
    1. Verifies the counter is greater than stored (replay protection)
    2. Updates the counter
    3. Creates a session
    4. Returns user and signed session JWT
    
    Body:
    - client_id, client_secret (OAuth client auth)
    - credential_id: string (required)
    - new_counter: int (required)
    """
    await _require_client_auth(request)

    body = await request.json()
    try:
        auth_data = PasskeyAuthenticate.model_validate(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    db = _get_pg(request)
    await db.connect()
    result = await db.authenticate_with_passkey(
        credential_id=auth_data.credential_id,
        new_counter=auth_data.new_counter,
    )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid passkey or counter replay detected",
        )

    user = result["user"]
    session = result["session"]
    
    # Sign a session JWT
    session_jwt, expires_at = await _sign_session_jwt(
        user_id=user["user_id"],
        email=user["email"],
        session_id=session["session_id"],
        db=db,
    )

    return {
        "user": {
            "user_id": user["user_id"],
            "email": user["email"],
            "status": user["status"],
            "roles": [
                {"id": r["id"], "name": r["name"]}
                for r in user.get("roles", [])
            ],
        },
        "session": {
            "token": session_jwt,
            "expires_at": _format_datetime_from_timestamp(expires_at),
            "token_type": "Bearer",
        },
    }


# ============================================================================
# Cleanup Endpoints (for scheduled jobs)
# ============================================================================


@router.post("/auth/cleanup")
async def cleanup_expired(request: Request):
    """
    Clean up expired sessions, magic links, TOTP codes, and passkey challenges.
    
    This should be called periodically by a scheduled job.
    
    Requires admin authentication.
    """
    await _require_client_auth(request)

    db = _get_pg(request)
    await db.connect()

    sessions = await db.cleanup_expired_sessions()
    magic_links = await db.cleanup_expired_magic_links()
    totp_codes = await db.cleanup_expired_totp_codes()
    challenges = await db.cleanup_expired_passkey_challenges()

    return {
        "status": "ok",
        "cleaned": {
            "sessions": sessions,
            "magic_links": magic_links,
            "totp_codes": totp_codes,
            "passkey_challenges": challenges,
        },
    }

