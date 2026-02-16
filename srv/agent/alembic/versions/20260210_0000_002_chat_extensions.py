"""Add chat extensions: conversation model/privacy/agent, message metadata, sharing, attachments

Revision ID: chat_extensions_002
Revises: collapsed_001
Create Date: 2026-02-10 00:00:00

Adds columns and tables needed for Busibox Portal chat migration from Prisma to agent-api.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'chat_extensions_002'
down_revision: Union[str, None] = 'collapsed_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add chat extension columns and tables."""

    # -- conversations: new columns -----------------------------------------
    op.add_column('conversations', sa.Column('model', sa.String(255), nullable=True))
    op.add_column('conversations', sa.Column('is_private', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('conversations', sa.Column('agent_id', sa.String(255), nullable=True))
    op.create_index('idx_conversations_agent_id', 'conversations', ['agent_id'])

    # -- messages: new metadata column --------------------------------------
    op.add_column('messages', sa.Column('metadata', sa.JSON(), nullable=True))

    # -- conversation_shares ------------------------------------------------
    op.create_table(
        'conversation_shares',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('role', sa.String(20), server_default='viewer', nullable=False),
        sa.Column('shared_by', sa.String(255), nullable=False),
        sa.Column('shared_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('idx_conversation_shares_conv', 'conversation_shares', ['conversation_id'])
    op.create_index('idx_conversation_shares_user', 'conversation_shares', ['user_id'])
    op.create_index('uq_conversation_shares_conv_user', 'conversation_shares',
                    ['conversation_id', 'user_id'], unique=True)

    # -- chat_attachments ---------------------------------------------------
    op.create_table(
        'chat_attachments',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('message_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=True),
        sa.Column('filename', sa.String(500), nullable=False),
        sa.Column('file_url', sa.Text(), nullable=False),
        sa.Column('mime_type', sa.String(255), nullable=True),
        sa.Column('size_bytes', sa.BigInteger(), nullable=True),
        sa.Column('added_to_library', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('library_document_id', sa.String(255), nullable=True),
        sa.Column('parsed_content', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index('idx_chat_attachments_message', 'chat_attachments', ['message_id'])

    # -- grant permissions --------------------------------------------------
    op.execute("GRANT ALL ON conversation_shares TO busibox_user")
    op.execute("GRANT ALL ON chat_attachments TO busibox_user")


def downgrade() -> None:
    """Revert chat extension changes."""
    op.drop_table('chat_attachments')
    op.drop_table('conversation_shares')

    op.drop_index('idx_conversations_agent_id', table_name='conversations')
    op.drop_column('conversations', 'agent_id')
    op.drop_column('conversations', 'is_private')
    op.drop_column('conversations', 'model')

    op.drop_column('messages', 'metadata')
