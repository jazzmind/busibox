"""Unit tests for Busibox HTTP client."""
from typing import Any, Dict, List

import pytest

from app.clients.busibox import BusiboxClient


class _StubResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Dict[str, Any]:
        return self._payload


class _StubAsyncClient:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, json=None, headers=None, timeout=None):  # type: ignore[override]
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _StubResponse({"ok": True, "url": url, "json": json})


@pytest.mark.asyncio
async def test_search_attaches_bearer(monkeypatch):
    stub_client = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *args, **kwargs: stub_client)

    client = BusiboxClient("token-123")
    result = await client.search("hello", top_k=3)

    assert result["ok"] is True
    assert stub_client.calls[0]["headers"]["Authorization"] == "Bearer token-123"
    assert "/search" in stub_client.calls[0]["url"]
    assert stub_client.calls[0]["json"] == {"query": "hello", "top_k": 3}


@pytest.mark.asyncio
async def test_ingest_document_payload(monkeypatch):
    stub_client = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *args, **kwargs: stub_client)

    client = BusiboxClient("token-123")
    await client.ingest_document(path="/tmp/file.pdf", metadata={"source": "test"})

    call = stub_client.calls[0]
    assert call["json"]["path"] == "/tmp/file.pdf"
    assert call["json"]["metadata"] == {"source": "test"}
    assert call["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_rag_query(monkeypatch):
    stub_client = _StubAsyncClient()
    monkeypatch.setattr("app.clients.busibox.httpx.AsyncClient", lambda *args, **kwargs: stub_client)

    client = BusiboxClient("token-123")
    await client.rag_query(database="main", query="find", top_k=2)

    call = stub_client.calls[0]
    assert "/databases/main/query" in call["url"]
    assert call["json"] == {"query": "find", "top_k": 2}








