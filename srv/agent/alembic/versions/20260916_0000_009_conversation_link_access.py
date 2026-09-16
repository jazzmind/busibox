"""Add link_access to conversations

Revision ID: conversation_link_access_009
Revises: agent_visibility_008
Create Date: 2026-09-16 00:00:00

Adds conversations.link_access ('private' | 'org'). When 'org', any
authenticated user of the deployment may open the conversation read-only via
its URL — the "shareable link" feature in the chat UI. Default 'private' keeps
existing behaviour (owner + explicit shares only).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'conversation_link_access_009'
down_revision: Union[str, None] = 'agent_visibility_008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'conversations' AND column_name = 'link_access'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'conversations',
            sa.Column('link_access', sa.String(20), nullable=False, server_default='private'),
        )


def downgrade() -> None:
    op.drop_column('conversations', 'link_access')
