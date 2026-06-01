"""
User Integration OAuth Routes

Provides endpoints for users to connect their Google and Microsoft accounts
for calendar and email read-only access (used by Chief of Staff agents).

OAuth Flow:
  1. GET  /integrations                   - list connected integrations for the current user
  2. GET  /integrations/{provider}/connect - redirect user to provider's OAuth consent screen
  3. GET  /integrations/{provider}/callback - OAuth callback; stores tokens encrypted in DB
  4. DELETE /integrations/{provider}       - disconnect / remove a provider integration

Internal (agent-api usage):
  5. GET  /internal/integrations/{provider}/token - return a valid (refreshed) access token
                                                    for the requesting user's integration

Supported providers: "google", "microsoft"
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from config import Config
from oauth.jwt_auth import require_auth_or_self_service, require_auth, AuthContext
from services.postgres import PostgresService

logger = logging.getLogger(__name__)

router = APIRouter()
config = Config()

pg: PostgresService = None
pg_test: PostgresService = None

TEST_MODE_HEADER = "X-Test-Mode"

# In-memory PKCE/state store (process-local; fine for single-process authz)
_oauth_state_store: Dict[str, Dict[str, Any]] = {}

NONCE_SIZE = 12  # AES-GCM nonce bytes


def set_pg_service(service: PostgresService, test_service: Optional[PostgresService] = None):
    global pg, pg_test
    pg = service
    pg_test = test_service


def _get_pg(request: Request) -> PostgresService:
    if pg_test and config.test_mode_enabled:
        if request.headers.get(TEST_MODE_HEADER, "").lower() == "true":
            return pg_test
    return pg


# ---------------------------------------------------------------------------
# Helpers: token encryption using AUTHZ_MASTER_KEY (same key as keystore)
# ---------------------------------------------------------------------------

def _derive_token_key() -> bytes:
    """Derive a 256-bit AES key from AUTHZ_MASTER_KEY for token encryption."""
    import hashlib
    master = (config.master_key or "").encode()
    if not master:
        raise ValueError("AUTHZ_MASTER_KEY must be set to store OAuth tokens")
    return hashlib.sha256(b"integration-token-v1:" + master).digest()


def _encrypt_token(plaintext: str) -> bytes:
    """Encrypt a token string; returns nonce + ciphertext."""
    key = _derive_token_key()
    nonce = secrets.token_bytes(NONCE_SIZE)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode(), None)
    return nonce + ct


def _decrypt_token(data: bytes) -> str:
    """Decrypt a token stored by _encrypt_token."""
    key = _derive_token_key()
    nonce, ct = data[:NONCE_SIZE], data[NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


# ---------------------------------------------------------------------------
# OAuth provider configurations
# ---------------------------------------------------------------------------

PROVIDERS = {
    "google": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revoke_url": "https://oauth2.googleapis.com/revoke",
        "scopes": [
            "openid",
            "email",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/gmail.readonly",
        ],
    },
    "microsoft": {
        "auth_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        "revoke_url": None,
        "scopes": [
            "openid",
            "email",
            "offline_access",
            "Calendars.Read",
            "Mail.Read",
        ],
    },
}


def _provider_cfg(provider: str) -> Dict[str, Any]:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    cfg = dict(PROVIDERS[provider])

    if provider == "google":
        client_id = config.google_integration_client_id
        client_secret = config.google_integration_client_secret
        if not client_id:
            raise HTTPException(status_code=503, detail="Google integration not configured")
        cfg["client_id"] = client_id
        cfg["client_secret"] = client_secret

    elif provider == "microsoft":
        client_id = config.microsoft_integration_client_id
        client_secret = config.microsoft_integration_client_secret
        tenant = config.microsoft_integration_tenant_id or "common"
        if not client_id:
            raise HTTPException(status_code=503, detail="Microsoft integration not configured")
        cfg["client_id"] = client_id
        cfg["client_secret"] = client_secret
        cfg["auth_url"] = cfg["auth_url"].format(tenant=tenant)
        cfg["token_url"] = cfg["token_url"].format(tenant=tenant)

    cfg["redirect_uri"] = f"{config.authz_base_url}/integrations/{provider}/callback"
    return cfg


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class IntegrationInfo(BaseModel):
    provider: str
    connected: bool
    email: Optional[str] = None
    scopes: List[str] = []
    connected_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Route: list integrations
# ---------------------------------------------------------------------------

@router.get("/integrations", response_model=List[IntegrationInfo])
async def list_integrations(request: Request):
    """Return which providers the current user has connected."""
    auth: AuthContext = await require_auth_or_self_service(request)
    db = _get_pg(request)

    rows = await db.fetch(
        "SELECT provider, email, scopes, updated_at FROM authz_user_integrations WHERE user_id = $1",
        auth.actor_id,
    )
    connected = {r["provider"]: r for r in rows}

    result = []
    for provider in PROVIDERS:
        if provider == "google" and not config.google_integration_enabled:
            continue
        if provider == "microsoft" and not config.microsoft_integration_enabled:
            continue

        row = connected.get(provider)
        result.append(IntegrationInfo(
            provider=provider,
            connected=bool(row),
            email=row["email"] if row else None,
            scopes=list(row["scopes"]) if row else [],
            connected_at=row["updated_at"].isoformat() if row else None,
        ))

    return result


# ---------------------------------------------------------------------------
# Route: initiate OAuth flow
# Two variants:
#   POST /integrations/{provider}/initiate - returns JSON { redirect_url }
#     Used by portal server-side to get the URL and redirect browser.
#   GET  /integrations/{provider}/connect  - direct browser redirect (needs
#     Authorization header, useful for testing / direct links).
# ---------------------------------------------------------------------------

def _build_oauth_url(provider: str, user_id: str) -> str:
    """Create a provider OAuth URL and register state. Returns the full URL."""
    cfg = _provider_cfg(provider)
    state = secrets.token_urlsafe(32)
    _oauth_state_store[state] = {
        "user_id": user_id,
        "provider": provider,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
    }

    params: Dict[str, str] = {
        "client_id": cfg["client_id"],
        "redirect_uri": cfg["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(cfg["scopes"]),
        "state": state,
    }
    if provider == "google":
        params["access_type"] = "offline"
        params["prompt"] = "consent"

    return cfg["auth_url"] + "?" + urllib.parse.urlencode(params)


@router.post("/integrations/{provider}/initiate")
async def initiate_integration(provider: str, request: Request):
    """
    Server-to-server: validate session JWT and return the OAuth redirect URL.
    The portal API route calls this and then issues a browser redirect.
    """
    auth: AuthContext = await require_auth_or_self_service(request)
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    url = _build_oauth_url(provider, auth.actor_id)
    return {"redirect_url": url}


@router.get("/integrations/{provider}/connect")
async def connect_integration(provider: str, request: Request):
    """Browser-facing: redirect to provider OAuth consent screen."""
    auth: AuthContext = await require_auth_or_self_service(request)
    url = _build_oauth_url(provider, auth.actor_id)
    return RedirectResponse(url)


# ---------------------------------------------------------------------------
# Route: OAuth callback
# ---------------------------------------------------------------------------

@router.get("/integrations/{provider}/callback")
async def oauth_callback(provider: str, request: Request):
    """Handle the OAuth callback; exchange code for tokens and store encrypted."""
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return RedirectResponse(
            f"{config.portal_base_url}/account?integration_error={urllib.parse.quote(error)}"
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    state_data = _oauth_state_store.pop(state, None)
    if not state_data or state_data["provider"] != provider:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    now = datetime.now(timezone.utc)
    if state_data["expires_at"] < now:
        raise HTTPException(status_code=400, detail="OAuth state expired")

    user_id = state_data["user_id"]
    cfg = _provider_cfg(provider)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "code": code,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri": cfg["redirect_uri"],
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Token exchange failed for {provider}: {resp.text}")
        return RedirectResponse(
            f"{config.portal_base_url}/account?integration_error=token_exchange_failed"
        )

    token_data = resp.json()
    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_in = token_data.get("expires_in", 3600)
    token_expiry = now + timedelta(seconds=expires_in)

    granted_scopes = token_data.get("scope", "").split()

    # Fetch user email from provider
    email = await _fetch_provider_email(provider, access_token)

    # Encrypt tokens
    enc_access = _encrypt_token(access_token) if access_token else None
    enc_refresh = _encrypt_token(refresh_token) if refresh_token else None

    db = _get_pg(request)
    await db.execute(
        """
        INSERT INTO authz_user_integrations
            (user_id, provider, access_token_encrypted, refresh_token_encrypted,
             token_expiry, scopes, email, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, now())
        ON CONFLICT (user_id, provider) DO UPDATE SET
            access_token_encrypted = EXCLUDED.access_token_encrypted,
            refresh_token_encrypted = EXCLUDED.refresh_token_encrypted,
            token_expiry = EXCLUDED.token_expiry,
            scopes = EXCLUDED.scopes,
            email = EXCLUDED.email,
            updated_at = now()
        """,
        user_id,
        provider,
        enc_access,
        enc_refresh,
        token_expiry,
        granted_scopes or cfg["scopes"],
        email,
    )

    logger.info(f"[integrations] {provider} connected for user {user_id} ({email})")
    return RedirectResponse(f"{config.portal_base_url}/account?integration_connected={provider}")


async def _fetch_provider_email(provider: str, access_token: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient() as client:
            if provider == "google":
                r = await client.get(
                    "https://www.googleapis.com/oauth2/v3/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if r.status_code == 200:
                    return r.json().get("email")
            elif provider == "microsoft":
                r = await client.get(
                    "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if r.status_code == 200:
                    data = r.json()
                    return data.get("mail") or data.get("userPrincipalName")
    except Exception as e:
        logger.warning(f"Could not fetch email from {provider}: {e}")
    return None


# ---------------------------------------------------------------------------
# Route: disconnect
# ---------------------------------------------------------------------------

@router.delete("/integrations/{provider}")
async def disconnect_integration(provider: str, request: Request):
    """Remove the user's connected integration."""
    auth: AuthContext = await require_auth_or_self_service(request)
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

    db = _get_pg(request)
    await db.execute(
        "DELETE FROM authz_user_integrations WHERE user_id = $1 AND provider = $2",
        auth.actor_id,
        provider,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Internal route: return a valid access token for agent-api tools
# ---------------------------------------------------------------------------

@router.get("/internal/integrations/{provider}/token")
async def get_integration_token(provider: str, request: Request):
    """
    Internal endpoint for agent-api tools to obtain a valid OAuth access token
    for the requesting user's integration.

    Requires a valid internal access token (authz-api audience).
    The user_id is derived from the 'sub' claim of the bearer token.
    """
    # Accept internal access tokens (agent-api calls this with an exchanged token)
    auth: AuthContext = await require_auth(request, required_scopes=[])
    db = _get_pg(request)

    row = await db.fetchrow(
        """
        SELECT access_token_encrypted, refresh_token_encrypted, token_expiry, scopes, email
        FROM authz_user_integrations
        WHERE user_id = $1 AND provider = $2
        """,
        auth.actor_id,
        provider,
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"No {provider} integration found for this user. "
                   "Please connect your account in the portal settings.",
        )

    # If token is still valid (>5 min margin), return it
    now = datetime.now(timezone.utc)
    expiry = row["token_expiry"]
    if expiry and expiry > now + timedelta(minutes=5):
        access_token = _decrypt_token(bytes(row["access_token_encrypted"]))
        return {
            "access_token": access_token,
            "provider": provider,
            "email": row["email"],
            "scopes": list(row["scopes"]),
        }

    # Refresh the token
    if not row["refresh_token_encrypted"]:
        raise HTTPException(
            status_code=401,
            detail=f"{provider} access token expired and no refresh token available. "
                   "Please reconnect your account.",
        )

    refresh_token = _decrypt_token(bytes(row["refresh_token_encrypted"]))
    cfg = _provider_cfg(provider)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            cfg["token_url"],
            data={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        logger.error(f"Token refresh failed for {provider}: {resp.text}")
        raise HTTPException(
            status_code=401,
            detail=f"Failed to refresh {provider} token. Please reconnect your account.",
        )

    token_data = resp.json()
    new_access_token = token_data["access_token"]
    new_refresh_token = token_data.get("refresh_token", refresh_token)
    expires_in = token_data.get("expires_in", 3600)
    new_expiry = now + timedelta(seconds=expires_in)

    enc_access = _encrypt_token(new_access_token)
    enc_refresh = _encrypt_token(new_refresh_token)

    await db.execute(
        """
        UPDATE authz_user_integrations SET
            access_token_encrypted = $1,
            refresh_token_encrypted = $2,
            token_expiry = $3,
            updated_at = now()
        WHERE user_id = $4 AND provider = $5
        """,
        enc_access,
        enc_refresh,
        new_expiry,
        auth.actor_id,
        provider,
    )

    return {
        "access_token": new_access_token,
        "provider": provider,
        "email": row["email"],
        "scopes": list(row["scopes"]),
    }
