"""
File service for MinIO operations.

Handles file downloads for processing, including decryption of encrypted files.
"""

import base64
import os
import tempfile
from typing import Optional, List

import httpx
import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger()

# File signatures for detecting encrypted vs unencrypted content
FILE_SIGNATURES = [
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


def is_encrypted(content: bytes) -> bool:
    """
    Check if content appears to be encrypted.
    
    Encrypted content has a specific format:
    - First 12 bytes: nonce
    - Rest: ciphertext with GCM tag
    
    This is a heuristic check - encrypted content won't have common
    file signatures (PDF, DOCX, etc.) and will be mostly non-ASCII bytes.
    
    Plain text files (markdown, txt, code) are mostly ASCII and should
    NOT be flagged as encrypted.
    """
    if len(content) < 28:  # Minimum: 12 byte nonce + 16 byte tag
        return False
    
    # Check for known file signatures
    for sig in FILE_SIGNATURES:
        if content.startswith(sig):
            return False
    
    # Check if content is mostly ASCII/UTF-8 text (plain text is not encrypted)
    # Sample the first 1KB for efficiency
    sample = content[:1024]
    try:
        # Try to decode as UTF-8 - if it decodes cleanly, it's likely text
        decoded = sample.decode('utf-8')
        # Count printable ASCII characters (including common text chars)
        printable_count = sum(1 for c in decoded if c.isprintable() or c in '\n\r\t')
        # If more than 90% is printable text, it's not encrypted
        if printable_count / len(decoded) > 0.9:
            return False
    except UnicodeDecodeError:
        # If UTF-8 decoding fails, check for high-entropy binary data
        pass
    
    # Count non-ASCII bytes in sample - encrypted data is mostly non-ASCII
    non_ascii_count = sum(1 for b in sample if b > 127)
    
    # If less than 30% is non-ASCII, probably not encrypted
    # (Real AES-GCM ciphertext has ~50% non-ASCII due to random bytes)
    if len(sample) > 0 and non_ascii_count / len(sample) < 0.3:
        return False
    
    # Content has no known signature AND is mostly non-ASCII - likely encrypted
    return True


class FileService:
    """Service for file operations with MinIO."""
    
    def __init__(self, config: dict):
        """Initialize file service with configuration."""
        self.config = config
        self.endpoint = config.get("minio_endpoint", "10.96.200.205:9000")
        self.access_key = config.get("minio_access_key", "minioadmin")
        self.secret_key = config.get("minio_secret_key", "minioadmin")
        self.secure = config.get("minio_secure", False)
        self.bucket = config.get("minio_bucket", "documents")
        
        # Encryption settings
        self.authz_base_url = config.get("authz_base_url") or os.getenv(
            "AUTHZ_BASE_URL", "http://10.96.201.210:8010"
        )
        self.admin_token = config.get("authz_admin_token") or os.getenv("AUTHZ_ADMIN_TOKEN")
        encryption_enabled = os.getenv("ENCRYPTION_ENABLED", "true").lower() == "true"
        self.encryption_enabled = encryption_enabled and bool(self.authz_base_url) and bool(self.admin_token)
        
        if self.encryption_enabled:
            logger.info(
                "FileService encryption enabled",
                authz_base_url=self.authz_base_url,
            )
        else:
            logger.warning(
                "FileService encryption disabled",
                has_base_url=bool(self.authz_base_url),
                has_admin_token=bool(self.admin_token),
                encryption_enabled_env=encryption_enabled,
            )
        
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )
        
        self.temp_dir = config.get("temp_dir", "/tmp/ingest")
        os.makedirs(self.temp_dir, exist_ok=True)
    
    def _decrypt_content_sync(
        self, 
        file_id: str, 
        encrypted_content: bytes,
        role_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> bytes:
        """
        Decrypt file content synchronously.
        
        Args:
            file_id: File UUID
            encrypted_content: Encrypted file content
            role_ids: User's role IDs for access
            user_id: User ID for personal files
            
        Returns:
            Decrypted content bytes (or original if decryption disabled/failed)
        """
        if not self.encryption_enabled:
            return encrypted_content
        
        try:
            headers = {
                "Authorization": f"Bearer {self.admin_token}",
                "Content-Type": "application/json",
            }
            if role_ids:
                headers["X-User-Role-Ids"] = ",".join(role_ids)
            if user_id:
                headers["X-User-Id"] = user_id
            
            # Use sync httpx client
            with httpx.Client(timeout=60.0) as client:
                resp = client.post(
                    f"{self.authz_base_url}/keystore/decrypt",
                    headers=headers,
                    json={
                        "file_id": file_id,
                        "encrypted_content": base64.b64encode(encrypted_content).decode(),
                    },
                )
            
            if resp.status_code == 200:
                result = resp.json()
                decrypted_b64 = result.get("content")
                if decrypted_b64:
                    decrypted = base64.b64decode(decrypted_b64)
                    logger.info(
                        "Content decrypted successfully (sync)",
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
                raise PermissionError(f"Access denied to decrypt file {file_id}")
                
            else:
                logger.error(
                    "Failed to decrypt content",
                    file_id=file_id,
                    status_code=resp.status_code,
                    response=resp.text,
                )
                # This might be an unencrypted file - return as-is
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
    
    def download(
        self, 
        storage_path: str,
        file_id: Optional[str] = None,
        role_ids: Optional[List[str]] = None,
        user_id: Optional[str] = None,
    ) -> str:
        """
        Download file from MinIO to temporary location, decrypting if necessary.
        
        Args:
            storage_path: S3 object path (e.g., "user-123/file-456/document.pdf")
            file_id: File UUID (required for decryption)
            role_ids: User's role IDs for decryption access
            user_id: User ID for personal file decryption
        
        Returns:
            Path to downloaded temporary file
        """
        try:
            # Create temporary file
            temp_file = tempfile.NamedTemporaryFile(
                dir=self.temp_dir,
                delete=False,
                suffix=os.path.splitext(storage_path)[1],
            )
            temp_path = temp_file.name
            temp_file.close()
            
            # Download from MinIO
            logger.info(
                "Downloading file from MinIO",
                bucket=self.bucket,
                storage_path=storage_path,
                temp_path=temp_path,
            )
            
            # Get the file object
            response = self.client.get_object(self.bucket, storage_path)
            content = response.read()
            response.close()
            response.release_conn()
            
            original_size = len(content)
            
            # Check if content is encrypted and we have decryption info
            if file_id and is_encrypted(content):
                logger.info(
                    "Content appears encrypted, attempting decryption",
                    file_id=file_id,
                    content_size=len(content),
                )
                content = self._decrypt_content_sync(
                    file_id=file_id,
                    encrypted_content=content,
                    role_ids=role_ids,
                    user_id=user_id,
                )
                logger.info(
                    "Decryption complete",
                    file_id=file_id,
                    original_size=original_size,
                    decrypted_size=len(content),
                )
            
            # Write content to temp file
            with open(temp_path, 'wb') as f:
                f.write(content)
            
            logger.info(
                "File downloaded successfully",
                storage_path=storage_path,
                temp_path=temp_path,
                size_bytes=len(content),
                was_encrypted=original_size != len(content),
            )
            
            return temp_path
        
        except S3Error as e:
            logger.error(
                "Failed to download file from MinIO",
                bucket=self.bucket,
                storage_path=storage_path,
                error=str(e),
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error downloading file",
                storage_path=storage_path,
                error=str(e),
                exc_info=True,
            )
            raise
    
    def cleanup_temp_file(self, file_path: str):
        """Clean up temporary file."""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug("Temporary file cleaned up", file_path=file_path)
        except Exception as e:
            logger.warning("Failed to cleanup temp file", file_path=file_path, error=str(e))
