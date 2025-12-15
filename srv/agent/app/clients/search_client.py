"""HTTP client for Busibox Search API."""
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from app.config.settings import get_settings

settings = get_settings()


class SearchResult(BaseModel):
    """Individual search result."""
    file_id: str
    filename: Optional[str] = None
    chunk_index: int
    page_number: int
    text: str
    score: float
    metadata: Optional[Dict[str, Any]] = None
    highlights: Optional[List[str]] = None


class SearchResponse(BaseModel):
    """Response from search API."""
    results: List[SearchResult]
    total: int
    query: str
    mode: str
    reranked: Optional[bool] = None
    execution_time_ms: Optional[float] = None


class SearchClient:
    """
    HTTP client for Busibox Search API.
    
    Provides semantic, keyword, and hybrid search capabilities with reranking,
    highlighting, and role-based filtering.
    """

    def __init__(self, auth_token: Optional[str] = None):
        """
        Initialize search client.
        
        Args:
            auth_token: Bearer token for authentication (optional)
        """
        self.base_url = str(settings.search_api_url)
        self.auth_token = auth_token
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._client:
            await self._client.aclose()

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers with auth."""
        headers = {"Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    async def search(
        self,
        query: str,
        limit: int = 5,
        mode: str = "hybrid",
        file_ids: Optional[List[str]] = None,
        rerank: bool = True,
        highlight: bool = False,
        mmr: bool = False,
        mmr_lambda: float = 0.5,
    ) -> SearchResponse:
        """
        Search documents with multiple modes.
        
        Args:
            query: Search query string
            limit: Maximum number of results (default: 5, max: 50)
            mode: Search mode - "hybrid", "semantic", or "keyword" (default: "hybrid")
            file_ids: Optional list of file IDs to filter results
            rerank: Enable cross-encoder reranking (default: True)
            highlight: Enable search term highlighting (default: False)
            mmr: Enable Maximal Marginal Relevance for diversity (default: False)
            mmr_lambda: MMR lambda parameter for relevance vs diversity (default: 0.5)
            
        Returns:
            SearchResponse with results and metadata
            
        Raises:
            httpx.HTTPError: If request fails
        """
        request_body: Dict[str, Any] = {
            "query": query,
            "limit": min(limit, 50),  # Cap at 50
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

        if self._client:
            response = await self._client.post(
                f"{self.base_url}/search",
                json=request_body,
                headers=self._get_headers(),
            )
        else:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/search",
                    json=request_body,
                    headers=self._get_headers(),
                )
        
        response.raise_for_status()
        data = response.json()
        return SearchResponse(**data)

    async def health(self) -> Dict[str, Any]:
        """
        Check search API health.
        
        Returns:
            Health status dictionary
        """
        if self._client:
            response = await self._client.get(f"{self.base_url}/health")
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/health")
        
        response.raise_for_status()
        return response.json()





