"""
Tests for Markdown API Endpoints
"""

import pytest
import uuid
from httpx import AsyncClient
from fastapi import status

# Note: These tests assume the API is running and accessible
# They require proper test setup with test database and MinIO


class TestMarkdownEndpoint:
    """Test suite for GET /files/{fileId}/markdown endpoint"""

    @pytest.mark.asyncio
    async def test_get_markdown_success(self, async_client: AsyncClient, test_file_with_markdown):
        """Test successful markdown retrieval"""
        file_id = test_file_with_markdown["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/markdown")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "fileId" in data
        assert "markdown" in data
        assert "hasImages" in data
        assert "imageCount" in data
        assert data["markdown"] is not None

    @pytest.mark.asyncio
    async def test_get_markdown_not_found(self, async_client: AsyncClient):
        """Test markdown retrieval for non-existent file"""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/markdown")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "error" in response.json()

    @pytest.mark.asyncio
    async def test_get_markdown_unauthorized(self, async_client_different_user: AsyncClient, test_file_with_markdown):
        """Test markdown retrieval for file owned by different user"""
        file_id = test_file_with_markdown["file_id"]
        
        response = await async_client_different_user.get(f"/files/{file_id}/markdown")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "error" in response.json()

    @pytest.mark.asyncio
    async def test_get_markdown_not_generated(self, async_client: AsyncClient, test_file_without_markdown):
        """Test markdown retrieval for file without markdown"""
        file_id = test_file_without_markdown["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/markdown")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        data = response.json()
        assert "Markdown not available" in data["error"]

    @pytest.mark.asyncio
    async def test_get_markdown_invalid_uuid(self, async_client: AsyncClient):
        """Test markdown retrieval with invalid UUID format"""
        response = await async_client.get("/files/not-a-uuid/markdown")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid file ID format" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_get_markdown_includes_metadata(self, async_client: AsyncClient, test_file_with_images):
        """Test that markdown response includes image metadata"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/markdown")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["hasImages"] is True
        assert data["imageCount"] > 0


class TestHtmlEndpoint:
    """Test suite for GET /files/{fileId}/html endpoint"""

    @pytest.mark.asyncio
    async def test_get_html_success(self, async_client: AsyncClient, test_file_with_markdown):
        """Test successful HTML retrieval"""
        file_id = test_file_with_markdown["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "html" in data
        assert "toc" in data
        assert isinstance(data["toc"], list)

    @pytest.mark.asyncio
    async def test_get_html_with_toc(self, async_client: AsyncClient, test_file_with_headings):
        """Test that HTML response includes table of contents"""
        file_id = test_file_with_headings["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["toc"]) > 0
        
        # Check TOC structure
        toc_item = data["toc"][0]
        assert "level" in toc_item
        assert "title" in toc_item
        assert "id" in toc_item

    @pytest.mark.asyncio
    async def test_get_html_not_found(self, async_client: AsyncClient):
        """Test HTML retrieval for non-existent file"""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/html")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_html_unauthorized(self, async_client_different_user: AsyncClient, test_file_with_markdown):
        """Test HTML retrieval for file owned by different user"""
        file_id = test_file_with_markdown["file_id"]
        
        response = await async_client_different_user.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_html_headings_have_ids(self, async_client: AsyncClient, test_file_with_headings):
        """Test that HTML headings have proper ID attributes"""
        file_id = test_file_with_headings["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_200_OK
        html = response.json()["html"]
        
        # Check for heading IDs
        assert 'id=' in html
        assert '<h' in html  # Has headings

    @pytest.mark.asyncio
    async def test_get_html_image_urls_resolved(self, async_client: AsyncClient, test_file_with_images):
        """Test that image URLs are resolved to API endpoints"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_200_OK
        html = response.json()["html"]
        
        # Check for API image URLs
        assert f'/api/files/{file_id}/images/' in html or 'src=' in html

    @pytest.mark.asyncio
    async def test_get_html_sanitized(self, async_client: AsyncClient, test_file_with_dangerous_content):
        """Test that HTML is sanitized (no script tags)"""
        file_id = test_file_with_dangerous_content["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/html")
        
        assert response.status_code == status.HTTP_200_OK
        html = response.json()["html"]
        
        # Check that dangerous content is removed
        assert '<script>' not in html.lower()
        assert 'onclick=' not in html.lower()
        assert '<iframe>' not in html.lower()


