"""
Unit tests for scorer service.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import EvalDefinition, RunRecord
from app.services.scorer_service import (
    ScorerResult,
    execute_scorer,
    get_score_aggregates,
    score_latency,
    score_success,
    score_tool_usage,
)


def _now_naive() -> datetime:
    """Return timezone-naive UTC datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_scorer_result_structure():
    """Test ScorerResult structure."""
    run_id = uuid.uuid4()
    result = ScorerResult(
        run_id=run_id,
        scorer_name="test",
        score=0.85,
        passed=True,
        details={"key": "value"},
    )
    
    assert result.run_id == run_id
    assert result.scorer_name == "test"
    assert result.score == 0.85
    assert result.passed is True
    assert result.details == {"key": "value"}
    assert isinstance(result.timestamp, datetime)


def test_score_latency_under_threshold():
    """Test score_latency gives perfect score under threshold."""
    now = datetime.now(timezone.utc)
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        created_at=now,
        updated_at=now + timedelta(seconds=2),  # 2 seconds
    )
    
    result = score_latency(run_record, threshold_ms=5000)
    
    assert result.score == 1.0
    assert result.passed is True
    assert result.details["latency_ms"] == 2000
    assert result.details["threshold_ms"] == 5000


def test_score_latency_over_threshold():
    """Test score_latency decreases score over threshold."""
    now = datetime.now(timezone.utc)
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        created_at=now,
        updated_at=now + timedelta(seconds=10),  # 10 seconds
    )
    
    result = score_latency(run_record, threshold_ms=5000)
    
    # 10s = 10000ms, threshold 5000ms, overage 5000ms = 5 seconds
    # Score = 1.0 - (5 * 0.1) = 0.5
    assert result.score == 0.5
    assert result.passed is False
    assert result.details["latency_ms"] == 10000


def test_score_latency_way_over_threshold():
    """Test score_latency floors at 0.0 for extreme latency."""
    now = datetime.now(timezone.utc)
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        created_at=now,
        updated_at=now + timedelta(seconds=60),  # 60 seconds
    )
    
    result = score_latency(run_record, threshold_ms=5000)
    
    # Should floor at 0.0
    assert result.score == 0.0
    assert result.passed is False


def test_score_success_succeeded():
    """Test score_success gives 1.0 for succeeded runs."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    
    result = score_success(run_record)
    
    assert result.score == 1.0
    assert result.passed is True
    assert result.details["status"] == "succeeded"


def test_score_success_failed():
    """Test score_success gives 0.0 for failed runs."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="failed",
        input={},
        output={},
        created_by="test",
    )
    
    result = score_success(run_record)
    
    assert result.score == 0.0
    assert result.passed is False
    assert result.details["status"] == "failed"


def test_score_tool_usage_no_expected():
    """Test score_tool_usage without expected tools."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        events=[
            {"type": "tool_call", "data": {"tool": "search"}},
            {"type": "tool_call", "data": {"tool": "rag"}},
        ],
    )
    
    result = score_tool_usage(run_record)
    
    assert result.score == 1.0
    assert result.passed is True
    assert result.details["total_tool_calls"] == 2


def test_score_tool_usage_with_expected_all_matched():
    """Test score_tool_usage with expected tools all matched."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        events=[
            {"type": "tool_call", "data": {"tool": "search"}},
            {"type": "tool_call", "data": {"tool": "rag"}},
        ],
    )
    
    result = score_tool_usage(run_record, expected_tools=["search", "rag"])
    
    assert result.score == 1.0
    assert result.passed is True
    assert result.details["matched"] == 2
    assert set(result.details["used_tools"]) == {"search", "rag"}


def test_score_tool_usage_with_expected_partial_match():
    """Test score_tool_usage with expected tools partially matched."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        events=[
            {"type": "tool_call", "data": {"tool": "search"}},
        ],
    )
    
    result = score_tool_usage(run_record, expected_tools=["search", "rag", "ingest"])
    
    # 1 out of 3 expected tools used
    assert result.score == pytest.approx(0.333, abs=0.01)
    assert result.passed is False
    assert result.details["matched"] == 1


def test_score_tool_usage_no_tools():
    """Test score_tool_usage with no tool usage."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        events=[],
    )
    
    result = score_tool_usage(run_record)
    
    assert result.score == 0.5  # No tools used
    assert result.passed is False


