"""Add workflow_id column to agent_tasks and make agent_id nullable

Revision ID: 011
Revises: 010
Create Date: 2026-01-21

Tasks can now be associated with either an agent or a workflow,
so we need to add workflow_id and make agent_id nullable.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '011'
down_revision: Union[str, None] = '010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add workflow_id column and make agent_id nullable."""
    # Check if workflow_id column already exists
    conn = op.get_bind()
    result = conn.execute(sa.text(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_name = 'agent_tasks' AND column_name = 'workflow_id'
        )
        """
    ))
    if not result.scalar():
        # Add workflow_id column
        op.add_column(
            'agent_tasks',
            sa.Column(
                'workflow_id',
                postgresql.UUID(as_uuid=True),
                nullable=True,
                comment='Workflow ID (may be built-in from code or from workflow_definitions table)'
            )
        )
        
        # Create index for workflow_id
        op.create_index('ix_agent_tasks_workflow_id', 'agent_tasks', ['workflow_id'])
    
    # Make agent_id nullable (it was NOT NULL before)
    # This allows tasks to be associated with either an agent OR a workflow
    op.alter_column(
        'agent_tasks',
        'agent_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True
    )


def downgrade() -> None:
    """Remove workflow_id column and make agent_id NOT NULL again."""
    # Make agent_id NOT NULL again
    # First, set any NULL agent_ids to a default value
    conn = op.get_bind()
    conn.execute(sa.text(
        """
        UPDATE agent_tasks 
        SET agent_id = '00000000-0000-0000-0000-000000000000' 
        WHERE agent_id IS NULL
        """
    ))
    
    op.alter_column(
        'agent_tasks',
        'agent_id',
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False
    )
    
    # Drop workflow_id column
    op.drop_index('ix_agent_tasks_workflow_id', 'agent_tasks')
    op.drop_column('agent_tasks', 'workflow_id')
