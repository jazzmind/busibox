"""
Tests for Markdown API Endpoints

Uses JWT auth fixtures from conftest.py.
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
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "error" in response.json()

    @pytest.mark.asyncio
    async def test_get_markdown_invalid_uuid(self, async_client):
        """Test markdown retrieval with invalid UUID format."""
        response = await async_client.get("/files/not-a-uuid/markdown")
        
        # Should return 400 or 422 for invalid UUID
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_422_UNPROCESSABLE_ENTITY]


class TestHtmlEndpoint:
    """Test suite for GET /files/{fileId}/html endpoint"""

    @pytest.mark.asyncio
    async def test_get_html_not_found(self, async_client):
        """Test HTML retrieval for non-existent file returns 404."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/html")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestImageEndpoint:
    """Test suite for GET /files/{fileId}/images/{index} endpoint"""

    @pytest.mark.asyncio
    async def test_get_image_not_found_file(self, async_client):
        """Test image retrieval for non-existent file returns 404."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/images/0")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_image_invalid_index(self, async_client):
        """Test image retrieval with invalid index format."""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/images/invalid")
        
        # Should return 404 or 422 for invalid index
        assert response.status_code in [status.HTTP_404_NOT_FOUND, status.HTTP_422_UNPROCESSABLE_ENTITY]
