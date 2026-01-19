"""
Task Creation Tool for Agents.

Allows agents (like the chat agent) to create scheduled tasks on behalf of users.
Users can request things like "send me a daily news summary" and the agent
will create an appropriate task using this tool.
"""

import logging
import uuid
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field

from app.agents.core import BusiboxDeps
from app.schemas.task import (
    InsightsConfig,
    NotificationConfig,
    TaskCreate,
    TriggerConfig,
    get_cron_from_preset,
    SCHEDULE_PRESETS,
)

logger = logging.getLogger(__name__)


# Schedule presets that agents can use
SchedulePreset = Literal[
    "every_5_minutes",
    "every_15_minutes",
    "every_30_minutes",
    "hourly",
    "every_2_hours",
    "every_6_hours",
    "daily",
    "daily_morning",
    "daily_evening",
    "weekly",
    "monthly",
]


class TaskCreationInput(BaseModel):
    """Input schema for the create_task tool."""
    
    name: str = Field(
        ...,
        description="Short, descriptive name for the task (e.g., 'Daily AI News Summary')"
    )
    description: Optional[str] = Field(
        None,
        description="Detailed description of what the task does"
    )
    agent_name: str = Field(
        ...,
        description="Name of the agent to use: 'web_search' for web research, 'document_search' for document analysis"
    )
    prompt: str = Field(
        ...,
        description="The prompt/instructions for the agent to execute"
    )
    schedule: str = Field(
        ...,
        description="Schedule preset (hourly, daily, weekly, monthly) or cron expression"
    )
    notification_channel: str = Field(
        "email",
        description="How to notify: 'email', 'teams', 'slack', or 'webhook'"
    )
    notification_recipient: str = Field(
        ...,
        description="Where to send notifications (email address or webhook URL)"
    )


class TaskCreationOutput(BaseModel):
    """Output schema for the create_task tool."""
    
    success: bool = Field(..., description="Whether the task was created successfully")
    task_id: Optional[str] = Field(None, description="UUID of the created task")
    task_name: str = Field(..., description="Name of the task")
    schedule_description: str = Field(..., description="Human-readable schedule description")
    next_run: Optional[str] = Field(None, description="When the task will first run")
    error: Optional[str] = Field(None, description="Error message if creation failed")


# Agent name to agent ID mapping
# These will be looked up from the database in practice
AGENT_NAME_MAPPING = {
    "web_search": "web-search-agent",
    "web_research": "web-search-agent",
    "document_search": "document-search-agent",
    "doc_search": "document-search-agent",
    "chat": "chat-agent",
    "weather": "weather-agent",
}


def _get_schedule_description(schedule: str) -> str:
    """Get human-readable description for a schedule."""
    descriptions = {
        "every_5_minutes": "every 5 minutes",
        "every_15_minutes": "every 15 minutes",
        "every_30_minutes": "every 30 minutes",
        "hourly": "every hour",
        "every_2_hours": "every 2 hours",
        "every_6_hours": "every 6 hours",
        "daily": "daily at 9 AM",
        "daily_morning": "daily at 9 AM",
        "daily_evening": "daily at 6 PM",
        "weekly": "weekly on Monday at 9 AM",
        "monthly": "monthly on the 1st at 9 AM",
    }
    return descriptions.get(schedule.lower(), f"custom schedule ({schedule})")


