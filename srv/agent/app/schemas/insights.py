"""
Pydantic schemas for chat insights API.

Insights are agent memories/context extracted from conversations and stored
in Milvus for vector similarity search.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class ChatInsight(BaseModel):
    """Chat insight entity (with pre-computed embedding)."""
    
    id: str = Field(..., description="Insight ID")
    user_id: str = Field(..., description="User ID who owns this insight", alias="userId")
    content: str = Field(..., description="The insight text")
    embedding: List[float] = Field(..., description="Vector embedding (1024 dimensions)")
    conversation_id: str = Field(..., description="Source conversation ID", alias="conversationId")
    analyzed_at: int = Field(..., description="Unix timestamp when insight was extracted", alias="analyzedAt")
    
    class Config:
        populate_by_name = True


class ChatInsightFrontend(BaseModel):
    """Chat insight in frontend-expected format (no embedding required)."""
    
    id: str = Field(default="", description="Insight ID (auto-generated if not provided)")
    content: str = Field(..., description="The insight text")
    category: str = Field(default="other", description="Category: preference, fact, goal, context, other")
    importance: float = Field(default=0.5, description="Importance score 0-1")
    source: str = Field(default="conversation", description="Source: conversation, explicit, inferred")
    conversation_id: str = Field(default="", description="Conversation ID", alias="conversationId")
    created_at: str = Field(default="", description="ISO timestamp", alias="createdAt")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")
    
    class Config:
        populate_by_name = True


class InsertInsightsRequest(BaseModel):
    """Request to insert insights (requires pre-computed embeddings)."""
    
    insights: List[ChatInsight] = Field(..., description="List of insights to insert", min_items=1)


class InsertInsightsFrontendRequest(BaseModel):
    """Request to insert insights from frontend (embeddings generated server-side)."""
    
    insights: List[ChatInsightFrontend] = Field(..., description="List of insights to insert", min_items=1)


class InsightSearchRequest(BaseModel):
    """Request to search insights."""
    
    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    user_id: Optional[str] = Field(None, description="User ID to filter results (auto-set from auth)", alias="userId")
    limit: int = Field(3, description="Maximum number of results", ge=1, le=20)
    score_threshold: float = Field(0.7, description="Maximum L2 distance threshold", ge=0.0, le=2.0, alias="scoreThreshold")
    
    class Config:
        populate_by_name = True


class InsightSearchResult(BaseModel):
    """Search result for chat insights - frontend-compatible format."""
    
    insight: ChatInsightFrontend = Field(..., description="The insight object")
    score: float = Field(..., description="Similarity score (0-1, higher is better)")
    distance: float = Field(default=0.0, description="Vector distance (lower is better)")
    
    class Config:
        populate_by_name = True


class InsightSearchResponse(BaseModel):
    """Response for insight search."""
    
    query: str = Field(..., description="Original query")
    results: List[InsightSearchResult] = Field(..., description="Search results")
    count: int = Field(..., description="Number of results returned")


class InsightStatsResponse(BaseModel):
    """Statistics for user insights.
    
    Compatible with frontend format: { total, by_category }
    Also includes legacy fields for backwards compatibility.
    """
    
    # Frontend expected format
    total: int = Field(..., description="Total number of insights")
    by_category: dict = Field(default_factory=dict, description="Insights grouped by category")
    
    # Legacy fields (for backwards compatibility)
    user_id: str = Field(..., description="User ID", alias="userId")
    count: int = Field(..., description="Number of insights (same as total)")
    collection_name: str = Field(..., description="Collection name", alias="collectionName")
    
    class Config:
        populate_by_name = True


class InsightListResponse(BaseModel):
    """Response for listing insights with pagination."""
    
    results: List[InsightSearchResult] = Field(..., description="List of insights")
    total: int = Field(..., description="Total number of insights matching filter")
    offset: int = Field(..., description="Current offset")
    limit: int = Field(..., description="Maximum results per page")
    by_category: dict = Field(default_factory=dict, description="Insights count by category")


class InsightUpdateRequest(BaseModel):
    """Request to update an insight."""
    
    content: Optional[str] = Field(None, description="New insight content")
    category: Optional[str] = Field(None, description="New category: preference, fact, goal, context, other")
