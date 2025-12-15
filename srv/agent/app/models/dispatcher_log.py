"""
Dispatcher decision logging model.

Records each dispatcher routing decision for accuracy measurement and debugging.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import CheckConstraint, Column, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DispatcherDecisionLog(Base):
    """
    Log of dispatcher routing decisions.
    
    Used for:
    - Measuring routing accuracy (SC-002: 95%+ target)
    - Debugging routing issues
    - Analyzing confidence score distribution
    - System improvement and optimization
    """
    __tablename__ = "dispatcher_decision_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    query_text: Mapped[str] = mapped_column(String(1000), nullable=False, comment="User query (truncated to 1000 chars)")
    selected_tools: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, comment="Tool names selected by dispatcher")
    selected_agents: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, comment="Agent IDs selected by dispatcher")
    confidence: Mapped[float] = mapped_column(
        Float, 
        nullable=False,
        comment="Confidence score 0-1"
    )
    reasoning: Mapped[str] = mapped_column(Text, nullable=False, comment="Explanation of routing decision")
    alternatives: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, comment="Alternative tools/agents suggested")
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True, comment="User who made the query")
    request_id: Mapped[str] = mapped_column(String(255), nullable=False, comment="Request correlation ID")
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_now, index=True)

    __table_args__ = (
        CheckConstraint('confidence >= 0 AND confidence <= 1', name='check_confidence_range'),
    )
    
    def __repr__(self) -> str:
        return f"<DispatcherDecisionLog(id={self.id}, user_id={self.user_id}, confidence={self.confidence}, timestamp={self.timestamp})>"





