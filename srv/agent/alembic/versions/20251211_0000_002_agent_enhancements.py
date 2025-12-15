"""Add agent enhancements for personal agents, dispatcher, and workflow resume

Revision ID: 002
Revises: 001
Create Date: 2025-12-11 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add fields for:
    - Personal agent management (is_builtin, created_by)
    - Version isolation (definition_snapshot)
    - Workflow resume (parent_run_id, resume_from_step, workflow_state)
    - Dispatcher decision logging (new table)
    """
    
    # Add is_builtin and created_by to agent_definitions
    op.add_column('agent_definitions', sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('agent_definitions', sa.Column('created_by', sa.String(length=255), nullable=True))
    
    # Add is_builtin and created_by to tool_definitions
    op.add_column('tool_definitions', sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default='false'))
    op.add_column('tool_definitions', sa.Column('created_by', sa.String(length=255), nullable=True))
    
    # Add created_by to workflow_definitions
    op.add_column('workflow_definitions', sa.Column('created_by', sa.String(length=255), nullable=True))
    
    # Add created_by to eval_definitions
    op.add_column('eval_definitions', sa.Column('created_by', sa.String(length=255), nullable=True))
    
    # Add version isolation and workflow resume fields to run_records
    op.add_column('run_records', sa.Column('definition_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('run_records', sa.Column('parent_run_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column('run_records', sa.Column('resume_from_step', sa.String(length=255), nullable=True))
    op.add_column('run_records', sa.Column('workflow_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    
    # Add foreign key for parent_run_id
    op.create_foreign_key(
        'fk_run_records_parent_run_id',
        'run_records', 'run_records',
        ['parent_run_id'], ['id'],
        ondelete='SET NULL'
    )
    
    # Create dispatcher_decision_log table
    op.create_table(
        'dispatcher_decision_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('query_text', sa.String(length=1000), nullable=False),
        sa.Column('selected_tools', postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column('selected_agents', postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('reasoning', sa.Text(), nullable=False),
        sa.Column('alternatives', postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='check_confidence_range')
    )
    
    # Create indexes for personal agent filtering
    op.create_index(
        'idx_agent_definitions_builtin_created',
        'agent_definitions',
        ['is_builtin', 'created_by'],
        postgresql_where=sa.text('is_active = true')
    )
    
    op.create_index(
        'idx_tool_definitions_builtin_created',
        'tool_definitions',
        ['is_builtin', 'created_by'],
        postgresql_where=sa.text('is_active = true')
    )
    
    op.create_index(
        'idx_tool_definitions_name_active',
        'tool_definitions',
        ['name'],
        postgresql_where=sa.text('is_active = true')
    )
    
    op.create_index(
        'idx_workflow_definitions_created_by',
        'workflow_definitions',
        ['created_by'],
        postgresql_where=sa.text('is_active = true')
    )
    
    op.create_index(
        'idx_eval_definitions_created_by',
        'eval_definitions',
        ['created_by'],
        postgresql_where=sa.text('is_active = true')
    )
    
    # Create indexes for run_records (version isolation and workflow resume)
    op.create_index(
        'idx_run_records_parent',
        'run_records',
        ['parent_run_id']
    )
    
    op.create_index(
        'idx_run_records_snapshot',
        'run_records',
        ['definition_snapshot'],
        postgresql_using='gin'
    )
    
    op.create_index(
        'idx_run_records_workflow_state',
        'run_records',
        ['workflow_state'],
        postgresql_using='gin'
    )
    
    # Create indexes for dispatcher_decision_log
    op.create_index(
        'idx_dispatcher_log_user_timestamp',
        'dispatcher_decision_log',
        ['user_id', sa.text('timestamp DESC')]
    )
    
    op.create_index(
        'idx_dispatcher_log_confidence',
        'dispatcher_decision_log',
        ['confidence']
    )
    
    op.create_index(
        'idx_dispatcher_log_timestamp',
        'dispatcher_decision_log',
        [sa.text('timestamp DESC')]
    )


def downgrade() -> None:
    """
    Remove agent enhancements.
    """
    # Drop indexes
    op.drop_index('idx_dispatcher_log_timestamp', table_name='dispatcher_decision_log')
    op.drop_index('idx_dispatcher_log_confidence', table_name='dispatcher_decision_log')
    op.drop_index('idx_dispatcher_log_user_timestamp', table_name='dispatcher_decision_log')
    op.drop_index('idx_run_records_workflow_state', table_name='run_records')
    op.drop_index('idx_run_records_snapshot', table_name='run_records')
    op.drop_index('idx_run_records_parent', table_name='run_records')
    op.drop_index('idx_eval_definitions_created_by', table_name='eval_definitions')
    op.drop_index('idx_workflow_definitions_created_by', table_name='workflow_definitions')
    op.drop_index('idx_tool_definitions_name_active', table_name='tool_definitions')
    op.drop_index('idx_tool_definitions_builtin_created', table_name='tool_definitions')
    op.drop_index('idx_agent_definitions_builtin_created', table_name='agent_definitions')
    
    # Drop dispatcher_decision_log table
    op.drop_table('dispatcher_decision_log')
    
    # Drop foreign key and columns from run_records
    op.drop_constraint('fk_run_records_parent_run_id', 'run_records', type_='foreignkey')
    op.drop_column('run_records', 'workflow_state')
    op.drop_column('run_records', 'resume_from_step')
    op.drop_column('run_records', 'parent_run_id')
    op.drop_column('run_records', 'definition_snapshot')
    
    # Drop columns from definition tables
    op.drop_column('eval_definitions', 'created_by')
    op.drop_column('workflow_definitions', 'created_by')
    op.drop_column('tool_definitions', 'created_by')
    op.drop_column('tool_definitions', 'is_builtin')
    op.drop_column('agent_definitions', 'created_by')
    op.drop_column('agent_definitions', 'is_builtin')





