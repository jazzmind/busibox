"""Rename workflow column to workflows in agent_definitions

Revision ID: 20260118_0001
Revises: 20260114_0000_007
Create Date: 2026-01-18 02:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Rename workflow column to workflows for consistency with the model
    """
    # Check if 'workflow' column exists before renaming
    # This makes the migration idempotent
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name='agent_definitions' 
        AND column_name='workflow'
    """))
    
    if result.fetchone():
        # Only rename if 'workflow' exists
        op.alter_column('agent_definitions', 'workflow', new_column_name='workflows')


def downgrade() -> None:
    """
    Revert workflow column rename
    """
    # Rename workflows back to workflow
    op.alter_column('agent_definitions', 'workflows', new_column_name='workflow')
