"""
Test client utilities for API testing.

Provides factories for creating test clients with different authentication levels.

Usage:
    from testing.clients import create_async_client, create_sync_client
    
    # In fixture:
    async def async_client(initialized_app, auth_client):
        async with create_async_client(app, auth_client) as client:
            yield client
"""

import os
from contextlib import asynccontextmanager, contextmanager
from typing import Optional, Dict, Any, TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from httpx import AsyncClient, Response
    from fastapi import FastAPI
    from .auth import AuthTestClient


@asynccontextmanager
async def create_async_client(
    app: "FastAPI",
    auth_client: Optional["AuthTestClient"] = None,
    audience: Optional[str] = None,
    base_url: str = "http://test",
    headers: Optional[Dict[str, str]] = None,
):
    """
    Create an async HTTP client for testing FastAPI apps.
    
    Args:
        app: FastAPI application instance
        auth_client: Optional AuthTestClient for authentication
        audience: JWT audience (default: derives from app or uses "test-api")
        base_url: Base URL for requests
        headers: Additional headers to include
    
    Yields:
        AsyncClient configured for testing
        
    Example:
        async with create_async_client(app, auth_client, audience="ingest-api") as client:
            response = await client.get("/health")
    """
    from httpx import AsyncClient, ASGITransport
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        # Add auth header if auth_client provided
        if auth_client:
            aud = audience or _guess_audience(app)
            token = auth_client.get_token(audience=aud)
            client.headers.update({"Authorization": f"Bearer {token}"})
        
        # Add any custom headers
        if headers:
            client.headers.update(headers)
        
        yield client


@asynccontextmanager
async def create_async_client_no_auth(
    app: "FastAPI",
    base_url: str = "http://test",
):
    """
    Create an async HTTP client without authentication.
    
    Use this to test that endpoints require authentication.
    
    Args:
        app: FastAPI application instance
        base_url: Base URL for requests
    
    Yields:
        AsyncClient without auth headers
    """
    from httpx import AsyncClient, ASGITransport
    
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    
    async with AsyncClient(transport=transport, base_url=base_url) as client:
        yield client


def create_sync_client(
    app: "FastAPI",
    auth_client: Optional["AuthTestClient"] = None,
    audience: Optional[str] = None,
):
    """
    Create a synchronous test client for FastAPI apps.
    
    Args:
        app: FastAPI application instance
        auth_client: Optional AuthTestClient for authentication
        audience: JWT audience
    
    Returns:
        TestClient instance
        
    Example:
        client = create_sync_client(app, auth_client)
        response = client.get("/health")
    """
    from fastapi.testclient import TestClient
    
    client = TestClient(app)
    
    if auth_client:
        aud = audience or _guess_audience(app)
        token = auth_client.get_token(audience=aud)
        client.headers.update({"Authorization": f"Bearer {token}"})
    
    return client


def _guess_audience(app: "FastAPI") -> str:
    """
    Try to guess the appropriate audience from the app.
    
    Looks at app title or falls back to "test-api".
    """
    title = getattr(app, "title", "").lower()
    
    if "ingest" in title:
        return "ingest-api"
    elif "search" in title:
        return "search-api"
    elif "agent" in title:
        return "agent-api"
    elif "auth" in title:
        return "authz-api"
    
    return "test-api"


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture
async def async_test_client(request):
    """
    Generic async test client fixture.
    
    Requires 'app' and optionally 'auth_client' fixtures to be defined.
    
    Usage:
        async def test_endpoint(async_test_client):
            response = await async_test_client.get("/health")
            assert response.status_code == 200
    """
    # Get app from test request
    app = request.getfixturevalue("app")
    
    # Try to get auth_client (optional)
    try:
        auth_client = request.getfixturevalue("auth_client")
    except pytest.FixtureLookupError:
        auth_client = None
    
    async with create_async_client(app, auth_client) as client:
        yield client

