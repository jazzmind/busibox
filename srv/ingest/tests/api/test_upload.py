"""
Unit tests for upload endpoint.

Uses JWT auth fixtures from conftest.py for proper authentication.

For full end-to-end testing with real MinIO, PostgreSQL, and Redis:
See: tests/integration/test_full_pipeline.py

IMPORTANT: 500 errors are NEVER acceptable responses. They indicate
the API is not properly catching and handling errors. All expected
error conditions should return 4xx status codes.
"""
from io import BytesIO
import pytest
from fastapi import status


@pytest.fixture
def sample_file():
    """Create sample file for upload."""
    content = b"Test document content"
    file = BytesIO(content)
    file.name = "test.txt"
    file.content_type = "text/plain"
    file.size = len(content)
    return file


class TestUploadValidation:
    """Test upload request validation."""
    
    @pytest.mark.asyncio
    async def test_upload_invalid_mime_type(self, async_client, sample_file):
        """Test upload with unsupported MIME type is rejected."""
        response = await async_client.post(
            "/upload",
            files={"file": ("test.exe", sample_file, "application/x-msdownload")},
        )
        
        # Should reject unsupported file types with 4xx, never 500
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY], \
            f"Expected 400 or 422, got {response.status_code}: {response.text}"
        if response.status_code == status.HTTP_400_BAD_REQUEST:
            data = response.json()
            assert "error" in data

    @pytest.mark.asyncio
    async def test_upload_missing_filename(self, async_client):
        """Test upload without filename is rejected."""
        response = await async_client.post(
            "/upload",
            files={"file": ("", BytesIO(b"content"), "text/plain")},
        )
        
        # FastAPI returns 422 for validation errors, but our code may check filename and return 400
        # Never 500 - missing filename is a client error
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY], \
            f"Expected 400 or 422, got {response.status_code}: {response.text}"


class TestUploadControlFlow:
    """Test upload endpoint control flow."""
    
    @pytest.mark.asyncio
    async def test_upload_success(self, async_client, sample_file):
        """Test successful file upload."""
        response = await async_client.post(
            "/upload",
            files={"file": ("test.txt", sample_file, "text/plain")},
        )
        
        # Upload should succeed. If it fails, that's a real failure.
        # 500 is never acceptable - indicates uncaught exception
        assert response.status_code == status.HTTP_200_OK, \
            f"Upload failed with {response.status_code}: {response.text}"
        
        data = response.json()
        assert "fileId" in data, f"Response missing fileId: {data}"

    @pytest.mark.asyncio
    async def test_upload_pdf(self, async_client, sample_pdf_simple):
        """Test PDF file upload."""
        if not sample_pdf_simple:
            pytest.skip("Sample PDF not available")
        
        with open(sample_pdf_simple, "rb") as f:
            response = await async_client.post(
                "/upload",
                files={"file": ("document.pdf", f, "application/pdf")},
            )
        
        # PDF upload should succeed
        assert response.status_code == status.HTTP_200_OK, \
            f"PDF upload failed with {response.status_code}: {response.text}"


class TestUploadMetadata:
    """Test upload with metadata."""
    
    @pytest.mark.asyncio
    async def test_upload_with_valid_metadata(self, async_client, sample_file):
        """Test upload with valid metadata in form field."""
        import json
        
        response = await async_client.post(
            "/upload",
            files={"file": ("test.txt", sample_file, "text/plain")},
            data={"metadata": json.dumps({"source": "test", "tags": ["unit-test"]})},
        )
        
        # Should succeed with valid metadata
        assert response.status_code == status.HTTP_200_OK, \
            f"Upload with metadata failed with {response.status_code}: {response.text}"

    @pytest.mark.asyncio
    async def test_upload_invalid_metadata(self, async_client, sample_file):
        """Test upload with invalid metadata format."""
        response = await async_client.post(
            "/upload",
            files={"file": ("test.txt", sample_file, "text/plain")},
            data={"metadata": "not-valid-json{"},
        )
        
        # Invalid JSON should return 400 (bad request), not 500
        # OR the API may gracefully ignore invalid metadata and return 200
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST], \
            f"Expected 200 or 400 for invalid metadata, got {response.status_code}: {response.text}"
