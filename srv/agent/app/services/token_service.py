from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import exchange_token
from app.models.domain import TokenGrant
from app.schemas.auth import Principal, TokenExchangeResponse

EXPIRY_REFRESH_BUFFER = timedelta(seconds=60)


def _normalize_scopes(scopes: List[str]) -> List[str]:
    """Sort scopes to ensure consistent cache lookups."""
    return sorted(set(scopes))


async def get_or_exchange_token(
    session: AsyncSession, principal: Principal, scopes: List[str], purpose: str
) -> TokenExchangeResponse:
    """
    Fetch a cached downstream token if valid; otherwise perform exchange and persist.
    """
    now = datetime.now(timezone.utc)
    scopes_key = _normalize_scopes(scopes)

    stmt = (
        select(TokenGrant)
        .where(
            and_(
                TokenGrant.subject == principal.sub,
                TokenGrant.scopes == scopes_key,
                TokenGrant.expires_at > now + EXPIRY_REFRESH_BUFFER,
            )
        )
        .order_by(TokenGrant.expires_at.desc())
    )
    result = await session.execute(stmt)
    grant = result.scalars().first()
    if grant:
        return TokenExchangeResponse(
            access_token=grant.token,
            token_type="bearer",
            expires_at=grant.expires_at,
            scopes=scopes_key,
        )

    exchanged = await exchange_token(principal, scopes=scopes_key, purpose=purpose)
    record = TokenGrant(
        subject=principal.sub,
        scopes=scopes_key,
        token=exchanged.access_token,
        expires_at=exchanged.expires_at,
    )
    session.add(record)
    await session.commit()
    return exchanged
