"""Add library_id and schema_document_id to agent_tasks

Revision ID: task_library_fields_004
Revises: agent_frontier_fallback_003
Create Date: 2026-02-14 00:00:00

Adds library_id and schema_document_id columns to agent_tasks table
for library-triggered task support.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'task_library_fields_004'
down_revision: Union[str, None] = 'agent_frontier_fallback_003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add library_id and schema_document_id columns to agent_tasks."""
    op.add_column(
        'agent_tasks',
        sa.Column(
            'library_id',
            sa.String(255),
            nullable=True,
            comment='Library ID for library-triggered tasks',
        ),
    )
    op.add_column(
        'agent_tasks',
        sa.Column(
            'schema_document_id',
            sa.String(255),
            nullable=True,
            comment='Data document ID containing the extraction schema',
        ),
    )
    op.create_index('idx_agent_tasks_library_id', 'agent_tasks', ['library_id'])


def downgrade() -> None:
    """Remove library_id and schema_document_id columns from agent_tasks."""
    op.drop_index('idx_agent_tasks_library_id', table_name='agent_tasks')
    op.drop_column('agent_tasks', 'schema_document_id')
    op.drop_column('agent_tasks', 'library_id')
