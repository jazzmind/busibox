"""Unit tests for token caching and exchange flow."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import TokenGrant
from app.schemas.auth import Principal, TokenExchangeResponse
from app.services.token_service import EXPIRY_REFRESH_BUFFER, get_or_exchange_token


def _now_naive() -> datetime:
    """Return timezone-naive UTC datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _future(minutes: int = 5) -> datetime:
    """Return a future datetime (timezone-naive for database compatibility)."""
    return _now_naive() + timedelta(minutes=minutes)


def _past(minutes: int = 5) -> datetime:
    """Return a past datetime (timezone-naive for database compatibility)."""
    return _now_naive() - timedelta(minutes=minutes)


def _principal(unique_id: str = None) -> Principal:
    """Create a principal with a unique subject to avoid cache conflicts."""
    if unique_id is None:
        unique_id = uuid.uuid4().hex[:8]
    return Principal(
        sub=f"user-{unique_id}",
        email=f"user-{unique_id}@example.com",
        roles=["user"],
        scopes=["search.read", "data.write"],
    )


@pytest.mark.asyncio
async def test_returns_cached_token_when_valid(monkeypatch, test_session: AsyncSession):
    """Test that a valid cached token is returned without calling exchange."""
    # Create a principal and matching cached token
    unique_id = uuid.uuid4().hex[:8]
    principal = _principal(unique_id)
    unique_token = f"cached-test-token-{unique_id}"
    
    # The token service adds "aud:{audience}" to the scopes key
    # For purpose="data", the audience is "data-api"
    # So the cache key scopes will be sorted(["search.read", "data.write", "aud:data-api"])
    cached_token = TokenGrant(
        subject=principal.sub,
        scopes=sorted(["aud:data-api", "data.write", "search.read"]),
        token=unique_token,
        expires_at=_future(60),  # Valid for 60 more minutes
    )
    test_session.add(cached_token)
    await test_session.commit()
    
    # Mock exchange_token to ensure it's not called (should use cached token)
    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("Should not exchange token when valid cached token exists")
    
    monkeypatch.setattr("app.services.token_service.exchange_token", should_not_be_called)

    token = await get_or_exchange_token(
        session=test_session,
        principal=principal,
        scopes=["data.write", "search.read"],  # order should be normalized
        purpose="data",
    )

    assert token.access_token == cached_token.token
    assert token.expires_at == cached_token.expires_at


@pytest.mark.asyncio
async def test_exchanges_when_expired(monkeypatch, test_session: AsyncSession):
    """Test that an expired token triggers a new exchange."""
    unique_id = uuid.uuid4().hex[:8]
    principal = _principal(unique_id)
    expired = TokenGrant(
        subject=principal.sub,
        scopes=["aud:data-api", "data.write", "search.read"],
        token=f"stale-token-{unique_id}",
        expires_at=_past(),
    )
    test_session.add(expired)
    await test_session.commit()

    async def fake_exchange(principal_arg: Principal, scopes: List[str], purpose: str) -> TokenExchangeResponse:
        return TokenExchangeResponse(
            access_token=f"fresh-token-{unique_id}",  # Use unique token
            token_type="bearer",
            expires_at=_future(30),
            scopes=scopes,
        )

    monkeypatch.setattr("app.services.token_service.exchange_token", fake_exchange)

    token = await get_or_exchange_token(
        session=test_session,
        principal=principal,
        scopes=["search.read", "data.write"],
        purpose="data",
    )

    assert token.access_token == f"fresh-token-{unique_id}"
    result = await test_session.execute(select(TokenGrant).where(TokenGrant.token == f"fresh-token-{unique_id}"))
    saved = result.scalars().first()
    assert saved is not None
    assert saved.subject == principal.sub


@pytest.mark.asyncio
async def test_refreshes_token_near_expiry(monkeypatch, test_session: AsyncSession):
    """Test that a token near expiry triggers a refresh."""
    unique_id = uuid.uuid4().hex[:8]
    principal = _principal(unique_id)
    # Near expiry: buffer is typically 60 seconds, so divide by 2 to be within buffer
    near_expiry_time = _now_naive() + EXPIRY_REFRESH_BUFFER / 2
    near_expiry = TokenGrant(
        subject=principal.sub,
        scopes=["aud:data-api", "data.write", "search.read"],
        token=f"almost-expired-{unique_id}",
        expires_at=near_expiry_time,
    )
    test_session.add(near_expiry)
    await test_session.commit()

    async def fake_exchange(_: Principal, scopes: List[str], purpose: str) -> TokenExchangeResponse:
        return TokenExchangeResponse(
            access_token="refreshed-token",
            token_type="bearer",
            expires_at=_future(45),
            scopes=scopes,
        )

    monkeypatch.setattr("app.services.token_service.exchange_token", fake_exchange)

    token = await get_or_exchange_token(
        session=test_session,
        principal=principal,
        scopes=["search.read", "data.write"],
        purpose="data",
    )

    assert token.access_token == "refreshed-token"
    result = await test_session.execute(
        select(TokenGrant).where(TokenGrant.token == "refreshed-token")
    )
    assert result.scalars().first() is not None
