"""Add allow_frontier_fallback to agent_definitions

Revision ID: agent_frontier_fallback_003
Revises: chat_extensions_002
Create Date: 2026-02-11 00:00:00

Adds allow_frontier_fallback boolean column to agent_definitions table.
When true, allows automatic fallback to frontier cloud model when context
window is exceeded.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'agent_frontier_fallback_003'
down_revision: Union[str, None] = 'chat_extensions_002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add allow_frontier_fallback column to agent_definitions."""
    op.add_column(
        'agent_definitions',
        sa.Column(
            'allow_frontier_fallback',
            sa.Boolean(),
            server_default='false',
            nullable=False,
            comment='Allow automatic fallback to frontier cloud model when context window is exceeded',
        ),
    )


def downgrade() -> None:
    """Remove allow_frontier_fallback column from agent_definitions."""
    op.drop_column('agent_definitions', 'allow_frontier_fallback')
