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
        """Test custom cron expression is returned as-is."""
        custom = "*/15 8-18 * * 1-5"
        result = get_cron_from_preset(custom)
        assert result == custom
    
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
        """Test _calculate_next_run with valid cron."""
        # Every hour at :00
        next_run = _calculate_next_run("0 * * * *")
        assert next_run is not None
        assert next_run > datetime.now(timezone.utc).replace(tzinfo=None)
    
    def test_calculate_next_run_daily(self):
        """Test _calculate_next_run for daily schedule."""
        # 9 AM daily
        next_run = _calculate_next_run("0 9 * * *")
        assert next_run is not None
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
        """Test disabled notification config."""
        config = NotificationConfig(enabled=False)
        assert config.enabled is False


class TestInsightsConfig:
    """Tests for insights configuration."""
    
    def test_insights_config_defaults(self):
        """Test default insights config values."""
        config = InsightsConfig()
        assert config.enabled is True
        assert config.max_insights == 50
        assert config.purge_after_days is None
    
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
    """Tests for task service CRUD operations."""
    
    async def test_create_task_basic(self):
        """Test create_task creates a new task."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        
        agent_id = uuid.uuid4()
        task_data = TaskCreate(
            name="Test Task",
            agent_id=agent_id,
            prompt="Test prompt",
            trigger_type="cron",
            trigger_config=TriggerConfig(cron="0 9 * * *"),
        )
        
        with patch("app.services.task_service.uuid.uuid4", return_value=uuid.UUID("12345678-1234-5678-1234-567812345678")):
            task = await create_task(
                session=mock_session,
                task_data=task_data,
                user_id="test-user",
            )
        
        assert mock_session.add.called
        assert mock_session.commit.called
    
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
    
    async def test_get_task_not_found(self):
        """Test get_task returns None when not found."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result
        
        task = await get_task(mock_session, task_id, "test-user")
        
        assert task is None
    
    async def test_list_tasks_empty(self):
        """Test list_tasks returns empty list when no tasks."""
        mock_session = AsyncMock()
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute.return_value = mock_result
        
        tasks = await list_tasks(mock_session, "test-user")
        
        assert tasks == []
    
    async def test_list_tasks_with_filter(self):
        """Test list_tasks filters by status."""
        mock_session = AsyncMock()
        
        mock_task = MagicMock()
        mock_task.status = "active"
        
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_task]
        mock_session.execute.return_value = mock_result
        
        tasks = await list_tasks(mock_session, "test-user", status="active")
        
        assert len(tasks) == 1
        assert tasks[0].status == "active"
    
    async def test_delete_task(self):
        """Test delete_task removes task."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.user_id = "test-user"
        mock_task.scheduler_job_id = "job-123"
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result
        mock_session.delete = AsyncMock()
        mock_session.commit = AsyncMock()
        
        with patch("app.services.task_service.task_scheduler") as mock_scheduler:
            result = await delete_task(mock_session, task_id, "test-user")
        
        assert result is True
        mock_session.delete.assert_called_once_with(mock_task)
        mock_scheduler.cancel_task.assert_called_once_with(task_id)
    
    async def test_pause_task(self):
        """Test pause_task changes status to paused."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.user_id = "test-user"
        mock_task.status = "active"
        mock_task.scheduler_job_id = "job-123"
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        with patch("app.services.task_service.task_scheduler") as mock_scheduler:
            task = await pause_task(mock_session, task_id, "test-user")
        
        assert mock_task.status == "paused"
        mock_scheduler.cancel_task.assert_called_once()
    
    async def test_resume_task(self):
        """Test resume_task changes status to active."""
        mock_session = AsyncMock()
        task_id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = task_id
        mock_task.user_id = "test-user"
        mock_task.status = "paused"
        mock_task.trigger_type = "cron"
        mock_task.trigger_config = {"cron": "0 9 * * *"}
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        with patch("app.services.task_service.task_scheduler") as mock_scheduler:
            mock_scheduler.schedule_task.return_value = "new-job-id"
            task = await resume_task(mock_session, task_id, "test-user", MagicMock())
        
        assert mock_task.status == "active"


@pytest.mark.asyncio
class TestTaskExecution:
    """Tests for task execution tracking."""
    
    async def test_create_task_execution(self):
        """Test create_task_execution creates execution record."""
        mock_session = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()
        
        task_id = uuid.uuid4()
        
        execution = await create_task_execution(
            session=mock_session,
            task_id=task_id,
            trigger_source="cron",
            input_data={"prompt": "test"},
        )
        
        assert mock_session.add.called
        assert mock_session.commit.called
    
    async def test_update_task_execution_success(self):
        """Test update_task_execution with successful run."""
        mock_session = AsyncMock()
        execution_id = uuid.uuid4()
        
        mock_execution = MagicMock()
        mock_execution.id = execution_id
        mock_execution.started_at = datetime.now(timezone.utc)
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_execution
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        await update_task_execution(
            session=mock_session,
            execution_id=execution_id,
            status="completed",
            output_data={"result": "success"},
            output_summary="Task completed successfully",
        )
        
        assert mock_execution.status == "completed"
        assert mock_execution.output_summary == "Task completed successfully"
    
    async def test_update_task_execution_failure(self):
        """Test update_task_execution with failed run."""
        mock_session = AsyncMock()
        execution_id = uuid.uuid4()
        
        mock_execution = MagicMock()
        mock_execution.id = execution_id
        mock_execution.started_at = datetime.now(timezone.utc)
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_execution
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        await update_task_execution(
            session=mock_session,
            execution_id=execution_id,
            status="failed",
            error="Connection timeout",
        )
        
        assert mock_execution.status == "failed"
        assert mock_execution.error == "Connection timeout"
    
    async def test_mark_notification_sent_success(self):
        """Test mark_notification_sent updates execution."""
        mock_session = AsyncMock()
        execution_id = uuid.uuid4()
        
        mock_execution = MagicMock()
        mock_execution.id = execution_id
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_execution
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        await mark_notification_sent(
            session=mock_session,
            execution_id=execution_id,
            success=True,
        )
        
        assert mock_execution.notification_sent is True
        assert mock_execution.notification_error is None
    
    async def test_mark_notification_sent_failure(self):
        """Test mark_notification_sent with error."""
        mock_session = AsyncMock()
        execution_id = uuid.uuid4()
        
        mock_execution = MagicMock()
        mock_execution.id = execution_id
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_execution
        mock_session.execute.return_value = mock_result
        mock_session.commit = AsyncMock()
        
        await mark_notification_sent(
            session=mock_session,
            execution_id=execution_id,
            success=False,
            error="SMTP connection failed",
        )
        
        assert mock_execution.notification_sent is False
        assert mock_execution.notification_error == "SMTP connection failed"
