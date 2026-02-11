"""
Deployment configuration CRUD routes.

Manages per-app deployment configurations (GitHub repo, port, build/start commands, etc.).
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import verify_admin_token
from .deployment_models import (
    DeploymentConfigCreate,
    DeploymentConfigUpdate,
    DeploymentConfigRead,
    NextPortResponse,
)
from . import deployment_db as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployment-configs", tags=["deployment-configs"])

# Reserved ports that should never be assigned
RESERVED_PORTS = {3000, 3001, 3002, 8000, 8001, 8002, 8010, 8011}


# ============================================================================
# Routes
# ============================================================================

@router.get("")
async def list_configs(
    token_payload: dict = Depends(verify_admin_token),
):
    """List all deployment configs with enriched relations."""
    configs = await db.list_deployment_configs()

    # Enrich each config with GitHub username and latest deployment
    enriched = []
    for dc in configs:
        dc = await db.enrich_config_with_relations(dc)
        enriched.append(dc)

    return {"configs": enriched}


@router.post("", status_code=201)
async def create_config(
    body: DeploymentConfigCreate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Create a new deployment configuration."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    # Verify user has a GitHub connection
    conn = await db.get_github_connection_by_user(user_id)
    if not conn:
        raise HTTPException(status_code=400, detail="No GitHub connection found. Connect GitHub first.")

    # Verify repo access
    access_token = await db.get_decrypted_github_token(user_id)
    if not access_token:
        raise HTTPException(status_code=400, detail="GitHub token not available")

    import httpx
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"https://api.github.com/repos/{body.github_repo_owner}/{body.github_repo_name}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=403,
                detail=f"Cannot access repository {body.github_repo_owner}/{body.github_repo_name}",
            )

    try:
        config = await db.create_deployment_config(
            app_id=body.app_id,
            github_connection_id=conn["id"],
            github_repo_owner=body.github_repo_owner,
            github_repo_name=body.github_repo_name,
            github_branch=body.github_branch,
            deploy_path=body.deploy_path,
            port=body.port,
            health_endpoint=body.health_endpoint,
            build_command=body.build_command,
            start_command=body.start_command,
            auto_deploy_enabled=body.auto_deploy_enabled,
            staging_enabled=body.staging_enabled,
            staging_port=body.staging_port,
            staging_path=body.staging_path,
        )
    except RuntimeError as e:
        if "duplicate key" in str(e).lower() or "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Deployment config already exists for this app")
        raise HTTPException(status_code=500, detail=str(e))

    config["github_username"] = conn["github_username"]
    return {"config": config}


@router.get("/helpers/next-port", response_model=NextPortResponse)
async def next_port(
    token_payload: dict = Depends(verify_admin_token),
):
    """Get the next available port for a new deployment."""
    used_ports = set(await db.get_used_ports())
    used_ports.update(RESERVED_PORTS)

    port = 3003
    while port in used_ports and port < 4000:
        port += 1

    if port >= 4000:
        raise HTTPException(status_code=500, detail="No available ports")

    return NextPortResponse(port=port)


@router.get("/{config_id}")
async def get_config(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Get a single deployment config with relations."""
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    dc = await db.enrich_config_with_relations(dc)
    dc["secrets"] = await db.list_secrets(config_id)
    dc["deployments"] = await db.list_deployments_for_config(config_id, limit=10)

    return {"config": dc}


@router.patch("/{config_id}")
async def update_config(
    config_id: str,
    body: DeploymentConfigUpdate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Update a deployment configuration."""
    existing = await db.get_deployment_config(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"config": existing}

    updated = await db.update_deployment_config(config_id, **updates)
    return {"config": updated}


@router.delete("/{config_id}")
async def delete_config(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Delete a deployment configuration (cascades to deployments, secrets, releases)."""
    existing = await db.get_deployment_config(config_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    await db.delete_deployment_config(config_id)
    return {"success": True}
