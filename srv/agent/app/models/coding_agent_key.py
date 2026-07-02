"""
Coding agent virtual-key index.

Busibox issues a LiteLLM virtual key (via /key/generate) per coding agent x
developer (e.g. Claude Code, OpenCode, Hermes, Pi) instead of handing out the
shared LiteLLM master key. LiteLLM's own key metadata (budget, models
allow-list, spend, status) remains the source of truth for a key's live
state, retrieved by key_alias via LiteLLM's /key/list. This table is only a
small local index of "which key aliases exist and what Busibox calls them" —
it intentionally does NOT store the raw key value (shown once at creation
and not recoverable afterward, matching standard credential-issuance UX).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CodingAgentKey(Base):
    """Local index of LiteLLM virtual keys issued to coding agents."""
    __tablename__ = "coding_agent_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False, index=True, comment="e.g. claude-code, opencode, hermes, pi-dev")
    developer: Mapped[str] = mapped_column(String(255), nullable=True, comment="Developer username, for per-developer keys")
    key_alias: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, comment="Matches the alias registered with LiteLLM via /key/generate")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)
    created_by: Mapped[str] = mapped_column(String(255), nullable=True, comment="Admin principal who issued the key")
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True, comment="Soft-delete marker; actual revocation calls LiteLLM's /key/delete")

    def __repr__(self) -> str:
        return f"<CodingAgentKey(agent_name={self.agent_name}, key_alias={self.key_alias})>"
