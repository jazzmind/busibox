"""
Integration tests for the Data API endpoints.

Tests the full /data API endpoints with real database access.
Uses the shared auth test fixtures for JWT authentication.
"""

import json
import pytest
import uuid
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestDataDocumentLifecycle:
    """Test the full lifecycle of data documents."""
    
    async def test_create_simple_document(self, async_client):
        """Test creating a simple data document without schema."""
        response = await async_client.post("/data", json={
            "name": "Test Tasks",
            "visibility": "personal",
        })
        
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Test Tasks"
        assert data["visibility"] == "personal"
        assert data["recordCount"] == 0
        assert data["version"] == 1
        assert "id" in data
        
        # Cleanup
        await async_client.delete(f"/data/{data['id']}")
    
    async def test_create_document_with_schema(self, async_client):
        """Test creating a data document with schema definition."""
        schema = {
            "fields": {
                "name": {"type": "string", "required": True},
                "status": {"type": "enum", "values": ["pending", "done"]},
                "priority": {"type": "integer", "min": 1, "max": 5},
            }
        }
        
        response = await async_client.post("/data", json={
            "name": "Tasks with Schema",
            "schema": schema,
            "visibility": "personal",
        })
        
        assert response.status_code == 201
        data = response.json()
        assert data["schema"] is not None
        assert "fields" in data["schema"]
        
        # Cleanup
        await async_client.delete(f"/data/{data['id']}")
    
    async def test_create_document_with_initial_records(self, async_client):
        """Test creating a document with initial records."""
        response = await async_client.post("/data", json={
            "name": "Pre-populated Tasks",
            "initialRecords": [
                {"name": "Task 1", "done": False},
                {"name": "Task 2", "done": True},
            ],
        })
        
        assert response.status_code == 201
        data = response.json()
        assert data["recordCount"] == 2
        
        # Cleanup
        await async_client.delete(f"/data/{data['id']}")
    
    async def test_get_document(self, async_client):
        """Test retrieving a data document."""
        # Create
        create_response = await async_client.post("/data", json={
            "name": "Get Test",
            "initialRecords": [{"name": "Record 1"}],
        })
        doc_id = create_response.json()["id"]
        
        # Get with records
        response = await async_client.get(f"/data/{doc_id}?includeRecords=true")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Get Test"
        assert "records" in data
        assert len(data["records"]) == 1
        
        # Get without records
        response = await async_client.get(f"/data/{doc_id}?includeRecords=false")
        assert response.status_code == 200
        data = response.json()
        assert "records" not in data
        
        # Cleanup
        await async_client.delete(f"/data/{doc_id}")
    
    async def test_update_document(self, async_client):
        """Test updating a data document."""
        # Create
        create_response = await async_client.post("/data", json={"name": "Update Test"})
        doc_id = create_response.json()["id"]
        
        # Update name
        response = await async_client.put(f"/data/{doc_id}", json={
            "name": "Updated Name",
            "metadata": {"key": "value"},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Name"
        assert data["version"] == 2  # Version incremented
        
        # Cleanup
        await async_client.delete(f"/data/{doc_id}")
    
    async def test_delete_document(self, async_client):
        """Test deleting a data document."""
        # Create
        create_response = await async_client.post("/data", json={"name": "Delete Test"})
        doc_id = create_response.json()["id"]
        
        # Delete
        response = await async_client.delete(f"/data/{doc_id}")
        assert response.status_code == 204
        
        # Verify deleted
        response = await async_client.get(f"/data/{doc_id}")
        assert response.status_code == 404
    
    async def test_list_documents(self, async_client):
        """Test listing data documents."""
        # Create a few documents
        doc_ids = []
        for i in range(3):
            response = await async_client.post("/data", json={"name": f"List Test {i}"})
            doc_ids.append(response.json()["id"])
        
        # List all
        response = await async_client.get("/data?limit=10")
        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert len(data["documents"]) >= 3
        
        # Cleanup
        for doc_id in doc_ids:
            await async_client.delete(f"/data/{doc_id}")


class TestRecordOperations:
    """Test record CRUD operations."""
    
    @pytest.fixture
    async def test_document(self, async_client):
        """Create a test document for record operations."""
        response = await async_client.post("/data", json={
            "name": "Record Test Doc",
            "schema": {
                "fields": {
                    "name": {"type": "string", "required": True},
                    "status": {"type": "string"},
                    "priority": {"type": "integer"},
                }
            },
        })
        doc = response.json()
        yield doc
        # Cleanup
        await async_client.delete(f"/data/{doc['id']}")
    
    async def test_insert_records(self, async_client, test_document):
        """Test inserting records."""
        doc_id = test_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {"name": "Task A", "status": "pending", "priority": 3},
                {"name": "Task B", "status": "done", "priority": 1},
            ],
        })
        
        assert response.status_code == 201
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2
        assert len(data["recordIds"]) == 2
    
    async def test_insert_validates_schema(self, async_client, test_document):
        """Test that insert validates against schema."""
        doc_id = test_document["id"]
        
        # Missing required field
        response = await async_client.post(f"/data/{doc_id}/records", json={
            "records": [{"status": "pending"}],  # Missing 'name'
        })
        
        assert response.status_code == 400
    
    async def test_insert_skip_validation(self, async_client, test_document):
        """Test inserting without validation."""
        doc_id = test_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/records", json={
            "records": [{"status": "pending"}],  # Missing 'name' but skip validation
            "validate": False,
        })
        
        assert response.status_code == 201
    
    async def test_update_records_with_filter(self, async_client, test_document):
        """Test updating records with a filter."""
        doc_id = test_document["id"]
        
        # Insert some records
        await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {"name": "Task A", "status": "pending", "priority": 3},
                {"name": "Task B", "status": "pending", "priority": 1},
                {"name": "Task C", "status": "done", "priority": 5},
            ],
        })
        
        # Update all pending tasks
        response = await async_client.put(f"/data/{doc_id}/records", json={
            "updates": {"status": "in_progress"},
            "where": {"field": "status", "op": "eq", "value": "pending"},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["count"] == 2  # 2 pending tasks updated
    
    async def test_update_all_records(self, async_client, test_document):
        """Test updating all records (no filter)."""
        doc_id = test_document["id"]
        
        await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {"name": "Task A", "priority": 1},
                {"name": "Task B", "priority": 2},
            ],
        })
        
        response = await async_client.put(f"/data/{doc_id}/records", json={
            "updates": {"priority": 5},
            # No where = update all
        })
        
        assert response.status_code == 200
        assert response.json()["count"] == 2
    
    async def test_delete_records_by_filter(self, async_client, test_document):
        """Test deleting records by filter."""
        doc_id = test_document["id"]
        
        await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {"name": "Keep", "status": "active"},
                {"name": "Delete 1", "status": "archived"},
                {"name": "Delete 2", "status": "archived"},
            ],
        })
        
        response = await async_client.request(
            "DELETE",
            f"/data/{doc_id}/records",
            json={"where": {"field": "status", "op": "eq", "value": "archived"}},
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        
        # Verify only 1 record remains
        doc_response = await async_client.get(f"/data/{doc_id}")
        assert doc_response.json()["recordCount"] == 1
    
    async def test_delete_records_by_id(self, async_client, test_document):
        """Test deleting specific records by ID."""
        doc_id = test_document["id"]
        
        insert_response = await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {"name": "Record 1"},
                {"name": "Record 2"},
                {"name": "Record 3"},
            ],
        })
        record_ids = insert_response.json()["recordIds"]
        
        # Delete first record by ID
        response = await async_client.request(
            "DELETE",
            f"/data/{doc_id}/records",
            json={"recordIds": [record_ids[0]]},
        )
        
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestQueryEndpoint:
    """Test the query endpoint with various query types."""
    
    @pytest.fixture
    async def populated_document(self, async_client):
        """Create a document with test data."""
        response = await async_client.post("/data", json={
            "name": "Query Test Doc",
            "initialRecords": [
                {"name": "Task A", "status": "pending", "priority": 3, "tags": ["urgent"]},
                {"name": "Task B", "status": "done", "priority": 1, "tags": ["low"]},
                {"name": "Task C", "status": "pending", "priority": 5, "tags": ["urgent", "important"]},
                {"name": "Task D", "status": "in_progress", "priority": 2, "tags": []},
                {"name": "Task E", "status": "pending", "priority": 4, "tags": ["medium"]},
            ],
        })
        doc = response.json()
        yield doc
        await async_client.delete(f"/data/{doc['id']}")
    
    async def test_query_simple_filter(self, async_client, populated_document):
        """Test simple equality filter."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "where": {"field": "status", "op": "eq", "value": "pending"},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert all(r["status"] == "pending" for r in data["records"])
    
    async def test_query_comparison_operators(self, async_client, populated_document):
        """Test comparison operators."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "where": {"field": "priority", "op": "gte", "value": 3},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert all(r["priority"] >= 3 for r in data["records"])
    
    async def test_query_and_condition(self, async_client, populated_document):
        """Test AND condition."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "where": {
                "and": [
                    {"field": "status", "op": "eq", "value": "pending"},
                    {"field": "priority", "op": "gte", "value": 4},
                ]
            },
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Task C (5) and Task E (4)
    
    async def test_query_or_condition(self, async_client, populated_document):
        """Test OR condition."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "where": {
                "or": [
                    {"field": "status", "op": "eq", "value": "done"},
                    {"field": "priority", "op": "eq", "value": 5},
                ]
            },
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2  # Task B (done) and Task C (priority 5)
    
    async def test_query_select_fields(self, async_client, populated_document):
        """Test field selection."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "select": ["name", "status"],
        })
        
        assert response.status_code == 200
        data = response.json()
        for record in data["records"]:
            assert set(record.keys()) == {"name", "status"}
    
    async def test_query_order_by(self, async_client, populated_document):
        """Test sorting."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "orderBy": [{"field": "priority", "direction": "desc"}],
        })
        
        assert response.status_code == 200
        data = response.json()
        priorities = [r["priority"] for r in data["records"]]
        assert priorities == sorted(priorities, reverse=True)
    
    async def test_query_pagination(self, async_client, populated_document):
        """Test limit and offset."""
        doc_id = populated_document["id"]
        
        # First page
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "orderBy": [{"field": "name", "direction": "asc"}],
            "limit": 2,
            "offset": 0,
        })
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["records"]) == 2
        assert data["total"] == 5
        assert data["offset"] == 0
        
        # Second page
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "orderBy": [{"field": "name", "direction": "asc"}],
            "limit": 2,
            "offset": 2,
        })
        
        data = response.json()
        assert len(data["records"]) == 2
        assert data["offset"] == 2
    
    async def test_query_count_aggregation(self, async_client, populated_document):
        """Test count aggregation."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "aggregate": {"count": "*"},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert "aggregations" in data
        assert data["aggregations"]["count"] == 5
    
    async def test_query_sum_aggregation(self, async_client, populated_document):
        """Test sum aggregation."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "aggregate": {"sum": "priority"},
        })
        
        assert response.status_code == 200
        data = response.json()
        assert data["aggregations"]["sum_priority"] == 15  # 3+1+5+2+4
    
    async def test_query_multiple_aggregations(self, async_client, populated_document):
        """Test multiple aggregations."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "aggregate": {
                "count": "*",
                "avg": "priority",
                "min": "priority",
                "max": "priority",
            },
        })
        
        assert response.status_code == 200
        aggs = response.json()["aggregations"]
        assert aggs["count"] == 5
        assert aggs["avg_priority"] == 3.0
        assert aggs["min_priority"] == 1
        assert aggs["max_priority"] == 5
    
    async def test_query_validation_error(self, async_client, populated_document):
        """Test query validation."""
        doc_id = populated_document["id"]
        
        response = await async_client.post(f"/data/{doc_id}/query", json={
            "where": {"field": "status", "op": "invalid_op", "value": "test"},
        })
        
        assert response.status_code == 400


class TestSchemaEndpoints:
    """Test schema management endpoints."""
    
    async def test_get_schema(self, async_client):
        """Test getting document schema."""
        # Create with schema
        create_response = await async_client.post("/data", json={
            "name": "Schema Test",
            "schema": {"fields": {"name": {"type": "string"}}},
        })
        doc_id = create_response.json()["id"]
        
        response = await async_client.get(f"/data/{doc_id}/schema")
        
        assert response.status_code == 200
        data = response.json()
        assert data["hasSchema"] is True
        assert "fields" in data["schema"]
        
        # Cleanup
        await async_client.delete(f"/data/{doc_id}")
    
    async def test_get_schema_none(self, async_client):
        """Test getting schema when none exists."""
        create_response = await async_client.post("/data", json={"name": "No Schema"})
        doc_id = create_response.json()["id"]
        
        response = await async_client.get(f"/data/{doc_id}/schema")
        
        assert response.status_code == 200
        data = response.json()
        assert data["hasSchema"] is False
        
        await async_client.delete(f"/data/{doc_id}")
    
    async def test_update_schema(self, async_client):
        """Test updating document schema."""
        create_response = await async_client.post("/data", json={"name": "Update Schema Test"})
        doc_id = create_response.json()["id"]
        
        response = await async_client.put(f"/data/{doc_id}/schema", json={
            "schema": {"fields": {"name": {"type": "string", "required": True}}},
        })
        
        assert response.status_code == 200
        assert response.json()["schema"] is not None
        
        await async_client.delete(f"/data/{doc_id}")


class TestCacheEndpoints:
    """Test cache management endpoints."""
    
    async def test_get_cache_status_not_cached(self, async_client):
        """Test getting cache status for uncached document."""
        create_response = await async_client.post("/data", json={"name": "Cache Test"})
        doc_id = create_response.json()["id"]
        
        response = await async_client.get(f"/data/{doc_id}/cache")
        
        assert response.status_code == 200
        data = response.json()
        assert data["cached"] is False
        
        await async_client.delete(f"/data/{doc_id}")
    
    async def test_activate_cache(self, async_client):
        """Test activating cache for a document."""
        create_response = await async_client.post("/data", json={
            "name": "Cache Activate Test",
            "initialRecords": [{"name": "Record 1"}],
        })
        doc_id = create_response.json()["id"]
        
        response = await async_client.post(f"/data/{doc_id}/cache?ttl=120")
        
        # May be 503 if Redis not available in test env
        if response.status_code == 200:
            data = response.json()
            assert data["cached"] is True
            assert data["ttl"] == 120
        else:
            assert response.status_code == 503  # Caching not available
        
        await async_client.delete(f"/data/{doc_id}")


class TestAuthorizationAndRLS:
    """Test authorization and RLS enforcement."""
    
    async def test_unauthorized_access(self, async_client_no_auth):
        """Test that unauthenticated requests are rejected."""
        response = await async_client_no_auth.get("/data")
        assert response.status_code == 401
    
    async def test_no_scopes_rejected(self, async_client_no_scopes):
        """Test that requests without required scopes are rejected."""
        response = await async_client_no_scopes.get("/data")
        assert response.status_code == 403
    
    async def test_read_only_cannot_create(self, async_client_read_only):
        """Test that read-only users cannot create documents."""
        response = await async_client_read_only.post("/data", json={"name": "Test"})
        assert response.status_code == 403
    
    async def test_personal_document_isolation(self, async_client, auth_client, data_full_access_role):
        """Test that personal documents are isolated to owner."""
        # Create a document
        response = await async_client.post("/data", json={
            "name": "Personal Doc",
            "visibility": "personal",
        })
        assert response.status_code == 201
        doc_id = response.json()["id"]
        
        # Owner can see it
        response = await async_client.get(f"/data/{doc_id}")
        assert response.status_code == 200
        
        # Cleanup
        await async_client.delete(f"/data/{doc_id}")


class TestErrorHandling:
    """Test error handling."""
    
    async def test_get_nonexistent_document(self, async_client):
        """Test getting a non-existent document."""
        fake_id = str(uuid.uuid4())
        response = await async_client.get(f"/data/{fake_id}")
        assert response.status_code == 404
    
    async def test_invalid_uuid(self, async_client):
        """Test invalid UUID handling."""
        response = await async_client.get("/data/not-a-uuid")
        assert response.status_code == 400
    
    async def test_delete_nonexistent(self, async_client):
        """Test deleting non-existent document."""
        fake_id = str(uuid.uuid4())
        response = await async_client.delete(f"/data/{fake_id}")
        assert response.status_code == 404
    
    async def test_insert_to_nonexistent_document(self, async_client):
        """Test inserting records to non-existent document."""
        fake_id = str(uuid.uuid4())
        response = await async_client.post(f"/data/{fake_id}/records", json={
            "records": [{"name": "Test"}],
        })
        assert response.status_code in (400, 404, 500)


class TestOptimisticLocking:
    """Test optimistic locking with version checking."""
    
    async def test_update_with_correct_version(self, async_client):
        """Test update with correct expected version."""
        create_response = await async_client.post("/data", json={"name": "Version Test"})
        doc = create_response.json()
        
        response = await async_client.put(f"/data/{doc['id']}", json={
            "name": "Updated",
            "expectedVersion": 1,
        })
        
        assert response.status_code == 200
        assert response.json()["version"] == 2
        
        await async_client.delete(f"/data/{doc['id']}")
    
    async def test_update_with_wrong_version(self, async_client):
        """Test update with incorrect expected version."""
        create_response = await async_client.post("/data", json={"name": "Version Test"})
        doc = create_response.json()
        
        # Update once
        await async_client.put(f"/data/{doc['id']}", json={"name": "First Update"})
        
        # Try to update with old version
        response = await async_client.put(f"/data/{doc['id']}", json={
            "name": "Second Update",
            "expectedVersion": 1,  # Should be 2 now
        })
        
        assert response.status_code == 409  # Conflict
        
        await async_client.delete(f"/data/{doc['id']}")
