"""
Internal access-token claim contract.

Downstream services validate these tokens (via JWKS) and use:
- `sub` for user identity
- `roles[]` for RBAC + document/library partition/RLS decisions (current compatibility shape)
- `scope` for coarse OAuth2-style permissioning
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class RoleClaim(BaseModel):
    id: str
    name: str
    permissions: List[str] = Field(default_factory=list)

    @field_validator("permissions")
    @classmethod
    def _normalize_permissions(cls, v: List[str]) -> List[str]:
        # stable ordering for deterministic tests/caches
        allowed = {"read", "create", "update", "delete"}
        out = [p for p in v if p in allowed]
        # de-dupe while preserving input order
        seen = set()
        deduped: List[str] = []
        for p in out:
            if p in seen:
                continue
            seen.add(p)
            deduped.append(p)
        return deduped


class AccessTokenClaims(BaseModel):
    # Standard registered claims
    iss: str
    sub: str
    aud: str
    exp: int
    iat: int
    nbf: Optional[int] = None
    jti: str

    # Token typing
    typ: str = "access"

    # OAuth2-style scopes
    scope: str = ""

    # Compatibility RBAC shape used by ingest/search today
    roles: List[RoleClaim] = Field(default_factory=list)

