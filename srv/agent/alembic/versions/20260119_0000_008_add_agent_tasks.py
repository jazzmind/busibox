"""Add agent_tasks and task_executions tables

Revision ID: 008
Revises: 007
Create Date: 2026-01-19 00:00:00

This migration adds support for Agent Tasks - event-driven actions
delegated to agents with pre-authorized tokens, insights/memories,
and notification capabilities.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '008'
down_revision: Union[str, None] = '007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create agent_tasks and task_executions tables.
    
    agent_tasks: Stores task definitions with trigger config, delegation tokens,
                 notification settings, and insights configuration.
    
    task_executions: Tracks individual task execution runs with timing,
                     output, and notification status.
    """
    conn = op.get_bind()
    
    # Check if agent_tasks table already exists
    result = conn.execute(sa.text(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'agent_tasks'
        )
        """
    ))
    if result.scalar():
        # Table already exists, skip creation
        return
    
    # Create agent_tasks table
    op.create_table(
        'agent_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('user_id', sa.String(255), nullable=False, index=True),
        
        # Target agent
        sa.Column(
            'agent_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agent_definitions.id', ondelete='CASCADE'),
            nullable=False,
            index=True
        ),
        
        # Task prompt/input
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column(
            'input_config',
            sa.JSON(),
            nullable=False,
            server_default='{}',
            comment='Additional input parameters'
        ),
        
        # Trigger configuration
        sa.Column(
            'trigger_type',
            sa.String(50),
            nullable=False,
            index=True,
            comment='Trigger type: cron, webhook, one_time'
        ),
        sa.Column(
            'trigger_config',
            sa.JSON(),
            nullable=False,
            server_default='{}',
            comment='Trigger-specific config'
        ),
        
        # Delegation token for autonomous execution
        sa.Column(
            'delegation_token',
            sa.Text(),
            nullable=True,
            comment='Encrypted delegation token'
        ),
        sa.Column(
            'delegation_scopes',
            sa.JSON(),
            nullable=False,
            server_default='[]',
            comment='Scopes granted to the task'
        ),
        sa.Column('delegation_expires_at', sa.DateTime(), nullable=True),
        
        # Notification configuration
        sa.Column(
            'notification_config',
            sa.JSON(),
            nullable=False,
            server_default='{}',
            comment='Notification settings'
        ),
        
        # Task insights/memory configuration
        sa.Column(
            'insights_config',
            sa.JSON(),
            nullable=False,
            server_default='{}',
            comment='Insights settings'
        ),
        
        # Execution state
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='active',
            index=True,
            comment='Status: active, paused, completed, failed, expired'
        ),
        sa.Column(
            'scheduler_job_id',
            sa.String(255),
            nullable=True,
            comment='APScheduler job ID for cron tasks'
        ),
        sa.Column(
            'webhook_secret',
            sa.String(255),
            nullable=True,
            comment='Secret for webhook validation'
        ),
        
        # Execution tracking
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column(
            'last_run_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('run_records.id', ondelete='SET NULL'),
            nullable=True
        ),
        sa.Column('next_run_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('run_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        
        # Metadata
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now()
        ),
    )
    
    # Create indexes for agent_tasks
    op.create_index('idx_agent_tasks_user_id', 'agent_tasks', ['user_id'])
    op.create_index('idx_agent_tasks_agent_id', 'agent_tasks', ['agent_id'])
    op.create_index('idx_agent_tasks_status', 'agent_tasks', ['status'])
    op.create_index('idx_agent_tasks_trigger_type', 'agent_tasks', ['trigger_type'])
    op.create_index('idx_agent_tasks_next_run_at', 'agent_tasks', ['next_run_at'])
    
    # Create task_executions table
    op.create_table(
        'task_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'task_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agent_tasks.id', ondelete='CASCADE'),
            nullable=False,
            index=True
        ),
        sa.Column(
            'run_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('run_records.id', ondelete='SET NULL'),
            nullable=True,
            index=True
        ),
        
        # Execution details
        sa.Column(
            'trigger_source',
            sa.String(50),
            nullable=False,
            comment='cron, webhook, manual'
        ),
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='pending',
            index=True,
            comment='Status: pending, running, completed, failed, timeout'
        ),
        
        # Input/Output
        sa.Column('input_data', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column(
            'output_summary',
            sa.Text(),
            nullable=True,
            comment='Summary for notifications'
        ),
        
        # Notification tracking
        sa.Column('notification_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notification_error', sa.Text(), nullable=True),
        
        # Timing
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        
        # Error tracking
        sa.Column('error', sa.Text(), nullable=True),
        
        # Metadata
        sa.Column(
            'created_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now()
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.now(),
            onupdate=sa.func.now()
        ),
    )
    
    # Create indexes for task_executions
    op.create_index('idx_task_executions_task_id', 'task_executions', ['task_id'])
    op.create_index('idx_task_executions_status', 'task_executions', ['status'])
    op.create_index('idx_task_executions_created_at', 'task_executions', ['created_at'])


def downgrade() -> None:
    """
    Drop agent_tasks and task_executions tables.
    """
    # Drop task_executions first (has FK to agent_tasks)
    op.drop_table('task_executions')
    
    # Drop agent_tasks
    op.drop_table('agent_tasks')
