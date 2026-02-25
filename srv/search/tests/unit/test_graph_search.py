"""Unit tests for GraphSearchService.

Tests the expand_context() context-building logic and service initialization
without requiring a live Neo4j connection.
"""

import pytest

from services.graph_search import GraphSearchService


# =============================================================================
# Initialization and availability
# =============================================================================

class TestGraphSearchServiceInit:
    def test_default_not_available(self):
        svc = GraphSearchService()
        assert svc.available is False

    def test_custom_config(self):
        svc = GraphSearchService(config={
            "neo4j_uri": "bolt://localhost:7687",
            "neo4j_user": "neo4j",
            "neo4j_password": "test",
        })
        assert svc.available is False  # not connected yet

    @pytest.mark.asyncio
    async def test_expand_context_returns_empty_when_unavailable(self):
        svc = GraphSearchService()
        result = await svc.expand_context(
            document_ids=["doc-1"],
            user_id="user-1",
        )
        assert result == {
            "related_entities": [],
            "related_documents": [],
            "graph_context": "",
        }

    @pytest.mark.asyncio
    async def test_expand_context_returns_empty_for_empty_doc_ids(self):
        svc = GraphSearchService()
        result = await svc.expand_context(
            document_ids=[],
            user_id="user-1",
        )
        assert result["related_entities"] == []
        assert result["graph_context"] == ""

    @pytest.mark.asyncio
    async def test_find_related_entities_returns_empty_when_unavailable(self):
        svc = GraphSearchService()
        result = await svc.find_related_entities(
            query="test query",
            user_id="user-1",
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_find_path_returns_empty_when_unavailable(self):
        svc = GraphSearchService()
        result = await svc.find_path(
            from_entity="Alice",
            to_entity="Bob",
            user_id="user-1",
        )
        assert result == {"nodes": [], "relationships": []}

    @pytest.mark.asyncio
    async def test_graph_query_returns_empty_when_unavailable(self):
        svc = GraphSearchService()
        result = await svc.graph_query(
            query_text="find things",
            user_id="user-1",
        )
        assert result == {"nodes": [], "edges": [], "query": "find things"}


# =============================================================================
# expand_context() graph_context text building (with mocked Neo4j session)
# =============================================================================

class _MockRecord:
    """Simulates a Neo4j async record."""
    def __init__(self, data):
        self._data = data

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)


class _MockResult:
    """Async iterator over mock records."""
    def __init__(self, records):
        self._records = records
        self._idx = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self._records):
            raise StopAsyncIteration
        rec = self._records[self._idx]
        self._idx += 1
        return rec


class _MockSession:
    def __init__(self, records):
        self._records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def run(self, cypher, params=None):
        return _MockResult(self._records)


class _MockDriver:
    def __init__(self, records):
        self._records = records

    def session(self):
        return _MockSession(self._records)


