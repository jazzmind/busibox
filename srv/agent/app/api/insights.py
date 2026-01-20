"""
Chat Insights API routes.

Provides endpoints for managing agent memories/context extracted from conversations.
Migrated from search-api to agent-api as insights are agent memories, not search functionality.
"""

import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional

from app.schemas.insights import (
    InsertInsightsRequest,
    InsightSearchRequest,
    InsightSearchResponse,
    InsightSearchResult,
    InsightStatsResponse,
)
from app.services.insights_service import InsightsService, ChatInsight as ServiceChatInsight
from app.auth.dependencies import get_principal
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/insights", tags=["insights"])

# Global insights service instance (will be initialized in main.py)
_insights_service: Optional[InsightsService] = None


def get_insights_service() -> InsightsService:
    """Get insights service instance."""
    if _insights_service is None:
        raise HTTPException(
            status_code=500,
            detail="Insights service not initialized"
        )
    return _insights_service


def init_insights_service(config: dict):
    """Initialize insights service with configuration."""
    global _insights_service
    _insights_service = InsightsService(config)
    logger.info("Insights service initialized")


@router.post("/init")
async def initialize_collection(
    principal: Principal = Depends(get_principal),
):
    """
    Initialize the chat_insights collection in Milvus.
    
    Creates the collection with schema if it doesn't exist.
    This endpoint is idempotent - safe to call multiple times.
    Requires authentication.
    """
    # Get service after auth check
    service = get_insights_service()
    
    try:
        service.initialize_collection()
        return {
            "message": "Collection initialized successfully",
            "collection": "chat_insights",
        }
    except Exception as e:
        logger.error(f"Failed to initialize collection: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to initialize collection: {str(e)}",
        )


