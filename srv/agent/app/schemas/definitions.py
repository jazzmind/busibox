import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


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


class AgentDefinitionRead(AgentDefinitionCreate):
    id: uuid.UUID
    is_builtin: bool
    created_by: Optional[str] = None
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
