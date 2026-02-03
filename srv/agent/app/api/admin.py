"""
Admin API endpoints for system-level statistics and management.

These endpoints require admin role and provide aggregate data across all users.
Includes LiteLLM spend tracking data for detailed token usage by model.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.config.settings import get_settings
from app.db.session import get_session
from app.schemas.auth import Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# =============================================================================
# Response Models
# =============================================================================

class ModelUsageStats(BaseModel):
    """Usage statistics for a single model."""
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    spend: float = 0.0  # Cost in dollars
    avg_latency_ms: float = 0.0


class UsageStatsResponse(BaseModel):
    """Overall LLM usage statistics."""
    models: list[ModelUsageStats]
    tokensToday: int
    totalRequests: int
    totalInputTokens: int
    totalOutputTokens: int
    totalSpend: float
    # Legacy fields for backward compatibility
    totalToolCalls: int = 0


class SystemStatsResponse(BaseModel):
    """System-wide statistics."""
    totalAgents: int
    totalConversations: int
    totalWorkflows: int
    totalWorkflowExecutions: int
    activeUsers: int


# =============================================================================
# Helper Functions
# =============================================================================

def require_admin(principal: Principal) -> None:
    """Verify that the principal has admin role."""
    if "admin" not in principal.roles and "Admin" not in principal.roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required"
        )


async def get_litellm_connection() -> Optional[asyncpg.Connection]:
    """
    Get a connection to the LiteLLM database for spend tracking queries.
    
    Returns None if the LiteLLM database URL is not configured.
    """
    settings = get_settings()
    if not settings.litellm_database_url:
        return None
    
    try:
        # Parse the database URL (convert from SQLAlchemy format if needed)
        db_url = settings.litellm_database_url
        # Remove any asyncpg prefix since we use psycopg-style URL for asyncpg
        if db_url.startswith("postgresql+asyncpg://"):
            db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
        
        conn = await asyncpg.connect(db_url)
        return conn
    except Exception as e:
        logger.warning(f"Failed to connect to LiteLLM database: {e}")
        return None


async def fetch_litellm_stats(days: int = 30) -> dict:
    """
    Fetch usage statistics from LiteLLM's SpendLogs table.
    
    Returns:
        Dictionary with model stats, totals, and today's usage.
    """
    conn = await get_litellm_connection()
    if not conn:
        return {
            "models": [],
            "tokens_today": 0,
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_spend": 0.0,
        }
    
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        today = datetime.utcnow().date()
        
        # Get per-model statistics
        model_query = """
            SELECT 
                model,
                COUNT(*) as requests,
                COALESCE(SUM(prompt_tokens), 0) as input_tokens,
                COALESCE(SUM(completion_tokens), 0) as output_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(SUM(spend), 0) as spend,
                AVG(EXTRACT(EPOCH FROM ("endTime" - "startTime")) * 1000) as avg_latency_ms
            FROM "LiteLLM_SpendLogs"
            WHERE "startTime" >= $1
            GROUP BY model
            ORDER BY requests DESC
        """
        model_rows = await conn.fetch(model_query, cutoff_date)
        
        models = []
        for row in model_rows:
            models.append({
                "model": row["model"] or "unknown",
                "requests": row["requests"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "total_tokens": row["total_tokens"],
                "spend": float(row["spend"]) if row["spend"] else 0.0,
                "avg_latency_ms": float(row["avg_latency_ms"]) if row["avg_latency_ms"] else 0.0,
            })
        
        # Get totals
        totals_query = """
            SELECT 
                COUNT(*) as total_requests,
                COALESCE(SUM(prompt_tokens), 0) as total_input_tokens,
                COALESCE(SUM(completion_tokens), 0) as total_output_tokens,
                COALESCE(SUM(spend), 0) as total_spend
            FROM "LiteLLM_SpendLogs"
            WHERE "startTime" >= $1
        """
        totals_row = await conn.fetchrow(totals_query, cutoff_date)
        
        # Get today's tokens
        today_query = """
            SELECT 
                COALESCE(SUM(total_tokens), 0) as tokens_today
            FROM "LiteLLM_SpendLogs"
            WHERE DATE("startTime") = $1
        """
        today_row = await conn.fetchrow(today_query, today)
        
        return {
            "models": models,
            "tokens_today": int(today_row["tokens_today"]) if today_row else 0,
            "total_requests": int(totals_row["total_requests"]) if totals_row else 0,
            "total_input_tokens": int(totals_row["total_input_tokens"]) if totals_row else 0,
            "total_output_tokens": int(totals_row["total_output_tokens"]) if totals_row else 0,
            "total_spend": float(totals_row["total_spend"]) if totals_row else 0.0,
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch LiteLLM stats: {e}", exc_info=True)
        return {
            "models": [],
            "tokens_today": 0,
            "total_requests": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_spend": 0.0,
        }
    finally:
        await conn.close()


# =============================================================================
# Admin Endpoints
# =============================================================================

@router.get("/stats/usage", response_model=UsageStatsResponse)
async def get_usage_stats(
    days: int = 30,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> UsageStatsResponse:
    """
    Get LLM usage statistics across all users.
    
    Combines data from:
    1. LiteLLM SpendLogs (detailed per-model token usage and costs)
    2. Agent workflow executions (tool calls, workflow-level metrics)
    
    Requires admin role.
    
    Args:
        days: Number of days to include in stats (default: 30)
    
    Returns:
        Aggregate usage statistics by model and overall totals.
    """
    require_admin(principal)
    
    try:
        # Fetch LiteLLM spend data (detailed per-model stats)
        litellm_stats = await fetch_litellm_stats(days)
        
        # Calculate date cutoff for workflow stats
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        
        # Get tool calls from workflow executions (not tracked in LiteLLM)
        try:
            workflow_query = text("""
                SELECT COALESCE(SUM(usage_tool_calls), 0) as total_tool_calls
                FROM workflow_executions
                WHERE created_at >= :cutoff_date
            """)
            result = await session.execute(workflow_query, {"cutoff_date": cutoff_date})
            row = result.fetchone()
            total_tool_calls = int(row.total_tool_calls) if row else 0
        except Exception as e:
            logger.warning(f"Failed to get workflow tool calls: {e}")
            total_tool_calls = 0
        
        # Build response using LiteLLM data primarily
        models = [
            ModelUsageStats(
                model=m["model"],
                requests=m["requests"],
                input_tokens=m["input_tokens"],
                output_tokens=m["output_tokens"],
                total_tokens=m["total_tokens"],
                spend=m["spend"],
                avg_latency_ms=m["avg_latency_ms"],
            )
            for m in litellm_stats["models"]
        ]
        
        return UsageStatsResponse(
            models=models[:10],  # Top 10 models
            tokensToday=litellm_stats["tokens_today"],
            totalRequests=litellm_stats["total_requests"],
            totalInputTokens=litellm_stats["total_input_tokens"],
            totalOutputTokens=litellm_stats["total_output_tokens"],
            totalSpend=litellm_stats["total_spend"],
            totalToolCalls=total_tool_calls,
        )
        
    except Exception as e:
        logger.error(f"Failed to get usage stats: {e}", exc_info=True)
        # Return empty stats on error
        return UsageStatsResponse(
            models=[],
            tokensToday=0,
            totalRequests=0,
            totalInputTokens=0,
            totalOutputTokens=0,
            totalSpend=0.0,
            totalToolCalls=0,
        )


@router.get("/stats/system", response_model=SystemStatsResponse)
async def get_system_stats(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> SystemStatsResponse:
    """
    Get system-wide statistics.
    
    Provides counts of agents, conversations, workflows, and active users.
    Requires admin role.
    
    Returns:
        System statistics.
    """
    require_admin(principal)
    
    try:
        # Count agents
        agents_query = text("SELECT COUNT(*) FROM agent_definitions WHERE is_active = true")
        agents_result = await session.execute(agents_query)
        total_agents = agents_result.scalar() or 0
        
        # Count conversations
        conversations_query = text("SELECT COUNT(*) FROM conversations")
        conversations_result = await session.execute(conversations_query)
        total_conversations = conversations_result.scalar() or 0
        
        # Count workflows
        workflows_query = text("SELECT COUNT(*) FROM workflow_definitions WHERE is_active = true")
        workflows_result = await session.execute(workflows_query)
        total_workflows = workflows_result.scalar() or 0
        
        # Count workflow executions (last 30 days)
        cutoff = datetime.utcnow() - timedelta(days=30)
        executions_query = text("""
            SELECT COUNT(*) FROM workflow_executions WHERE created_at >= :cutoff
        """)
        executions_result = await session.execute(executions_query, {"cutoff": cutoff})
        total_executions = executions_result.scalar() or 0
        
        # Count active users (users with conversations in last 7 days)
        users_cutoff = datetime.utcnow() - timedelta(days=7)
        users_query = text("""
            SELECT COUNT(DISTINCT user_id) FROM conversations WHERE created_at >= :cutoff
        """)
        users_result = await session.execute(users_query, {"cutoff": users_cutoff})
        active_users = users_result.scalar() or 0
        
        return SystemStatsResponse(
            totalAgents=total_agents,
            totalConversations=total_conversations,
            totalWorkflows=total_workflows,
            totalWorkflowExecutions=total_executions,
            activeUsers=active_users,
        )
        
    except Exception as e:
        logger.error(f"Failed to get system stats: {e}", exc_info=True)
        return SystemStatsResponse(
            totalAgents=0,
            totalConversations=0,
            totalWorkflows=0,
            totalWorkflowExecutions=0,
            activeUsers=0,
        )