async def create_task(
    ctx: Any,  # RunContext[BusiboxDeps]
    name: str,
    agent_name: str,
    prompt: str,
    schedule: str,
    notification_channel: str,
    notification_recipient: str,
    description: Optional[str] = None,
) -> TaskCreationOutput:
    """
    Create a scheduled agent task.
    
    This tool allows the chat agent to create tasks that run automatically
    on a schedule. For example:
    - "Send me daily tech news" -> creates web_search task running daily
    - "Email me weekly document summaries" -> creates document_search task
    
    Args:
        ctx: Agent context with dependencies
        name: Task name
        agent_name: Agent to use (web_search, document_search, etc.)
        prompt: Instructions for the agent
        schedule: Schedule preset or cron expression
        notification_channel: email, teams, slack, or webhook
        notification_recipient: Where to send results
        description: Optional description
        
    Returns:
        TaskCreationOutput with task details or error
    """
    logger.info(
        f"Creating task '{name}' with agent '{agent_name}' on schedule '{schedule}'"
    )
    
    try:
        from app.db.session import SessionLocal
        from app.models.domain import AgentDefinition
        from app.schemas.auth import Principal
        from app.services.task_service import create_task as create_task_db, task_to_read
        from sqlalchemy import select
        
        # Get principal from context
        deps = ctx.deps if hasattr(ctx, 'deps') else None
        if not deps or not deps.principal:
            return TaskCreationOutput(
                success=False,
                task_name=name,
                schedule_description=_get_schedule_description(schedule),
                error="Authentication required to create tasks",
            )
        
        principal = deps.principal
        
        # Resolve agent name to ID
        agent_key = AGENT_NAME_MAPPING.get(agent_name.lower().replace("-", "_"), agent_name)
        
        async with SessionLocal() as session:
            # Look up agent by name
            stmt = select(AgentDefinition).where(AgentDefinition.name == agent_key)
            result = await session.execute(stmt)
            agent = result.scalar_one_or_none()
            
            if not agent:
                # Try finding by display name
                stmt = select(AgentDefinition).where(
                    AgentDefinition.display_name.ilike(f"%{agent_name}%")
                )
                result = await session.execute(stmt)
                agent = result.scalar_one_or_none()
            
            if not agent:
                return TaskCreationOutput(
                    success=False,
                    task_name=name,
                    schedule_description=_get_schedule_description(schedule),
                    error=f"Agent '{agent_name}' not found. Available agents: web_search, document_search, chat, weather",
                )
            
            # Resolve schedule to cron expression
            cron = get_cron_from_preset(schedule)
            if not cron:
                # Assume it's a cron expression
                cron = schedule
            
            # Validate cron expression
            try:
                from croniter import croniter
                croniter(cron)
            except Exception as e:
                return TaskCreationOutput(
                    success=False,
                    task_name=name,
                    schedule_description=schedule,
                    error=f"Invalid schedule '{schedule}'. Use presets like 'hourly', 'daily', 'weekly' or a cron expression.",
                )
            
            # Build notification config
            notification_config = NotificationConfig(
                enabled=True,
                channel=notification_channel,
                recipient=notification_recipient,
                include_summary=True,
                include_portal_link=True,
            )
            
            # Build insights config
            insights_config = InsightsConfig(
                enabled=True,
                max_insights=50,
                purge_after_days=30,
                include_in_context=True,
                context_limit=10,
            )
            
            # Build trigger config
            trigger_config = TriggerConfig(cron=cron)
            
            # Create task
            task_data = TaskCreate(
                name=name,
                description=description or f"Automated task: {prompt[:100]}...",
                agent_id=agent.id,
                prompt=prompt,
                trigger_type="cron",
                trigger_config=trigger_config,
                notification_config=notification_config,
                insights_config=insights_config,
                scopes=agent.scopes or ["search.read", "web_search.read"],
            )
            
            task = await create_task_db(session, principal, task_data)
            
            logger.info(f"Created task {task.id} for user {principal.sub}")
            
            return TaskCreationOutput(
                success=True,
                task_id=str(task.id),
                task_name=name,
                schedule_description=_get_schedule_description(schedule),
                next_run=task.next_run_at.isoformat() if task.next_run_at else None,
            )
    
    except Exception as e:
        logger.error(f"Failed to create task: {e}", exc_info=True)
        return TaskCreationOutput(
            success=False,
            task_name=name,
            schedule_description=_get_schedule_description(schedule),
            error=f"Failed to create task: {str(e)}",
        )


# Register the task creation tool with the agent framework
def register_task_tool():
    """Register the create_task tool with the ToolRegistry."""
    from app.agents.base_agent import ToolRegistry
    
    ToolRegistry.register(
        name="create_task",
        func=create_task,
        output_type=TaskCreationOutput,
    )
    
    logger.info("Registered create_task tool")


# Tool metadata for agent discovery
TASK_TOOL_SCHEMA = {
    "name": "create_task",
    "description": """Create a scheduled agent task that runs automatically.

Use this tool when users want recurring automated actions like:
- "Send me daily news about AI"
- "Email me weekly document summaries"
- "Notify me hourly about stock prices"

The task will run on the specified schedule and send results via the chosen notification channel.""",
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Short, descriptive name for the task"
            },
            "agent_name": {
                "type": "string",
                "enum": ["web_search", "document_search", "weather"],
                "description": "Agent to use for the task"
            },
            "prompt": {
                "type": "string",
                "description": "Instructions for what the agent should do"
            },
            "schedule": {
                "type": "string",
                "enum": list(SCHEDULE_PRESETS.keys()),
                "description": "How often to run the task"
            },
            "notification_channel": {
                "type": "string",
                "enum": ["email", "teams", "slack", "webhook"],
                "description": "How to send results"
            },
            "notification_recipient": {
                "type": "string",
                "description": "Email address or webhook URL for notifications"
            },
            "description": {
                "type": "string",
                "description": "Optional detailed description"
            }
        },
        "required": [
            "name",
            "agent_name",
            "prompt",
            "schedule",
            "notification_channel",
            "notification_recipient"
        ]
    }
}
