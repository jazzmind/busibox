"""
Tests for Markdown API Endpoints

Uses JWT auth fixtures from conftest.py.

IMPORTANT: 500 errors are NEVER acceptable responses. They indicate
the API is not properly catching and handling errors. All expected
error conditions should return 4xx status codes.
"""

import pytest
import uuid
from fastapi import status


class TestMarkdownEndpoint:
    """Test suite for GET /files/{fileId}/markdown endpoint"""

    @pytest.mark.asyncio
    async def test_get_markdown_not_found(self, async_client):
        """Test markdown retrieval for non-existent file returns 404."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/markdown")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND, \
            f"Expected 404, got {response.status_code}: {response.text}"
        assert "error" in response.json()

    @pytest.mark.asyncio
    async def test_get_markdown_invalid_uuid(self, async_client):
        """Test markdown retrieval with invalid UUID format."""
        response = await async_client.get("/files/not-a-uuid/markdown")
        
        # Invalid UUID is a client error (4xx), never a server error (500)
        # 422 from FastAPI validation or 400 from explicit check
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST, 
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ], f"Expected 400 or 422 for invalid UUID, got {response.status_code}: {response.text}"


class TestHtmlEndpoint:
    """Test suite for GET /files/{fileId}/html endpoint"""

    @pytest.mark.asyncio
    async def test_get_html_not_found(self, async_client):
        """Test HTML retrieval for non-existent file returns 404."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/html")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND, \
            f"Expected 404, got {response.status_code}: {response.text}"


class TestImageEndpoint:
    """Test suite for GET /files/{fileId}/images/{index} endpoint"""

    @pytest.mark.asyncio
    async def test_get_image_not_found_file(self, async_client):
        """Test image retrieval for non-existent file returns 404."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/images/0")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND, \
            f"Expected 404, got {response.status_code}: {response.text}"

    @pytest.mark.asyncio
    async def test_get_image_invalid_index(self, async_client):
        """Test image retrieval with invalid index format."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/images/invalid")
        
        # Invalid index is a client error (4xx), never a server error (500)
        # Should be 422 from FastAPI or 400 from explicit check
        # 404 is also acceptable if file not found is checked first
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND, 
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ], f"Expected 400, 404, or 422 for invalid index, got {response.status_code}: {response.text}"
