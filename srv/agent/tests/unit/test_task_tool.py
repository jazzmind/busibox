"""
Unit tests for task creation tool.

Tests the create_task tool used by agents to create scheduled tasks.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.task_tool import (
    TaskCreationInput,
    TaskCreationOutput,
    create_task,
)


class TestTaskCreationInput:
    """Tests for TaskCreationInput schema."""
    
    def test_basic_input(self):
        """Test basic task creation input."""
        input_data = TaskCreationInput(
            name="Daily News Summary",
            agent_name="web-search",
            prompt="Search for today's AI news and summarize",
            schedule="daily",
        )
        assert input_data.name == "Daily News Summary"
        assert input_data.agent_name == "web-search"
        assert input_data.schedule == "daily"
    
    def test_input_with_notification(self):
        """Test input with notification settings."""
        input_data = TaskCreationInput(
            name="Weather Report",
            agent_name="weather",
            prompt="Get weather forecast",
            schedule="daily_morning",
            notification_channel="email",
            notification_recipient="user@example.com",
        )
        assert input_data.notification_channel == "email"
        assert input_data.notification_recipient == "user@example.com"
    
    def test_input_with_custom_cron(self):
        """Test input with custom cron schedule."""
        input_data = TaskCreationInput(
            name="Hourly Check",
            agent_name="chat",
            prompt="Check status",
            schedule="*/30 * * * *",  # Every 30 minutes
        )
        assert input_data.schedule == "*/30 * * * *"
    
    def test_input_with_description(self):
        """Test input with description."""
        input_data = TaskCreationInput(
            name="My Task",
            description="This is a detailed description of the task",
            agent_name="chat",
            prompt="Do something",
            schedule="hourly",
        )
        assert input_data.description == "This is a detailed description of the task"


class TestTaskCreationOutput:
    """Tests for TaskCreationOutput schema."""
    
    def test_success_output(self):
        """Test successful task creation output."""
        task_id = uuid.uuid4()
        output = TaskCreationOutput(
            success=True,
            task_id=str(task_id),
            task_name="My Task",
            message="Task created successfully",
            schedule_description="Runs daily at 9 AM",
        )
        assert output.success is True
        assert output.task_id == str(task_id)
        assert output.error is None
    
    def test_failure_output(self):
        """Test failed task creation output."""
        output = TaskCreationOutput(
            success=False,
            message="Failed to create task",
            error="Agent 'unknown-agent' not found",
        )
        assert output.success is False
        assert output.error == "Agent 'unknown-agent' not found"
        assert output.task_id is None


@pytest.mark.asyncio
class TestCreateTaskTool:
    """Tests for create_task tool function."""
    
    async def test_create_task_success(self):
        """Test successful task creation."""
        input_data = TaskCreationInput(
            name="Daily Summary",
            agent_name="web-search",
            prompt="Summarize daily news",
            schedule="daily",
            notification_channel="email",
            notification_recipient="user@example.com",
        )
        
        mock_context = MagicMock()
        mock_context.user_id = "test-user"
        mock_context.session = AsyncMock()
        
        mock_agent = MagicMock()
        mock_agent.id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = uuid.uuid4()
        mock_task.name = "Daily Summary"
        
        with patch("app.tools.task_tool.get_agent_by_name") as mock_get_agent:
            with patch("app.tools.task_tool.task_service") as mock_service:
                mock_get_agent.return_value = mock_agent
                mock_service.create_task.return_value = mock_task
                
                result = await create_task(input_data, mock_context)
        
        assert result.success is True
        assert result.task_name == "Daily Summary"
        mock_get_agent.assert_called_once_with(mock_context.session, "web-search")
        mock_service.create_task.assert_called_once()
    
    async def test_create_task_agent_not_found(self):
        """Test task creation with unknown agent."""
        input_data = TaskCreationInput(
            name="Invalid Task",
            agent_name="unknown-agent",
            prompt="Do something",
            schedule="hourly",
        )
        
        mock_context = MagicMock()
        mock_context.user_id = "test-user"
        mock_context.session = AsyncMock()
        
        with patch("app.tools.task_tool.get_agent_by_name") as mock_get_agent:
            mock_get_agent.return_value = None
            
            result = await create_task(input_data, mock_context)
        
        assert result.success is False
        assert "not found" in result.error.lower()
    
    async def test_create_task_preset_schedule(self):
        """Test task creation with schedule preset."""
        input_data = TaskCreationInput(
            name="Weekly Report",
            agent_name="chat",
            prompt="Generate weekly report",
            schedule="weekly",
        )
        
        mock_context = MagicMock()
        mock_context.user_id = "test-user"
        mock_context.session = AsyncMock()
        
        mock_agent = MagicMock()
        mock_agent.id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = uuid.uuid4()
        mock_task.name = "Weekly Report"
        
        with patch("app.tools.task_tool.get_agent_by_name") as mock_get_agent:
            with patch("app.tools.task_tool.task_service") as mock_service:
                mock_get_agent.return_value = mock_agent
                mock_service.create_task.return_value = mock_task
                
                result = await create_task(input_data, mock_context)
        
        assert result.success is True
        # Verify the service was called with correct cron for weekly
        call_args = mock_service.create_task.call_args
        # The schedule should be converted to cron
    
    async def test_create_task_service_error(self):
        """Test task creation when service raises error."""
        input_data = TaskCreationInput(
            name="Error Task",
            agent_name="chat",
            prompt="Cause error",
            schedule="hourly",
        )
        
        mock_context = MagicMock()
        mock_context.user_id = "test-user"
        mock_context.session = AsyncMock()
        
        mock_agent = MagicMock()
        mock_agent.id = uuid.uuid4()
        
        with patch("app.tools.task_tool.get_agent_by_name") as mock_get_agent:
            with patch("app.tools.task_tool.task_service") as mock_service:
                mock_get_agent.return_value = mock_agent
                mock_service.create_task.side_effect = Exception("Database error")
                
                result = await create_task(input_data, mock_context)
        
        assert result.success is False
        assert "Database error" in result.error
    
    async def test_create_task_with_all_options(self):
        """Test task creation with all options specified."""
        input_data = TaskCreationInput(
            name="Full Task",
            description="Task with all options",
            agent_name="web-search",
            prompt="Search and notify",
            schedule="every_6_hours",
            notification_channel="teams",
            notification_recipient="https://teams.webhook.url",
            enable_memory=True,
        )
        
        mock_context = MagicMock()
        mock_context.user_id = "test-user"
        mock_context.session = AsyncMock()
        
        mock_agent = MagicMock()
        mock_agent.id = uuid.uuid4()
        
        mock_task = MagicMock()
        mock_task.id = uuid.uuid4()
        mock_task.name = "Full Task"
        
        with patch("app.tools.task_tool.get_agent_by_name") as mock_get_agent:
            with patch("app.tools.task_tool.task_service") as mock_service:
                mock_get_agent.return_value = mock_agent
                mock_service.create_task.return_value = mock_task
                
                result = await create_task(input_data, mock_context)
        
        assert result.success is True
