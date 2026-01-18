"""Rename workflow column to workflows in agent_definitions

Revision ID: 20260118_0001
Revises: 20260114_0000_007
Create Date: 2026-01-18 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260118_0001'
down_revision = '20260114_0000_007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Rename workflow column to workflows for consistency with the model
    """
    # Rename workflow to workflows in agent_definitions
    op.alter_column('agent_definitions', 'workflow', new_column_name='workflows')


def downgrade() -> None:
    """
    Revert workflow column rename
    """
    # Rename workflows back to workflow
    op.alter_column('agent_definitions', 'workflows', new_column_name='workflow')
