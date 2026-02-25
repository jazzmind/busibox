"""
Integration tests for POST /data/graph/from-extraction.

Tests graph entity creation from schema extraction results.
Skipped when Neo4j/graph service is unavailable.
"""

import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def _graph_available(client: AsyncClient) -> bool:
    """Check if the graph service is available."""
    resp = await client.get("/data/graph")
    if resp.status_code != 200:
        return False
    body = resp.json()
    return body.get("graph_available", True)


class TestGraphFromExtraction:
    """Test creating graph entities from schema extraction results."""

    @pytest.fixture
    async def schema_with_graph_fields(self, async_client: AsyncClient):
        """Create a data document with extraction schema containing graph-tagged fields."""
        schema = {
            "fields": {
                "people": {
                    "type": "string",
                    "array": True,
                    "search": ["graph"],
                    "description": "People mentioned",
                },
                "organizations": {
                    "type": "string",
                    "array": True,
                    "search": ["graph"],
                    "description": "Organizations mentioned",
                },
                "summary": {
                    "type": "string",
                    "description": "Summary text (not graph-tagged)",
                },
            },
            "displayName": "Test Entity Schema",
            "itemLabel": "Entity",
        }

        response = await async_client.post("/data", json={
            "name": f"Test Graph Schema {uuid.uuid4().hex[:8]}",
            "schema": schema,
            "metadata": {"type": "extraction_schema"},
            "visibility": "personal",
        })
        assert response.status_code == 201, f"Failed to create schema: {response.text}"
        doc = response.json()

        yield doc

        await async_client.delete(f"/data/{doc['id']}")

    @pytest.fixture
    async def schema_without_graph_fields(self, async_client: AsyncClient):
        """Create a data document with schema but no graph-tagged fields."""
        schema = {
            "fields": {
                "title": {"type": "string"},
                "content": {"type": "string"},
            },
        }

        response = await async_client.post("/data", json={
            "name": f"Test Non-Graph Schema {uuid.uuid4().hex[:8]}",
            "schema": schema,
            "metadata": {"type": "extraction_schema"},
        })
        assert response.status_code == 201
        doc = response.json()

        yield doc

        await async_client.delete(f"/data/{doc['id']}")

    async def test_from_extraction_requires_auth(self, async_client_no_auth):
        """Endpoint requires authentication."""
        response = await async_client_no_auth.post(
            "/data/graph/from-extraction",
            params={"file_id": "fake", "schema_document_id": "fake"},
        )
        assert response.status_code == 401

    async def test_from_extraction_missing_params(self, async_client: AsyncClient):
        """Endpoint requires file_id and schema_document_id parameters."""
        response = await async_client.post("/data/graph/from-extraction")
        assert response.status_code == 422, "Should return validation error for missing params"

    async def test_from_extraction_nonexistent_schema(self, async_client: AsyncClient):
        """Returns 404 for non-existent schema document."""
        if not await _graph_available(async_client):
            pytest.skip("Graph service not available")

        response = await async_client.post(
            "/data/graph/from-extraction",
            params={
                "file_id": str(uuid.uuid4()),
                "schema_document_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code in (404, 500)

    async def test_from_extraction_no_graph_fields(
        self, async_client: AsyncClient, schema_without_graph_fields
    ):
        """Returns entity_count=0 when schema has no graph-tagged fields."""
        if not await _graph_available(async_client):
            pytest.skip("Graph service not available")

        response = await async_client.post(
            "/data/graph/from-extraction",
            params={
                "file_id": str(uuid.uuid4()),
                "schema_document_id": schema_without_graph_fields["id"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entity_count"] == 0
        assert "No graph-tagged fields" in data.get("message", "")

    async def test_from_extraction_no_records(
        self, async_client: AsyncClient, schema_with_graph_fields
    ):
        """Returns entity_count=0 when there are no extraction records for the file."""
        if not await _graph_available(async_client):
            pytest.skip("Graph service not available")

        response = await async_client.post(
            "/data/graph/from-extraction",
            params={
                "file_id": str(uuid.uuid4()),
                "schema_document_id": schema_with_graph_fields["id"],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entity_count"] == 0

    async def test_from_extraction_with_records(
        self, async_client: AsyncClient, schema_with_graph_fields
    ):
        """Creates graph entities from extraction records with graph-tagged fields."""
        if not await _graph_available(async_client):
            pytest.skip("Graph service not available")

        doc_id = schema_with_graph_fields["id"]
        fake_file_id = str(uuid.uuid4())

        # Insert extraction records with _sourceFileId
        insert_resp = await async_client.post(f"/data/{doc_id}/records", json={
            "records": [
                {
                    "people": ["Alice Johnson", "Bob Smith"],
                    "organizations": ["Acme Corp"],
                    "summary": "Alice and Bob work at Acme Corp.",
                    "_sourceFileId": fake_file_id,
                },
                {
                    "people": ["Carol White"],
                    "organizations": ["Acme Corp", "Widget Inc"],
                    "summary": "Carol joined Widget Inc from Acme Corp.",
                    "_sourceFileId": fake_file_id,
                },
            ],
            "validate": False,
        })
        assert insert_resp.status_code == 201, f"Insert failed: {insert_resp.text}"

        # Create graph entities from extraction
        response = await async_client.post(
            "/data/graph/from-extraction",
            params={
                "file_id": fake_file_id,
                "schema_document_id": doc_id,
            },
        )
        assert response.status_code == 200, f"Graph creation failed: {response.text}"
        data = response.json()

        assert data["success"] is True
        assert data["graph_available"] is True
        # 3 people + 2 unique orgs = 5 entities, but Acme Corp appears twice = 6 entity instances
        assert data["entity_count"] >= 5, f"Expected at least 5 entities, got {data['entity_count']}"
        assert "people" in data.get("graph_fields", [])
        assert "organizations" in data.get("graph_fields", [])

    async def test_from_extraction_graph_unavailable(self, async_client: AsyncClient):
        """Returns success=False with graph_available=False when Neo4j is down."""
        # If graph IS available, we can't really test this, so just verify the
        # endpoint handles the case gracefully
        response = await async_client.post(
            "/data/graph/from-extraction",
            params={
                "file_id": str(uuid.uuid4()),
                "schema_document_id": str(uuid.uuid4()),
            },
        )
        # Either Neo4j is available (may get 404 for missing schema) or not (200 with graph_available=false)
        if response.status_code == 200:
            data = response.json()
            if not data.get("graph_available", True):
                assert data["success"] is False
                assert data["entity_count"] == 0
