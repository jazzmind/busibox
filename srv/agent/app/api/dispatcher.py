"""
Dispatcher API endpoints for intelligent query routing.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.schemas.auth import Principal
from app.schemas.dispatcher import DispatcherRequest, DispatcherResponse
from app.services.dispatcher_service import route_query

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





