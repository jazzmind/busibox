"""
Integration tests for library management endpoints.

Tests:
- Personal library auto-creation (DOCS, RESEARCH, TASKS)
- Folder name to library resolution
- Library CRUD operations
- List libraries
"""
import uuid
import pytest
from fastapi import status


# =============================================================================
# Personal Library Tests
# =============================================================================

@pytest.mark.asyncio
async def test_get_library_by_folder_personal_tasks(async_client):
    """Test resolving 'personal-tasks' folder creates and returns TASKS library."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "personal-tasks"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    assert "data" in data
    library = data["data"]["library"]
    
    assert library["isPersonal"] is True
    assert library["libraryType"] == "TASKS"
    assert library["name"] == "Tasks"
    assert library["id"] is not None


@pytest.mark.asyncio
async def test_get_library_by_folder_tasks_alias(async_client):
    """Test 'tasks' is an alias for personal-tasks."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "tasks"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    library = data["data"]["library"]
    
    assert library["libraryType"] == "TASKS"


@pytest.mark.asyncio
async def test_get_library_by_folder_research(async_client):
    """Test resolving 'personal-research' folder creates RESEARCH library."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "personal-research"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    library = data["data"]["library"]
    
    assert library["isPersonal"] is True
    assert library["libraryType"] == "RESEARCH"
    assert library["name"] == "Research"


@pytest.mark.asyncio
async def test_get_library_by_folder_research_alias(async_client):
    """Test 'research' is an alias for personal-research."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "research"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    library = data["data"]["library"]
    
    assert library["libraryType"] == "RESEARCH"


@pytest.mark.asyncio
async def test_get_library_by_folder_docs(async_client):
    """Test resolving 'personal-docs' folder creates DOCS library."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "personal-docs"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    library = data["data"]["library"]
    
    assert library["isPersonal"] is True
    assert library["libraryType"] == "DOCS"
    assert library["name"] == "Personal"


@pytest.mark.asyncio
async def test_get_library_by_folder_docs_aliases(async_client):
    """Test 'personal' and 'docs' are aliases for personal-docs."""
    # Test 'personal' alias
    response1 = await async_client.get("/libraries/by-folder", params={"folder": "personal"})
    assert response1.status_code == status.HTTP_200_OK
    lib1 = response1.json()["data"]["library"]
    
    # Test 'docs' alias
    response2 = await async_client.get("/libraries/by-folder", params={"folder": "docs"})
    assert response2.status_code == status.HTTP_200_OK
    lib2 = response2.json()["data"]["library"]
    
    # Both should return the same DOCS library
    assert lib1["libraryType"] == "DOCS"
    assert lib2["libraryType"] == "DOCS"
    assert lib1["id"] == lib2["id"]


@pytest.mark.asyncio
async def test_get_library_by_folder_unknown(async_client):
    """Test unknown folder name returns 404."""
    response = await async_client.get("/libraries/by-folder", params={"folder": "nonexistent-folder"})
    
    assert response.status_code == status.HTTP_404_NOT_FOUND
    data = response.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_get_library_by_folder_missing_param(async_client):
    """Test missing folder parameter returns 422."""
    response = await async_client.get("/libraries/by-folder")
    
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


# =============================================================================
# List Libraries Tests
# =============================================================================

@pytest.mark.asyncio
async def test_list_libraries(async_client):
    """Test listing user's libraries."""
    # First ensure some libraries exist
    await async_client.get("/libraries/by-folder", params={"folder": "personal-tasks"})
    await async_client.get("/libraries/by-folder", params={"folder": "research"})
    
    response = await async_client.get("/libraries")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    assert "data" in data
    assert "total" in data
    assert isinstance(data["data"], list)
    assert data["total"] >= 2  # At least TASKS and RESEARCH
    
    # Check that personal libraries are included
    library_types = [lib["libraryType"] for lib in data["data"] if lib["isPersonal"]]
    assert "TASKS" in library_types
    assert "RESEARCH" in library_types


