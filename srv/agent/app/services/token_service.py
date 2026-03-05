import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import and_, cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import _audience_for_purpose, exchange_token
from app.models.domain import TokenGrant
from app.schemas.auth import Principal, TokenExchangeResponse

logger = logging.getLogger(__name__)

EXPIRY_REFRESH_BUFFER = timedelta(seconds=60)


def _normalize_scopes(scopes: List[str]) -> List[str]:
    """Sort scopes to ensure consistent cache lookups."""
    return sorted(set(scopes))


async def _do_get_or_exchange(
    session: AsyncSession, principal: Principal, scopes_key: List[str], scopes_out: List[str], purpose: str, now_naive
) -> TokenExchangeResponse:
    """Core cache-or-exchange logic using the provided session."""
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

    expires_at_naive = exchanged.expires_at.replace(tzinfo=None) if exchanged.expires_at.tzinfo else exchanged.expires_at

    record = TokenGrant(
        subject=principal.sub,
        scopes=scopes_key,
        token=exchanged.access_token,
        expires_at=expires_at_naive,
    )
    session.add(record)
    await session.commit()
    return TokenExchangeResponse(
        access_token=exchanged.access_token,
        token_type=exchanged.token_type,
        expires_at=exchanged.expires_at,
        scopes=scopes_out,
    )


async def get_or_exchange_token(
    session: Optional[AsyncSession], principal: Principal, scopes: List[str], purpose: str
) -> TokenExchangeResponse:
    """
    Fetch a cached downstream token if valid; otherwise perform exchange and persist.

    Uses the caller-provided session when it is still usable.  If the session
    has been closed (common during streaming responses where FastAPI tears down
    the dependency-injected session before the generator finishes), falls back
    to a fresh standalone session so the token cache still works.
    """
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    audience = _audience_for_purpose(purpose, scopes)
    scopes_key = _normalize_scopes(scopes + [f"aud:{audience}"])
    scopes_out = _normalize_scopes(scopes)

    # Try the caller-provided session first
    if session is not None:
        try:
            return await _do_get_or_exchange(session, principal, scopes_key, scopes_out, purpose, now_naive)
        except Exception as exc:
            if "closed" in str(exc).lower() or "can't be called" in str(exc).lower():
                logger.debug("Caller session unusable (%s), opening dedicated session", exc)
            else:
                raise

    # Fallback: open a dedicated short-lived session
    from app.db.session import get_session_context

    async with get_session_context() as fresh_session:
        return await _do_get_or_exchange(fresh_session, principal, scopes_key, scopes_out, purpose, now_naive)
