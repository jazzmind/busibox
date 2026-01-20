from typing import Any, Dict, List, Optional

import httpx

from app.config.settings import get_settings

settings = get_settings()


class BusiboxClient:
    """
    Thin HTTP client for Busibox APIs (search, ingest, RAG).
    Attaches downstream bearer tokens obtained via token exchange.
    """

    def __init__(self, access_token: str) -> None:
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {self._token}"}

    async def search(
        self,
        query: str,
        top_k: int = 10,
        limit: Optional[int] = None,  # Alias for top_k
        mode: str = "hybrid",
        file_ids: Optional[List[str]] = None,
        rerank: bool = True,
        highlight: bool = False,
        mmr: bool = False,
        mmr_lambda: float = 0.5,
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
            
        Returns:
            Search response with results and metadata
        """
        # Use limit if provided, otherwise use top_k
        result_limit = limit if limit is not None else top_k
        
        request_body: Dict[str, Any] = {
            "query": query,
            "limit": min(result_limit, 50),  # Search API uses 'limit', cap at 50
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
        
        # Remove trailing slash from base URL to avoid double slashes
        base_url = str(settings.search_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/search",
                json=request_body,
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def ingest_document(self, path: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Remove trailing slash from base URL to avoid double slashes
        base_url = str(settings.ingest_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/documents",
                json={"path": path, "metadata": metadata or {}},
                headers=self._headers,
                timeout=60,
            )
            resp.raise_for_status()
            return resp.json()

    async def rag_query(self, database: str, query: str, top_k: int = 5) -> Dict[str, Any]:
        # Remove trailing slash from base URL to avoid double slashes
        base_url = str(settings.rag_api_url).rstrip('/')
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{base_url}/databases/{database}/query",
                json={"query": query, "top_k": top_k},
                headers=self._headers,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()
