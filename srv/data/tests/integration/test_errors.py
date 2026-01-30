"""
Integration test for error scenarios.

Uses JWT auth fixtures from conftest.py.

IMPORTANT: 500 errors are NEVER acceptable responses. They indicate
the API is not properly catching and handling errors. All expected
error conditions should return 4xx status codes.
"""
import uuid
from io import BytesIO

import pytest
import structlog
from fastapi import status

logger = structlog.get_logger()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_invalid_file_type(async_client):
    """Test that invalid file types are rejected."""
    file_content = BytesIO(b"Some binary content")
    
    response = await async_client.post(
        "/upload",
        files={"file": ("test.exe", file_content, "application/x-msdownload")},
    )
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST, \
        f"Expected 400 for invalid file type, got {response.status_code}: {response.text}"
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_file_not_found(async_client):
    """Test that requesting metadata for non-existent file returns 404."""
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client.get(f"/files/{fake_file_id}")
    
    # Non-existent file should return 404, never 500
    assert response.status_code == status.HTTP_404_NOT_FOUND, \
        f"Expected 404 for non-existent file, got {response.status_code}: {response.text}"
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
@pytest.mark.integration
async def test_delete_non_existent_file(async_client):
    """Test that deleting non-existent file returns 404."""
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client.delete(f"/files/{fake_file_id}")
    
    # Deleting non-existent file should return 404, never 500
    assert response.status_code == status.HTTP_404_NOT_FOUND, \
        f"Expected 404 for deleting non-existent file, got {response.status_code}: {response.text}"
