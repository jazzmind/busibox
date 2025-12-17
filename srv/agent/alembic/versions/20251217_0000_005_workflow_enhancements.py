"""workflow enhancements with triggers, guardrails, and executions

Revision ID: 005
Revises: 004
Create Date: 2025-12-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '005'
down_revision: Union[str, None] = '004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add workflow enhancements:
    1. Add trigger and guardrails columns to workflow_definitions
    2. Create workflow_executions table
    3. Create step_executions table
    """
    # Add columns to workflow_definitions
    op.add_column('workflow_definitions', sa.Column('trigger', sa.JSON(), nullable=False, server_default='{}'))
    op.add_column('workflow_definitions', sa.Column('guardrails', sa.JSON(), nullable=True))
    
    # Create workflow_executions table
    op.create_table(
        'workflow_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('trigger_source', sa.String(255), nullable=False),
        sa.Column('input_data', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('current_step_id', sa.String(255), nullable=True),
        sa.Column('step_outputs', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('usage_requests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_tool_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost_dollars', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('failed_step_id', sa.String(255), nullable=True),
        sa.Column('awaiting_approval_data', sa.JSON(), nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflow_definitions.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for workflow_executions
    op.create_index('idx_workflow_executions_workflow_id', 'workflow_executions', ['workflow_id'])
    op.create_index('idx_workflow_executions_status', 'workflow_executions', ['status'])
    op.create_index('idx_workflow_executions_created_at', 'workflow_executions', ['created_at'])
    
    # Create step_executions table
    op.create_table(
        'step_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('step_id', sa.String(255), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('input_data', sa.JSON(), nullable=True),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column('usage_requests', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('usage_tool_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost_dollars', sa.Float(), nullable=False, server_default='0.0'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['execution_id'], ['workflow_executions.id'], ondelete='CASCADE'),
    )
    
    # Create indexes for step_executions
    op.create_index('idx_step_executions_execution_id', 'step_executions', ['execution_id'])
    op.create_index('idx_step_executions_step_id', 'step_executions', ['step_id'])
    op.create_index('idx_step_executions_status', 'step_executions', ['status'])


def downgrade() -> None:
    """
    Revert workflow enhancements
    """
    # Drop tables in reverse order
    op.drop_index('idx_step_executions_status', table_name='step_executions')
    op.drop_index('idx_step_executions_step_id', table_name='step_executions')
    op.drop_index('idx_step_executions_execution_id', table_name='step_executions')
    op.drop_table('step_executions')
    
    op.drop_index('idx_workflow_executions_created_at', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_status', table_name='workflow_executions')
    op.drop_index('idx_workflow_executions_workflow_id', table_name='workflow_executions')
    op.drop_table('workflow_executions')
    
    # Remove columns from workflow_definitions
    op.drop_column('workflow_definitions', 'guardrails')
    op.drop_column('workflow_definitions', 'trigger')
