import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
from jose import jwk, jwt
from jose.utils import base64url_decode

from app.config.settings import get_settings
from app.schemas.auth import Principal, TokenExchangeResponse

settings = get_settings()

CLAIM_LEEWAY_SECONDS = 30
TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


class JWKSCache:
    def __init__(self) -> None:
        self._jwks: Optional[Dict] = None
        self._fetched_at: Optional[float] = None
        self._ttl_seconds = 300

    async def get(self) -> Dict:
        now = time.time()
        if self._jwks and self._fetched_at and now - self._fetched_at < self._ttl_seconds:
            return self._jwks
        if not settings.auth_jwks_url:
            raise ValueError("auth_jwks_url not configured")
        async with httpx.AsyncClient() as client:
            resp = await client.get(str(settings.auth_jwks_url), timeout=10)
            resp.raise_for_status()
            self._jwks = resp.json()
            self._fetched_at = now
            return self._jwks


jwks_cache = JWKSCache()


async def _verify_signature(token: str, jwks: Dict) -> Dict:
    headers = jwt.get_unverified_header(token)
    kid = headers.get("kid")
    if not kid:
        raise jwt.JWTError("kid missing from token header")

    key_data = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
    if not key_data:
        raise jwt.JWTError("matching jwk not found")

    public_key = jwk.construct(key_data)
    message, encoded_sig = token.rsplit(".", 1)
    decoded_sig = base64url_decode(encoded_sig.encode())
    if not public_key.verify(message.encode(), decoded_sig):
        raise jwt.JWTError("signature verification failed")

    return jwt.get_unverified_claims(token)


def _validate_claims(claims: Dict) -> None:
    now = datetime.now(timezone.utc)
    leeway = timedelta(seconds=CLAIM_LEEWAY_SECONDS)

    exp = claims.get("exp")
    if exp is not None:
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        if exp_dt <= now - leeway:
            raise jwt.ExpiredSignatureError("token expired")

    nbf = claims.get("nbf")
    if nbf is not None:
        nbf_dt = datetime.fromtimestamp(nbf, tz=timezone.utc)
        if nbf_dt > now + leeway:
            raise jwt.JWTError("token not yet valid")

    iat = claims.get("iat")
    if iat is not None:
        iat_dt = datetime.fromtimestamp(iat, tz=timezone.utc)
        if iat_dt > now + leeway:
            raise jwt.JWTError("token issued in the future")


def _extract_scopes(claims: Dict) -> List[str]:
    if "scope" in claims and isinstance(claims["scope"], str):
        return claims["scope"].split()
    if "scp" in claims and isinstance(claims["scp"], list):
        return [str(scope) for scope in claims["scp"]]
    return []


async def validate_bearer(token: str) -> Principal:
    jwks = await jwks_cache.get()
    claims = await _verify_signature(token, jwks)

    _validate_claims(claims)

    if settings.auth_issuer and claims.get("iss") != settings.auth_issuer:
        raise jwt.JWTError(f"issuer mismatch: expected {settings.auth_issuer}, got {claims.get('iss')}")

    audience_claim = claims.get("aud")
    if settings.auth_audience:
        if isinstance(audience_claim, list):
            if settings.auth_audience not in audience_claim:
                raise jwt.JWTError(f"audience mismatch: expected {settings.auth_audience} in {audience_claim}")
        elif audience_claim and audience_claim != settings.auth_audience:
            raise jwt.JWTError(f"audience mismatch: expected {settings.auth_audience}, got {audience_claim}")

    try:
        sub = claims["sub"]
    except KeyError as exc:
        raise jwt.JWTError("sub missing") from exc

    principal = Principal(
        sub=sub,
        scopes=_extract_scopes(claims),
        roles=claims.get("roles", []),
        email=claims.get("email"),
        token=token,
    )
    return principal


async def exchange_token(
    principal: Principal, scopes: List[str], purpose: str
) -> TokenExchangeResponse:
    """
    Exchange a user token for a downstream token using OAuth2 token exchange (RFC 8693 style).
    Tokens are audience-bound to a single downstream service.
    """
    audience = _audience_for_purpose(purpose, scopes)
    payload = {
        "grant_type": TOKEN_EXCHANGE_GRANT,
        "client_id": settings.auth_client_id,
        "client_secret": settings.auth_client_secret,
        "scope": " ".join(scopes),
        "audience": audience,
        "requested_subject": principal.sub,
        "requested_purpose": purpose,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(str(settings.auth_token_url), data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()

    expires_in = data.get("expires_in", 3600)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return TokenExchangeResponse(
        access_token=data["access_token"],
        token_type=data.get("token_type", "bearer"),
        expires_at=expires_at,
        scopes=scopes,
    )


def _audience_for_purpose(purpose: str, scopes: List[str]) -> str:
    """
    Infer the downstream audience from purpose and/or scopes.

    We keep this mapping local to agent-server so we don't have to change its DB schema
    (TokenGrant is keyed on scopes), while still ensuring tokens are audience-bound.
    """
    p = (purpose or "").lower()
    if "ingest" in p:
        return "ingest-api"
    if "search" in p or "rag" in p:
        return "search-api"
    # fallback: infer by scope prefix
    for s in scopes:
        if s.startswith("ingest."):
            return "ingest-api"
        if s.startswith("search."):
            return "search-api"
    return "agent-api"
