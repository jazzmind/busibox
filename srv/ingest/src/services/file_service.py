"""File service for MinIO operations (stub)."""


class FileService:
    """Service for file operations with MinIO."""
    
    def __init__(self, config: dict):
        """Initialize file service with configuration."""
        self.config = config
        # TODO: Initialize MinIO client
    
    def download(self, bucket: str, object_key: str) -> bytes:
        """
        Download file from MinIO.
        
        Args:
            bucket: MinIO bucket name
            object_key: Object key
            
        Returns:
            File contents as bytes
        """
        # TODO: Implement MinIO download
        raise NotImplementedError("MinIO download not implemented")

