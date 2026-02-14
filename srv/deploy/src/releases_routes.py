"""
GitHub releases and app database routes.

Manages cached GitHub release info and app database provisioning records.
"""

import logging
from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException

from .auth import verify_admin_token
from .deployment_models import (
    GitHubReleaseRead,
    ReleaseSyncResponse,
    AppDatabaseCreate,
    AppDatabaseRead,
)
from . import deployment_db as db
from .database import create_database, provision_database, generate_password
from .config import config as app_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["releases"])

GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@router.get("/api/v1/deployment-configs/{config_id}/releases")
async def list_releases(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """List cached releases for a deployment config, annotated with deployment status."""
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    releases = await db.list_releases(config_id)

    # Find the currently deployed release
    latest_deploy = await db.get_latest_deployment(config_id)
    deployed_release_id = latest_deploy.get("release_id") if latest_deploy else None

    for r in releases:
        r["is_currently_deployed"] = (r["release_id"] == deployed_release_id) if deployed_release_id else False

    return {"releases": releases}


@router.post("/api/v1/deployment-configs/{config_id}/releases/sync")
async def sync_releases(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Sync releases from GitHub for a deployment config."""
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    # Get GitHub token
    user_id = token_payload.get("user_id", "")
    conn = await db.get_github_connection_by_user(user_id)
    if not conn:
        # Try getting the token from the config's connection
        from . import authz_crypto
        from .database import execute_sql
        # Fetch connection directly (need user_id for the file_id)
        conn_sql = f"SELECT user_id, access_token FROM github_connections WHERE id = '{dc['github_connection_id']}'"
        stdout, stderr, code = await execute_sql(conn_sql, 'data')
        if code != 0 or not stdout.strip():
            raise HTTPException(status_code=400, detail="GitHub connection not available")
        parts = stdout.strip().split('|')
        if len(parts) < 2:
            raise HTTPException(status_code=400, detail="GitHub connection not available")
        conn_user_id, enc_token = parts[0], parts[1]
        access_token = await authz_crypto.decrypt(enc_token, f"github:{conn_user_id}:access")
    else:
        access_token = await db.get_decrypted_github_token(user_id)
        if not access_token:
            raise HTTPException(status_code=400, detail="GitHub token not available")

    # Fetch releases from GitHub
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{dc['github_repo_owner']}/{dc['github_repo_name']}/releases?per_page=50",
            headers={**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=502, detail="Failed to fetch releases from GitHub")
        gh_releases = resp.json()

    # Upsert each release
    for gh_rel in gh_releases:
        published_at = gh_rel.get("published_at")
        if published_at:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))

        await db.upsert_release(
            config_id=config_id,
            release_id=str(gh_rel["id"]),
            tag_name=gh_rel["tag_name"],
            release_name=gh_rel.get("name"),
            body=gh_rel.get("body"),
            commit_sha=gh_rel.get("target_commitish"),
            published_at=published_at,
            is_prerelease=gh_rel.get("prerelease", False),
            is_draft=gh_rel.get("draft", False),
            tarball_url=gh_rel.get("tarball_url"),
        )

    # Return all releases
    releases = await db.list_releases(config_id)
    return {
        "success": True,
        "count": len(releases),
        "releases": releases,
    }


# ============================================================================
# App Database provisioning
# ============================================================================

@router.get("/api/v1/deployment-configs/{config_id}/database")
async def get_app_database(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Get app database info for a deployment config."""
    app_db = await db.get_app_database(config_id)
    if not app_db:
        return {"database": None}
    return {"database": app_db}


@router.post("/api/v1/deployment-configs/{config_id}/database", status_code=201)
async def provision_app_database(
    config_id: str,
    body: AppDatabaseCreate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Provision a new database for an app and store the record."""
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    # Check if already provisioned
    existing = await db.get_app_database(config_id)
    if existing:
        raise HTTPException(status_code=409, detail="Database already provisioned for this config")

    # Create the actual database
    result = await create_database(body.database_name, body.database_user, body.password)
    if not result.success:
        raise HTTPException(status_code=500, detail=f"Database provisioning failed: {result.error}")

    # Store the record
    app_db = await db.create_app_database(
        config_id=config_id,
        database_name=body.database_name,
        database_user=body.database_user,
        password=body.password,
        host=app_config.postgres_host,
        port=app_config.postgres_port,
    )

    # Also create DATABASE_URL secret
    database_url = f"postgresql://{body.database_user}:{body.password}@{app_config.postgres_host}:{app_config.postgres_port}/{body.database_name}"
    await db.upsert_secret(
        config_id=config_id,
        key="DATABASE_URL",
        value=database_url,
        secret_type="DATABASE_URL",
        description="Auto-generated database connection string",
    )

    return {"database": app_db}


@router.delete("/api/v1/deployment-configs/{config_id}/database")
async def delete_app_database(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Delete an app's provisioned database."""
    from .database import delete_database as drop_db
    app_db = await db.get_app_database(config_id)
    if not app_db:
        return {"success": True}

    await drop_db(app_db["database_name"])
    await db.delete_app_database(config_id)
    return {"success": True}
