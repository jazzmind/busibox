"""
OAuth2-aligned request/response contracts for Busibox authz.

Authz supports:
- OAuth2 client credentials: grant_type=client_credentials
- OAuth2 token exchange (RFC 8693 style): grant_type=urn:ietf:params:oauth:grant-type:token-exchange

We intentionally keep the response shape close to RFC 6749/8693.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

TOKEN_EXCHANGE_GRANT = "urn:ietf:params:oauth:grant-type:token-exchange"


class OAuthTokenRequest(BaseModel):
    grant_type: str = Field(..., description="OAuth2 grant type")

    # OAuth2 client authentication (body form for now; HTTP Basic can be added later)
    client_id: str = Field(..., min_length=1)
    client_secret: str = Field(..., min_length=1)

    # Requested token restrictions
    scope: str = Field("", description="Space-delimited OAuth2 scopes")
    audience: Optional[str] = Field(None, description="Requested audience/service identifier (e.g. ingest-api)")

    # Token exchange (OBO) inputs
    subject_token: Optional[str] = Field(
        None,
        description="RFC8693 subject_token (optional; not relied upon initially for ai-portal)",
    )
    subject_token_type: Optional[str] = Field(None, description="RFC8693 subject_token_type")

    # Compatibility with existing agent token-exchange client code
    requested_subject: Optional[str] = Field(None, description="User ID (uuid) to mint token for")
    requested_purpose: Optional[str] = Field(None, description="Purpose label (audit/debug)")

    @field_validator("scope")
    @classmethod
    def _normalize_scope(cls, v: str) -> str:
        if not v:
            return ""
        parts = [p for p in v.split(" ") if p]
        # de-dupe while preserving order
        seen = set()
        out: List[str] = []
        for p in parts:
            if p in seen:
                continue
            seen.add(p)
            out.append(p)
        return " ".join(out)


class OAuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(..., ge=1)
    scope: str = ""

    # RFC 8693 fields
    issued_token_type: Optional[str] = Field(
        None, description="RFC8693 issued_token_type (e.g. urn:ietf:params:oauth:token-type:access_token)"
    )


class SyncRole(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


class SyncUser(BaseModel):
    """
    Minimal user+RBAC sync payload from ai-portal to authz.

    Authz becomes the RBAC authority over time; short-term this sync avoids duplicating
    RBAC logic in every downstream service.
    """

    user_id: str
    email: str
    status: Optional[str] = None

    # Role assignments
    roles: List[SyncRole] = Field(default_factory=list)
    user_role_ids: List[str] = Field(default_factory=list)

    # External IdP claims (optional)
    idp_provider: Optional[str] = None
    idp_tenant_id: Optional[str] = None
    idp_object_id: Optional[str] = None
    idp_roles: List[str] = Field(default_factory=list)
    idp_groups: List[str] = Field(default_factory=list)

