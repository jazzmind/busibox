"""Add source column to conversations table

Revision ID: 007
Revises: 006
Create Date: 2026-01-14 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '007'
down_revision: Union[str, None] = '006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add source column to conversations table.
    
    This allows filtering conversations by the app/client that created them
    (e.g., 'ai-portal', 'agent-manager').
    """
    
    # Check if column already exists (idempotent migration)
    conn = op.get_bind()
    result = conn.execute(sa.text(
        """
        SELECT EXISTS (
            SELECT FROM information_schema.columns 
            WHERE table_name = 'conversations' 
            AND column_name = 'source'
        )
        """
    ))
    column_exists = result.scalar()
    
    if column_exists:
        # Column already exists, skip creation
        return
    
    # Add source column
    op.add_column(
        'conversations',
        sa.Column(
            'source',
            sa.String(length=50),
            nullable=True,
            comment="App/client that created this conversation (e.g., 'ai-portal', 'agent-manager')"
        )
    )
    
    # Create index for efficient filtering
    op.create_index('idx_conversations_source', 'conversations', ['source'])


def downgrade() -> None:
    """
    Remove source column from conversations table.
    """
    
    # Drop index
    op.drop_index('idx_conversations_source', table_name='conversations')
    
    # Drop column
    op.drop_column('conversations', 'source')
