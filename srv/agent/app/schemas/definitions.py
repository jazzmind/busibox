import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from enum import Enum

from pydantic import BaseModel, Field


class AgentVisibility(str, Enum):
    BUILTIN = "builtin"
    APPLICATION = "application"
    SHARED = "shared"
    PERSONAL = "personal"


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


class MCPServerEntry(BaseModel):
    """Configuration for an MCP server that the agent can use for tools."""
    name: str = Field(description="Unique name for this MCP server connection")
    transport: str = Field(default="stdio", description="Transport: 'stdio' or 'sse'")
    url: Optional[str] = Field(default=None, description="SSE endpoint URL (required for sse transport)")
    command: Optional[str] = Field(default=None, description="Command to launch (required for stdio transport)")
    args: Optional[List[str]] = Field(default=None, description="Command arguments for stdio transport")
    env: Optional[Dict[str, str]] = Field(default=None, description="Environment variables for stdio subprocess")
    headers: Optional[Dict[str, str]] = Field(default=None, description="HTTP headers for SSE transport")
    tool_filter: Optional[List[str]] = Field(default=None, description="Whitelist of tool names (None = all)")
    timeout_seconds: int = Field(default=30, description="Per-call timeout")


class AgentDefinitionCreate(BaseModel):
    name: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    model: str
    instructions: str
    tools: Dict[str, Any] = Field(default_factory=dict)
    workflows: Optional[Dict[str, Any]] = None
    scopes: List[str] = Field(default_factory=list)
    mcp_servers: Optional[List[MCPServerEntry]] = Field(
        default=None,
        description="External MCP servers whose tools this agent can use. "
        "Tools are discovered at runtime and registered alongside built-in tools."
    )
    is_active: bool = True
    is_builtin: bool = Field(
        default=False,
        description="Deprecated: use visibility instead. "
        "Kept for backward compat — is_builtin=True maps to visibility='application'."
    )
    visibility: Optional[AgentVisibility] = Field(
        default=None,
        description="Agent visibility category: builtin, application, shared, personal. "
        "When omitted, derived from is_builtin (True→application, False→personal)."
    )
    app_id: Optional[str] = Field(
        default=None,
        description="Application ID for application-scoped agents (required when visibility='application')."
    )
    allow_frontier_fallback: bool = Field(
        default=False,
        description="Allow automatic fallback to frontier cloud model when context window is exceeded. Only enable for non-sensitive data."
    )
    response_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional JSON Schema envelope ({name, strict, schema}) enforced on every run output. "
        "When set, response_format is sent to the LLM on every run so the output always matches this shape. "
        "Invoke-time response_schema overrides this definition-level value."
    )
    context_compression: Optional[ContextCompressionConfig] = Field(
        default_factory=ContextCompressionConfig,
        description="Configuration for conversation history compression"
    )

    def resolved_visibility(self) -> str:
        """Return the effective visibility, applying backward compat from is_builtin."""
        if self.visibility is not None:
            return self.visibility.value
        return AgentVisibility.APPLICATION.value if self.is_builtin else AgentVisibility.PERSONAL.value


class AgentDefinitionRead(AgentDefinitionCreate):
    id: uuid.UUID
    is_builtin: bool
    visibility: Optional[AgentVisibility] = AgentVisibility.PERSONAL
    app_id: Optional[str] = None
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
    mcp_servers: Optional[List[MCPServerEntry]] = None
    is_active: Optional[bool] = None
    is_builtin: Optional[bool] = None
    visibility: Optional[AgentVisibility] = None
    app_id: Optional[str] = None
    allow_frontier_fallback: Optional[bool] = None
    response_schema: Optional[Dict[str, Any]] = None


class ToolDefinitionCreate(BaseModel):
    model_config = {"populate_by_name": True}

    name: str
    description: Optional[str] = None
    tool_schema: Dict[str, Any] = Field(default_factory=dict, validation_alias="schema", serialization_alias="schema")
    entrypoint: str
    scopes: List[str] = Field(default_factory=list)
    is_active: bool = True


class ToolDefinitionUpdate(BaseModel):
    """Schema for updating tool definitions."""
    model_config = {"populate_by_name": True}

    name: Optional[str] = Field(None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_]*$')
    description: Optional[str] = None
    tool_schema: Optional[Dict[str, Any]] = Field(None, validation_alias="schema", serialization_alias="schema")
    entrypoint: Optional[str] = Field(None, pattern=r'^[a-zA-Z_][a-zA-Z0-9_.]*:[a-zA-Z_][a-zA-Z0-9_]*$')
    scopes: Optional[List[str]] = None
    is_active: Optional[bool] = None


class ToolDefinitionRead(ToolDefinitionCreate):
    model_config = {"populate_by_name": True, "from_attributes": True}

    id: uuid.UUID
    is_builtin: bool
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime


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
