"""
API endpoints for agent run scoring and evaluation.

Provides:
- POST /scores/execute: Execute scorer against a run
- GET /scores/aggregates: Get aggregated score statistics
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.db.session import get_session
from app.schemas.auth import Principal
from app.services.scorer_service import execute_scorer, get_score_aggregates

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/scores", tags=["scores"])


class ScoreExecuteRequest(BaseModel):
    """Request to execute a scorer against a run."""
    
    scorer_id: uuid.UUID = Field(description="Scorer definition UUID")
    run_id: uuid.UUID = Field(description="Run UUID to score")


class ScoreResult(BaseModel):
    """Result of scoring a run."""
    
    run_id: uuid.UUID
    scorer_name: str
    score: float = Field(ge=0.0, le=1.0, description="Score between 0 and 1")
    passed: bool
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str


@router.post("/execute", response_model=ScoreResult)
async def execute_score(
    payload: ScoreExecuteRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ScoreResult:
    """
    Execute a scorer against a completed run.
    
    Args:
        payload: Score execution request with scorer_id and run_id
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        ScoreResult with score, pass/fail status, and details
        
    Raises:
        HTTPException: 404 if scorer or run not found, 400 if run not completed
    """
    try:
        result = await execute_scorer(
            session=session,
            scorer_id=payload.scorer_id,
            run_id=payload.run_id,
        )
        
        logger.info(
            f"Executed scorer {result.scorer_name} on run {result.run_id}: "
            f"score={result.score}, passed={result.passed}",
            extra={
                "run_id": str(result.run_id),
                "scorer_name": result.scorer_name,
                "score": result.score,
                "passed": result.passed,
            },
        )
        
        return ScoreResult(
            run_id=result.run_id,
            scorer_name=result.scorer_name,
            score=result.score,
            passed=result.passed,
            details=result.details,
            timestamp=result.timestamp.isoformat(),
        )
        
    except ValueError as e:
        logger.error(f"Score execution failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Score execution error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Score execution failed: {str(e)}"
        )


@router.get("/aggregates")
async def get_aggregates(
    agent_id: Optional[uuid.UUID] = Query(None, description="Filter by agent ID"),
    scorer_name: Optional[str] = Query(None, description="Filter by scorer name"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Dict[str, Any]:
    """
    Get aggregated score statistics.
    
    Args:
        agent_id: Optional filter by agent
        scorer_name: Optional filter by scorer
        principal: Authenticated user principal
        session: Database session
        
    Returns:
        Dict with aggregated statistics (avg, min, max, percentiles)
    """
    try:
        aggregates = await get_score_aggregates(
            session=session,
            agent_id=agent_id,
            scorer_name=scorer_name,
        )
        
        logger.info(
            f"Retrieved score aggregates: {aggregates.get('total_runs', 0)} runs",
            extra={
                "agent_id": str(agent_id) if agent_id else None,
                "scorer_name": scorer_name,
            },
        )
        
        return aggregates
        
    except Exception as e:
        logger.error(f"Failed to get score aggregates: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get aggregates: {str(e)}"
        )