@pytest.mark.asyncio
async def test_execute_scorer_latency(test_session: AsyncSession):
    """Test execute_scorer with latency scorer."""
    # Create scorer
    scorer = EvalDefinition(
        name="latency-scorer",
        description="Latency evaluation",
        config={"type": "latency", "threshold_ms": 3000},
        is_active=True,
    )
    test_session.add(scorer)
    
    # Create run with timezone-naive datetimes for database compatibility
    now = _now_naive()
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
        created_at=now,
        updated_at=now + timedelta(seconds=2),
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(scorer)
    await test_session.refresh(run_record)
    
    # Execute scorer
    result = await execute_scorer(test_session, scorer.id, run_record.id)
    
    assert result.scorer_name == "latency"
    assert result.score == 1.0
    assert result.passed is True


@pytest.mark.asyncio
async def test_execute_scorer_success(test_session: AsyncSession):
    """Test execute_scorer with success scorer."""
    # Create scorer
    scorer = EvalDefinition(
        name="success-scorer",
        config={"type": "success"},
        is_active=True,
    )
    test_session.add(scorer)
    
    # Create succeeded run
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(scorer)
    await test_session.refresh(run_record)
    
    # Execute scorer
    result = await execute_scorer(test_session, scorer.id, run_record.id)
    
    assert result.scorer_name == "success"
    assert result.score == 1.0
    assert result.passed is True


@pytest.mark.asyncio
async def test_execute_scorer_not_found(test_session: AsyncSession):
    """Test execute_scorer raises ValueError for non-existent scorer."""
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)
    
    with pytest.raises(ValueError, match="Scorer .* not found"):
        await execute_scorer(test_session, uuid.uuid4(), run_record.id)


@pytest.mark.asyncio
async def test_execute_scorer_run_not_completed(test_session: AsyncSession):
    """Test execute_scorer raises ValueError for incomplete run."""
    scorer = EvalDefinition(
        name="test-scorer",
        config={"type": "success"},
        is_active=True,
    )
    test_session.add(scorer)
    
    # Create running (not completed) run
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="running",  # Not completed
        input={},
        output={},
        created_by="test",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(scorer)
    await test_session.refresh(run_record)
    
    with pytest.raises(ValueError, match="is not completed"):
        await execute_scorer(test_session, scorer.id, run_record.id)


@pytest.mark.asyncio
async def test_get_score_aggregates_no_runs(test_session: AsyncSession):
    """Test get_score_aggregates with no runs."""
    aggregates = await get_score_aggregates(test_session)
    
    assert aggregates["total_runs"] == 0
    assert aggregates["successful_runs"] == 0
    assert aggregates["success_rate"] == 0.0


@pytest.mark.asyncio
async def test_get_score_aggregates_with_runs(test_session: AsyncSession):
    """Test get_score_aggregates calculates statistics."""
    agent_id = uuid.uuid4()
    
    # Create runs
    run1 = RunRecord(
        agent_id=agent_id,
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    run2 = RunRecord(
        agent_id=agent_id,
        status="succeeded",
        input={},
        output={},
        created_by="test",
    )
    run3 = RunRecord(
        agent_id=agent_id,
        status="failed",
        input={},
        output={},
        created_by="test",
    )
    
    test_session.add_all([run1, run2, run3])
    await test_session.commit()
    
    # Get aggregates
    aggregates = await get_score_aggregates(test_session, agent_id=agent_id)
    
    assert aggregates["total_runs"] == 3
    assert aggregates["successful_runs"] == 2
    assert aggregates["success_rate"] == pytest.approx(0.667, abs=0.01)
    assert aggregates["agent_id"] == str(agent_id)








