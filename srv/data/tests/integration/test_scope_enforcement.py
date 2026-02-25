"""
Tests for OAuth2 scope enforcement on data API endpoints.

Verifies that endpoints correctly require the appropriate scopes.
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_upload_requires_write_scope(async_client_read_only: AsyncClient):
    """
    POST /upload should require data.write scope.
    A client with only data.read should be rejected with 403.
    """
    # Create minimal form data for upload attempt
    files = {"file": ("test.txt", b"test content", "text/plain")}
    
    response = await async_client_read_only.post("/upload", files=files)
    
    assert response.status_code == 403
    assert "data.write" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_get_file_requires_read_scope(async_client: AsyncClient):
    """
    GET /files/{id} should require data.read scope.
    A client with data.read should be allowed (may get 404 for non-existent file).
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client.get(f"/files/{fake_file_id}")
    
    # Should not be 403 (scope check passed), will be 404 for non-existent file
    assert response.status_code in (200, 404)


@pytest.mark.asyncio
async def test_delete_requires_delete_scope(async_client_read_only: AsyncClient):
    """
    DELETE /files/{id} should require data.delete scope.
    A client with only data.read should be rejected with 403.
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client_read_only.delete(f"/files/{fake_file_id}")
    
    assert response.status_code == 403
    assert "data.delete" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_reprocess_requires_write_scope(async_client_read_only: AsyncClient):
    """
    POST /files/{id}/reprocess should require data.write scope.
    A client with only data.read should be rejected with 403.
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client_read_only.post(f"/files/{fake_file_id}/reprocess")
    
    assert response.status_code == 403
    assert "data.write" in response.json().get("detail", "")


@pytest.mark.asyncio
async def test_search_requires_read_scope(async_client_read_only: AsyncClient):
    """
    POST /search should require data.read scope.
    A client with data.read should be allowed.
    """
    response = await async_client_read_only.post(
        "/search",
        json={"query": "test query", "limit": 10}
    )
    
    # Should not be 403 (scope check passed), may fail for other reasons
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_status_requires_read_scope(async_client: AsyncClient):
    """
    GET /status/{id} should require data.read scope.
    A client with data.read (or admin) should be allowed.
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client.get(f"/status/{fake_file_id}")
    
    # Should not be 403 (scope check passed - admin client has all scopes)
    assert response.status_code != 403


@pytest.mark.asyncio
async def test_status_rejects_no_scopes(async_client_no_scopes: AsyncClient):
    """
    GET /status/{id} should reject a client with no scopes.
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client_no_scopes.get(f"/status/{fake_file_id}")
    
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_roles_requires_write_scope(async_client_read_only: AsyncClient):
    """
    PUT /files/{id}/roles should require data.write scope.
    A client with only data.read should be rejected with 403.
    """
    import uuid
    fake_file_id = str(uuid.uuid4())
    
    response = await async_client_read_only.put(
        f"/files/{fake_file_id}/roles",
        json={"add_role_ids": [], "add_role_names": [], "remove_role_ids": []}
    )
    
    assert response.status_code == 403
    assert "data.write" in response.json().get("detail", "")

