"""
Enhanced unit tests for run service with event tracking and tracing.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.services.run_service import (
    add_run_event,
    get_agent_memory_limit,
    get_agent_timeout,
    get_run_by_id,
    list_runs,
)


def test_get_agent_timeout_returns_correct_values():
    """Test get_agent_timeout returns correct timeout for each tier."""
    assert get_agent_timeout("simple") == 30
    assert get_agent_timeout("complex") == 300
    assert get_agent_timeout("batch") == 1800
    assert get_agent_timeout("invalid") == 30  # defaults to simple
    assert get_agent_timeout() == 30  # defaults to simple


def test_get_agent_memory_limit_returns_correct_values():
    """Test get_agent_memory_limit returns correct memory limit for each tier."""
    assert get_agent_memory_limit("simple") == 512
    assert get_agent_memory_limit("complex") == 2048
    assert get_agent_memory_limit("batch") == 4096
    assert get_agent_memory_limit("invalid") == 512  # defaults to simple
    assert get_agent_memory_limit() == 512  # defaults to simple


def test_add_run_event_creates_event_with_timestamp():
    """Test add_run_event creates properly formatted event."""
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="running",
        input={"prompt": "test"},
        events=[],
    )

    add_run_event(run_record, "test_event", data={"key": "value"})

    assert len(run_record.events) == 1
    event = run_record.events[0]
    assert event["type"] == "test_event"
    assert event["data"] == {"key": "value"}
    assert "timestamp" in event
    # Verify timestamp is ISO format
    datetime.fromisoformat(event["timestamp"])


def test_add_run_event_handles_error():
    """Test add_run_event includes error message."""
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="running",
        input={"prompt": "test"},
        events=[],
    )

    add_run_event(run_record, "error", error="Something went wrong")

    assert len(run_record.events) == 1
    event = run_record.events[0]
    assert event["type"] == "error"
    assert event["error"] == "Something went wrong"


def test_add_run_event_initializes_empty_list():
    """Test add_run_event initializes events list if not present."""
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="running",
        input={"prompt": "test"},
    )
    # events might be None or not set
    run_record.events = None

    add_run_event(run_record, "test_event")

    assert isinstance(run_record.events, list)
    assert len(run_record.events) == 1


def test_add_run_event_appends_to_existing_events():
    """Test add_run_event appends to existing events list."""
    run_record = RunRecord(
        agent_id=uuid.uuid4(),
        status="running",
        input={"prompt": "test"},
        events=[{"type": "existing", "timestamp": "2025-01-01T00:00:00Z"}],
    )

    add_run_event(run_record, "new_event", data={"new": "data"})

    assert len(run_record.events) == 2
    assert run_record.events[0]["type"] == "existing"
    assert run_record.events[1]["type"] == "new_event"


@pytest.mark.asyncio
async def test_get_run_by_id_returns_run(test_session):
    """Test get_run_by_id retrieves run record."""
    from app.services.run_service import get_run_by_id

    # Create a run record
    run_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    run_record = RunRecord(
        id=run_id,
        agent_id=agent_id,
        status="succeeded",
        input={"prompt": "test"},
        output={"message": "success"},
    )
    test_session.add(run_record)
    await test_session.commit()

    # Retrieve it
    result = await get_run_by_id(test_session, run_id)

    assert result is not None
    assert result.id == run_id
    assert result.agent_id == agent_id
    assert result.status == "succeeded"


@pytest.mark.asyncio
async def test_get_run_by_id_returns_none_for_missing(test_session):
    """Test get_run_by_id returns None for non-existent run."""
    from app.services.run_service import get_run_by_id

    result = await get_run_by_id(test_session, uuid.uuid4())

    assert result is None


@pytest.mark.asyncio
async def test_list_runs_returns_all_runs(test_session):
    """Test list_runs returns all run records."""
    from app.services.run_service import list_runs

    # Create multiple run records
    agent_id = uuid.uuid4()
    for i in range(3):
        run_record = RunRecord(
            agent_id=agent_id,
            status="succeeded",
            input={"prompt": f"test {i}"},
        )
        test_session.add(run_record)
    await test_session.commit()

    # List all runs
    results = await list_runs(test_session)

    assert len(results) >= 3


@pytest.mark.asyncio
async def test_list_runs_filters_by_agent_id(test_session):
    """Test list_runs filters by agent_id."""
    from app.services.run_service import list_runs

    # Create runs for different agents
    agent_id_1 = uuid.uuid4()
    agent_id_2 = uuid.uuid4()

    run1 = RunRecord(agent_id=agent_id_1, status="succeeded", input={"prompt": "test1"})
    run2 = RunRecord(agent_id=agent_id_2, status="succeeded", input={"prompt": "test2"})
    test_session.add(run1)
    test_session.add(run2)
    await test_session.commit()

    # Filter by agent_id_1
    results = await list_runs(test_session, agent_id=agent_id_1)

    assert len(results) >= 1
    assert all(r.agent_id == agent_id_1 for r in results)


@pytest.mark.asyncio
async def test_list_runs_filters_by_status(test_session):
    """Test list_runs filters by status."""
    from app.services.run_service import list_runs

    # Create runs with different statuses
    agent_id = uuid.uuid4()
    run1 = RunRecord(agent_id=agent_id, status="succeeded", input={"prompt": "test1"})
    run2 = RunRecord(agent_id=agent_id, status="failed", input={"prompt": "test2"})
    test_session.add(run1)
    test_session.add(run2)
    await test_session.commit()

    # Filter by status
    results = await list_runs(test_session, status="succeeded")

    assert len(results) >= 1
    assert all(r.status == "succeeded" for r in results)


@pytest.mark.asyncio
async def test_list_runs_respects_limit(test_session):
    """Test list_runs respects limit parameter."""
    from app.services.run_service import list_runs

    # Create multiple runs
    agent_id = uuid.uuid4()
    for i in range(10):
        run_record = RunRecord(
            agent_id=agent_id,
            status="succeeded",
            input={"prompt": f"test {i}"},
        )
        test_session.add(run_record)
    await test_session.commit()

    # List with limit
    results = await list_runs(test_session, limit=5)

    assert len(results) <= 5


@pytest.mark.asyncio
async def test_list_runs_respects_offset(test_session):
    """Test list_runs respects offset parameter."""
    from app.services.run_service import list_runs

    # Create multiple runs
    agent_id = uuid.uuid4()
    for i in range(10):
        run_record = RunRecord(
            agent_id=agent_id,
            status="succeeded",
            input={"prompt": f"test {i}"},
        )
        test_session.add(run_record)
    await test_session.commit()

    # Get first page
    page1 = await list_runs(test_session, limit=5, offset=0)
    # Get second page
    page2 = await list_runs(test_session, limit=5, offset=5)

    # Pages should be different
    page1_ids = {r.id for r in page1}
    page2_ids = {r.id for r in page2}
    assert page1_ids.isdisjoint(page2_ids)





