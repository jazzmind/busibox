"""
AuthZ Keystore Encryption Client

Provides encrypt/decrypt operations via the AuthZ keystore API.

Uses a system-level KEK owned by deploy-api so that all deployment
secrets (GitHub tokens, app secrets, DB passwords) are encrypted
with envelope encryption managed by AuthZ.

Authentication: The caller must provide a user JWT (from the admin who
initiated the operation). This is exchanged for an authz-api scoped
token via Zero Trust token exchange — no service credentials.
"""

import os
import uuid
import base64
import logging
from typing import Optional

import httpx

from busibox_common.auth import exchange_token_zero_trust

# Namespace UUID for generating deterministic file_ids from string identifiers.
_DEPLOY_NAMESPACE = uuid.UUID("d3a10b0e-0000-4000-8000-d3a10b0e0000")

logger = logging.getLogger(__name__)

_authz_base_url: Optional[str] = None
_system_kek_ensured: bool = False
_system_owner_id: str = "deploy-api"


def _file_id_to_uuid(file_id: str) -> str:
    """Convert a string file identifier to a deterministic UUID."""
    return str(uuid.uuid5(_DEPLOY_NAMESPACE, file_id))


def _get_authz_url() -> str:
    global _authz_base_url
    if _authz_base_url is None:
        _authz_base_url = os.getenv("AUTHZ_URL", "http://localhost:8010")
    return _authz_base_url


async def _get_keystore_token(caller_token: str, user_id: str) -> str:
    """Exchange the caller's JWT for an authz-api scoped token."""
    result = await exchange_token_zero_trust(
        subject_token=caller_token,
        target_audience="authz-api",
        user_id=user_id,
    )
    if not result:
        raise RuntimeError("Token exchange for authz-api failed — cannot access keystore")
    return result.access_token


async def ensure_system_kek(caller_token: str, user_id: str) -> None:
    """Ensure a system-level KEK exists for deploy-api."""
    global _system_kek_ensured
    if _system_kek_ensured:
        return

    token = await _get_keystore_token(caller_token, user_id)
    url = f"{_get_authz_url()}/keystore/kek"
    headers = {"Authorization": f"Bearer {token}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                url,
                json={"owner_type": "system", "owner_id": _system_owner_id},
                headers=headers,
            )
            if resp.status_code in (200, 409):
                pass
            else:
                logger.warning("Unexpected response creating system KEK: %s %s", resp.status_code, resp.text)
        except Exception as e:
            logger.warning("Failed to ensure system KEK (will retry): %s", e)
            return

    _system_kek_ensured = True


async def encrypt(plaintext: str, file_id: str, caller_token: str, user_id: str) -> str:
    """Encrypt a string via the AuthZ keystore.

    Args:
        plaintext: The string to encrypt
        file_id: Logical identifier (e.g. "secret:{config_id}:{key}")
        caller_token: The authenticated user's JWT
        user_id: User ID of the caller
    """
    await ensure_system_kek(caller_token, user_id)

    token = await _get_keystore_token(caller_token, user_id)
    url = f"{_get_authz_url()}/keystore/encrypt"
    headers = {"Authorization": f"Bearer {token}"}

    uuid_file_id = _file_id_to_uuid(file_id)
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
            raise RuntimeError(f"AuthZ encrypt failed ({resp.status_code}): {resp.text}")

        return resp.json()["encrypted_content"]


async def decrypt(encrypted_data: str, file_id: str, caller_token: str, user_id: str) -> str:
    """Decrypt a string via the AuthZ keystore.

    Args:
        encrypted_data: Base64-encoded encrypted content from AuthZ
        file_id: Same logical identifier used during encryption
        caller_token: The authenticated user's JWT
        user_id: User ID of the caller
    """
    await ensure_system_kek(caller_token, user_id)

    token = await _get_keystore_token(caller_token, user_id)
    url = f"{_get_authz_url()}/keystore/decrypt"
    headers = {
        "Authorization": f"Bearer {token}",
        "X-System-Owner-Id": _system_owner_id,
    }

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
            raise RuntimeError(f"AuthZ decrypt failed ({resp.status_code}): {resp.text}")

        return base64.b64decode(resp.json()["content"]).decode("utf-8")
