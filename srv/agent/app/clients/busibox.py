import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 0.5  # seconds


class BusiboxClient:
    """
    Thin HTTP client for Busibox APIs (search, data, RAG).

    Supports per-audience tokens so that each downstream service receives
    a correctly audience-scoped JWT.  Falls back to a single ``access_token``
    for backward compatibility (existing callers that pass one token).
    """

    def __init__(
        self,
        access_token: str,
        *,
        tokens_by_audience: Optional[Dict[str, str]] = None,
    ) -> None:
        self._default_token = access_token
        self._tokens: Dict[str, str] = tokens_by_audience or {}
        self._default_headers = {"Authorization": f"Bearer {self._default_token}"}

    def _headers_for(self, audience: str) -> Dict[str, str]:
        """Return auth headers using the audience-specific token if available."""
        token = self._tokens.get(audience, self._default_token)
        return {"Authorization": f"Bearer {token}"}

    async def request(
        self,
        method: str,
        path: str,
        json: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Generic HTTP request to data-api with automatic retry for transient errors.

        Retries on connection errors and 502/503/504 responses with exponential
        backoff (up to ``_MAX_RETRIES`` attempts).

        Args:
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g. "/data", "/data/{id}/query")
            json: Request body (for POST/PUT)
            params: Query parameters (for GET)
            timeout: Request timeout in seconds
        """
        base_url = str(settings.data_api_url).rstrip('/')
        url = f"{base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.request(
                        method=method,
                        url=url,
                        json=json,
                        params=params,
                        headers=self._headers_for("data-api"),
                        timeout=timeout,
                    )
                if resp.status_code not in _RETRYABLE_STATUS_CODES:
                    resp.raise_for_status()
                    return resp.json()

                last_exc = httpx.HTTPStatusError(
                    f"Server error {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_exc = exc

            wait = _RETRY_BACKOFF_BASE * (2 ** attempt)
            logger.warning(
                "Retrying %s %s (attempt %d/%d) after %.1fs: %s",
                method, path, attempt + 1, _MAX_RETRIES, wait, last_exc,
            )
            await asyncio.sleep(wait)

        raise last_exc  # type: ignore[misc]

    async def search(
        self,
        query: str,
        top_k: int = 10,
        limit: Optional[int] = None,
        mode: str = "hybrid",
        file_ids: Optional[List[str]] = None,
        rerank: bool = True,
        highlight: bool = False,
        mmr: bool = False,
        mmr_lambda: float = 0.5,
        expand_graph: bool = False,
    ) -> Dict[str, Any]:
        """
        Search documents with multiple modes.
        
        Args:
            query: Search query string
            top_k: Maximum number of results (default: 10, max: 50)
            mode: Search mode - "hybrid", "semantic", or "keyword" (default: "hybrid")
            file_ids: Optional list of file IDs to filter results
            rerank: Enable cross-encoder reranking (default: True)
            highlight: Enable search term highlighting (default: False)
            mmr: Enable Maximal Marginal Relevance for diversity (default: False)
            mmr_lambda: MMR lambda parameter for relevance vs diversity (default: 0.5)
            expand_graph: Expand results with graph entity context (default: False)
            
        Returns:
            Search response with results and metadata
        """
        result_limit = limit if limit is not None else top_k
        
        request_body: Dict[str, Any] = {
            "query": query,
            "limit": min(result_limit, 50),
            "mode": mode,
            "rerank": rerank,
        }
        
        if file_ids:
            request_body["filters"] = {"file_ids": file_ids}
        
        if highlight:
            request_body["highlight"] = True
            
        if mmr:
            request_body["mmr"] = True
            request_body["mmr_lambda"] = mmr_lambda

        if expand_graph:
            request_body["expand_graph"] = True
        
        base_url = str(settings.search_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/search",
                json=request_body,
                headers=self._headers_for("search-api"),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def data_document(self, path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Legacy file-based ingestion (for file paths).
        """
        base_url = str(settings.data_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/documents",
                json={"path": path, "metadata": metadata or {}},
                headers=self._headers_for("data-api"),
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()
    
    async def data_content(
        self,
        content: str,
        title: str,
        url: Optional[str] = None,
        folder: Optional[str] = None,
        library_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Data text/markdown content as a document.
        
        Used by web research workflows to store scraped content.
        
        Args:
            content: Text or markdown content to ingest
            title: Document title
            url: Optional source URL
            folder: Target folder name (e.g., 'personal-research')
            library_id: Target library ID (alternative to folder)
            metadata: Additional metadata
            
        Returns:
            Response with fileId, libraryId, status
        """
        base_url = str(settings.data_api_url).rstrip('/')
        
        payload: Dict[str, Any] = {
            "content": content,
            "title": title,
        }
        if url:
            payload["url"] = url
        if folder:
            payload["folder"] = folder
        if library_id:
            payload["library_id"] = library_id
        if metadata:
            payload["metadata"] = metadata
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/data/content",
                json=payload,
                headers=self._headers_for("data-api"),
                timeout=180,
            )
            resp.raise_for_status()
            return resp.json()

    async def rag_query(self, database: str, query: str, top_k: int = 5) -> Dict[str, Any]:
        base_url = str(settings.rag_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/databases/{database}/query",
                json={"query": query, "top_k": top_k},
                headers=self._headers_for("search-api"),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
