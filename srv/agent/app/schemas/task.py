"""
Schemas for Agent Tasks.

Agent Tasks are event-driven actions delegated to agents or workflows
with pre-authorized tokens and notification capabilities.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# Trigger types
TriggerType = Literal["cron", "webhook", "one_time"]

# Notification channels
NotificationChannel = Literal["email", "teams", "slack", "webhook"]

# Task status
TaskStatus = Literal["active", "paused", "completed", "failed", "expired"]


class TriggerConfig(BaseModel):
    """Configuration for task triggers."""
    
    # For cron triggers
    cron: Optional[str] = Field(
        None,
        description="Cron expression (5 fields: minute hour day month day_of_week)",
        pattern=r"^[\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+ [\d\*\-,/]+$",
    )
    
    # For one_time triggers
    run_at: Optional[datetime] = Field(
        None,
        description="Datetime for one-time execution"
    )
    
    # For webhook triggers - secret is auto-generated
    webhook_path: Optional[str] = Field(
        None,
        description="Custom webhook path suffix (auto-generated if not provided)"
    )


class NotificationChannelConfig(BaseModel):
    """Configuration for a single notification channel."""
    
    channel: NotificationChannel = Field(..., description="Notification channel type")
    recipient: str = Field(..., description="Email address, webhook URL, or channel ID")
    enabled: bool = Field(True, description="Whether this channel is enabled")
    
    # Channel-specific settings
    email_subject_template: Optional[str] = Field(
        None,
        description="Custom email subject template (supports {task_name}, {status})"
    )
    teams_card_style: Optional[str] = Field(
        None,
        description="Teams Adaptive Card style: simple, detailed"
    )
    slack_format: Optional[str] = Field(
        None,
        description="Slack message format: text, blocks"
    )


class NotificationConfig(BaseModel):
    """Configuration for task completion notifications.
    
    Supports both single channel (legacy) and multiple channels:
    
    Single channel (legacy):
        {
            "enabled": true,
            "channel": "email",
            "recipient": "user@example.com"
        }
    
    Multiple channels:
        {
            "enabled": true,
            "channels": [
                {"channel": "email", "recipient": "user@example.com"},
                {"channel": "teams", "recipient": "https://webhook.teams.com/..."},
                {"channel": "slack", "recipient": "https://hooks.slack.com/..."}
            ]
        }
    """
    
    enabled: bool = Field(True, description="Whether to send notifications")
    
    # Legacy single-channel fields (still supported for backward compatibility)
    channel: Optional[NotificationChannel] = Field(None, description="Notification channel (legacy, use 'channels' for multiple)")
    recipient: Optional[str] = Field(None, description="Email/webhook URL (legacy, use 'channels' for multiple)")
    
    # New multi-channel support
    channels: Optional[List[NotificationChannelConfig]] = Field(
        None,
        description="List of notification channels to send to (preferred over single channel/recipient)"
    )
    
    # Common settings
    include_summary: bool = Field(True, description="Include result summary in notification")
    include_portal_link: bool = Field(True, description="Include link to portal for details")
    on_success: bool = Field(True, description="Send notification on successful completion")
    on_failure: bool = Field(True, description="Send notification on failure")
    
    # Legacy channel-specific settings (for single-channel mode)
    email_subject_template: Optional[str] = Field(
        None,
        description="Custom email subject template (supports {task_name}, {status})"
    )
    teams_card_style: Optional[str] = Field(
        None,
        description="Teams Adaptive Card style: simple, detailed"
    )
    slack_format: Optional[str] = Field(
        None,
        description="Slack message format: text, blocks"
    )
    
    @model_validator(mode='after')
    def validate_channels_or_recipient(self):
        """Validate that at least one channel is configured when enabled."""
        if not self.enabled:
            return self
        
        has_channels = self.channels and len(self.channels) > 0
        has_legacy = self.channel and self.recipient
        
        if not has_channels and not has_legacy:
            raise ValueError(
                "At least one notification channel must be configured when enabled. "
                "Either provide 'channels' array or 'channel' + 'recipient'."
            )
        
        return self
    
    def get_all_channels(self) -> List[Dict[str, Any]]:
        """Get all configured channels as a list of dicts."""
        result = []
        
        # Add multi-channel configs
        if self.channels:
            for ch in self.channels:
                if ch.enabled:
                    result.append({
                        "channel": ch.channel,
                        "recipient": ch.recipient,
                        "email_subject_template": ch.email_subject_template,
                        "teams_card_style": ch.teams_card_style,
                        "slack_format": ch.slack_format,
                    })
        
        # Add legacy single-channel config if no channels defined
        if not result and self.channel and self.recipient:
            result.append({
                "channel": self.channel,
                "recipient": self.recipient,
                "email_subject_template": self.email_subject_template,
                "teams_card_style": self.teams_card_style,
                "slack_format": self.slack_format,
            })
        
        return result


class InsightsConfig(BaseModel):
    """Configuration for task insights/memories."""
    
    enabled: bool = Field(True, description="Whether to store task insights")
    max_insights: int = Field(50, description="Maximum insights to retain", ge=1, le=500)
    purge_after_days: Optional[int] = Field(
        30,
        description="Auto-purge insights older than N days (null = never purge)",
        ge=1
    )
    include_in_context: bool = Field(
        True,
        description="Include prior insights in agent context to avoid duplicates"
    )
    context_limit: int = Field(
        10,
        description="Max insights to include in agent context",
        ge=1,
        le=50
    )


class OutputSavingConfig(BaseModel):
    """Configuration for saving task output to document library.
    
    When enabled, task outputs are saved to the user's personal 'Tasks' library
    as documents that can be searched and referenced later.
    """
    
    enabled: bool = Field(False, description="Whether to save task output to library")
    library_type: str = Field(
        "TASKS",
        description="Library type to save to (TASKS for personal tasks library)"
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Tags to apply to saved documents for organization"
    )
    title_template: Optional[str] = Field(
        None,
        description="Template for document title. Supports {task_name}, {date}, {status}"
    )
    on_success_only: bool = Field(
        True,
        description="Only save output when task succeeds"
    )


class TaskCreate(BaseModel):
    """Schema for creating a new agent task."""
    
    name: str = Field(..., description="Human-readable task name", min_length=1, max_length=255)
    description: Optional[str] = Field(None, description="Task description")
    agent_id: Optional[uuid.UUID] = Field(None, description="Target agent to execute (either agent_id or workflow_id required)")
    workflow_id: Optional[uuid.UUID] = Field(None, description="Target workflow to execute (either agent_id or workflow_id required)")
    prompt: str = Field(..., description="Task prompt/instructions for the agent")
    
    # Trigger configuration
    trigger_type: TriggerType = Field(..., description="How the task is triggered")
    trigger_config: TriggerConfig = Field(
        default_factory=TriggerConfig,
        description="Trigger-specific configuration"
    )
    
    # Notification configuration
    notification_config: Optional[NotificationConfig] = Field(
        None,
        description="Notification settings (optional)"
    )
    
    # Insights configuration
    insights_config: Optional[InsightsConfig] = Field(
        default_factory=InsightsConfig,
        description="Task memory/insights settings"
    )
    
    # Output saving configuration
    output_saving_config: Optional[OutputSavingConfig] = Field(
        None,
        description="Settings for saving task output to document library"
    )
    
    # Additional input parameters
    input_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional input parameters for the agent"
    )
    
    # Delegation scopes
    scopes: List[str] = Field(
        default_factory=lambda: ["search.read", "ingest.read", "agent.execute"],
        description="OAuth scopes to grant the task"
    )
    
    @field_validator('trigger_config')
    @classmethod
    def validate_trigger_config(cls, v: TriggerConfig, info) -> TriggerConfig:
        """Validate trigger config matches trigger type."""
        trigger_type = info.data.get('trigger_type')
        if trigger_type == 'cron' and not v.cron:
            raise ValueError("Cron trigger requires 'cron' expression in trigger_config")
        if trigger_type == 'one_time' and not v.run_at:
            raise ValueError("One-time trigger requires 'run_at' datetime in trigger_config")
        return v
    
    @model_validator(mode='after')
    def validate_agent_or_workflow(self):
        """Validate that either agent_id or workflow_id is provided."""
        if not self.agent_id and not self.workflow_id:
            raise ValueError("Either agent_id or workflow_id must be provided")
        if self.agent_id and self.workflow_id:
            raise ValueError("Only one of agent_id or workflow_id should be provided, not both")
        return self


class TaskUpdate(BaseModel):
    """Schema for updating an agent task."""
    
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    prompt: Optional[str] = None
    
    # Target agent or workflow update (set one, clear the other)
    agent_id: Optional[uuid.UUID] = Field(None, description="Target agent to execute")
    workflow_id: Optional[uuid.UUID] = Field(None, description="Target workflow to execute")
    
    # Trigger updates
    trigger_config: Optional[TriggerConfig] = None
    
    # Notification updates
    notification_config: Optional[NotificationConfig] = None
    
    # Insights updates
    insights_config: Optional[InsightsConfig] = None
    
    # Output saving updates
    output_saving_config: Optional[OutputSavingConfig] = None
    
    # Status updates
    status: Optional[TaskStatus] = None
    
    # Input config updates
    input_config: Optional[Dict[str, Any]] = None


class TaskRead(BaseModel):
    """Schema for reading an agent task."""
    
    id: uuid.UUID
    name: str
    description: Optional[str]
    user_id: str
    agent_id: Optional[uuid.UUID] = None
    workflow_id: Optional[uuid.UUID] = None
    prompt: str
    
    # Trigger
    trigger_type: str
    trigger_config: Dict[str, Any]
    
    # Delegation
    delegation_scopes: List[str]
    delegation_expires_at: Optional[datetime]
    
    # Notification
    notification_config: Dict[str, Any]
    
    # Insights
    insights_config: Dict[str, Any]
    
    # Output saving
    output_saving_config: Optional[Dict[str, Any]] = None
    
    # Input
    input_config: Dict[str, Any]
    
    # Status
    status: str
    webhook_secret: Optional[str] = Field(None, description="Webhook secret (only returned on creation)")
    
    # Execution tracking
    last_run_at: Optional[datetime]
    last_run_id: Optional[uuid.UUID]
    next_run_at: Optional[datetime]
    run_count: int
    error_count: int
    last_error: Optional[str]
    
    # Metadata
    created_at: datetime
    updated_at: datetime
    
    # Computed fields
    webhook_url: Optional[str] = Field(None, description="Webhook URL for webhook triggers")
    
    class Config:
        from_attributes = True


class TaskExecutionRead(BaseModel):
    """Schema for reading a task execution."""
    
    id: uuid.UUID
    task_id: uuid.UUID
    run_id: Optional[uuid.UUID]
    
    trigger_source: str
    status: str
    
    input_data: Dict[str, Any]
    output_data: Optional[Dict[str, Any]]
    output_summary: Optional[str]
    
    notification_sent: bool
    notification_error: Optional[str]
    
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    duration_seconds: Optional[float]
    
    error: Optional[str]
    
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class TaskListResponse(BaseModel):
    """Response for listing tasks."""
    
    tasks: List[TaskRead]
    total: int
    limit: int
    offset: int


class TaskRunRequest(BaseModel):
    """Request to manually trigger a task run."""
    
    input_override: Optional[Dict[str, Any]] = Field(
        None,
        description="Override task input for this execution"
    )
    skip_notification: bool = Field(
        False,
        description="Skip sending notification after completion"
    )


class TaskRunResponse(BaseModel):
    """Response from triggering a task run."""
    
    execution_id: uuid.UUID
    task_id: uuid.UUID
    run_id: Optional[uuid.UUID]
    status: str
    message: str


# Schedule presets for user-friendly task creation
SCHEDULE_PRESETS = {
    "every_5_minutes": "*/5 * * * *",
    "every_15_minutes": "*/15 * * * *",
    "every_30_minutes": "*/30 * * * *",
    "hourly": "0 * * * *",
    "every_2_hours": "0 */2 * * *",
    "every_6_hours": "0 */6 * * *",
    "daily": "0 9 * * *",  # 9 AM
    "daily_morning": "0 9 * * *",  # 9 AM
    "daily_evening": "0 18 * * *",  # 6 PM
    "weekly": "0 9 * * 1",  # Monday 9 AM
    "monthly": "0 9 1 * *",  # 1st of month 9 AM
}


def get_cron_from_preset(preset: str) -> Optional[str]:
    """Convert schedule preset to cron expression."""
    return SCHEDULE_PRESETS.get(preset.lower())
