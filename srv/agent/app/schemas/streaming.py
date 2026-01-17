"""
Streaming event types for agentic dispatcher.

Defines the event schema for real-time streaming of thoughts, tool calls,
and content from the dispatcher and all nested agents.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


class StreamEvent(BaseModel):
    """
    Event streamed from dispatcher/agents to the frontend.
    
    All layers (dispatcher, agents, tools) use this same schema to stream
    their thoughts and outputs directly to the user.
    """
    
    type: Literal[
        "thought",      # Agent's reasoning/explanation
        "tool_start",   # Starting a tool execution
        "tool_result",  # Tool completed with result
        "content",      # Final response content (streams to chat message)
        "complete",     # Execution finished
        "error",        # Error occurred
    ]
    
    source: str = Field(
        description="Source of the event: 'dispatcher', 'web_search_agent', 'web_scraper', etc."
    )
    
    message: str = Field(
        description="Human-readable message, markdown formatted"
    )
    
    data: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Additional structured data (tool results, metadata, etc.)"
    )
    
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When this event was created"
    )
    
    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


def thought(source: str, message: str, data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create a thought event."""
    return StreamEvent(type="thought", source=source, message=message, data=data)


def tool_start(source: str, message: str, data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create a tool_start event."""
    return StreamEvent(type="tool_start", source=source, message=message, data=data)


def tool_result(source: str, message: str, data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create a tool_result event."""
    return StreamEvent(type="tool_result", source=source, message=message, data=data)


def content(source: str, message: str, data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create a content event."""
    return StreamEvent(type="content", source=source, message=message, data=data)


def complete(source: str, message: str = "Done!", data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create a complete event."""
    return StreamEvent(type="complete", source=source, message=message, data=data)


def error(source: str, message: str, data: Optional[Dict[str, Any]] = None) -> StreamEvent:
    """Helper to create an error event."""
    return StreamEvent(type="error", source=source, message=message, data=data)
