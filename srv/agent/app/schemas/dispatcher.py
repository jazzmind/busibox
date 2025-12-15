"""
Pydantic schemas for dispatcher routing requests and responses.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class FileAttachment(BaseModel):
    """File attachment metadata."""
    name: str
    type: str
    url: str


class UserSettings(BaseModel):
    """User's enabled/disabled tool and agent settings."""
    enabled_tools: List[str] = Field(default_factory=list)
    enabled_agents: List[str] = Field(default_factory=list)


class DispatcherRequest(BaseModel):
    """Request to dispatcher for query routing."""
    query: str = Field(..., min_length=1, max_length=1000, description="Natural language query from user")
    available_tools: List[str] = Field(default_factory=list, description="List of tool names available to user")
    available_agents: List[str] = Field(default_factory=list, description="List of agent IDs available to user")
    attachments: List[FileAttachment] = Field(default_factory=list, description="File attachments with the query")
    user_settings: Optional[UserSettings] = Field(default=None, description="User's enabled/disabled settings")


class RoutingDecision(BaseModel):
    """Dispatcher's routing decision."""
    selected_tools: List[str] = Field(default_factory=list, description="Tool names selected for routing")
    selected_agents: List[str] = Field(default_factory=list, description="Agent IDs selected for routing")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score for routing decision (0-1)")
    reasoning: str = Field(..., description="Explanation of why tools/agents were selected")
    alternatives: List[str] = Field(default_factory=list, description="Alternative tools/agents suggested")
    requires_disambiguation: bool = Field(..., description="True if confidence < 0.7")
    
    @field_validator('requires_disambiguation', mode='before')
    @classmethod
    def set_requires_disambiguation(cls, v: Any, info: Any) -> bool:
        """Automatically set requires_disambiguation based on confidence."""
        if v is not None:
            return v
        # If not provided, calculate from confidence
        confidence = info.data.get('confidence', 1.0)
        return confidence < 0.7


class ExecutionStep(BaseModel):
    """Single step in execution plan."""
    step: int = Field(..., ge=1, description="Step number (1-indexed)")
    type: str = Field(..., pattern="^(tool|agent)$", description="Step type: tool or agent")
    name: str = Field(..., description="Tool name or agent ID")
    input: Dict[str, Any] = Field(default_factory=dict, description="Input for this step")


class DispatcherResponse(BaseModel):
    """Response from dispatcher with routing decision."""
    routing_decision: RoutingDecision
    execution_plan: Optional[List[ExecutionStep]] = Field(default=None, description="Sequential execution plan (optional)")





