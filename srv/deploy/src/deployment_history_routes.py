"""
Deployment history routes.

Create, read, and update deployment records.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import verify_admin_token
from .deployment_models import (
    DeploymentCreate,
    DeploymentUpdate,
    DeploymentRead,
)
from . import deployment_db as db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/deployments", tags=["deployments"])


@router.post("", status_code=201)
async def create_deployment(
    body: DeploymentCreate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Create a new deployment record."""
    # Verify config exists
    dc = await db.get_deployment_config(body.deployment_config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    deployment = await db.create_deployment(
        deployment_config_id=body.deployment_config_id,
        deployed_by=body.deployed_by,
        environment=body.environment.value,
        deployment_type=body.deployment_type.value,
        release_tag=body.release_tag,
        release_id=body.release_id,
        commit_sha=body.commit_sha,
        previous_deployment_id=body.previous_deployment_id,
        is_rollback=body.is_rollback,
    )
    return {"deployment": deployment}


@router.get("/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Get deployment details including status, logs, and error."""
    deployment = await db.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return {"deployment": deployment}


@router.patch("/{deployment_id}")
async def update_deployment(
    deployment_id: str,
    body: DeploymentUpdate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Update deployment status, logs, or error message."""
    existing = await db.get_deployment(deployment_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Deployment not found")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        return {"deployment": existing}

    updated = await db.update_deployment(deployment_id, **updates)
    return {"deployment": updated}


@router.post("/{deployment_id}/rollback", status_code=202)
async def rollback_deployment(
    deployment_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Create a rollback deployment from a failed deployment."""
    user_id = token_payload.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="No user_id in token")

    # Get the failed deployment
    deployment = await db.get_deployment(deployment_id)
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    # Get the previous successful deployment
    prev_id = deployment.get("previous_deployment_id")
    if not prev_id:
        # Try to find the last successful deployment for this config
        prev = await db.get_latest_deployment(
            deployment["deployment_config_id"],
            environment=deployment["environment"],
            status="COMPLETED",
        )
        if not prev:
            raise HTTPException(
                status_code=400,
                detail="No previous deployment to rollback to",
            )
        prev_id = prev["id"]

    prev_deployment = await db.get_deployment(prev_id)
    if not prev_deployment:
        raise HTTPException(status_code=400, detail="Previous deployment not found")

    # Create rollback deployment
    rollback = await db.create_deployment(
        deployment_config_id=deployment["deployment_config_id"],
        deployed_by=user_id,
        environment=deployment["environment"],
        deployment_type=prev_deployment.get("deployment_type", "RELEASE"),
        release_tag=prev_deployment.get("release_tag"),
        release_id=prev_deployment.get("release_id"),
        commit_sha=prev_deployment.get("commit_sha"),
        previous_deployment_id=deployment_id,
        is_rollback=True,
    )

    # Mark original as ROLLED_BACK
    await db.update_deployment(deployment_id, status="ROLLED_BACK")

    return {"deployment": rollback}
