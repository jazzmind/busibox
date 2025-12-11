import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    """Schema for creating a new agent run."""

    agent_id: uuid.UUID = Field(description="Agent UUID to execute")
    workflow_id: Optional[uuid.UUID] = Field(None, description="Optional workflow UUID")
    input: Dict[str, Any] = Field(
        default_factory=dict, description="Input payload with 'prompt' and other fields"
    )
    agent_tier: str = Field(
        "simple",
        description="Execution tier: simple (30s/512MB), complex (5min/2GB), batch (30min/4GB)",
        pattern="^(simple|complex|batch)$",
    )


class RunRead(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    workflow_id: Optional[uuid.UUID]
    status: str
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]]
    events: List[Any]
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
