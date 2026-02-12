import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ContextCompressionConfig(BaseModel):
    """
    Configuration for conversation history compression.
    
    When the conversation history exceeds the character threshold, older messages
    are compressed into a summary while recent messages are kept in full.
    """
    # Whether compression is enabled for this agent
    enabled: bool = True
    
    # Character threshold after which to start compressing history
    # Default is 8000 chars (~2000 tokens) - keeps compression manageable
    compression_threshold_chars: int = Field(default=8000, ge=1000, le=100000)
    
    # Number of recent message pairs (user + assistant) to keep in full
    # These are never compressed and always included verbatim
    recent_messages_to_keep: int = Field(default=5, ge=1, le=20)
    
    # Maximum length of compressed summary in characters
    max_summary_chars: int = Field(default=2000, ge=500, le=10000)
    
    # Model to use for compression (defaults to fast model for efficiency)
    compression_model: Optional[str] = Field(default="fast")


class AgentDefinitionCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    model: str
    instructions: str
    tools: Dict[str, Any] = Field(default_factory=dict)
    workflows: Optional[Dict[str, Any]] = None
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True
    is_builtin: bool = Field(
        default=False,
        description="Mark as built-in agent visible to all users. "
        "When True, the agent is shared across all users and cannot be deleted by non-admins."
    )
    allow_frontier_fallback: bool = Field(
        default=False,
        description="Allow automatic fallback to frontier cloud model when context window is exceeded. Only enable for non-sensitive data."
    )
    context_compression: Optional[ContextCompressionConfig] = Field(
        default_factory=ContextCompressionConfig,
        description="Configuration for conversation history compression"
    )


class AgentDefinitionRead(AgentDefinitionCreate):
    id: uuid.UUID
    is_builtin: bool
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentDefinitionUpdate(BaseModel):
    """Schema for partial update of agent definitions. All fields optional."""
    display_name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    instructions: Optional[str] = None
    tools: Optional[Dict[str, Any]] = None
    workflows: Optional[Dict[str, Any]] = None
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None
    is_builtin: Optional[bool] = None
    allow_frontier_fallback: Optional[bool] = None


class ToolDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    schema: Dict[str, Any] = Field(default_factory=dict)
    entrypoint: str
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True


class ToolDefinitionUpdate(BaseModel):
    """Schema for updating tool definitions."""
    name: Optional[str] = Field(None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    description: Optional[str] = None
    schema: Optional[Dict[str, Any]] = None
    entrypoint: Optional[str] = Field(None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$')
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ToolDefinitionRead(ToolDefinitionCreate):
    id: uuid.UUID
    is_builtin: bool
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    trigger: Dict[str, Any] = Field(default_factory=dict)
    guardrails: Optional[Dict[str, Any]] = None
    is_active: bool = True


class WorkflowDefinitionUpdate(BaseModel):
    """Schema for updating workflow definitions."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    steps: Optional[List[Dict[str, Any]]] = None
    trigger: Optional[Dict[str, Any]] = None
    guardrails: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class WorkflowDefinitionRead(WorkflowDefinitionCreate):
    id: uuid.UUID
    is_builtin: bool = False
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EvalDefinitionCreate(BaseModel):
    name: str
    description: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class EvalDefinitionUpdate(BaseModel):
    """Schema for updating evaluator definitions."""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    is_active: Optional[bool] = None


class EvalDefinitionRead(EvalDefinitionCreate):
    id: uuid.UUID
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
