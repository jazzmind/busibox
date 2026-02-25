"""Tests for expand_graph support in BusiboxClient.search() and document_search tool."""
from typing import Any, Dict, List, Optional

import pytest

from app.clients.busibox import BusiboxClient
from app.tools.document_search_tool import (
    DocumentSearchOutput,
    SearchResultItem,
    search_documents,
)


# =============================================================================
# Stubs
# =============================================================================

class _StubResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


class _StubAsyncClient:
    """Captures outgoing requests for assertion."""

    def __init__(self, response_payload: Optional[Dict[str, Any]] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._response = response_payload or {"ok": True}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _StubResponse(self._response)


# =============================================================================
# BusiboxClient.search() – expand_graph parameter
# =============================================================================

@pytest.mark.asyncio
async def test_search_passes_expand_graph_true(monkeypatch):
    stub = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *a, **kw: stub)

    client = BusiboxClient("tok")
    await client.search("test query", expand_graph=True)

    body = stub.calls[0]["json"]
    assert body["expand_graph"] is True


@pytest.mark.asyncio
async def test_search_omits_expand_graph_when_false(monkeypatch):
    stub = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *a, **kw: stub)

    client = BusiboxClient("tok")
    await client.search("test query", expand_graph=False)

    body = stub.calls[0]["json"]
    assert "expand_graph" not in body


@pytest.mark.asyncio
async def test_search_default_expand_graph_is_false(monkeypatch):
    stub = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *a, **kw: stub)

    client = BusiboxClient("tok")
    await client.search("test query")

    body = stub.calls[0]["json"]
    assert "expand_graph" not in body


# =============================================================================
# document_search tool – graph context handling
# =============================================================================

class _FakeBusiboxClient:
    """Fake client that returns a configurable search response."""

    def __init__(self, results: List[Dict], graph: Optional[Dict] = None):
        self._results = results
        self._graph = graph

    async def search(self, **kwargs):
        resp: Dict[str, Any] = {"results": self._results}
        if self._graph:
            resp["graph"] = self._graph
        return resp


class _FakeDeps:
    def __init__(self, client):
        self.busibox_client = client


class _FakeRunContext:
    def __init__(self, deps):
        self.deps = deps


@pytest.mark.asyncio
async def test_document_search_includes_graph_context():
    fake_client = _FakeBusiboxClient(
        results=[
            {"filename": "report.pdf", "text": "Sales grew 20%", "score": 0.9, "chunk_index": 0}
        ],
        graph={
            "graph_context": "Related entities: Alice (Person, mentioned in 3 search results).",
            "related_entities": [{"name": "Alice", "type": "Person", "relevance": 3}],
            "related_documents": [],
        },
    )
    ctx = _FakeRunContext(_FakeDeps(fake_client))

    output = await search_documents(ctx, query="sales report", limit=5)

    assert output.found is True
    assert output.graph_context is not None
    assert "Alice" in output.graph_context
    assert "Graph Context" in output.context


@pytest.mark.asyncio
async def test_document_search_no_graph_context_when_absent():
    fake_client = _FakeBusiboxClient(
        results=[
            {"filename": "doc.txt", "text": "Hello world", "score": 0.7, "chunk_index": 0}
        ],
        graph=None,
    )
    ctx = _FakeRunContext(_FakeDeps(fake_client))

    output = await search_documents(ctx, query="hello", limit=5)

    assert output.found is True
    assert output.graph_context is None
    assert "Graph Context" not in output.context


@pytest.mark.asyncio
async def test_document_search_empty_graph_context():
    fake_client = _FakeBusiboxClient(
        results=[
            {"filename": "doc.txt", "text": "Hello world", "score": 0.7, "chunk_index": 0}
        ],
        graph={"graph_context": "", "related_entities": [], "related_documents": []},
    )
    ctx = _FakeRunContext(_FakeDeps(fake_client))

    output = await search_documents(ctx, query="hello", limit=5)

    assert output.found is True
    assert output.graph_context is None
    assert "Graph Context" not in output.context


@pytest.mark.asyncio
async def test_document_search_no_results():
    fake_client = _FakeBusiboxClient(results=[])
    ctx = _FakeRunContext(_FakeDeps(fake_client))

    output = await search_documents(ctx, query="nonexistent", limit=5)

    assert output.found is False
    assert output.result_count == 0
    assert output.graph_context is None


@pytest.mark.asyncio
async def test_document_search_error_handling():
    class _FailingClient:
        async def search(self, **kwargs):
            raise ConnectionError("Search API unreachable")

    ctx = _FakeRunContext(_FakeDeps(_FailingClient()))

    output = await search_documents(ctx, query="test", limit=5)

    assert output.found is False
    assert output.error is not None
    assert "Search API unreachable" in output.error
