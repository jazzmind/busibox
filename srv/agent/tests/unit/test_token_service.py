"""Unit tests for token caching and exchange flow."""
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import TokenGrant
from app.schemas.auth import Principal, TokenExchangeResponse
from app.services.token_service import EXPIRY_REFRESH_BUFFER, get_or_exchange_token


def _future(minutes: int = 5) -> datetime:
    return datetime.now(timezone.utc) + timedelta(minutes=minutes)


def _past(minutes: int = 5) -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


def _principal() -> Principal:
    return Principal(
        sub="user-123",
        email="user@example.com",
        roles=["user"],
        scopes=["search.read", "ingest.write"],
    )


@pytest.mark.asyncio
async def test_returns_cached_token_when_valid(monkeypatch, test_session: AsyncSession, test_token: TokenGrant, mock_principal: Principal):
    # Mock exchange_token to ensure it's not called (should use cached token)
    async def should_not_be_called(*args, **kwargs):
        raise AssertionError("Should not exchange token when valid cached token exists")
    
    monkeypatch.setattr("app.services.token_service.exchange_token", should_not_be_called)

    token = await get_or_exchange_token(
        session=test_session,
        principal=mock_principal,
        scopes=["ingest.write", "search.read"],  # order should be normalized
        purpose="ingest",
    )

    assert token.access_token == test_token.token
    assert token.expires_at == test_token.expires_at


@pytest.mark.asyncio
async def test_exchanges_when_expired(monkeypatch, test_session: AsyncSession):
    principal = _principal()
    expired = TokenGrant(
        subject=principal.sub,
        scopes=["aud:ingest-api", "ingest.write", "search.read"],
        token="stale-token",
        expires_at=_past(),
    )
    test_session.add(expired)
    await test_session.commit()

    async def fake_exchange(_: Principal, scopes: List[str], purpose: str) -> TokenExchangeResponse:
        return TokenExchangeResponse(
            access_token="fresh-token",
            token_type="bearer",
            expires_at=_future(30),
            scopes=scopes,
        )

    monkeypatch.setattr("app.services.token_service.exchange_token", fake_exchange)

    token = await get_or_exchange_token(
        session=test_session,
        principal=principal,
        scopes=["search.read", "ingest.write"],
        purpose="ingest",
    )

    assert token.access_token == "fresh-token"
    result = await test_session.execute(select(TokenGrant).where(TokenGrant.token == "fresh-token"))
    saved = result.scalars().first()
    assert saved is not None
    assert saved.subject == principal.sub


@pytest.mark.asyncio
async def test_refreshes_token_near_expiry(monkeypatch, test_session: AsyncSession):
    principal = _principal()
    near_expiry = TokenGrant(
        subject=principal.sub,
        scopes=["aud:ingest-api", "ingest.write", "search.read"],
        token="almost-expired",
        expires_at=datetime.now(timezone.utc) + EXPIRY_REFRESH_BUFFER / 2,
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
        scopes=["search.read", "ingest.write"],
        purpose="ingest",
    )

    assert token.access_token == "refreshed-token"
    result = await test_session.execute(
        select(TokenGrant).where(TokenGrant.token == "refreshed-token")
    )
    assert result.scalars().first() is not None