@pytest.mark.asyncio
async def test_list_libraries_personal_only(async_client):
    """Test listing only personal libraries."""
    response = await async_client.get("/libraries", params={"include_shared": "false"})
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    # All returned libraries should be personal
    for lib in data["data"]:
        assert lib["isPersonal"] is True


@pytest.mark.asyncio
async def test_list_libraries_includes_document_count(async_client):
    """Test that library list includes document counts."""
    response = await async_client.get("/libraries")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    for lib in data["data"]:
        assert "documentCount" in lib
        assert isinstance(lib["documentCount"], int)


# =============================================================================
# Ensure Personal Libraries Tests
# =============================================================================

@pytest.mark.asyncio
async def test_ensure_personal_libraries(async_client):
    """Test ensuring all personal libraries exist."""
    response = await async_client.post("/libraries/ensure-personal")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    assert "data" in data
    libraries = data["data"]
    
    # Should have all three personal library types
    library_types = [lib["libraryType"] for lib in libraries]
    assert "DOCS" in library_types
    assert "RESEARCH" in library_types
    assert "TASKS" in library_types


# =============================================================================
# Get Library by ID Tests
# =============================================================================

@pytest.mark.asyncio
async def test_get_library_by_id(async_client):
    """Test getting a library by ID."""
    # First create/get a library
    create_response = await async_client.get("/libraries/by-folder", params={"folder": "tasks"})
    library_id = create_response.json()["data"]["library"]["id"]
    
    # Get by ID
    response = await async_client.get(f"/libraries/{library_id}")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    
    assert data["data"]["id"] == library_id
    assert data["data"]["libraryType"] == "TASKS"
    assert "documentCount" in data["data"]


@pytest.mark.asyncio
async def test_get_library_by_id_not_found(async_client):
    """Test getting a non-existent library returns 404."""
    fake_id = str(uuid.uuid4())
    response = await async_client.get(f"/libraries/{fake_id}")
    
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_library_by_id_invalid_uuid(async_client):
    """Test getting with invalid UUID returns 400."""
    response = await async_client.get("/libraries/not-a-uuid")
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "error" in data


# =============================================================================
# Create Library Tests
# =============================================================================

@pytest.mark.asyncio
async def test_create_shared_library(async_client):
    """Test creating a shared library."""
    library_name = f"Test Library {uuid.uuid4().hex[:8]}"
    
    response = await async_client.post("/libraries", json={
        "name": library_name,
        "is_personal": False,
    })
    
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    
    library = data["data"]
    assert library["name"] == library_name
    assert library["isPersonal"] is False
    assert library["libraryType"] is None
    
    # Cleanup - delete the library
    await async_client.delete(f"/libraries/{library['id']}", params={"hard_delete": "true"})


@pytest.mark.asyncio
async def test_create_personal_library_requires_type(async_client):
    """Test that creating a personal library requires library_type."""
    response = await async_client.post("/libraries", json={
        "name": "My Library",
        "is_personal": True,
    })
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "library_type" in data["error"].lower()


@pytest.mark.asyncio
async def test_create_personal_library_invalid_type(async_client):
    """Test that creating a personal library with invalid type fails."""
    response = await async_client.post("/libraries", json={
        "name": "My Library",
        "is_personal": True,
        "library_type": "INVALID",
    })
    
    assert response.status_code == status.HTTP_400_BAD_REQUEST


# =============================================================================
# Update Library Tests
# =============================================================================

@pytest.mark.asyncio
async def test_update_library_name(async_client):
    """Test updating a library name."""
    # Create a shared library to update
    create_response = await async_client.post("/libraries", json={
        "name": "Original Name",
        "is_personal": False,
    })
    library_id = create_response.json()["data"]["id"]
    
    # Update the name
    response = await async_client.put(f"/libraries/{library_id}", json={
        "name": "Updated Name",
    })
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["data"]["name"] == "Updated Name"
    
    # Cleanup
    await async_client.delete(f"/libraries/{library_id}", params={"hard_delete": "true"})


