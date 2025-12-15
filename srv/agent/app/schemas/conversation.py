import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ========== Attachment Schema ==========

class Attachment(BaseModel):
    """File attachment in a message"""
    name: str
    type: str
    url: str
    size: int
    knowledge_base_id: Optional[str] = None


# ========== Message Schemas ==========

class MessageBase(BaseModel):
    """Base schema for message"""
    role: str = Field(description="Message role: user, assistant, or system")
    content: str = Field(description="Message content")
    attachments: Optional[List[Attachment]] = Field(None, description="File attachments")
    run_id: Optional[uuid.UUID] = Field(None, description="Associated run ID")
    routing_decision: Optional[Dict[str, Any]] = Field(None, description="Dispatcher routing decision")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Tool call results")

    @field_validator('role')
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ['user', 'assistant', 'system']:
            raise ValueError("Role must be 'user', 'assistant', or 'system'")
        return v


class MessageCreate(MessageBase):
    """Schema for creating a message"""
    pass


class MessageRead(MessageBase):
    """Schema for reading a message"""
    id: uuid.UUID
    conversation_id: uuid.UUID
    created_at: datetime

    class Config:
        from_attributes = True


class MessagePreview(BaseModel):
    """Preview of last message in conversation list"""
    role: str
    content: str = Field(description="Preview content (max 100 chars)")
    created_at: datetime

    class Config:
        from_attributes = True


# ========== Conversation Schemas ==========

class ConversationBase(BaseModel):
    """Base schema for conversation"""
    title: str = Field(max_length=255, description="Conversation title")


class ConversationCreate(BaseModel):
    """Schema for creating a conversation"""
    title: Optional[str] = Field(None, max_length=255, description="Optional conversation title")


class ConversationUpdate(BaseModel):
    """Schema for updating a conversation"""
    title: Optional[str] = Field(None, max_length=255, description="Updated conversation title")


class ConversationRead(ConversationBase):
    """Schema for reading a conversation"""
    id: uuid.UUID
    user_id: str
    message_count: Optional[int] = None
    last_message: Optional[MessagePreview] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationWithMessages(ConversationRead):
    """Schema for conversation with full messages"""
    messages: List[MessageRead] = []


class ConversationListResponse(BaseModel):
    """Response schema for listing conversations"""
    conversations: List[ConversationRead]
    total: int
    limit: int
    offset: int


class MessageListResponse(BaseModel):
    """Response schema for listing messages"""
    messages: List[MessageRead]
    total: int
    limit: int
    offset: int


# ========== Chat Settings Schemas ==========

class ChatSettingsBase(BaseModel):
    """Base schema for chat settings"""
    enabled_tools: Optional[List[str]] = Field(default_factory=list, description="Enabled tool names")
    enabled_agents: Optional[List[uuid.UUID]] = Field(default_factory=list, description="Enabled agent IDs")
    model: Optional[str] = Field(None, description="Preferred model")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0, description="Temperature setting")
    max_tokens: Optional[int] = Field(2000, ge=1, le=32000, description="Max tokens setting")

    @field_validator('temperature')
    @classmethod
    def validate_temperature(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 <= v <= 2.0):
            raise ValueError("Temperature must be between 0.0 and 2.0")
        return v

    @field_validator('max_tokens')
    @classmethod
    def validate_max_tokens(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (1 <= v <= 32000):
            raise ValueError("Max tokens must be between 1 and 32000")
        return v


class ChatSettingsUpdate(ChatSettingsBase):
    """Schema for updating chat settings"""
    pass


class ChatSettingsRead(ChatSettingsBase):
    """Schema for reading chat settings"""
    id: uuid.UUID
    user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True






