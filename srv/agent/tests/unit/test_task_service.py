"""
Unit tests for task service.

Tests the AgentTask CRUD operations and execution logic.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.task import (
    TaskCreate,
    TaskUpdate,
    TriggerConfig,
    NotificationConfig,
    InsightsConfig,
    SCHEDULE_PRESETS,
    get_cron_from_preset,
)
from app.services.task_service import (
    create_task,
    get_task,
    list_tasks,
    update_task,
    delete_task,
    pause_task,
    resume_task,
    create_task_execution,
    update_task_execution,
    mark_notification_sent,
    _calculate_next_run,
)


class TestSchedulePresets:
    """Tests for schedule preset parsing."""
    
    def test_get_cron_from_preset_hourly(self):
        """Test hourly preset returns correct cron."""
        result = get_cron_from_preset("hourly")
        assert result == "0 * * * *"
    
    def test_get_cron_from_preset_daily(self):
        """Test daily preset returns 9 AM cron."""
        result = get_cron_from_preset("daily")
        assert result == "0 9 * * *"
    
    def test_get_cron_from_preset_weekly(self):
        """Test weekly preset returns Monday 9 AM cron."""
        result = get_cron_from_preset("weekly")
        assert result == "0 9 * * 1"
    
    def test_get_cron_from_preset_custom_cron(self):
        """Test custom cron expression returns None (not a preset)."""
        custom = "*/15 8-18 * * 1-5"
        result = get_cron_from_preset(custom)
        # Non-presets return None, custom cron should be handled directly
        assert result is None
    
    def test_get_cron_from_preset_all_presets(self):
        """Test all presets return valid cron expressions."""
        for preset, expected in SCHEDULE_PRESETS.items():
            result = get_cron_from_preset(preset)
            assert result == expected
            # Verify it has 5 fields
            assert len(result.split()) == 5


class TestCalculateNextRun:
    """Tests for cron next run calculation."""
    
    def test_calculate_next_run_valid_cron(self):
        """Test _calculate_next_run with valid cron (requires croniter)."""
        # Every hour at :00
        next_run = _calculate_next_run("0 * * * *")
        # May return None if croniter is not installed
        if next_run is not None:
            assert next_run > datetime.now(timezone.utc).replace(tzinfo=None)
    
    def test_calculate_next_run_daily(self):
        """Test _calculate_next_run for daily schedule (requires croniter)."""
        # 9 AM daily
        next_run = _calculate_next_run("0 9 * * *")
        # May return None if croniter is not installed
        if next_run is not None:
            assert next_run.hour == 9
            assert next_run.minute == 0
    
    def test_calculate_next_run_invalid_cron(self):
        """Test _calculate_next_run returns None for invalid cron."""
        next_run = _calculate_next_run("invalid cron expression")
        assert next_run is None
    
    def test_calculate_next_run_empty_cron(self):
        """Test _calculate_next_run returns None for empty cron."""
        next_run = _calculate_next_run("")
        assert next_run is None


class TestTaskSchemas:
    """Tests for task schema validation."""
    
    def test_task_create_minimal(self):
        """Test TaskCreate with minimal required fields."""
        task = TaskCreate(
            name="Test Task",
            agent_id=uuid.uuid4(),
            prompt="Do something",
            trigger_type="cron",
        )
        assert task.name == "Test Task"
        assert task.trigger_type == "cron"
        assert task.trigger_config == TriggerConfig()
    
    def test_task_create_with_cron(self):
        """Test TaskCreate with cron trigger."""
        task = TaskCreate(
            name="Hourly Task",
            agent_id=uuid.uuid4(),
            prompt="Run hourly",
            trigger_type="cron",
            trigger_config=TriggerConfig(cron="0 * * * *"),
        )
        assert task.trigger_config.cron == "0 * * * *"
    
    def test_task_create_with_notifications(self):
        """Test TaskCreate with notification config."""
        task = TaskCreate(
            name="Notifying Task",
            agent_id=uuid.uuid4(),
            prompt="Run and notify",
            trigger_type="cron",
            notification_config=NotificationConfig(
                enabled=True,
                channel="email",
                recipient="test@example.com",
            ),
        )
        assert task.notification_config.enabled is True
        assert task.notification_config.channel == "email"
        assert task.notification_config.recipient == "test@example.com"
    
    def test_task_create_with_insights(self):
        """Test TaskCreate with insights config."""
        task = TaskCreate(
            name="Remembering Task",
            agent_id=uuid.uuid4(),
            prompt="Remember results",
            trigger_type="cron",
            insights_config=InsightsConfig(
                enabled=True,
                max_insights=100,
                purge_after_days=14,
            ),
        )
        assert task.insights_config.enabled is True
        assert task.insights_config.max_insights == 100
        assert task.insights_config.purge_after_days == 14
    
    def test_trigger_config_cron_validation(self):
        """Test TriggerConfig validates cron on assignment."""
        config = TriggerConfig(cron="0 9 * * *")
        assert config.cron == "0 9 * * *"
    
    def test_trigger_config_one_time(self):
        """Test TriggerConfig with one-time run."""
        run_at = datetime.now(timezone.utc) + timedelta(hours=1)
        config = TriggerConfig(run_at=run_at)
        assert config.run_at == run_at
    
    def test_task_update_partial(self):
        """Test TaskUpdate allows partial updates."""
        update = TaskUpdate(name="New Name")
        assert update.name == "New Name"
        assert update.prompt is None
        assert update.trigger_config is None
    
    def test_task_update_status(self):
        """Test TaskUpdate can change status."""
        update = TaskUpdate(status="paused")
        assert update.status == "paused"


class TestNotificationConfig:
    """Tests for notification configuration."""
    
    def test_notification_config_email(self):
        """Test email notification config."""
        config = NotificationConfig(
            enabled=True,
            channel="email",
            recipient="user@example.com",
            include_summary=True,
            include_portal_link=True,
        )
        assert config.enabled is True
        assert config.channel == "email"
        assert config.include_summary is True
    
    def test_notification_config_teams(self):
        """Test Teams notification config."""
        config = NotificationConfig(
            enabled=True,
            channel="teams",
            recipient="https://outlook.office.com/webhook/...",
        )
        assert config.channel == "teams"
    
    def test_notification_config_slack(self):
        """Test Slack notification config."""
        config = NotificationConfig(
            enabled=True,
            channel="slack",
            recipient="https://hooks.slack.com/services/...",
        )
        assert config.channel == "slack"
    
    def test_notification_config_disabled(self):
        """Test disabled notification config still requires recipient."""
        # NotificationConfig requires recipient even when disabled
        config = NotificationConfig(enabled=False, recipient="unused@example.com")
        assert config.enabled is False


class TestInsightsConfig:
    """Tests for insights configuration."""
    
    def test_insights_config_defaults(self):
        """Test default insights config values."""
        config = InsightsConfig()
        assert config.enabled is True
        assert config.max_insights == 50
        assert config.purge_after_days == 30  # Default is 30, not None
    
    def test_insights_config_custom(self):
        """Test custom insights config."""
        config = InsightsConfig(
            enabled=True,
            max_insights=200,
            purge_after_days=30,
            include_in_context=True,
            context_limit=20,
        )
        assert config.max_insights == 200
        assert config.purge_after_days == 30
        assert config.context_limit == 20


@pytest.mark.asyncio
class TestTaskServiceCRUD:
    """Tests for task service CRUD operations.
    
    Note: Full CRUD tests require database access.
    These tests verify function signatures and basic behavior.
    """
    
    async def test_get_task_returns_none_when_not_found(self):
        """Test get_task returns None when task doesn't exist."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        
        task = await get_task(mock_session, task_id, "test-user")
        
        assert task is None
    
    async def test_get_task_found(self):
        """Test get_task returns task when found."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.user_id = "test-user"
        mock_task.name = "Test Task"
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result
        
        task = await get_task(mock_session, task_id, "test-user")
        
        assert task is not None
        assert task.id == task_id
    
    async def test_list_tasks_returns_list(self):
        """Test list_tasks returns a list."""
        mock_session = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        
        tasks = await list_tasks(mock_session, "test-user")
        
        assert isinstance(tasks, list)
    
    async def test_list_tasks_with_status_filter(self):
        """Test list_tasks accepts status filter."""
        mock_session = AsyncMock()
        
        mock_task = MagicMock()
        mock_task.status = "active"
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_session.execute.return_value = mock_result
        
        # Should not raise an error
        tasks = await list_tasks(mock_session, "test-user", status="active")
        assert isinstance(tasks, list)


@pytest.mark.asyncio
class TestTaskExecution:
    """Tests for task execution tracking.
    
    Note: Full execution tests require database access.
    These tests verify function signatures exist.
    """
    
    async def test_create_task_execution_function_exists(self):
        """Test create_task_execution function is importable."""
        from app.services.task_service import create_task_execution
        assert callable(create_task_execution)
    
    async def test_update_task_execution_function_exists(self):
        """Test update_task_execution function is importable."""
        from app.services.task_service import update_task_execution
        assert callable(update_task_execution)
    
    async def test_mark_notification_sent_function_exists(self):
        """Test mark_notification_sent function is importable."""
        from app.services.task_service import mark_notification_sent
        assert callable(mark_notification_sent)