@pytest.mark.asyncio
async def test_update_library_not_found(async_client):
    """Test updating a non-existent library returns 404."""
    fake_id = str(uuid.uuid4())
    response = await async_client.put(f"/libraries/{fake_id}", json={
        "name": "New Name",
    })
    
    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Delete Library Tests
# =============================================================================

@pytest.mark.asyncio
async def test_delete_library_soft(async_client):
    """Test soft deleting a library."""
    # Create a library to delete
    create_response = await async_client.post("/libraries", json={
        "name": "Library to Delete",
        "is_personal": False,
    })
    library_id = create_response.json()["data"]["id"]
    
    # Soft delete
    response = await async_client.delete(f"/libraries/{library_id}")
    
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == library_id
    
    # Library should not be found anymore (soft deleted)
    get_response = await async_client.get(f"/libraries/{library_id}")
    assert get_response.status_code == status.HTTP_404_NOT_FOUND
    
    # Hard delete to clean up
    await async_client.delete(f"/libraries/{library_id}", params={"hard_delete": "true"})


@pytest.mark.asyncio
async def test_delete_library_hard(async_client):
    """Test hard deleting a library."""
    # Create a library to delete
    create_response = await async_client.post("/libraries", json={
        "name": "Library to Hard Delete",
        "is_personal": False,
    })
    library_id = create_response.json()["data"]["id"]
    
    # Hard delete
    response = await async_client.delete(f"/libraries/{library_id}", params={"hard_delete": "true"})
    
    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_delete_library_not_found(async_client):
    """Test deleting a non-existent library returns 404."""
    fake_id = str(uuid.uuid4())
    response = await async_client.delete(f"/libraries/{fake_id}")
    
    assert response.status_code == status.HTTP_404_NOT_FOUND


# =============================================================================
# Idempotency Tests
# =============================================================================

@pytest.mark.asyncio
async def test_personal_library_idempotent(async_client):
    """Test that requesting the same personal library multiple times returns the same library."""
    # Request TASKS library twice
    response1 = await async_client.get("/libraries/by-folder", params={"folder": "tasks"})
    response2 = await async_client.get("/libraries/by-folder", params={"folder": "tasks"})
    
    lib1 = response1.json()["data"]["library"]
    lib2 = response2.json()["data"]["library"]
    
    # Should be the same library
    assert lib1["id"] == lib2["id"]


@pytest.mark.asyncio
async def test_ensure_personal_libraries_idempotent(async_client):
    """Test that ensure_personal_libraries is idempotent."""
    # Call twice
    response1 = await async_client.post("/libraries/ensure-personal")
    response2 = await async_client.post("/libraries/ensure-personal")
    
    libs1 = {lib["libraryType"]: lib["id"] for lib in response1.json()["data"]}
    libs2 = {lib["libraryType"]: lib["id"] for lib in response2.json()["data"]}
    
    # Same libraries should be returned
    assert libs1 == libs2


# =============================================================================
# Authorization Tests
# =============================================================================

@pytest.mark.asyncio
async def test_list_libraries_no_auth(async_client_no_auth):
    """Test that listing libraries without auth returns 401."""
    response = await async_client_no_auth.get("/libraries")
    
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_get_library_by_folder_no_auth(async_client_no_auth):
    """Test that resolving folder without auth returns 401."""
    response = await async_client_no_auth.get("/libraries/by-folder", params={"folder": "tasks"})
    
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_library_read_only(async_client_read_only):
    """Test that creating a library with only read scope returns 403."""
    response = await async_client_read_only.post("/libraries", json={
        "name": "Test Library",
        "is_personal": False,
    })
    
    assert response.status_code == status.HTTP_403_FORBIDDEN
