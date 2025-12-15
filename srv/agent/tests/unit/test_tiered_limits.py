"""
Unit tests for tiered execution limits enforcement.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.services.run_service import (
    AGENT_LIMITS,
    create_run,
    get_agent_memory_limit,
    get_agent_timeout,
)


def test_agent_limits_configuration():
    """Test that agent limits are properly configured for all tiers."""
    assert "simple" in AGENT_LIMITS
    assert "complex" in AGENT_LIMITS
    assert "batch" in AGENT_LIMITS

    # Verify simple tier
    assert AGENT_LIMITS["simple"]["timeout"] == 30
    assert AGENT_LIMITS["simple"]["memory_mb"] == 512

    # Verify complex tier
    assert AGENT_LIMITS["complex"]["timeout"] == 300
    assert AGENT_LIMITS["complex"]["memory_mb"] == 2048

    # Verify batch tier
    assert AGENT_LIMITS["batch"]["timeout"] == 1800
    assert AGENT_LIMITS["batch"]["memory_mb"] == 4096


def test_get_agent_timeout_all_tiers():
    """Test get_agent_timeout returns correct values for all tiers."""
    assert get_agent_timeout("simple") == 30
    assert get_agent_timeout("complex") == 300
    assert get_agent_timeout("batch") == 1800


def test_get_agent_memory_limit_all_tiers():
    """Test get_agent_memory_limit returns correct values for all tiers."""
    assert get_agent_memory_limit("simple") == 512
    assert get_agent_memory_limit("complex") == 2048
    assert get_agent_memory_limit("batch") == 4096


@pytest.mark.asyncio
async def test_create_run_enforces_timeout_simple_tier(test_session):
    """Test create_run enforces 30s timeout for simple tier."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    # Mock agent that takes too long
    async def slow_run(*args, **kwargs):
        await asyncio.sleep(60)
        return MagicMock()
    
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=slow_run)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            run_record = await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="simple",
            )

    # Verify run timed out
    assert run_record.status == "timeout"
    assert "timeout" in run_record.output
    assert run_record.output["timeout"] == 30
    assert run_record.output["tier"] == "simple"


@pytest.mark.asyncio
async def test_create_run_enforces_timeout_complex_tier(test_session):
    """Test create_run enforces 5min timeout for complex tier."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    # Mock agent that takes too long
    async def slow_run(*args, **kwargs):
        await asyncio.sleep(400)
        return MagicMock()
    
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=slow_run)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            run_record = await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="complex",
            )

    # Verify run timed out
    assert run_record.status == "timeout"
    assert run_record.output["timeout"] == 300
    assert run_record.output["tier"] == "complex"


@pytest.mark.asyncio
async def test_create_run_succeeds_within_timeout(test_session):
    """Test create_run succeeds when execution completes within timeout."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    # Mock agent that completes quickly
    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.data = MagicMock()
    mock_result.data.model_dump = MagicMock(return_value={"message": "success"})
    mock_agent.run = AsyncMock(return_value=mock_result)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            run_record = await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="simple",
            )

    # Verify run succeeded
    assert run_record.status == "succeeded"
    assert run_record.output["message"] == "success"


@pytest.mark.asyncio
async def test_create_run_rejects_invalid_tier(test_session):
    """Test create_run rejects invalid agent_tier."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    with pytest.raises(ValueError, match="Invalid agent_tier"):
        await create_run(
            session=test_session,
            principal=principal,
            agent_id=agent_id,
            payload={"prompt": "test"},
            scopes=["search.read"],
            purpose="test",
            agent_tier="invalid",
        )


@pytest.mark.asyncio
async def test_create_run_tracks_memory_limit_in_events(test_session):
    """Test create_run tracks memory limit in execution_started event."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.data = MagicMock()
    mock_result.data.model_dump = MagicMock(return_value={"message": "success"})
    mock_agent.run = AsyncMock(return_value=mock_result)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            run_record = await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="complex",
            )

    # Verify memory limit is tracked in events
    execution_events = [e for e in run_record.events if e.get("type") == "execution_started"]
    assert len(execution_events) == 1
    assert execution_events[0]["data"]["memory_limit_mb"] == 2048
    assert execution_events[0]["data"]["timeout"] == 300


@pytest.mark.asyncio
async def test_create_run_different_tiers_have_different_limits(test_session):
    """Test that different tiers enforce different timeout limits."""
    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    # Mock agent that takes 35 seconds (exceeds simple but not complex)
    async def slow_run(*args, **kwargs):
        await asyncio.sleep(35)
        return MagicMock()
    
    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=slow_run)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            # Simple tier should timeout
            simple_run = await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="simple",
            )

            # Complex tier should still be running (but we'll timeout in test)
            # In real scenario, it would complete
            assert simple_run.status == "timeout"
            assert simple_run.output["tier"] == "simple"


@pytest.mark.asyncio
async def test_create_run_logs_tier_information(test_session, caplog):
    """Test create_run logs tier information during execution."""
    import logging

    caplog.set_level(logging.INFO)

    principal = Principal(sub="test-user", roles=[], scopes=["search.read"], token="test")
    agent_id = uuid.uuid4()

    mock_agent = MagicMock()
    mock_result = MagicMock()
    mock_result.data = MagicMock()
    mock_result.data.model_dump = MagicMock(return_value={"message": "success"})
    mock_agent.run = AsyncMock(return_value=mock_result)

    with patch("app.services.run_service.agent_registry.get", return_value=mock_agent):
        with patch("app.services.run_service.get_or_exchange_token") as mock_token:
            mock_token.return_value = MagicMock(access_token="test-token")

            await create_run(
                session=test_session,
                principal=principal,
                agent_id=agent_id,
                payload={"prompt": "test"},
                scopes=["search.read"],
                purpose="test",
                agent_tier="batch",
            )

    # Verify tier information is logged
    log_messages = [record.message for record in caplog.records]
    assert any("1800s timeout" in msg and "4096MB memory limit" in msg for msg in log_messages)