class TestExpandContextTextBuilding:
    def _make_service(self, records):
        svc = GraphSearchService()
        svc._available = True
        svc._driver = _MockDriver(records)
        return svc

    @pytest.mark.asyncio
    async def test_single_entity_with_type(self):
        svc = self._make_service([
            _MockRecord({
                "entity": {"name": "Alice", "entity_type": "Person"},
                "doc_count": 1,
                "other_documents": [],
            }),
        ])
        result = await svc.expand_context(["doc-1"], "user-1")
        assert len(result["related_entities"]) == 1
        assert result["related_entities"][0]["name"] == "Alice"
        assert result["related_entities"][0]["type"] == "Person"
        assert "Alice (Person)" in result["graph_context"]

    @pytest.mark.asyncio
    async def test_entity_mentioned_in_multiple_results(self):
        svc = self._make_service([
            _MockRecord({
                "entity": {"name": "Python", "entity_type": "Technology"},
                "doc_count": 3,
                "other_documents": [],
            }),
        ])
        result = await svc.expand_context(["d1", "d2", "d3"], "user-1")
        ctx = result["graph_context"]
        assert "Python" in ctx
        assert "mentioned in 3 search results" in ctx

    @pytest.mark.asyncio
    async def test_entity_with_single_mention_no_count_text(self):
        svc = self._make_service([
            _MockRecord({
                "entity": {"name": "Alice", "entity_type": "Person"},
                "doc_count": 1,
                "other_documents": [],
            }),
        ])
        result = await svc.expand_context(["d1"], "user-1")
        assert "mentioned in" not in result["graph_context"]

    @pytest.mark.asyncio
    async def test_related_documents_section(self):
        svc = self._make_service([
            _MockRecord({
                "entity": {"name": "TensorFlow", "entity_type": "Technology"},
                "doc_count": 2,
                "other_documents": [
                    {"node_id": "doc-extra-1", "name": "ML Guide.pdf"},
                ],
            }),
        ])
        result = await svc.expand_context(["doc-1", "doc-2"], "user-1")
        assert len(result["related_documents"]) == 1
        assert result["related_documents"][0]["name"] == "ML Guide.pdf"
        ctx = result["graph_context"]
        assert "Related documents not in search results" in ctx
        assert "ML Guide.pdf" in ctx

    @pytest.mark.asyncio
    async def test_shared_entities_across_documents(self):
        svc = self._make_service([
            _MockRecord({
                "entity": {"name": "Alice", "entity_type": "Person"},
                "doc_count": 1,
                "other_documents": [
                    {"node_id": "doc-extra", "name": "Memo.pdf"},
                ],
            }),
            _MockRecord({
                "entity": {"name": "Bob", "entity_type": "Person"},
                "doc_count": 1,
                "other_documents": [
                    {"node_id": "doc-extra", "name": "Memo.pdf"},
                ],
            }),
        ])
        result = await svc.expand_context(["doc-1"], "user-1")
        rd = result["related_documents"]
        assert len(rd) == 1
        assert rd[0]["id"] == "doc-extra"
        shared = rd[0].get("shared_entities", [])
        assert "Alice" in shared
        assert "Bob" in shared
        assert "shares 2 entities" in result["graph_context"]

    @pytest.mark.asyncio
    async def test_no_entities_returns_empty_context(self):
        svc = self._make_service([])
        result = await svc.expand_context(["doc-1"], "user-1")
        assert result["graph_context"] == ""
        assert result["related_entities"] == []
        assert result["related_documents"] == []

    @pytest.mark.asyncio
    async def test_max_entities_in_context_text(self):
        """Only the top 8 entities should appear in the context text."""
        records = [
            _MockRecord({
                "entity": {"name": f"Entity-{i}", "entity_type": "Keyword"},
                "doc_count": 10 - i,
                "other_documents": [],
            })
            for i in range(12)
        ]
        svc = self._make_service(records)
        result = await svc.expand_context(["doc-1"], "user-1")
        assert len(result["related_entities"]) == 12
        # Context text should only mention up to 8
        ctx = result["graph_context"]
        for i in range(8):
            assert f"Entity-{i}" in ctx
        for i in range(8, 12):
            assert f"Entity-{i}" not in ctx

    @pytest.mark.asyncio
    async def test_max_related_documents_in_context(self):
        """Only up to 5 related documents appear in context text."""
        records = [
            _MockRecord({
                "entity": {"name": f"Entity-{i}", "entity_type": "Keyword"},
                "doc_count": 1,
                "other_documents": [
                    {"node_id": f"extra-doc-{i}", "name": f"Doc {i}.pdf"},
                ],
            })
            for i in range(8)
        ]
        svc = self._make_service(records)
        result = await svc.expand_context(["doc-1"], "user-1")
        assert len(result["related_documents"]) == 8
        ctx = result["graph_context"]
        # Only first 5 should appear
        for i in range(5):
            assert f"Doc {i}.pdf" in ctx
        for i in range(5, 8):
            assert f"Doc {i}.pdf" not in ctx

    @pytest.mark.asyncio
    async def test_driver_error_returns_empty(self):
        """If the driver raises an exception, graceful fallback."""
        svc = GraphSearchService()
        svc._available = True

        class _BadDriver:
            def session(self):
                raise RuntimeError("Neo4j down")

        svc._driver = _BadDriver()
        result = await svc.expand_context(["doc-1"], "user-1")
        assert result["graph_context"] == ""
        assert result["related_entities"] == []