class TestImageEndpoint:
    """Test suite for GET /files/{fileId}/images/{imageIndex} endpoint"""

    @pytest.mark.asyncio
    async def test_get_image_success(self, async_client: AsyncClient, test_file_with_images):
        """Test successful image retrieval"""
        file_id = test_file_with_images["file_id"]
        image_index = 0
        
        response = await async_client.get(f"/files/{file_id}/images/{image_index}")
        
        assert response.status_code == status.HTTP_200_OK
        assert response.headers["content-type"] == "image/png"
        assert len(response.content) > 0

    @pytest.mark.asyncio
    async def test_get_image_not_found_file(self, async_client: AsyncClient):
        """Test image retrieval for non-existent file"""
        fake_id = str(uuid.uuid4())
        
        response = await async_client.get(f"/files/{fake_id}/images/0")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_image_not_found_index(self, async_client: AsyncClient, test_file_with_images):
        """Test image retrieval with out-of-range index"""
        file_id = test_file_with_images["file_id"]
        image_count = test_file_with_images["image_count"]
        
        # Request image beyond available count
        response = await async_client.get(f"/files/{file_id}/images/{image_count + 10}")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "not found" in response.json()["error"].lower()

    @pytest.mark.asyncio
    async def test_get_image_unauthorized(self, async_client_different_user: AsyncClient, test_file_with_images):
        """Test image retrieval for file owned by different user"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client_different_user.get(f"/files/{file_id}/images/0")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_get_image_correct_content_type(self, async_client: AsyncClient, test_file_with_images):
        """Test that image response has correct content-type header"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/images/0")
        
        assert response.status_code == status.HTTP_200_OK
        assert "image/png" in response.headers["content-type"]

    @pytest.mark.asyncio
    async def test_get_image_invalid_index(self, async_client: AsyncClient, test_file_with_images):
        """Test image retrieval with invalid (negative) index"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/images/-1")
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Invalid image index" in response.json()["error"]

    @pytest.mark.asyncio
    async def test_get_image_cache_headers(self, async_client: AsyncClient, test_file_with_images):
        """Test that image response includes cache headers"""
        file_id = test_file_with_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/images/0")
        
        assert response.status_code == status.HTTP_200_OK
        assert "cache-control" in response.headers

    @pytest.mark.asyncio
    async def test_get_image_no_images_available(self, async_client: AsyncClient, test_file_without_images):
        """Test image retrieval for file with no images"""
        file_id = test_file_without_images["file_id"]
        
        response = await async_client.get(f"/files/{file_id}/images/0")
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert "No images available" in response.json()["error"]


# Fixtures for test setup (these would need to be implemented in conftest.py)
@pytest.fixture
async def test_file_with_markdown():
    """Create a test file with markdown content"""
    # This is a placeholder - actual implementation needed
    return {"file_id": str(uuid.uuid4()), "has_markdown": True}


@pytest.fixture
async def test_file_without_markdown():
    """Create a test file without markdown content"""
    return {"file_id": str(uuid.uuid4()), "has_markdown": False}


@pytest.fixture
async def test_file_with_images():
    """Create a test file with images"""
    return {"file_id": str(uuid.uuid4()), "image_count": 3}


@pytest.fixture
async def test_file_without_images():
    """Create a test file without images"""
    return {"file_id": str(uuid.uuid4()), "image_count": 0}


@pytest.fixture
async def test_file_with_headings():
    """Create a test file with heading structure"""
    return {"file_id": str(uuid.uuid4()), "has_headings": True}


@pytest.fixture
async def test_file_with_dangerous_content():
    """Create a test file with potentially dangerous HTML content"""
    return {"file_id": str(uuid.uuid4()), "has_scripts": True}


@pytest.fixture
async def async_client():
    """Create async HTTP client for testing"""
    # Placeholder - actual implementation needed with proper auth
    pass


@pytest.fixture
async def async_client_different_user():
    """Create async HTTP client for different user"""
    # Placeholder - actual implementation needed
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

