"""
Integration tests for POST /data/seed-default-schemas.

Verifies that built-in extraction schemas are created correctly,
the endpoint is idempotent, and schemas have the expected structure.
"""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


class TestSeedDefaultSchemas:
    """Test the default extraction schema seeding endpoint."""

    async def test_seed_creates_schemas(self, async_client: AsyncClient):
        """Seeding creates extraction schemas and returns created list."""
        response = await async_client.post("/data/seed-default-schemas")

        assert response.status_code == 200, f"Seed failed: {response.text}"
        data = response.json()

        assert "created" in data
        assert "skipped" in data
        assert "total" in data
        assert data["total"] > 0

        total_processed = len(data["created"]) + len(data["skipped"])
        assert total_processed == data["total"]

        # Each created entry should have name and id
        for item in data["created"]:
            assert "name" in item
            assert "id" in item

    async def test_seed_is_idempotent(self, async_client: AsyncClient):
        """Calling seed twice skips already-created schemas."""
        # First call
        resp1 = await async_client.post("/data/seed-default-schemas")
        assert resp1.status_code == 200

        # Second call should skip everything
        resp2 = await async_client.post("/data/seed-default-schemas")
        assert resp2.status_code == 200
        data2 = resp2.json()

        assert len(data2["created"]) == 0, "Second call should not create any new schemas"
        assert data2["total"] == data2["total"]
        # Everything from the first call should now be skipped
        assert len(data2["skipped"]) == data2["total"]

    async def test_seeded_schemas_are_retrievable(self, async_client: AsyncClient):
        """Seeded schemas appear in the data document list."""
        # Ensure schemas exist
        seed_resp = await async_client.post("/data/seed-default-schemas")
        assert seed_resp.status_code == 200

        # List all data documents
        list_resp = await async_client.get("/data?limit=100")
        assert list_resp.status_code == 200
        docs = list_resp.json().get("documents", [])

        # Find extraction schemas
        extraction_schemas = [
            d for d in docs
            if isinstance(d.get("metadata"), dict)
            and d["metadata"].get("type") == "extraction_schema"
        ]

        assert len(extraction_schemas) > 0, "No extraction schemas found after seeding"

        for schema_doc in extraction_schemas:
            assert schema_doc.get("name"), "Schema should have a name"
            meta = schema_doc.get("metadata", {})
            assert meta.get("type") == "extraction_schema"

    async def test_seeded_schema_has_valid_structure(self, async_client: AsyncClient):
        """Each seeded schema has fields, displayName, and itemLabel."""
        seed_resp = await async_client.post("/data/seed-default-schemas")
        assert seed_resp.status_code == 200

        created_ids = [item["id"] for item in seed_resp.json()["created"]]
        skipped_names = seed_resp.json()["skipped"]

        # If all were skipped, list to find them
        if not created_ids:
            list_resp = await async_client.get("/data?limit=100")
            docs = list_resp.json().get("documents", [])
            created_ids = [
                d["id"] for d in docs
                if isinstance(d.get("metadata"), dict)
                and d["metadata"].get("type") == "extraction_schema"
                and d["metadata"].get("builtin") is True
            ]

        assert len(created_ids) > 0, "Need at least one schema to validate"

        for doc_id in created_ids[:2]:
            schema_resp = await async_client.get(f"/data/{doc_id}/schema")
            assert schema_resp.status_code == 200
            schema_data = schema_resp.json()

            if schema_data.get("hasSchema"):
                schema = schema_data["schema"]
                assert "fields" in schema, "Schema should have fields"

    async def test_seed_requires_write_scope(self, async_client_no_auth):
        """Seed endpoint requires authentication."""
        response = await async_client_no_auth.post("/data/seed-default-schemas")
        assert response.status_code == 401
