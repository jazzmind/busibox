"""
Chat Insights API routes.

Provides endpoints for managing agent memories/context extracted from conversations.
Migrated from search-api to agent-api as insights are agent memories, not search functionality.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Query

from app.schemas.insights import (
    InsertInsightsRequest,
    InsertInsightsFrontendRequest,
    InsightSearchRequest,
    InsightSearchResponse,
    InsightSearchResult,
    InsightStatsResponse,
    InsightListResponse,
    InsightUpdateRequest,
    ChatInsightFrontend,
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
    insert_request: InsertInsightsFrontendRequest,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Insert insights into Milvus.
    
    Accepts frontend-style insights (without embeddings) and generates
    embeddings server-side using the configured embedding model.
    
    Requires authentication via Bearer token.
    The authenticated user is automatically set as the insight owner.
    """
    import time
    import uuid
    
    user_id = principal.sub
    
    try:
        # Generate embeddings for each insight
        service_insights = []
        for insight in insert_request.insights:
            # Generate ID if not provided
            insight_id = insight.id if insight.id else str(uuid.uuid4())
            
            # Get conversation_id from either field name
            conversation_id = insight.conversation_id or ""
            
            # Generate embedding from content
            try:
                embedding = await service.generate_embedding(
                    text=insight.content,
                    user_id=user_id,
                )
            except Exception as e:
                logger.warning(f"Failed to generate embedding for insight: {e}")
                # Use zero vector as fallback (will have poor search quality)
                embedding = [0.0] * 1024
            
            # Create service model
            # Note: category is supported, but importance/source are stored in metadata
            service_insight = ServiceChatInsight(
                id=insight_id,
                user_id=user_id,  # Always use authenticated user
                content=insight.content,
                embedding=embedding,
                conversation_id=conversation_id,
                analyzed_at=int(time.time()),
                category=insight.category,
            )
            service_insights.append(service_insight)
        
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
    
    If user_id is not provided in the request, it defaults to the authenticated user.
    """
    user_id = principal.sub
    
    # Use authenticated user's ID if not provided in request
    search_user_id = search_request.user_id or user_id
    
    # Verify user_id matches authenticated user (can only search own insights)
    if search_user_id != user_id:
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
            user_id=search_user_id,  # Use resolved user_id
            authorization=authorization,
            limit=search_request.limit,
            score_threshold=search_request.score_threshold,
        )
        
        # Convert service results to frontend-compatible format
        api_results = [
            InsightSearchResult(
                insight=ChatInsightFrontend(
                    id=r.id,
                    content=r.content,
                    category=r.category,  # Category from Milvus
                    importance=1.0 - min(r.score, 1.0),  # Convert L2 distance to importance (lower distance = higher importance)
                    source="conversation",
                    conversationId=r.conversation_id,
                    createdAt=r.analyzed_at.isoformat() if hasattr(r.analyzed_at, 'isoformat') else str(r.analyzed_at),
                    metadata={},
                ),
                score=1.0 - min(r.score, 1.0),  # Convert L2 distance to similarity score
                distance=r.score,  # L2 distance directly
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


@router.get("/list", response_model=InsightListResponse)
async def list_my_insights(
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
    category: Optional[str] = Query(None, description="Filter by category: preference, fact, goal, context, other"),
    offset: int = Query(0, ge=0, description="Number of results to skip"),
    limit: int = Query(50, ge=1, le=100, description="Maximum results to return"),
):
    """
    List all insights for the authenticated user.
    
    Supports pagination and optional category filtering.
    Returns insights in frontend-compatible format.
    """
    user_id = principal.sub
    
    try:
        # Get paginated insights
        results, total = service.list_user_insights(
            user_id=user_id,
            category=category,
            offset=offset,
            limit=limit,
        )
        
        # Get category counts
        category_counts = service.get_category_counts(user_id)
        
        # Convert to frontend format
        api_results = [
            InsightSearchResult(
                insight=ChatInsightFrontend(
                    id=r.get("id", ""),
                    content=r.get("content", ""),
                    category=r.get("category", "other"),
                    importance=0.5,  # Default importance
                    source="conversation",
                    conversationId=r.get("conversationId", ""),
                    createdAt=datetime.fromtimestamp(r.get("analyzedAt", 0)).isoformat() if r.get("analyzedAt") else "",
                    metadata={},
                ),
                score=1.0,  # No search score for list
                distance=0.0,
            )
            for r in results
        ]
        
        return InsightListResponse(
            results=api_results,
            total=total,
            offset=offset,
            limit=limit,
            by_category=category_counts,
        )
    
    except Exception as e:
        logger.error(f"Failed to list insights: user_id={user_id}, error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list insights: {str(e)}",
        )


@router.get("/stats/me", response_model=InsightStatsResponse)
async def get_my_stats(
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Get statistics for the authenticated user's insights.
    
    Requires authentication. Returns stats for the current user.
    Returns format compatible with frontend: { total, by_category }.
    """
    user_id = principal.sub
    
    try:
        count = service.get_user_insight_count(user_id)
        stats = service.get_collection_stats()
        category_counts = service.get_category_counts(user_id)
        
        return InsightStatsResponse(
            # Frontend expected format
            total=count,
            by_category=category_counts,
            # Legacy fields
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
    Returns format compatible with frontend: { total, by_category }.
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
            # Frontend expected format
            total=count,
            by_category={},  # TODO: Implement category grouping if needed
            # Legacy fields
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


@router.patch("/{insight_id}")
async def update_insight(
    insight_id: str,
    update_request: InsightUpdateRequest,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Update an insight's content or category.
    
    Requires authentication. User can only update their own insights.
    """
    user_id = principal.sub
    
    try:
        # Update the insight
        updated = service.update_insight(
            insight_id=insight_id,
            user_id=user_id,
            content=update_request.content,
            category=update_request.category,
        )
        
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"Insight not found or not owned by user",
            )
        
        logger.info(f"Updated insight: id={insight_id}, user_id={user_id}")
        
        return {
            "message": "Insight updated",
            "id": insight_id,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update insight: id={insight_id}, error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update insight: {str(e)}",
        )


@router.delete("/{insight_id}")
async def delete_insight(
    insight_id: str,
    principal: Principal = Depends(get_principal),
    service: InsightsService = Depends(get_insights_service),
):
    """
    Delete an insight by ID.
    
    Requires authentication. User can only delete their own insights.
    """
    user_id = principal.sub
    
    try:
        deleted = service.delete_insight(insight_id=insight_id, user_id=user_id)
        
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Insight not found or not owned by user",
            )
        
        logger.info(f"Deleted insight: id={insight_id}, user_id={user_id}")
        
        return {
            "message": "Insight deleted",
            "id": insight_id,
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete insight: id={insight_id}, error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete insight: {str(e)}",
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
