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


class RunInvoke(BaseModel):
    """Schema for synchronous/programmatic agent invocation."""

    agent_id: Optional[uuid.UUID] = Field(None, description="Agent UUID to execute")
    agent_name: Optional[str] = Field(None, description="Agent name to execute (e.g. built-in name)")
    input: Dict[str, Any] = Field(
        default_factory=dict,
        description="Input payload with required 'prompt' and optional execution context",
    )
    response_schema: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional JSON Schema used to force deterministic structured output",
    )
    agent_tier: str = Field(
        "simple",
        description="Execution tier: simple (30s/512MB), complex (5min/2GB), batch (30min/4GB)",
        pattern="^(simple|complex|batch)$",
    )


class RunInvokeResponse(BaseModel):
    """Response for synchronous/programmatic invocation."""

    run_id: uuid.UUID
    status: str
    output: Optional[Any] = None
    error: Optional[str] = None


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


class ScheduleCreate(BaseModel):
    """Schema for creating a scheduled agent run."""
    
    agent_id: uuid.UUID = Field(description="Agent UUID to execute")
    input: Dict[str, Any] = Field(
        default_factory=dict, description="Input payload with 'prompt' and other fields"
    )
    cron: str = Field(
        description="Cron expression (5 fields: minute hour day month day_of_week)",
        pattern=r"^[\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+$",
    )
    agent_tier: str = Field(
        "simple",
        description="Execution tier: simple (30s/512MB), complex (5min/2GB), batch (30min/4GB)",
        pattern="^(simple|complex|batch)$",
    )
    scopes: List[str] = Field(
        default_factory=list,
        description="Required scopes for execution (defaults to agent.execute)",
    )
    purpose: str = Field(
        "scheduled-run",
        description="Purpose for token exchange",
    )


class ScheduleRead(BaseModel):
    """Schema for reading scheduled job information."""
    
    job_id: str = Field(description="Unique job identifier")
    agent_id: uuid.UUID = Field(description="Agent UUID being executed")
    cron: str = Field(description="Cron expression")
    principal_sub: str = Field(description="User who created the schedule")
    next_run_time: Optional[datetime] = Field(
        None, description="Next scheduled execution time"
    )
