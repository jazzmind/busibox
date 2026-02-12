import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator


# ========== Attachment Schema ==========

class Attachment(BaseModel):
    """File attachment in a message (inline JSON)"""
    name: str
    type: str
    url: str
    size: int
    knowledge_base_id: Optional[str] = None


# ========== Chat Attachment Schemas ==========

class ChatAttachmentCreate(BaseModel):
    """Schema for creating a chat attachment"""
    filename: str = Field(max_length=500, description="Original filename")
    file_url: str = Field(description="URL to the uploaded file")
    mime_type: Optional[str] = Field(None, max_length=255)
    size_bytes: Optional[int] = None
    added_to_library: bool = False
    library_document_id: Optional[str] = None
    parsed_content: Optional[str] = None


class ChatAttachmentRead(ChatAttachmentCreate):
    """Schema for reading a chat attachment"""
    id: uuid.UUID
    message_id: Optional[uuid.UUID] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ========== Message Schemas ==========

class MessageBase(BaseModel):
    """Base schema for message"""
    model_config = {"populate_by_name": True}

    role: str = Field(description="Message role: user, assistant, or system")
    content: str = Field(description="Message content")
    attachments: Optional[List[Attachment]] = Field(None, description="File attachments (inline JSON)")
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional metadata: web_search_results, doc_search_results, used_insight_ids",
        validation_alias=AliasChoices('metadata_json', 'metadata'),
    )
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
    attachment_ids: Optional[List[uuid.UUID]] = Field(None, description="IDs of chat_attachments to link to this message")


class MessageRead(MessageBase):
    """Schema for reading a message"""
    model_config = {"from_attributes": True, "populate_by_name": True}

    id: uuid.UUID
    conversation_id: uuid.UUID
    chat_attachments: Optional[List[ChatAttachmentRead]] = Field(None, description="Linked chat attachments")
    created_at: datetime


class MessagePreview(BaseModel):
    """Preview of last message in conversation list"""
    role: str
    content: str = Field(description="Preview content (max 100 chars)")
    created_at: datetime

    class Config:
        from_attributes = True


# ========== Conversation Share Schemas ==========

class ConversationShareCreate(BaseModel):
    """Schema for sharing a conversation"""
    user_id: str = Field(description="User to share with")
    role: str = Field("viewer", description="Share role: viewer or editor")

    @field_validator('role')
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ['viewer', 'editor']:
            raise ValueError("Role must be 'viewer' or 'editor'")
        return v


class ConversationShareRead(BaseModel):
    """Schema for reading a conversation share"""
    id: uuid.UUID
    conversation_id: uuid.UUID
    user_id: str
    role: str
    shared_by: str
    shared_at: datetime

    class Config:
        from_attributes = True


class ConversationShareListResponse(BaseModel):
    """Response schema for listing conversation shares"""
    shares: List[ConversationShareRead]


# ========== Conversation Schemas ==========

class ConversationBase(BaseModel):
    """Base schema for conversation"""
    title: str = Field(max_length=255, description="Conversation title")


class ConversationCreate(BaseModel):
    """Schema for creating a conversation"""
    title: Optional[str] = Field(None, max_length=255, description="Optional conversation title")
    source: Optional[str] = Field(None, max_length=50, description="App/client creating the conversation")
    model: Optional[str] = Field(None, max_length=255, description="LLM model used")
    is_private: bool = Field(False, description="Private conversations don't appear in insights")
    agent_id: Optional[str] = Field(None, description="Agent used in conversation")


class ConversationUpdate(BaseModel):
    """Schema for updating a conversation"""
    title: Optional[str] = Field(None, max_length=255, description="Updated conversation title")
    is_private: Optional[bool] = Field(None, description="Updated privacy setting")
    model: Optional[str] = Field(None, max_length=255, description="Updated model")


class ConversationRead(ConversationBase):
    """Schema for reading a conversation"""
    id: uuid.UUID
    user_id: str
    source: Optional[str] = None
    model: Optional[str] = None
    is_private: bool = False
    agent_id: Optional[str] = None
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









