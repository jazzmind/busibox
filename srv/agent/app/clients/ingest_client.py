"""HTTP client for Busibox Ingest API."""
from typing import Any, Dict, List, Optional
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from app.config.settings import get_settings

settings = get_settings()


class UploadResponse(BaseModel):
    """Response from document upload."""
    file_id: str
    filename: str
    size: int
    content_hash: str
    status: str
    duplicate_detected: bool = False
    reused_vectors: bool = False
    message: Optional[str] = None


class ProcessingStatus(BaseModel):
    """Document processing status."""
    file_id: str
    status: str  # pending, parsing, chunking, embedding, indexing, completed, failed
    progress: float
    stage: Optional[str] = None
    error: Optional[str] = None
    chunks_processed: Optional[int] = None
    total_chunks: Optional[int] = None


class DocumentMetadata(BaseModel):
    """Document metadata."""
    file_id: str
    filename: str
    size: int
    content_type: str
    content_hash: str
    status: str
    uploaded_at: str
    processed_at: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    chunk_count: Optional[int] = None


class IngestClient:
    """
    HTTP client for Busibox Ingest API.
    
    Provides document upload, processing status tracking, and metadata retrieval.
    """

    def __init__(self, auth_token: Optional[str] = None):
        """
        Initialize ingest client.
        
        Args:
            auth_token: Bearer token for authentication (optional)
        """
        self.base_url = str(settings.ingest_api_url)
        self.auth_token = auth_token
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=60.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with auth."""
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    async def upload_document(
        self,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
        processing_config: Optional[Dict[str, Any]] = None,
        visibility: str = "personal",
        role_ids: Optional[List[str]] = None,
        force_reprocess: bool = False,
    ) -> UploadResponse:
        """
        Upload a document for processing.
        
        Args:
            file_path: Path to file to upload
            metadata: Optional metadata dictionary
            processing_config: Optional processing configuration
            visibility: "personal" or "shared" (default: "personal")
            role_ids: List of role UUIDs for shared visibility
            force_reprocess: Force reprocessing even if duplicate (default: False)
            
        Returns:
            UploadResponse with file_id and status
            
        Raises:
            httpx.HTTPError: If upload fails
            FileNotFoundError: If file doesn't exist
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # Prepare form data
        files = {"file": (path.name, open(path, "rb"), "application/octet-stream")}
        data = {
            "visibility": visibility,
            "force_reprocess": str(force_reprocess).lower(),
        }
        
        if metadata:
            import json
            data["metadata"] = json.dumps(metadata)
        
        if processing_config:
            import json
            data["processing_config"] = json.dumps(processing_config)
        
        if role_ids:
            data["role_ids"] = ",".join(role_ids)

        if self._client:
            response = await self._client.post(
                f"{self.base_url}/upload",
                files=files,
                data=data,
                headers=self._get_headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/upload",
                    files=files,
                    data=data,
                    headers=self._get_headers(),
                )
        
        response.raise_for_status()
        return UploadResponse(**response.json())

    async def get_status(self, file_id: str) -> ProcessingStatus:
        """
        Get processing status for a document.
        
        Args:
            file_id: File ID to check
            
        Returns:
            ProcessingStatus with current status and progress
            
        Raises:
            httpx.HTTPError: If request fails
        """
        if self._client:
            response = await self._client.get(
                f"{self.base_url}/status/{file_id}",
                headers=self._get_headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/status/{file_id}",
                    headers=self._get_headers(),
                )
        
        response.raise_for_status()
        return ProcessingStatus(**response.json())

    async def get_document(self, file_id: str) -> DocumentMetadata:
        """
        Get document metadata.
        
        Args:
            file_id: File ID to retrieve
            
        Returns:
            DocumentMetadata with file information
            
        Raises:
            httpx.HTTPError: If request fails
        """
        if self._client:
            response = await self._client.get(
                f"{self.base_url}/files/{file_id}",
                headers=self._get_headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.base_url}/files/{file_id}",
                    headers=self._get_headers(),
                )
        
        response.raise_for_status()
        return DocumentMetadata(**response.json())

    async def delete_document(self, file_id: str) -> Dict[str, Any]:
        """
        Delete a document and its embeddings.
        
        Args:
            file_id: File ID to delete
            
        Returns:
            Deletion confirmation
            
        Raises:
            httpx.HTTPError: If request fails
        """
        if self._client:
            response = await self._client.delete(
                f"{self.base_url}/files/{file_id}",
                headers=self._get_headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.delete(
                    f"{self.base_url}/files/{file_id}",
                    headers=self._get_headers(),
                )
        
        response.raise_for_status()
        return response.json()

    async def health(self) -> Dict[str, Any]:
        """
        Check ingest API health.
        
        Returns:
            Health status dictionary
        """
        if self._client:
            response = await self._client.get(f"{self.base_url}/health")
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/health")
        
        response.raise_for_status()
        return response.json()





