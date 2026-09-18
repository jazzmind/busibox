"""Add chat_turns and chat_settings.notify_email_on_completion

Revision ID: chat_turns_009
Revises: agent_visibility_008
Create Date: 2026-09-15 00:00:00

Chat requests become server-side jobs (services/chat_turns.py). The
chat_turns row is created before the agent runs and updated when it ends,
so a turn whose browser went away (laptop sleep, closed tab) still leaves a
record, keeps running, and can email the user when it finishes.

Mirrors the idempotent definitions in app/schema.py, which the service also
applies at startup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'chat_turns_009'
down_revision: Union[str, None] = 'agent_visibility_008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS chat_turns (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id VARCHAR(255) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'running',
            query TEXT NOT NULL,
            user_message_id UUID,
            assistant_message_id UUID,
            error TEXT,
            event_count INTEGER NOT NULL DEFAULT 0,
            last_event_id VARCHAR(64),
            notified_at TIMESTAMP,
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMP
        )
    """))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chat_turns_conversation_status ON chat_turns(conversation_id, status)"
    ))
    conn.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS idx_chat_turns_user_status ON chat_turns(user_id, status)"
    ))
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'chat_settings' AND column_name = 'notify_email_on_completion'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'chat_settings',
            sa.Column('notify_email_on_completion', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        )


def downgrade() -> None:
    op.drop_column('chat_settings', 'notify_email_on_completion')
    op.drop_table('chat_turns')
