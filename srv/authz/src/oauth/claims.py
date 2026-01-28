"""
Internal access-token claim contract.

Downstream services validate these tokens (via JWKS) and use:
- `sub` for user identity
- `roles[]` for data access filtering (RLS/Milvus partitions)
- `scope` for operation authorization (OAuth2 scopes aggregated from all roles)
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class RoleClaim(BaseModel):
    """
    Role claim for data access filtering.
    
    Contains only id and name - scopes are aggregated at the token level.
    """
    id: str
    name: str


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

    # OAuth2-style scopes (aggregated from all user roles)
    scope: str = ""

    # Role memberships for data access filtering (RLS/partitions)
    roles: List[RoleClaim] = Field(default_factory=list)
    
    # User email for display purposes
    # Included in access tokens so downstream apps can display user info
    email: Optional[str] = None