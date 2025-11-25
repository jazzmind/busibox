"""
MinIO S3 client wrapper for file storage.

Handles file uploads with chunked streaming and SHA-256 hash calculation.
"""

import hashlib
from typing import BinaryIO, Optional

import structlog
from minio import Minio
from minio.error import S3Error

logger = structlog.get_logger()


class MinIOService:
    """Service for MinIO S3 operations."""
    
    def __init__(self, config: dict):
        """Initialize MinIO client."""
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
    
    async def check_health(self):
        """Check MinIO connectivity by listing buckets."""
        import asyncio
        try:
            # Run sync operation in thread pool
            loop = asyncio.get_event_loop()
            buckets = await loop.run_in_executor(None, self.client.list_buckets)
            return True
        except Exception as e:
            logger.error("MinIO health check failed", error=str(e))
            raise
    
    async def upload_file_stream(
        self,
        file_obj: BinaryIO,
        object_path: str,
        content_hash: Optional[str] = None,
    ) -> str:
        """
        Upload file with streaming and optional hash calculation.
        
        Args:
            file_obj: File-like object to upload
            object_path: S3 object path (e.g., "user-123/file-456/document.pdf")
            content_hash: Optional SHA-256 hash (if None, calculates during upload)
        
        Returns:
            Content hash (SHA-256 hex digest)
        """
        hasher = hashlib.sha256()
        
        # Calculate hash while uploading
        # Note: MinIO Python SDK doesn't support streaming upload directly
        # We'll read in chunks and calculate hash
        chunk_size = 8 * 1024 * 1024  # 8MB chunks
        data_parts = []
        
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
            data_parts.append(chunk)
        
        calculated_hash = hasher.hexdigest()
        
        # Use provided hash or calculated hash
        final_hash = content_hash if content_hash else calculated_hash
        
        # Upload to MinIO
        try:
            # Ensure bucket exists (run sync operations in thread pool)
            import asyncio
            loop = asyncio.get_event_loop()
            bucket_exists = await loop.run_in_executor(
                None,
                lambda: self.client.bucket_exists(self.bucket)
            )
            if not bucket_exists:
                await loop.run_in_executor(
                    None,
                    lambda: self.client.make_bucket(self.bucket)
                )
            
            # Upload file (run sync operation in thread pool)
            from io import BytesIO
            file_data = b"".join(data_parts)
            file_obj_reset = BytesIO(file_data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.put_object(
                    self.bucket,
                    object_path,
                    file_obj_reset,
                    length=len(file_data),
                )
            )
            
            logger.info(
                "File uploaded to MinIO",
                bucket=self.bucket,
                object_path=object_path,
                size_bytes=len(file_data),
                content_hash=final_hash,
            )
            
            return final_hash
        except S3Error as e:
            logger.error(
                "MinIO upload failed",
                bucket=self.bucket,
                object_path=object_path,
                error=str(e),
            )
            raise
    
    async def delete_file(self, object_path: str):
        """Delete file from MinIO."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.remove_object(self.bucket, object_path)
            )
            logger.info(
                "File deleted from MinIO",
                bucket=self.bucket,
                object_path=object_path,
            )
        except S3Error as e:
            logger.error(
                "MinIO delete failed",
                bucket=self.bucket,
                object_path=object_path,
                error=str(e),
            )
            raise
    
    async def file_exists(self, object_path: str) -> bool:
        """Check if file exists in MinIO."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.stat_object(self.bucket, object_path)
            )
            return True
        except S3Error:
            return False
    
    def get_file_content(self, object_path: str) -> str:
        """
        Get file content as string (for text files like markdown).
        
        Args:
            object_path: S3 object path
            
        Returns:
            File content as string
        """
        try:
            response = self.client.get_object(self.bucket, object_path)
            content = response.read().decode('utf-8')
            response.close()
            response.release_conn()
            return content
        except S3Error as e:
            logger.error("Failed to get file from MinIO", error=str(e), object_path=object_path)
            raise
    
    def get_file_bytes(self, object_path: str) -> bytes:
        """
        Get file content as bytes (for binary files like images).
        
        Args:
            object_path: S3 object path
            
        Returns:
            File content as bytes
        """
        try:
            response = self.client.get_object(self.bucket, object_path)
            content = response.read()
            response.close()
            response.release_conn()
            return content
        except S3Error as e:
            logger.error("Failed to get file from MinIO", error=str(e), object_path=object_path)
            raise
    
    async def upload_text(self, content: str, object_path: str) -> None:
        """
        Upload text content to MinIO.
        
        Args:
            content: Text content to upload
            object_path: S3 object path
        """
        import asyncio
        from io import BytesIO
        
        try:
            data = content.encode('utf-8')
            data_stream = BytesIO(data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.put_object(
                    self.bucket,
                    object_path,
                    data_stream,
                    length=len(data),
                    content_type='text/markdown'
                )
            )
            
            logger.info("Text uploaded to MinIO", object_path=object_path, size=len(data))
        except S3Error as e:
            logger.error("Failed to upload text to MinIO", error=str(e), object_path=object_path)
            raise
    
    async def upload_bytes(self, data: bytes, object_path: str, content_type: str = 'application/octet-stream') -> None:
        """
        Upload binary data to MinIO.
        
        Args:
            data: Binary data to upload
            object_path: S3 object path
            content_type: MIME type of the content
        """
        import asyncio
        from io import BytesIO
        
        try:
            data_stream = BytesIO(data)
            
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self.client.put_object(
                    self.bucket,
                    object_path,
                    data_stream,
                    length=len(data),
                    content_type=content_type
                )
            )
            
            logger.info("Bytes uploaded to MinIO", object_path=object_path, size=len(data))
        except S3Error as e:
            logger.error("Failed to upload bytes to MinIO", error=str(e), object_path=object_path)
            raise

