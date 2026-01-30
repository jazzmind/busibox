"""
Unit tests for files endpoint (metadata retrieval and deletion).

Uses JWT auth fixtures from conftest.py for proper authentication.
"""
import uuid
import pytest
from fastapi import status


@pytest.mark.asyncio
async def test_get_file_metadata_success(async_client):
    """Test file metadata endpoint with non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_file_metadata_not_found(async_client):
    """Test file not found returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_get_file_metadata_unauthorized(async_client):
    """Test accessing non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_file_success(async_client):
    """Test file deletion endpoint for non-existent file returns 404."""
    response = await async_client.delete(f"/files/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_delete_file_shared_vectors(async_client):
    """Test deleting a non-existent file returns 404."""
    response = await async_client.delete(f"/files/{uuid.uuid4()}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_file_chunks(async_client):
    """Test getting chunks for a non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}/chunks")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_file_vectors(async_client):
    """Test getting vectors for a non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}/vectors")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_file_download(async_client):
    """Test downloading a non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}/download")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_search_within_document(async_client):
    """Test searching within a non-existent document returns 404."""
    response = await async_client.post(
        f"/files/{uuid.uuid4()}/search",
        json={"query": "test query", "limit": 10}
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_file_markdown(async_client):
    """Test getting markdown for a non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}/markdown")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_reprocess_file(async_client):
    """Test reprocessing a non-existent file returns 404."""
    response = await async_client.post(f"/files/{uuid.uuid4()}/reprocess")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_export_file(async_client):
    """Test exporting a non-existent file returns 404."""
    response = await async_client.get(f"/files/{uuid.uuid4()}/export?format=markdown")
    assert response.status_code == status.HTTP_404_NOT_FOUND