@router.post("")
async def insert_insights(
    insert_request: InsertInsightsRequest,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Insert insights into Milvus.
    
    Requires authentication via Bearer token.
    Users can only insert insights for themselves.
    """
    user_id = principal.sub
    
    # Verify all insights belong to the authenticated user
    for insight in insert_request.insights:
        if insight.user_id != user_id:
            raise HTTPException(
                status_code=403,
                detail="Cannot insert insights for other users",
            )
    
    try:
        # Convert Pydantic models to service models
        service_insights = [
            ServiceChatInsight(
                id=insight.id,
                user_id=insight.user_id,
                content=insight.content,
                embedding=insight.embedding,
                conversation_id=insight.conversation_id,
                analyzed_at=insight.analyzed_at,
            )
            for insight in insert_request.insights
        ]
        
        service.insert_insights(service_insights)
        
        logger.info(
            f"Insights inserted: user_id={user_id}, count={len(service_insights)}"
        )
        
        return {
            "message": f"Successfully inserted {len(service_insights)} insights",
            "count": len(service_insights),
        }
    
    except Exception as e:
        logger.error(f"Failed to insert insights for user {user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to insert insights: {str(e)}",
        )


@router.post("/search", response_model=InsightSearchResponse)
async def search_insights(
    search_request: InsightSearchRequest,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
    request: Request = None,
):
    """
    Search for relevant insights based on query.
    
    Filters results by user_id to ensure users only see their own insights.
    Requires authentication via Bearer token.
    """
    user_id = principal.sub
    # Verify user_id matches authenticated user
    if search_request.user_id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Cannot search insights for other users",
        )
    
    try:
        # Get authorization header if present
        authorization = None
        if request and hasattr(request, 'headers'):
            authorization = request.headers.get('Authorization')
        
        results = await service.search_insights(
            query=search_request.query,
            user_id=search_request.user_id,
            authorization=authorization,
            limit=search_request.limit,
            score_threshold=search_request.score_threshold,
        )
        
        # Convert service results to API results
        api_results = [
            InsightSearchResult(
                id=r.id,
                userId=r.user_id,
                content=r.content,
                conversationId=r.conversation_id,
                analyzedAt=r.analyzed_at.isoformat(),
                score=r.score,
            )
            for r in results
        ]
        
        logger.info(
            f"Insights search completed: user_id={user_id}, query={search_request.query}, results_count={len(api_results)}"
        )
        
        return InsightSearchResponse(
            query=search_request.query,
            results=api_results,
            count=len(api_results),
        )
    
    except Exception as e:
        logger.error(
            f"Insights search failed: user_id={user_id}, query={search_request.query}, error={e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Insights search failed: {str(e)}",
        )


@router.delete("/conversation/{conversation_id}")
async def delete_conversation_insights(
    conversation_id: str,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Delete insights for a conversation.
    
    Requires authentication. Users can only delete their own insights.
    """
    user_id = principal.sub
    try:
        service.delete_conversation_insights(conversation_id, user_id)
        
        logger.info(
            f"Conversation insights deleted: conversation_id={conversation_id}, user_id={user_id}"
        )
        
        return {
            "message": f"Deleted insights for conversation {conversation_id}",
            "conversationId": conversation_id,
        }
    
    except Exception as e:
        logger.error(
            f"Failed to delete conversation insights: conversation_id={conversation_id}, user_id={user_id}, error={e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete conversation insights: {str(e)}",
        )


@router.delete("/user/{user_id}")
async def delete_user_insights(
    user_id: str,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Delete all insights for a user (for account deletion/cleanup).
    
    Requires authentication. Users can only delete their own insights.
    """
    # Verify user can only delete their own insights
    if user_id != principal.sub:
        raise HTTPException(
            status_code=403,
            detail="Cannot delete insights for other users",
        )
    
    try:
        service.delete_user_insights(user_id)
        
        logger.info(f"User insights deleted: user_id={user_id}")
        
        return {
            "message": f"Deleted all insights for user {user_id}",
            "userId": user_id,
        }
    
    except Exception as e:
        logger.error(
            f"Failed to delete user insights: user_id={user_id}, error={e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete user insights: {str(e)}",
        )


@router.get("/stats/me", response_model=InsightStatsResponse)
async def get_my_stats(
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Get statistics for the authenticated user's insights.
    
    Requires authentication. Returns stats for the current user.
    """
    user_id = principal.sub
    
    try:
        count = service.get_user_insight_count(user_id)
        stats = service.get_collection_stats()
        
        return InsightStatsResponse(
            userId=user_id,
            count=count,
            collectionName=stats.get("collectionName", "chat_insights"),
        )
    
    except Exception as e:
        logger.error(
            f"Failed to get user stats: user_id={user_id}, error={e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user stats: {str(e)}",
        )


@router.get("/stats/{user_id}", response_model=InsightStatsResponse)
async def get_user_stats(
    user_id: str,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Get statistics for user insights.
    
    Requires authentication. Users can only view their own stats.
    """
    # Verify user can only view their own stats
    if user_id != principal.sub:
        raise HTTPException(
            status_code=403,
            detail="Cannot view stats for other users",
        )
    
    try:
        count = service.get_user_insight_count(user_id)
        stats = service.get_collection_stats()
        
        return InsightStatsResponse(
            userId=user_id,
            count=count,
            collectionName=stats.get("collectionName", "chat_insights"),
        )
    
    except Exception as e:
        logger.error(
            f"Failed to get user stats: user_id={user_id}, error={e}"
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get user stats: {str(e)}",
        )


@router.post("/flush")
async def flush_collection(
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Flush collection to ensure data persistence.
    
    Call this after batch inserts for data durability.
    Requires authentication.
    """
    try:
        service.flush_collection()
        
        logger.info("Collection flushed")
        
        return {
            "message": "Collection flushed successfully",
            "collection": "chat_insights",
        }
    
    except Exception as e:
        logger.error(f"Failed to flush collection: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to flush collection: {str(e)}",
        )
