"""Add response_schema to agent_definitions

Revision ID: agent_response_schema_009
Revises: agent_visibility_008
Create Date: 2026-06-30 00:00:00

Adds an optional response_schema JSON column to agent_definitions.
When set, the schema envelope ({name, strict, schema}) is enforced on
every run output for this agent, allowing structured output to be
declared alongside the agent definition rather than at invoke time.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'agent_response_schema_009'
down_revision: Union[str, None] = 'agent_visibility_008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'agent_definitions' AND column_name = 'response_schema'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'agent_definitions',
            sa.Column('response_schema', sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column('agent_definitions', 'response_schema')
