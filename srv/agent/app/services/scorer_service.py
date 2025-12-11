"""
Scorer service for evaluating agent run performance.

Supports:
- Latency scoring
- Success rate scoring
- Custom metric evaluation
- Score aggregation and statistics
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalDefinition, RunRecord

logger = logging.getLogger(__name__)


class ScorerResult:
    """Result of scoring a run."""
    
    def __init__(
        self,
        run_id: uuid.UUID,
        scorer_name: str,
        score: float,
        passed: bool,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.run_id = run_id
        self.scorer_name = scorer_name
        self.score = score
        self.passed = passed
        self.details = details or {}
        self.timestamp = datetime.now(timezone.utc)


def score_latency(run_record: RunRecord, threshold_ms: int = 5000) -> ScorerResult:
    """
    Score run based on execution latency.
    
    Args:
        run_record: Run to score
        threshold_ms: Maximum acceptable latency in milliseconds
        
    Returns:
        ScorerResult with latency score (0-1, higher is better)
    """
    # Calculate latency
    latency_seconds = (run_record.updated_at - run_record.created_at).total_seconds()
    latency_ms = latency_seconds * 1000
    
    # Score: 1.0 if under threshold, decreases linearly above threshold
    if latency_ms <= threshold_ms:
        score = 1.0
    else:
        # Decrease score by 0.1 for each second over threshold
        overage_seconds = (latency_ms - threshold_ms) / 1000
        score = max(0.0, 1.0 - (overage_seconds * 0.1))
    
    passed = latency_ms <= threshold_ms
    
    return ScorerResult(
        run_id=run_record.id,
        scorer_name="latency",
        score=score,
        passed=passed,
        details={
            "latency_ms": latency_ms,
            "threshold_ms": threshold_ms,
            "latency_seconds": latency_seconds,
        },
    )


def score_success(run_record: RunRecord) -> ScorerResult:
    """
    Score run based on success/failure status.
    
    Args:
        run_record: Run to score
        
    Returns:
        ScorerResult with success score (1.0 or 0.0)
    """
    score = 1.0 if run_record.status == "succeeded" else 0.0
    passed = run_record.status == "succeeded"
    
    return ScorerResult(
        run_id=run_record.id,
        scorer_name="success",
        score=score,
        passed=passed,
        details={
            "status": run_record.status,
        },
    )


def score_tool_usage(run_record: RunRecord, expected_tools: Optional[List[str]] = None) -> ScorerResult:
    """
    Score run based on tool usage patterns.
    
    Args:
        run_record: Run to score
        expected_tools: Optional list of tools that should be used
        
    Returns:
        ScorerResult with tool usage score
    """
    # Count tool calls in events
    tool_events = [
        e for e in run_record.events
        if e.get("type") in ["tool_call", "step_completed"] and "tool" in e.get("data", {})
    ]
    
    tool_count = len(tool_events)
    
    if expected_tools:
        # Check if expected tools were used
        used_tools = set()
        for event in tool_events:
            tool_name = event.get("data", {}).get("tool")
            if tool_name:
                used_tools.add(tool_name)
        
        expected_set = set(expected_tools)
        matched = len(used_tools & expected_set)
        score = matched / len(expected_set) if expected_set else 1.0
        passed = matched == len(expected_set)
        
        details = {
            "expected_tools": expected_tools,
            "used_tools": list(used_tools),
            "matched": matched,
            "total_tool_calls": tool_count,
        }
    else:
        # Just score based on whether tools were used
        score = 1.0 if tool_count > 0 else 0.5
        passed = tool_count > 0
        
        details = {
            "total_tool_calls": tool_count,
        }
    
    return ScorerResult(
        run_id=run_record.id,
        scorer_name="tool_usage",
        score=score,
        passed=passed,
        details=details,
    )


async def execute_scorer(
    session: AsyncSession,
    scorer_id: uuid.UUID,
    run_id: uuid.UUID,
) -> ScorerResult:
    """
    Execute a scorer against a completed run.
    
    Args:
        session: Database session
        scorer_id: Scorer definition UUID
        run_id: Run to score
        
    Returns:
        ScorerResult with score and details
        
    Raises:
        ValueError: If scorer or run not found, or run not completed
    """
    # Load scorer definition
    scorer = await session.get(EvalDefinition, scorer_id)
    if not scorer:
        raise ValueError(f"Scorer {scorer_id} not found")
    
    if not scorer.is_active:
        raise ValueError(f"Scorer {scorer.name} is not active")
    
    # Load run record
    run_record = await session.get(RunRecord, run_id)
    if not run_record:
        raise ValueError(f"Run {run_id} not found")
    
    # Only score completed runs
    if run_record.status not in ["succeeded", "failed", "timeout"]:
        raise ValueError(f"Run {run_id} is not completed (status: {run_record.status})")
    
    # Execute scorer based on type
    scorer_type = scorer.config.get("type", "success")
    
    if scorer_type == "latency":
        threshold_ms = scorer.config.get("threshold_ms", 5000)
        result = score_latency(run_record, threshold_ms)
    elif scorer_type == "success":
        result = score_success(run_record)
    elif scorer_type == "tool_usage":
        expected_tools = scorer.config.get("expected_tools")
        result = score_tool_usage(run_record, expected_tools)
    else:
        raise ValueError(f"Unknown scorer type: {scorer_type}")
    
    logger.info(
        f"Scored run {run_id} with {scorer.name}: score={result.score}, passed={result.passed}"
    )
    
    return result


async def get_score_aggregates(
    session: AsyncSession,
    agent_id: Optional[uuid.UUID] = None,
    scorer_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get aggregated score statistics.
    
    Note: This is a simplified implementation. In production, scores would be
    stored in a separate table for efficient aggregation.
    
    Args:
        session: Database session
        agent_id: Optional filter by agent
        scorer_name: Optional filter by scorer name
        
    Returns:
        Dict with aggregated statistics
    """
    # For now, return placeholder aggregates
    # In a full implementation, we'd query a scores table
    
    stmt = select(func.count(RunRecord.id)).where(RunRecord.status == "succeeded")
    
    if agent_id:
        stmt = stmt.where(RunRecord.agent_id == agent_id)
    
    result = await session.execute(stmt)
    success_count = result.scalar() or 0
    
    stmt = select(func.count(RunRecord.id))
    if agent_id:
        stmt = stmt.where(RunRecord.agent_id == agent_id)
    
    result = await session.execute(stmt)
    total_count = result.scalar() or 0
    
    success_rate = success_count / total_count if total_count > 0 else 0.0
    
    return {
        "total_runs": total_count,
        "successful_runs": success_count,
        "success_rate": success_rate,
        "agent_id": str(agent_id) if agent_id else None,
        "scorer_name": scorer_name,
        "note": "Simplified aggregation - full implementation would use scores table",
    }
