"""Add task_notifications table

Revision ID: 012
Revises: 011
Create Date: 2026-01-21

Adds the task_notifications table to track notification delivery
for task executions. Each notification tracks channel, recipient,
content, and delivery status.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '012'
down_revision: Union[str, None] = '011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create task_notifications table."""
    # Check if table already exists
    conn = op.get_bind()
    result = conn.execute(sa.text(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_name = 'task_notifications'
        )
        """
    ))
    if result.scalar():
        # Table already exists, skip creation
        return
    
    op.create_table(
        'task_notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'task_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('agent_tasks.id', ondelete='CASCADE'),
            nullable=False,
            index=True
        ),
        sa.Column(
            'execution_id',
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey('task_executions.id', ondelete='CASCADE'),
            nullable=False,
            index=True
        ),
        
        # Channel and recipient
        sa.Column(
            'channel',
            sa.String(50),
            nullable=False,
            comment='email, slack, teams, webhook'
        ),
        sa.Column(
            'recipient',
            sa.String(500),
            nullable=False,
            comment='Email address or webhook URL'
        ),
        
        # Content
        sa.Column('subject', sa.String(500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        
        # Delivery tracking
        sa.Column(
            'status',
            sa.String(50),
            nullable=False,
            server_default='pending',
            index=True,
            comment='pending, sent, failed, delivered, read, bounced'
        ),
        sa.Column(
            'message_id',
            sa.String(500),
            nullable=True,
            comment='External message ID from provider'
        ),
        
        # Timing
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        
        # Error tracking
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_retry_at', sa.DateTime(), nullable=True),
        
        # Timestamps
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    
    # Create additional indexes
    op.create_index('idx_task_notifications_task_id', 'task_notifications', ['task_id'])
    op.create_index('idx_task_notifications_execution_id', 'task_notifications', ['execution_id'])
    op.create_index('idx_task_notifications_status', 'task_notifications', ['status'])
    op.create_index('idx_task_notifications_channel', 'task_notifications', ['channel'])
    op.create_index('idx_task_notifications_created_at', 'task_notifications', ['created_at'])


def downgrade() -> None:
    """Drop task_notifications table."""
    op.drop_table('task_notifications')
