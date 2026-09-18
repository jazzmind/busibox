"""Add user_memory_files and chat_settings.memory_enabled

Revision ID: user_memory_010
Revises: chat_turns_009
Create Date: 2026-09-16 00:00:00

Per-user personal memory as encrypted markdown files (services/user_memory.py).
Owner-only: rows are filtered by the caller's user_id, content is
envelope-encrypted under the user's keystore key, and the row-level security
policy in docs/developers/user-memory.md closes the database itself.

Mirrors the idempotent definitions in app/schema.py.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'user_memory_010'
down_revision: Union[str, None] = 'chat_turns_009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        CREATE TABLE IF NOT EXISTS user_memory_files (
            id UUID PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL,
            path VARCHAR(200) NOT NULL,
            description VARCHAR(300),
            content BYTEA NOT NULL,
            is_encrypted BOOLEAN NOT NULL DEFAULT true,
            blob_id UUID NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """))
    conn.execute(sa.text(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_memory_files_user_path ON user_memory_files(user_id, path)"
    ))
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = 'chat_settings' AND column_name = 'memory_enabled'"
    ))
    if result.fetchone() is None:
        op.add_column(
            'chat_settings',
            sa.Column('memory_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        )


def downgrade() -> None:
    op.drop_column('chat_settings', 'memory_enabled')
    op.drop_table('user_memory_files')
