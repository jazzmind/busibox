"""
GitHub OAuth and connection management routes.

Handles GitHub OAuth flow, connection CRUD, and repository verification.
"""

import os
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from .auth import verify_admin_token
from .deployment_models import (
    GitHubConnectionRead,
    GitHubAuthUrlResponse,
    GitHubCallbackRequest,
    GitHubVerifyRepoRequest,
    GitHubVerifyRepoResponse,
)
from . import deployment_db as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/github", tags=["github"])

GITHUB_API_BASE = "https://api.github.com"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _get_github_client_id() -> str:
    val = os.getenv("GITHUB_CLIENT_ID", "")
    if not val:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not configured")
    return val


def _get_github_client_secret() -> str:
    val = os.getenv("GITHUB_CLIENT_SECRET", "")
    if not val:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_SECRET not configured")
    return val


def _get_app_url() -> str:
    """Get the Busibox Portal URL for OAuth redirects."""
    return os.getenv("BUSIBOX_PORTAL_URL", os.getenv("APP_URL", "http://localhost:3000"))


# ============================================================================
# Routes
# ============================================================================

@router.get("/auth-url", response_model=GitHubAuthUrlResponse)
async def get_auth_url(
    token_payload: dict = Depends(verify_admin_token),
):
    """Generate a GitHub OAuth authorization URL."""
    client_id = _get_github_client_id()
    app_url = _get_app_url()
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", f"{app_url}/api/admin/github/callback")
    state = str(uuid.uuid4())

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "repo read:user user:email",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"https://github.com/login/oauth/authorize?{query}"

    logger.info(f"[GitHub] Generated auth URL for user {token_payload.get('user_id')}")
    return GitHubAuthUrlResponse(auth_url=auth_url)


@router.post("/callback")
async def github_callback(
    body: GitHubCallbackRequest,
    token_payload: dict = Depends(verify_admin_token),
):
    """Exchange OAuth code for token and store connection."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    client_id = _get_github_client_id()
    client_secret = _get_github_client_secret()

    # Exchange code for token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": body.code,
            },
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to exchange GitHub code")

        data = resp.json()
        if "error" in data:
            raise HTTPException(
                status_code=400,
                detail=data.get("error_description", data["error"]),
            )

        access_token = data["access_token"]
        refresh_token = data.get("refresh_token")
        expires_in = data.get("expires_in")
        scope = data.get("scope", "")

    # Fetch user info
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user",
            headers={**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch GitHub user")
        gh_user = resp.json()

    # Compute expiry
    token_expires_at = None
    if expires_in:
        token_expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))

    scopes = [s.strip() for s in scope.split(",") if s.strip()] if scope else []

    # Store connection
    conn = await db.upsert_github_connection(
        user_id=user_id,
        access_token=access_token,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
        github_user_id=str(gh_user["id"]),
        github_username=gh_user["login"],
        scopes=scopes,
    )

    return {
        "success": True,
        "connection": {
            "id": conn["id"],
            "github_username": conn["github_username"],
            "scopes": conn["scopes"],
        },
    }


@router.get("/status")
async def get_status(
    token_payload: dict = Depends(verify_admin_token),
):
    """Get GitHub connection status for current user."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    conn = await db.get_github_connection_by_user(user_id)
    if not conn:
        return {"connected": False}

    expired = False
    if conn.get("token_expires_at"):
        try:
            expires = datetime.fromisoformat(str(conn["token_expires_at"]).replace("Z", "+00:00"))
            expired = expires < datetime.now(timezone.utc)
        except Exception:
            pass

    return {
        "connected": True,
        "expired": expired,
        "username": conn["github_username"],
        "scopes": conn["scopes"],
        "connectedAt": conn["created_at"],
    }


@router.delete("/disconnect")
async def disconnect(
    token_payload: dict = Depends(verify_admin_token),
):
    """Remove GitHub connection for current user."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    await db.delete_github_connection_by_user(user_id)
    return {"success": True}


@router.post("/reconnect")
async def reconnect(
    token_payload: dict = Depends(verify_admin_token),
):
    """Delete existing connection and return new auth URL."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    await db.delete_github_connection_by_user(user_id)

    # Generate new auth URL
    client_id = _get_github_client_id()
    app_url = _get_app_url()
    redirect_uri = os.getenv("GITHUB_REDIRECT_URI", f"{app_url}/api/admin/github/callback")
    state = str(uuid.uuid4())

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "repo read:user user:email",
        "state": state,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    auth_url = f"https://github.com/login/oauth/authorize?{query}"

    return {"success": True, "message": "Connection cleared", "authUrl": auth_url}


@router.post("/verify-repo", response_model=GitHubVerifyRepoResponse)
async def verify_repo(
    body: GitHubVerifyRepoRequest,
    token_payload: dict = Depends(verify_admin_token),
):
    """Verify user has access to a GitHub repository."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    access_token = await db.get_decrypted_github_token(user_id)
    if not access_token:
        return GitHubVerifyRepoResponse(
            verified=False, error="No GitHub connection found"
        )

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{body.github_repo_owner}/{body.github_repo_name}",
            headers={**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"},
        )

    if resp.status_code == 200:
        return GitHubVerifyRepoResponse(verified=True, repository=resp.json())
    elif resp.status_code == 404:
        return GitHubVerifyRepoResponse(
            verified=False,
            error=f"Repository not found: {body.github_repo_owner}/{body.github_repo_name}",
        )
    elif resp.status_code == 403:
        return GitHubVerifyRepoResponse(
            verified=False,
            error="Access forbidden. Token may lack 'repo' scope.",
        )
    else:
        return GitHubVerifyRepoResponse(
            verified=False,
            error=f"GitHub API error: {resp.status_code}",
        )
