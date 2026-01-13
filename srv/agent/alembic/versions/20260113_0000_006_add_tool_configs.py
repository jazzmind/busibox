"""Add tool_configs table for hierarchical tool configuration

Revision ID: 006
Revises: 005
Create Date: 2026-01-13 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '006'
down_revision: Union[str, None] = '005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add tool_configs table for storing tool configuration at different scopes:
    - system: Global defaults for all users/agents
    - agent: Default settings for a specific agent
    - user: Personal settings that override everything else
    
    Configuration lookup priority: user > agent > system > app settings
    """
    
    # Check if table already exists (idempotent migration)
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'tool_configs')"
    ))
    table_exists = result.scalar()
    
    if table_exists:
        # Table already exists, skip creation
        return
    
    # Create tool_configs table
    op.create_table(
        'tool_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tool_id', postgresql.UUID(as_uuid=True), nullable=False, comment='Tool UUID (can be built-in)'),
        sa.Column('tool_name', sa.String(length=120), nullable=False, comment='Tool name for lookup'),
        sa.Column('scope', sa.String(length=20), nullable=False, server_default='user', comment='Config scope: system, agent, or user'),
        sa.Column('user_id', sa.String(length=255), nullable=True, comment='User ID for user-scoped config'),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True, comment='Agent ID for agent-scoped config'),
        sa.Column('config', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}', comment='Provider configuration (enabled, api_keys, etc.)'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for efficient lookups
    op.create_index('ix_tool_configs_tool_id', 'tool_configs', ['tool_id'])
    op.create_index('ix_tool_configs_tool_name', 'tool_configs', ['tool_name'])
    op.create_index('ix_tool_configs_user_id', 'tool_configs', ['user_id'])
    op.create_index('ix_tool_configs_agent_id', 'tool_configs', ['agent_id'])
    op.create_index('ix_tool_configs_scope', 'tool_configs', ['scope'])
    
    # Create unique constraint for scope-based lookups
    # Ensures only one config per tool/scope/user/agent combination
    op.create_index(
        'ix_tool_config_unique_scope',
        'tool_configs',
        ['tool_id', 'scope', 'user_id', 'agent_id'],
        unique=True
    )


def downgrade() -> None:
    """
    Remove tool_configs table.
    """
    
    # Drop indexes
    op.drop_index('ix_tool_config_unique_scope', table_name='tool_configs')
    op.drop_index('ix_tool_configs_scope', table_name='tool_configs')
    op.drop_index('ix_tool_configs_agent_id', table_name='tool_configs')
    op.drop_index('ix_tool_configs_user_id', table_name='tool_configs')
    op.drop_index('ix_tool_configs_tool_name', table_name='tool_configs')
    op.drop_index('ix_tool_configs_tool_id', table_name='tool_configs')
    
    # Drop table
    op.drop_table('tool_configs')
