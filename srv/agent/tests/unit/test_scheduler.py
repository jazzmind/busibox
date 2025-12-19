"""
Unit tests for scheduler service.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.auth import Principal
from app.services.scheduler import RunScheduler, ScheduledJob


def test_parse_cron_valid():
    """Test _parse_cron with valid cron expressions."""
    scheduler = RunScheduler()
    
    # Standard cron
    result = scheduler._parse_cron("0 12 * * *")
    assert result == {
        "minute": "0",
        "hour": "12",
        "day": "*",
        "month": "*",
        "day_of_week": "*",
    }
    
    # Complex cron
    result = scheduler._parse_cron("*/5 9-17 * * 1-5")
    assert result == {
        "minute": "*/5",
        "hour": "9-17",
        "day": "*",
        "month": "*",
        "day_of_week": "1-5",
    }


def test_parse_cron_invalid():
    """Test _parse_cron raises ValueError for invalid expressions."""
    scheduler = RunScheduler()
    
    # Too few fields
    with pytest.raises(ValueError, match="must have 5 fields"):
        scheduler._parse_cron("0 12 *")
    
    # Too many fields
    with pytest.raises(ValueError, match="must have 5 fields"):
        scheduler._parse_cron("0 12 * * * *")
    
    # Empty
    with pytest.raises(ValueError, match="must have 5 fields"):
        scheduler._parse_cron("")


def test_scheduled_job_metadata():
    """Test ScheduledJob metadata structure."""
    agent_id = uuid.uuid4()
    next_run = datetime.now(timezone.utc)
    
    job = ScheduledJob(
        job_id="test-job-123",
        agent_id=agent_id,
        cron="0 12 * * *",
        principal_sub="user-123",
        next_run_time=next_run,
    )
    
    assert job.job_id == "test-job-123"
    assert job.agent_id == agent_id
    assert job.cron == "0 12 * * *"
    assert job.principal_sub == "user-123"
    assert job.next_run_time == next_run


def test_scheduler_initialization():
    """Test RunScheduler initializes correctly."""
    scheduler = RunScheduler()
    
    assert scheduler._started is False
    assert scheduler._job_metadata == {}
    assert scheduler._scheduler is not None


def test_ensure_started():
    """Test _ensure_started starts scheduler once."""
    scheduler = RunScheduler()
    
    assert scheduler._started is False
    
    scheduler._ensure_started()
    assert scheduler._started is True
    
    # Calling again should not restart
    scheduler._ensure_started()
    assert scheduler._started is True


def test_schedule_agent_run_starts_scheduler():
    """Test schedule_agent_run starts scheduler on first use."""
    scheduler = RunScheduler()
    principal = Principal(
        sub="test-user",
        email="test@example.com",
        roles=["user"],
        scopes=["agent.execute"],
    )
    agent_id = uuid.uuid4()
    
    assert scheduler._started is False
    
    # Mock session factory
    session_factory = MagicMock()
    
    job_id = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id,
        payload={"prompt": "test"},
        scopes=["agent.execute"],
        purpose="test",
        cron="0 12 * * *",
    )
    
    assert scheduler._started is True
    assert job_id in scheduler._job_metadata
    
    # Cleanup
    scheduler.shutdown(wait=False)


def test_schedule_agent_run_stores_metadata():
    """Test schedule_agent_run stores job metadata."""
    scheduler = RunScheduler()
    principal = Principal(
        sub="test-user",
        email="test@example.com",
        roles=["user"],
        scopes=["agent.execute"],
    )
    agent_id = uuid.uuid4()
    session_factory = MagicMock()
    
    job_id = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id,
        payload={"prompt": "test"},
        scopes=["agent.execute"],
        purpose="test",
        cron="*/5 * * * *",
        agent_tier="complex",
    )
    
    # Verify metadata stored
    assert job_id in scheduler._job_metadata
    metadata = scheduler._job_metadata[job_id]
    
    assert metadata.job_id == job_id
    assert metadata.agent_id == agent_id
    assert metadata.cron == "*/5 * * * *"
    assert metadata.principal_sub == "test-user"
    assert metadata.next_run_time is not None
    
    # Cleanup
    scheduler.shutdown(wait=False)


def test_get_job():
    """Test get_job retrieves job metadata."""
    scheduler = RunScheduler()
    principal = Principal(sub="test-user", roles=[], scopes=[])
    agent_id = uuid.uuid4()
    session_factory = MagicMock()
    
    job_id = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id,
        payload={"prompt": "test"},
        scopes=[],
        purpose="test",
        cron="0 12 * * *",
    )
    
    # Get job
    job = scheduler.get_job(job_id)
    assert job is not None
    assert job.job_id == job_id
    assert job.agent_id == agent_id
    
    # Non-existent job
    assert scheduler.get_job("non-existent") is None
    
    # Cleanup
    scheduler.shutdown(wait=False)


def test_list_jobs():
    """Test list_jobs returns all scheduled jobs."""
    scheduler = RunScheduler()
    principal = Principal(sub="test-user", roles=[], scopes=[])
    session_factory = MagicMock()
    
    # Schedule multiple jobs
    agent_id_1 = uuid.uuid4()
    agent_id_2 = uuid.uuid4()
    
    job_id_1 = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id_1,
        payload={"prompt": "test1"},
        scopes=[],
        purpose="test",
        cron="0 12 * * *",
    )
    
    job_id_2 = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id_2,
        payload={"prompt": "test2"},
        scopes=[],
        purpose="test",
        cron="0 18 * * *",
    )
    
    # List jobs
    jobs = scheduler.list_jobs()
    assert len(jobs) == 2
    
    job_ids = [job.job_id for job in jobs]
    assert job_id_1 in job_ids
    assert job_id_2 in job_ids
    
    # Cleanup
    scheduler.shutdown(wait=False)


def test_cancel_job():
    """Test cancel_job removes scheduled job."""
    scheduler = RunScheduler()
    principal = Principal(sub="test-user", roles=[], scopes=[])
    agent_id = uuid.uuid4()
    session_factory = MagicMock()
    
    job_id = scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id,
        payload={"prompt": "test"},
        scopes=[],
        purpose="test",
        cron="0 12 * * *",
    )
    
    # Verify job exists
    assert scheduler.get_job(job_id) is not None
    assert len(scheduler.list_jobs()) == 1
    
    # Cancel job
    success = scheduler.cancel_job(job_id)
    assert success is True
    
    # Verify job removed
    assert scheduler.get_job(job_id) is None
    assert len(scheduler.list_jobs()) == 0
    
    # Cancel non-existent job
    success = scheduler.cancel_job("non-existent")
    assert success is False
    
    # Cleanup
    scheduler.shutdown(wait=False)


def test_shutdown():
    """Test shutdown stops scheduler."""
    scheduler = RunScheduler()
    principal = Principal(sub="test-user", roles=[], scopes=[])
    agent_id = uuid.uuid4()
    session_factory = MagicMock()
    
    # Schedule a job (starts scheduler)
    scheduler.schedule_agent_run(
        session_factory=session_factory,
        principal=principal,
        agent_id=agent_id,
        payload={"prompt": "test"},
        scopes=[],
        purpose="test",
        cron="0 12 * * *",
    )
    
    assert scheduler._started is True
    
    # Shutdown
    scheduler.shutdown(wait=False)
    assert scheduler._started is False


@pytest.mark.asyncio
async def test_scheduled_job_execution_with_token_refresh():
    """Test that scheduled jobs refresh tokens before execution."""
    scheduler = RunScheduler()
    principal = Principal(sub="test-user", roles=[], scopes=["agent.execute"])
    agent_id = uuid.uuid4()
    
    # Mock session factory
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock(return_value=mock_session)
    
    # Mock token service and run service
    with patch("app.services.scheduler.get_or_exchange_token") as mock_token:
        with patch("app.services.scheduler.create_run") as mock_run:
            mock_token.return_value = MagicMock(access_token="refreshed-token")
            mock_run_record = MagicMock()
            mock_run_record.id = uuid.uuid4()
            mock_run_record.status = "succeeded"
            mock_run.return_value = mock_run_record
            
            # Schedule job
            job_id = scheduler.schedule_agent_run(
                session_factory=session_factory,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["agent.execute"],
                purpose="test",
                cron="0 12 * * *",
            )
            
            # Get the job function and execute it manually
            apscheduler_job = scheduler._scheduler.get_job(job_id)
            assert apscheduler_job is not None
            
            # Execute the job function
            await apscheduler_job.func()
            
            # Verify token was refreshed
            mock_token.assert_called_once()
            
            # Verify run was created
            mock_run.assert_called_once()
    
    # Cleanup
    scheduler.shutdown(wait=False)








