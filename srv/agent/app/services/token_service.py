from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import _audience_for_purpose, exchange_token
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
    # Convert to naive datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE comparison
    now_naive = now.replace(tzinfo=None)
    
    # Tokens are audience-bound; incorporate inferred audience into the cache key
    # without changing the DB schema by adding a pseudo-scope marker.
    audience = _audience_for_purpose(purpose, scopes)
    scopes_key = _normalize_scopes(scopes + [f"aud:{audience}"])
    scopes_out = _normalize_scopes(scopes)

    stmt = (
        select(TokenGrant)
        .where(
            and_(
                TokenGrant.subject == principal.sub,
                cast(TokenGrant.scopes, JSONB).op('@>')(cast(scopes_key, JSONB)),
                cast(TokenGrant.scopes, JSONB).op('<@')(cast(scopes_key, JSONB)),
                TokenGrant.expires_at > now_naive + EXPIRY_REFRESH_BUFFER,
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
            scopes=scopes_out,
        )

    exchanged = await exchange_token(principal, scopes=scopes_out, purpose=purpose)
    
    # Convert expires_at to naive datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE
    expires_at_naive = exchanged.expires_at.replace(tzinfo=None) if exchanged.expires_at.tzinfo else exchanged.expires_at
    
    record = TokenGrant(
        subject=principal.sub,
        scopes=scopes_key,
        token=exchanged.access_token,
        expires_at=expires_at_naive,
    )
    session.add(record)
    await session.commit()
    # Exchange function returns requested scopes; override to avoid leaking pseudo marker.
    return TokenExchangeResponse(
        access_token=exchanged.access_token,
        token_type=exchanged.token_type,
        expires_at=exchanged.expires_at,
        scopes=scopes_out,
    )
