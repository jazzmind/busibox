"""Add coding_agent_keys table

Revision ID: coding_agent_keys_010
Revises: agent_response_schema_009
Create Date: 2026-07-02 00:00:00

Local index of LiteLLM virtual keys issued to coding agents (Claude Code,
OpenCode, Hermes, Pi), replacing the shared LiteLLM master key previously
handed to every developer. LiteLLM remains the source of truth for a key's
live state (budget, models, spend); this table only tracks which key
aliases exist and what Busibox calls them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = 'coding_agent_keys_010'
down_revision: Union[str, None] = 'agent_response_schema_009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'coding_agent_keys',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('agent_name', sa.String(120), nullable=False),
        sa.Column('developer', sa.String(255), nullable=True),
        sa.Column('key_alias', sa.String(255), nullable=False, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_coding_agent_keys_agent_name', 'coding_agent_keys', ['agent_name'])


def downgrade() -> None:
    op.drop_index('ix_coding_agent_keys_agent_name', table_name='coding_agent_keys')
    op.drop_table('coding_agent_keys')
