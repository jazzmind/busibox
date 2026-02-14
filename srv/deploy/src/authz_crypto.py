"""
AuthZ Keystore Encryption Client

Provides encrypt/decrypt operations via the AuthZ keystore API,
replacing the standalone crypto_utils.py approach.

Uses a system-level KEK owned by deploy-api so that all deployment
secrets (GitHub tokens, app secrets, DB passwords) are encrypted
with envelope encryption managed by AuthZ.

The deploy-api authenticates to AuthZ via its OAuth client credentials
(AUTHZ_URL already configured in config.py).
"""

import os
import uuid
import base64
import logging
from typing import Optional

import httpx

# Namespace UUID for generating deterministic file_ids from string identifiers.
# This converts "github:{user_id}:access" -> a stable UUID via uuid5.
_DEPLOY_NAMESPACE = uuid.UUID("d3a10b0e-0000-4000-8000-d3a10b0e0000")

logger = logging.getLogger(__name__)

# Module-level state
_authz_base_url: Optional[str] = None
_system_kek_ensured: bool = False
_system_owner_id: str = "deploy-api"


def _file_id_to_uuid(file_id: str) -> str:
    """Convert a string file identifier to a deterministic UUID.
    
    The authz keystore uses UUID file_ids internally. We generate
    deterministic UUIDs from our string identifiers so the same
    file_id always maps to the same UUID.
    
    Examples:
        "github:user123:access" -> "a1b2c3d4-..."
        "secret:config456:DB_PASSWORD" -> "e5f6g7h8-..."
    """
    return str(uuid.uuid5(_DEPLOY_NAMESPACE, file_id))


def _get_authz_url() -> str:
    """Get the AuthZ base URL from config."""
    global _authz_base_url
    if _authz_base_url is None:
        _authz_base_url = os.getenv("AUTHZ_URL", "http://localhost:8010")
    return _authz_base_url


def _get_auth_headers() -> dict:
    """Get authorization headers for AuthZ API calls.
    
    Uses the deploy-api's bootstrap token or falls back to
    constructing a bearer token from available credentials.
    """
    # Use bootstrap token if available (deploy-api always has this)
    bootstrap_token = os.getenv("DEPLOY_BOOTSTRAP_TOKEN", "")
    if bootstrap_token:
        return {"Authorization": f"Bearer {bootstrap_token}"}
    
    # Fallback: no auth (for local dev where authz doesn't require it)
    return {}


async def ensure_system_kek() -> None:
    """Ensure a system-level KEK exists for deploy-api.
    
    Called once on first encrypt/decrypt. Creates the KEK if it
    doesn't exist (idempotent via 409 Conflict handling).
    """
    global _system_kek_ensured
    if _system_kek_ensured:
        return
    
    url = f"{_get_authz_url()}/keystore/kek"
    headers = _get_auth_headers()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                url,
                json={
                    "owner_type": "system",
                    "owner_id": _system_owner_id,
                },
                headers=headers,
            )
            if resp.status_code == 200:
                logger.info("System KEK created for deploy-api")
            elif resp.status_code == 409:
                # Already exists - this is fine
                logger.debug("System KEK already exists for deploy-api")
            else:
                logger.warning(
                    f"Unexpected response creating system KEK: {resp.status_code} {resp.text}"
                )
        except Exception as e:
            logger.warning(f"Failed to ensure system KEK (will retry on next call): {e}")
            return  # Don't mark as ensured so we retry
    
    _system_kek_ensured = True


async def encrypt(plaintext: str, file_id: str) -> str:
    """Encrypt a string via the AuthZ keystore.
    
    Args:
        plaintext: The string to encrypt
        file_id: Logical identifier for the encrypted data
                 (e.g., "github:{user_id}:access", "secret:{config_id}:{key}")
    
    Returns:
        Base64-encoded encrypted content from AuthZ
    """
    await ensure_system_kek()
    
    url = f"{_get_authz_url()}/keystore/encrypt"
    headers = _get_auth_headers()
    
    # Convert string file_id to deterministic UUID
    uuid_file_id = _file_id_to_uuid(file_id)
    
    # Encode plaintext as base64 for the API
    content_b64 = base64.b64encode(plaintext.encode("utf-8")).decode("ascii")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={
                "file_id": uuid_file_id,
                "content": content_b64,
                "role_ids": [],
                "system_owner_id": _system_owner_id,
            },
            headers=headers,
        )
        
        if resp.status_code != 200:
            raise RuntimeError(
                f"AuthZ encrypt failed ({resp.status_code}): {resp.text}"
            )
        
        data = resp.json()
        return data["encrypted_content"]


async def decrypt(encrypted_data: str, file_id: str) -> str:
    """Decrypt a string via the AuthZ keystore.
    
    Args:
        encrypted_data: Base64-encoded encrypted content from AuthZ
        file_id: Same logical identifier used during encryption
    
    Returns:
        The decrypted plaintext string
    """
    await ensure_system_kek()
    
    url = f"{_get_authz_url()}/keystore/decrypt"
    headers = _get_auth_headers()
    # Pass system_owner_id so authz knows to use the system KEK
    headers["X-System-Owner-Id"] = _system_owner_id
    
    # Convert string file_id to deterministic UUID
    uuid_file_id = _file_id_to_uuid(file_id)
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url,
            json={
                "file_id": uuid_file_id,
                "encrypted_content": encrypted_data,
            },
            headers=headers,
        )
        
        if resp.status_code != 200:
            raise RuntimeError(
                f"AuthZ decrypt failed ({resp.status_code}): {resp.text}"
            )
        
        data = resp.json()
        # Decode base64 content back to string
        return base64.b64decode(data["content"]).decode("utf-8")
