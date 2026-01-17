"""
Pydantic schemas for dispatcher routing requests and responses.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

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


# Streaming event types for real-time dispatcher updates
StreamEventType = Literal[
    "planning",      # Initial analysis of the query
    "routing",       # Routing decision made
    "tool_start",    # Starting a tool execution
    "tool_result",   # Tool execution completed
    "agent_start",   # Starting an agent execution
    "agent_result",  # Agent execution completed
    "synthesis",     # Synthesizing results
    "suggestion",    # Follow-up suggestions (non-blocking)
    "content",       # Streaming content chunk
    "complete",      # Execution complete
    "error",         # Error occurred
]


class DispatcherStreamEvent(BaseModel):
    """
    Event emitted during streaming dispatcher execution.
    
    Events provide real-time feedback about what the dispatcher is doing,
    including planning, tool/agent execution, and result synthesis.
    """
    type: StreamEventType = Field(..., description="Type of event")
    message: str = Field(..., description="Human-readable message describing the event")
    data: Optional[Dict[str, Any]] = Field(default=None, description="Additional event data")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Event timestamp")
    
    def to_sse(self) -> str:
        """Convert event to Server-Sent Events format."""
        import json
        event_data = self.model_dump(mode="json")
        return f"data: {json.dumps(event_data)}\n\n"


class StreamingDispatcherRequest(BaseModel):
    """Request for streaming dispatcher execution."""
    query: str = Field(..., min_length=1, max_length=2000, description="Natural language query from user")
    available_tools: List[str] = Field(default_factory=list, description="List of tool names available to user")
    available_agents: List[str] = Field(default_factory=list, description="List of agent IDs available to user")
    attachments: List[FileAttachment] = Field(default_factory=list, description="File attachments with the query")
    user_settings: Optional[UserSettings] = Field(default=None, description="User's enabled/disabled settings")
    conversation_history: Optional[List[Dict[str, str]]] = Field(default=None, description="Previous conversation messages")
    model: Optional[str] = Field(default=None, description="Model to use for execution")








