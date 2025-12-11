from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class TokenExchangeRequest(BaseModel):
    scopes: List[str] = Field(default_factory=list)
    purpose: str = Field(
        ..., description="Description of the downstream purpose (e.g., search, ingest, rag)"
    )


class TokenExchangeResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    scopes: List[str]


class Principal(BaseModel):
    sub: str
    email: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    scopes: List[str] = Field(default_factory=list)
    token: Optional[str] = None
