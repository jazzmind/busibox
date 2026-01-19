"""Add conversation and message management

Revision ID: 003
Revises: 002
Create Date: 2025-12-12 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add conversation, message, and chat settings tables for agent-manager chat interface.
    """
    
    # Create conversations table
    op.create_table(
        'conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for conversations
    op.create_index('idx_conversations_user_id', 'conversations', ['user_id'])
    op.create_index('idx_conversations_created_at', 'conversations', ['created_at'])
    
    # Create messages table
    op.create_table(
        'messages',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('attachments', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('routing_decision', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('tool_calls', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['conversation_id'], 
            ['conversations.id'], 
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['run_id'], 
            ['run_records.id']
        )
    )
    
    # Create indexes for messages
    op.create_index('idx_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('idx_messages_created_at', 'messages', ['created_at'])
    op.create_index('idx_messages_run_id', 'messages', ['run_id'])
    
    # Create chat_settings table
    op.create_table(
        'chat_settings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('enabled_tools', postgresql.ARRAY(sa.String()), nullable=True, server_default='{}'),
        sa.Column('enabled_agents', postgresql.ARRAY(postgresql.UUID(as_uuid=True)), nullable=True, server_default='{}'),
        sa.Column('model', sa.String(length=255), nullable=True),
        sa.Column('temperature', sa.Float(), nullable=False, server_default='0.7'),
        sa.Column('max_tokens', sa.Integer(), nullable=False, server_default='2000'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
    )
    
    # Create index for chat_settings
    op.create_index('idx_chat_settings_user_id', 'chat_settings', ['user_id'], unique=True)


def downgrade() -> None:
    """
    Remove conversation, message, and chat settings tables.
    """
    
    # Drop chat_settings table
    op.drop_index('idx_chat_settings_user_id', table_name='chat_settings')
    op.drop_table('chat_settings')
    
    # Drop messages table
    op.drop_index('idx_messages_run_id', table_name='messages')
    op.drop_index('idx_messages_created_at', table_name='messages')
    op.drop_index('idx_messages_conversation_id', table_name='messages')
    op.drop_table('messages')
    
    # Drop conversations table
    op.drop_index('idx_conversations_created_at', table_name='conversations')
    op.drop_index('idx_conversations_user_id', table_name='conversations')
    op.drop_table('conversations')









