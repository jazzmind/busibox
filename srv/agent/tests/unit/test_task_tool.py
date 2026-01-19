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
            notification_recipient="user@example.com",
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
            notification_recipient="user@example.com",
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
            notification_recipient="user@example.com",
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
            schedule_description="Runs daily at 9 AM",
        )
        assert output.success is True
        assert output.task_id == str(task_id)
        assert output.error is None
    
    def test_failure_output(self):
        """Test failed task creation output."""
        output = TaskCreationOutput(
            success=False,
            task_name="Failed Task",
            schedule_description="N/A",
            error="Agent 'unknown-agent' not found",
        )
        assert output.success is False
        assert output.error == "Agent 'unknown-agent' not found"
        assert output.task_id is None


@pytest.mark.asyncio
class TestCreateTaskTool:
    """Tests for create_task tool function.
    
    Note: The create_task function takes individual parameters, not TaskCreationInput.
    These tests verify the input/output schemas and basic validation.
    Full integration tests require database access.
    """
    
    async def test_create_task_requires_auth(self):
        """Test task creation fails without authentication."""
        # Mock context without principal
        mock_context = MagicMock()
        mock_context.deps = None
        
        result = await create_task(
            ctx=mock_context,
            name="Test Task",
            agent_name="web-search",
            prompt="Test prompt",
            schedule="daily",
            notification_channel="email",
            notification_recipient="user@example.com",
        )
        
        assert result.success is False
        assert "auth" in result.error.lower()
    
    async def test_create_task_with_missing_deps(self):
        """Test task creation with missing deps."""
        mock_context = MagicMock()
        mock_context.deps = MagicMock()
        mock_context.deps.principal = None
        
        result = await create_task(
            ctx=mock_context,
            name="Test Task",
            agent_name="web-search",
            prompt="Test prompt",
            schedule="daily",
            notification_channel="email",
            notification_recipient="user@example.com",
        )
        
        assert result.success is False
        assert "auth" in result.error.lower()
    
    async def test_schedule_description_helper(self):
        """Test the schedule description helper function."""
        from app.tools.task_tool import _get_schedule_description
        
        assert "hourly" in _get_schedule_description("hourly").lower()
        assert "daily" in _get_schedule_description("daily").lower()
        assert "weekly" in _get_schedule_description("weekly").lower()
        assert "custom" in _get_schedule_description("*/5 * * * *").lower()
    
    async def test_agent_name_mapping(self):
        """Test agent name mapping dictionary."""
        from app.tools.task_tool import AGENT_NAME_MAPPING
        
        assert "web_search" in AGENT_NAME_MAPPING
        assert "document_search" in AGENT_NAME_MAPPING
        assert "chat" in AGENT_NAME_MAPPING
        assert "weather" in AGENT_NAME_MAPPING
    
    async def test_output_schema_fields(self):
        """Test TaskCreationOutput has required fields."""
        # Success case
        output = TaskCreationOutput(
            success=True,
            task_id="123",
            task_name="Test",
            schedule_description="Daily",
        )
        assert output.success is True
        assert output.task_name == "Test"
        
        # Failure case
        output = TaskCreationOutput(
            success=False,
            task_name="Test",
            schedule_description="Daily",
            error="Some error",
        )
        assert output.success is False
        assert output.error == "Some error"
