"""
Unit tests for notification tool.

Tests the send_notification tool and underlying services.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.notification_tool import (
    NotificationInput,
    NotificationOutput,
    send_notification,
)


class TestNotificationInput:
    """Tests for NotificationInput schema."""
    
    def test_email_notification_input(self):
        """Test email notification input."""
        input_data = NotificationInput(
            channel="email",
            recipient="user@example.com",
            subject="Test Subject",
            message="Test message body",
        )
        assert input_data.channel == "email"
        assert input_data.recipient == "user@example.com"
        assert input_data.subject == "Test Subject"
    
    def test_teams_notification_input(self):
        """Test Teams notification input."""
        input_data = NotificationInput(
            channel="teams",
            recipient="https://outlook.office.com/webhook/...",
            message="Team notification message",
            title="Alert Title",
        )
        assert input_data.channel == "teams"
        assert input_data.title == "Alert Title"
    
    def test_slack_notification_input(self):
        """Test Slack notification input."""
        input_data = NotificationInput(
            channel="slack",
            recipient="https://hooks.slack.com/services/...",
            message="Slack message",
        )
        assert input_data.channel == "slack"
    
    def test_webhook_notification_input(self):
        """Test generic webhook input."""
        input_data = NotificationInput(
            channel="webhook",
            recipient="https://api.example.com/webhook",
            message="Webhook payload",
            data={"custom_field": "value"},
        )
        assert input_data.channel == "webhook"
        assert input_data.data == {"custom_field": "value"}
    
    def test_notification_input_with_link(self):
        """Test notification with action link."""
        input_data = NotificationInput(
            channel="email",
            recipient="user@example.com",
            message="Check results",
            link="https://portal.example.com/results/123",
            link_text="View Results",
        )
        assert input_data.link == "https://portal.example.com/results/123"
        assert input_data.link_text == "View Results"


class TestNotificationOutput:
    """Tests for NotificationOutput schema."""
    
    def test_success_output(self):
        """Test successful notification output."""
        output = NotificationOutput(
            success=True,
            message="Email sent successfully",
            channel="email",
            recipient="user@example.com",
        )
        assert output.success is True
        assert output.error is None
    
    def test_failure_output(self):
        """Test failed notification output."""
        output = NotificationOutput(
            success=False,
            message="Failed to send notification",
            channel="email",
            recipient="user@example.com",
            error="SMTP connection refused",
        )
        assert output.success is False
        assert output.error == "SMTP connection refused"


@pytest.mark.asyncio
class TestSendNotification:
    """Tests for send_notification function."""
    
    async def test_send_email_notification_success(self):
        """Test successful email notification."""
        input_data = NotificationInput(
            channel="email",
            recipient="user@example.com",
            subject="Test",
            message="Test message",
        )
        
        mock_context = MagicMock()
        
        with patch("app.tools.notification_tool.send_email") as mock_email:
            mock_email.return_value = True
            
            result = await send_notification(input_data, mock_context)
        
        assert result.success is True
        assert result.channel == "email"
        mock_email.assert_called_once()
    
    async def test_send_email_notification_failure(self):
        """Test failed email notification."""
        input_data = NotificationInput(
            channel="email",
            recipient="user@example.com",
            subject="Test",
            message="Test message",
        )
        
        mock_context = MagicMock()
        
        with patch("app.tools.notification_tool.send_email") as mock_email:
            mock_email.side_effect = Exception("SMTP error")
            
            result = await send_notification(input_data, mock_context)
        
        assert result.success is False
        assert "SMTP error" in result.error
    
    async def test_send_teams_notification_success(self):
        """Test successful Teams notification."""
        input_data = NotificationInput(
            channel="teams",
            recipient="https://outlook.office.com/webhook/...",
            message="Teams message",
            title="Alert",
        )
        
        mock_context = MagicMock()
        
        with patch("app.tools.notification_tool.send_teams_message") as mock_teams:
            mock_teams.return_value = True
            
            result = await send_notification(input_data, mock_context)
        
        assert result.success is True
        assert result.channel == "teams"
        mock_teams.assert_called_once()
    
    async def test_send_slack_notification_success(self):
        """Test successful Slack notification."""
        input_data = NotificationInput(
            channel="slack",
            recipient="https://hooks.slack.com/services/...",
            message="Slack message",
        )
        
        mock_context = MagicMock()
        
        with patch("app.tools.notification_tool.send_slack_message") as mock_slack:
            mock_slack.return_value = True
            
            result = await send_notification(input_data, mock_context)
        
        assert result.success is True
        assert result.channel == "slack"
        mock_slack.assert_called_once()
    
    async def test_send_webhook_notification_success(self):
        """Test successful generic webhook."""
        input_data = NotificationInput(
            channel="webhook",
            recipient="https://api.example.com/webhook",
            message="Webhook payload",
        )
        
        mock_context = MagicMock()
        
        with patch("app.tools.notification_tool.send_generic_webhook") as mock_webhook:
            mock_webhook.return_value = True
            
            result = await send_notification(input_data, mock_context)
        
        assert result.success is True
        assert result.channel == "webhook"
        mock_webhook.assert_called_once()
    
    async def test_send_notification_invalid_channel(self):
        """Test notification with invalid channel."""
        input_data = NotificationInput(
            channel="invalid",
            recipient="somewhere",
            message="Test",
        )
        
        mock_context = MagicMock()
        
        result = await send_notification(input_data, mock_context)
        
        assert result.success is False
        assert "unsupported" in result.error.lower() or "invalid" in result.error.lower()
