"""
App secrets management routes.

Encrypted environment variables per deployment config.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from .auth import verify_admin_token
from .deployment_models import AppSecretCreate, AppSecretRead
from . import deployment_db as db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["secrets"])


@router.get("/api/v1/deployment-configs/{config_id}/secrets")
async def list_secrets(
    config_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """List secrets for a deployment config (values are NOT returned)."""
    # Verify config exists
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    secrets = await db.list_secrets(config_id)
    return {"secrets": secrets}


@router.post("/api/v1/deployment-configs/{config_id}/secrets", status_code=201)
async def upsert_secret(
    config_id: str,
    body: AppSecretCreate,
    token_payload: dict = Depends(verify_admin_token),
):
    """Create or update an app secret (value is encrypted server-side)."""
    dc = await db.get_deployment_config(config_id)
    if not dc:
        raise HTTPException(status_code=404, detail="Deployment config not found")

    secret = await db.upsert_secret(
        config_id=config_id,
        key=body.key,
        value=body.value,
        secret_type=body.type.value,
        description=body.description,
    )
    return {"secret": secret}


@router.delete("/api/v1/secrets/{secret_id}")
async def delete_secret(
    secret_id: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Delete an app secret by ID."""
    await db.delete_secret(secret_id)
    return {"success": True}


@router.get("/api/v1/deployment-configs/{config_id}/secrets/{key}/value")
async def get_secret_value(
    config_id: str,
    key: str,
    token_payload: dict = Depends(verify_admin_token),
):
    """Get a decrypted secret value. For internal deployment use only."""
    value = await db.get_secret_decrypted(config_id, key)
    if value is None:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"key": key, "value": value}
