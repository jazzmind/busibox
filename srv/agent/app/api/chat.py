"""
Simple chat API endpoint for backward compatibility.

This provides a simplified chat interface that wraps the dispatcher
and runs functionality for legacy clients.
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.schemas.auth import Principal
from app.services.dispatcher_service import route_query
from app.schemas.dispatcher import DispatcherRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """Simple chat request."""
    message: str = Field(..., description="User message")
    agentId: Optional[str] = Field(default="default", description="Agent ID (optional)")
    context: Optional[Dict[str, Any]] = Field(default=None, description="Additional context")


class ChatResponse(BaseModel):
    """Simple chat response."""
    response: str = Field(..., description="Agent response")
    success: bool = Field(default=True, description="Success flag")


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ChatResponse:
    """
    Simple chat endpoint for backward compatibility.
    
    This endpoint provides a simplified interface that:
    1. Routes the query through the dispatcher
    2. Returns a simple text response
    
    For more advanced features, use /dispatcher/route or /runs endpoints directly.
    
    Args:
        payload: Chat request with message and optional context
        principal: Authenticated user
        session: Database session
        
    Returns:
        ChatResponse with agent response
        
    Raises:
        HTTPException: 400 if request is invalid
    """
    try:
        logger.info(
            f"Chat request from user {principal.sub}: {payload.message[:50]}...",
            extra={
                "user_sub": principal.sub,
                "agent_id": payload.agentId,
                "message_length": len(payload.message),
            },
        )
        
        # Use dispatcher to route the query
        dispatcher_request = DispatcherRequest(
            query=payload.message,
            available_tools=["web_search", "doc_search"],
            available_agents=[],
            attachments=[],
            user_settings={
                "enabled_tools": ["web_search", "doc_search"],
                "enabled_agents": [],
            },
        )
        
        # Generate request ID for tracing
        request_id = str(uuid.uuid4())
        
        # Route query
        routing_response = await route_query(
            request=dispatcher_request,
            user_id=principal.sub,
            request_id=request_id,
            session=session,
        )
        
        # For now, return a simple response based on routing decision
        # In a full implementation, this would execute the selected tools/agents
        response_text = (
            f"Query routed to: {', '.join(routing_response.selected_tools or ['none'])}. "
            f"Confidence: {routing_response.confidence:.2f}. "
            f"Reasoning: {routing_response.reasoning}"
        )
        
        logger.info(
            f"Chat response generated for user {principal.sub}",
            extra={
                "user_sub": principal.sub,
                "response_length": len(response_text),
                "confidence": routing_response.confidence,
            },
        )
        
        return ChatResponse(
            response=response_text,
            success=True,
        )
        
    except Exception as e:
        logger.error(f"Chat request failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Chat request failed: {str(e)}",
        )

