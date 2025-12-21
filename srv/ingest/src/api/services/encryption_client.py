"""
Encryption Client for Authz Keystore

Provides client-side integration with the AuthZ envelope encryption keystore.
Handles encrypting file content before upload to MinIO and decrypting on download.

Usage:
    client = EncryptionClient(config)
    
    # On upload
    encrypted_content = await client.encrypt_for_upload(
        file_id="...",
        content=raw_bytes,
        role_ids=["role1", "role2"],
        user_id="user123"
    )
    
    # On download
    decrypted_content = await client.decrypt_for_download(
        file_id="...",
        encrypted_content=encrypted_bytes,
        role_ids=["role1"],
        user_id="user123"
    )
"""

import base64
import os
from typing import List, Optional

import httpx
import structlog

logger = structlog.get_logger()

# Encryption is enabled by default if AUTHZ_BASE_URL is set
ENCRYPTION_ENABLED = os.getenv("ENCRYPTION_ENABLED", "true").lower() == "true"


class EncryptionClient:
    """Client for the AuthZ envelope encryption keystore."""
    
    def __init__(self, config: dict):
        """
        Initialize the encryption client.
        
        Args:
            config: Configuration dict with authz_base_url and admin_token
        """
        self.authz_base_url = config.get("authz_base_url") or os.getenv(
            "AUTHZ_BASE_URL", "http://10.96.201.210:8010"
        )
        self.admin_token = config.get("authz_admin_token") or os.getenv("AUTHZ_ADMIN_TOKEN")
        
        # Check if encryption should be enabled
        self.enabled = ENCRYPTION_ENABLED and bool(self.authz_base_url) and bool(self.admin_token)
        
        if not self.enabled:
            logger.warning(
                "Envelope encryption disabled",
                has_base_url=bool(self.authz_base_url),
                has_admin_token=bool(self.admin_token),
                encryption_enabled_env=ENCRYPTION_ENABLED,
            )
        else:
            logger.info(
                "Envelope encryption client initialized",
                authz_base_url=self.authz_base_url,
            )
    
    def _get_headers(self) -> dict:
        """Get headers for keystore API calls."""
        return {
            "Authorization": f"Bearer {self.admin_token}",
            "Content-Type": "application/json",
        }
    
    async def ensure_kek_for_role(self, role_id: str) -> Optional[dict]:
        """
        Ensure a KEK exists for a role.
        
        Args:
            role_id: Role UUID
            
        Returns:
            KEK metadata or None if failed
        """
        if not self.enabled:
            return None
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.authz_base_url}/keystore/kek/ensure-for-role/{role_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                
                if resp.status_code == 200:
                    return resp.json()
                else:
                    logger.error(
                        "Failed to ensure KEK for role",
                        role_id=role_id,
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    return None
                    
            except Exception as e:
                logger.error(
                    "Error ensuring KEK for role",
                    role_id=role_id,
                    error=str(e),
                )
                return None
    
    async def encrypt_for_upload(
        self,
        file_id: str,
        content: bytes,
        role_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> bytes:
        """
        Encrypt file content for upload using envelope encryption.
        
        Args:
            file_id: File UUID
            content: Raw file content
            role_ids: Role IDs that should have access (for shared files)
            user_id: User ID for personal files
            
        Returns:
            Encrypted content bytes (or original if encryption disabled/failed)
        """
        if not self.enabled:
            logger.debug("Encryption disabled, returning original content", file_id=file_id)
            return content
        
        if not role_ids and not user_id:
            logger.warning(
                "No role_ids or user_id provided for encryption, returning original",
                file_id=file_id,
            )
            return content
        
        # Ensure KEKs exist for all roles
        if role_ids:
            for role_id in role_ids:
                await self.ensure_kek_for_role(role_id)
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.authz_base_url}/keystore/encrypt",
                    headers=self._get_headers(),
                    json={
                        "file_id": file_id,
                        "content": base64.b64encode(content).decode(),
                        "role_ids": role_ids or [],
                        "user_id": user_id,
                    },
                    timeout=60.0,  # Larger timeout for big files
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    encrypted_b64 = result.get("encrypted_content")
                    if encrypted_b64:
                        logger.info(
                            "Content encrypted successfully",
                            file_id=file_id,
                            original_size=len(content),
                            wrapped_dek_count=result.get("wrapped_dek_count"),
                        )
                        return base64.b64decode(encrypted_b64)
                    else:
                        logger.error(
                            "Empty encrypted content returned",
                            file_id=file_id,
                        )
                        return content
                else:
                    logger.error(
                        "Failed to encrypt content",
                        file_id=file_id,
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    # Fail open for now - return original content
                    # In strict mode, you might want to raise an exception
                    return content
                    
            except Exception as e:
                logger.error(
                    "Error encrypting content",
                    file_id=file_id,
                    error=str(e),
                    exc_info=True,
                )
                return content
    
    async def decrypt_for_download(
        self,
        file_id: str,
        encrypted_content: bytes,
        role_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> bytes:
        """
        Decrypt file content for download.
        
        Args:
            file_id: File UUID
            encrypted_content: Encrypted file content
            role_ids: User's role IDs for access
            user_id: User ID for personal files
            
        Returns:
            Decrypted content bytes (or original if decryption disabled/failed)
        """
        if not self.enabled:
            logger.debug("Encryption disabled, returning original content", file_id=file_id)
            return encrypted_content
        
        async with httpx.AsyncClient() as client:
            try:
                # Pass role and user info in headers for access check
                headers = self._get_headers()
                if role_ids:
                    headers["X-User-Role-Ids"] = ",".join(role_ids)
                if user_id:
                    headers["X-User-Id"] = user_id
                
                resp = await client.post(
                    f"{self.authz_base_url}/keystore/decrypt",
                    headers=headers,
                    json={
                        "file_id": file_id,
                        "encrypted_content": base64.b64encode(encrypted_content).decode(),
                    },
                    timeout=60.0,
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    decrypted_b64 = result.get("content")
                    if decrypted_b64:
                        decrypted = base64.b64decode(decrypted_b64)
                        logger.info(
                            "Content decrypted successfully",
                            file_id=file_id,
                            decrypted_size=len(decrypted),
                        )
                        return decrypted
                    else:
                        logger.error(
                            "Empty decrypted content returned",
                            file_id=file_id,
                        )
                        return encrypted_content
                        
                elif resp.status_code == 403:
                    logger.warning(
                        "Access denied for decryption",
                        file_id=file_id,
                        role_ids=role_ids,
                        user_id=user_id,
                    )
                    # Don't return the encrypted content - raise an error instead
                    raise PermissionError(f"Access denied to decrypt file {file_id}")
                    
                else:
                    logger.error(
                        "Failed to decrypt content",
                        file_id=file_id,
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    # This might be an unencrypted file - return as-is
                    # A smarter approach would check for encryption marker
                    return encrypted_content
                    
            except PermissionError:
                raise
            except Exception as e:
                logger.error(
                    "Error decrypting content",
                    file_id=file_id,
                    error=str(e),
                    exc_info=True,
                )
                # This might be an unencrypted file - return as-is
                return encrypted_content
    
    async def add_role_access(self, file_id: str, role_id: str) -> bool:
        """
        Add a role's access to a file.
        
        Args:
            file_id: File UUID
            role_id: Role UUID to grant access
            
        Returns:
            True if successful
        """
        if not self.enabled:
            return True
        
        # Ensure the role has a KEK first
        await self.ensure_kek_for_role(role_id)
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.post(
                    f"{self.authz_base_url}/keystore/file/{file_id}/add-role/{role_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                
                if resp.status_code == 200:
                    logger.info(
                        "Role access added to file",
                        file_id=file_id,
                        role_id=role_id,
                    )
                    return True
                else:
                    logger.error(
                        "Failed to add role access",
                        file_id=file_id,
                        role_id=role_id,
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    return False
                    
            except Exception as e:
                logger.error(
                    "Error adding role access",
                    file_id=file_id,
                    role_id=role_id,
                    error=str(e),
                )
                return False
    
    async def remove_role_access(self, file_id: str, role_id: str) -> bool:
        """
        Remove a role's access to a file.
        
        Args:
            file_id: File UUID
            role_id: Role UUID to revoke access
            
        Returns:
            True if successful
        """
        if not self.enabled:
            return True
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.delete(
                    f"{self.authz_base_url}/keystore/file/{file_id}/remove-role/{role_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                
                if resp.status_code == 200:
                    logger.info(
                        "Role access removed from file",
                        file_id=file_id,
                        role_id=role_id,
                    )
                    return True
                else:
                    logger.error(
                        "Failed to remove role access",
                        file_id=file_id,
                        role_id=role_id,
                        status_code=resp.status_code,
                    )
                    return False
                    
            except Exception as e:
                logger.error(
                    "Error removing role access",
                    file_id=file_id,
                    role_id=role_id,
                    error=str(e),
                )
                return False
    
    async def delete_file_keys(self, file_id: str) -> bool:
        """
        Delete all encryption keys for a file (when file is deleted).
        
        Args:
            file_id: File UUID
            
        Returns:
            True if successful
        """
        if not self.enabled:
            return True
        
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.delete(
                    f"{self.authz_base_url}/keystore/file/{file_id}",
                    headers=self._get_headers(),
                    timeout=30.0,
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    logger.info(
                        "File keys deleted",
                        file_id=file_id,
                        deleted_count=result.get("deleted_count"),
                    )
                    return True
                else:
                    logger.error(
                        "Failed to delete file keys",
                        file_id=file_id,
                        status_code=resp.status_code,
                    )
                    return False
                    
            except Exception as e:
                logger.error(
                    "Error deleting file keys",
                    file_id=file_id,
                    error=str(e),
                )
                return False
    
    def is_encrypted(self, content: bytes) -> bool:
        """
        Check if content appears to be encrypted.
        
        Encrypted content has a specific format:
        - First 12 bytes: nonce
        - Rest: ciphertext with GCM tag
        
        This is a heuristic check - encrypted content won't have common
        file signatures (PDF, DOCX, etc.)
        """
        if len(content) < 28:  # Minimum: 12 byte nonce + 16 byte tag
            return False
        
        # Check for common unencrypted file signatures
        signatures = [
            b'%PDF',           # PDF
            b'PK\x03\x04',     # ZIP/DOCX/XLSX
            b'\x89PNG',        # PNG
            b'\xff\xd8\xff',   # JPEG
            b'GIF8',           # GIF
            b'<!DOCTYPE',      # HTML
            b'<html',          # HTML
            b'{',              # JSON
            b'[',              # JSON array
        ]
        
        for sig in signatures:
            if content.startswith(sig):
                return False
        
        # If no known signature and content is mostly non-ASCII, likely encrypted
        return True

