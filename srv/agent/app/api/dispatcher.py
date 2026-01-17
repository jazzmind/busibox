"""
Dispatcher API endpoints for intelligent query routing.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.schemas.auth import Principal
from app.schemas.dispatcher import DispatcherRequest, DispatcherResponse, StreamingDispatcherRequest
from app.services.dispatcher_service import route_query
from app.services.streaming_dispatcher import route_and_execute_stream

router = APIRouter(prefix="/dispatcher", tags=["dispatcher"])
logger = get_logger(__name__)


@router.post("/route", response_model=DispatcherResponse)
async def route_user_query(
    payload: DispatcherRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> DispatcherResponse:
    """
    Route a natural language query to appropriate tools and agents.
    
    Analyzes the query and returns:
    - Selected tools/agents for routing
    - Confidence score (0-1)
    - Reasoning for the decision
    - Alternative suggestions
    
    The routing decision is logged for accuracy measurement and debugging.
    
    Args:
        payload: Dispatcher request with query and available resources
        request: FastAPI request object (for correlation ID)
        principal: Authenticated user
        session: Database session
        
    Returns:
        DispatcherResponse with routing decision
        
    Raises:
        HTTPException: 400 if request is invalid
    """
    # Generate request ID for correlation
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    
    logger.info(
        "dispatcher_route_request",
        user_id=principal.sub,
        request_id=request_id,
        query_length=len(payload.query),
        available_tools_count=len(payload.available_tools),
        available_agents_count=len(payload.available_agents),
        has_attachments=len(payload.attachments) > 0
    )
    
    try:
        # Route query via dispatcher service
        # TODO: Pass Redis client when available
        response = await route_query(
            request=payload,
            user_id=principal.sub,
            request_id=request_id,
            session=session,
            redis_client=None,  # TODO: Add Redis dependency
        )
        
        return response
        
    except Exception as e:
        logger.error(
            "dispatcher_route_error",
            user_id=principal.sub,
            request_id=request_id,
            error=str(e),
            error_type=type(e).__name__
        )
        
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Routing failed: {str(e)}"
        )


@router.post("/route/stream")
async def route_user_query_stream(
    payload: StreamingDispatcherRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """
    Route and execute a query with real-time streaming updates.
    
    This endpoint provides progressive feedback during query execution:
    - Planning phase: Analyzing the query
    - Routing phase: Determining which tools/agents to use
    - Execution phase: Running tools and agents with status updates
    - Synthesis phase: Combining results
    - Completion: Final response with suggestions
    
    The response is a Server-Sent Events (SSE) stream with JSON events.
    
    Event types:
    - planning: Initial query analysis
    - routing: Routing decision made
    - tool_start: Starting tool execution
    - tool_result: Tool execution completed
    - agent_start: Starting agent execution  
    - agent_result: Agent execution completed
    - synthesis: Combining results
    - content: Streaming content chunk
    - suggestion: Follow-up suggestions
    - complete: Execution finished
    - error: Error occurred
    
    Args:
        payload: Streaming dispatcher request
        request: FastAPI request object
        principal: Authenticated user
        session: Database session
        
    Returns:
        StreamingResponse with SSE events
    """
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    
    logger.info(
        "dispatcher_stream_request",
        user_id=principal.sub,
        request_id=request_id,
        query_length=len(payload.query),
        available_tools_count=len(payload.available_tools),
        available_agents_count=len(payload.available_agents),
    )
    
    async def event_generator():
        """Generate SSE events from the streaming dispatcher."""
        try:
            async for event in route_and_execute_stream(
                request=payload,
                user_id=principal.sub,
                request_id=request_id,
                session=session,
                principal=principal,
            ):
                yield event.to_sse()
        except Exception as e:
            logger.error(
                "dispatcher_stream_error",
                user_id=principal.sub,
                request_id=request_id,
                error=str(e),
            )
            # Send error event
            from app.schemas.dispatcher import DispatcherStreamEvent
            from datetime import datetime, timezone
            error_event = DispatcherStreamEvent(
                type="error",
                message=f"Stream error: {str(e)}",
                timestamp=datetime.now(timezone.utc),
            )
            yield error_event.to_sse()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )




