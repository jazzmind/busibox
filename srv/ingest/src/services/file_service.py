"""
File service for MinIO operations.

Handles file downloads for processing.
"""

import os
import tempfile
from typing import Optional

import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger()


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
        
        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )
        
        self.temp_dir = config.get("temp_dir", "/tmp/ingest")
        os.makedirs(self.temp_dir, exist_ok=True)
    
    def download(self, storage_path: str) -> str:
        """
        Download file from MinIO to temporary location.
        
        Args:
            storage_path: S3 object path (e.g., "user-123/file-456/document.pdf")
        
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
            
            self.client.fget_object(self.bucket, storage_path, temp_path)
            
            logger.info(
                "File downloaded successfully",
                storage_path=storage_path,
                temp_path=temp_path,
                size_bytes=os.path.getsize(temp_path),
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
