"""
Schemas for Agent Tasks.

Agent Tasks are event-driven actions delegated to agents or workflows
with pre-authorized tokens and notification capabilities.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator


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


class NotificationConfig(BaseModel):
    """Configuration for task completion notifications."""
    
    enabled: bool = Field(True, description="Whether to send notifications")
    channel: NotificationChannel = Field("email", description="Notification channel")
    recipient: Optional[str] = Field(None, description="Email address, webhook URL, or channel ID (required if enabled)")
    include_summary: bool = Field(True, description="Include result summary in notification")
    include_portal_link: bool = Field(True, description="Include link to portal for details")
    
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
    
    @field_validator("recipient")
    @classmethod
    def validate_recipient(cls, v, info):
        """Validate that recipient is provided when notifications are enabled."""
        # We can't access other fields in field_validator directly in Pydantic v2
        # So we'll do this validation in the model_validator instead
        return v
    
    def model_post_init(self, __context):
        """Validate recipient is provided when enabled."""
        if self.enabled and not self.recipient:
            raise ValueError("recipient is required when notifications are enabled")


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
    
    # Additional input parameters
    input_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional input parameters for the agent"
    )
    
    # Delegation scopes
    scopes: List[str] = Field(
        default_factory=lambda: ["search.read", "web_search.read"],
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
