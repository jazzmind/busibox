"""Collapsed schema - all migrations merged into one

Revision ID: collapsed_001
Revises:
Create Date: 2026-01-30 00:00:00

This migration represents the complete, final schema for the agent service.
All previous migrations have been collapsed into this single migration.

To apply this to an existing database, you must:
1. Drop all tables or use a fresh database
2. Run: alembic upgrade head
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'collapsed_001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create complete agent service schema."""
    
    # ==========================================================================
    # Core Definition Tables
    # ==========================================================================
    
    # Agent Definitions
    op.create_table(
        'agent_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('display_name', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('model', sa.String(length=255), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('tools', sa.JSON(), nullable=False),
        sa.Column('workflows', sa.JSON(), nullable=True),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_agent_definitions_name', 'agent_definitions', ['name'], unique=True)
    op.create_index('idx_agent_definitions_builtin_created', 'agent_definitions', ['is_builtin', 'created_by'], postgresql_where=sa.text('is_active = true'))
    
    # Tool Definitions
    op.create_table(
        'tool_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('schema', sa.JSON(), nullable=False),
        sa.Column('entrypoint', sa.String(length=255), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('is_builtin', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tool_definitions_name', 'tool_definitions', ['name'], unique=True)
    op.create_index('idx_tool_definitions_builtin_created', 'tool_definitions', ['is_builtin', 'created_by'], postgresql_where=sa.text('is_active = true'))
    op.create_index('idx_tool_definitions_name_active', 'tool_definitions', ['name'], postgresql_where=sa.text('is_active = true'))
    
    # Workflow Definitions
    op.create_table(
        'workflow_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('trigger', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('guardrails', sa.JSON(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_workflow_definitions_name', 'workflow_definitions', ['name'], unique=True)
    op.create_index('idx_workflow_definitions_created_by', 'workflow_definitions', ['created_by'], postgresql_where=sa.text('is_active = true'))
    
    # Eval Definitions
    op.create_table(
        'eval_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_eval_definitions_name', 'eval_definitions', ['name'], unique=True)
    op.create_index('idx_eval_definitions_created_by', 'eval_definitions', ['created_by'], postgresql_where=sa.text('is_active = true'))
    
    # ==========================================================================
    # RAG Tables
    # ==========================================================================
    
    # RAG Databases
    op.create_table(
        'rag_databases',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rag_databases_name', 'rag_databases', ['name'], unique=True)
    
    # RAG Documents
    op.create_table(
        'rag_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rag_database_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['rag_database_id'], ['rag_databases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rag_documents_rag_database_id', 'rag_documents', ['rag_database_id'])
    
    # ==========================================================================
    # Run Records and Execution Tables
    # ==========================================================================
    
    # Run Records
    op.create_table(
        'run_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON(), nullable=True),
        sa.Column('events', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('definition_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('parent_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resume_from_step', sa.String(length=255), nullable=True),
        sa.Column('workflow_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_foreign_key('fk_run_records_parent_run_id', 'run_records', 'run_records', ['parent_run_id'], ['id'], ondelete='SET NULL')
    op.create_index('idx_run_records_parent', 'run_records', ['parent_run_id'])
    op.create_index('idx_run_records_snapshot', 'run_records', ['definition_snapshot'], postgresql_using='gin')
    op.create_index('idx_run_records_workflow_state', 'run_records', ['workflow_state'], postgresql_using='gin')
    op.create_index('idx_run_records_agent', 'run_records', ['agent_id'])
    op.create_index('idx_run_records_created', 'run_records', ['created_at'])
    
    # Token Grants
    op.create_table(
        'token_grants',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_token_grants_subject', 'token_grants', ['subject'])
    
    # Dispatcher Decision Log
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
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint('confidence >= 0 AND confidence <= 1', name='check_confidence_range')
    )
    op.create_index('idx_dispatcher_log_user_timestamp', 'dispatcher_decision_log', ['user_id', sa.text('timestamp DESC')])
    op.create_index('idx_dispatcher_log_confidence', 'dispatcher_decision_log', ['confidence'])
    op.create_index('idx_dispatcher_log_timestamp', 'dispatcher_decision_log', [sa.text('timestamp DESC')])
    
    # ==========================================================================
    # Workflow Execution Tables
    # ==========================================================================
    
    # Workflow Executions
    op.create_table(
        'workflow_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflow_definitions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_workflow_executions_workflow_id', 'workflow_executions', ['workflow_id'])
    op.create_index('idx_workflow_executions_status', 'workflow_executions', ['status'])
    op.create_index('idx_workflow_executions_created_at', 'workflow_executions', ['created_at'])
    
    # Step Executions
    op.create_table(
        'step_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
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
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['execution_id'], ['workflow_executions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_step_executions_execution_id', 'step_executions', ['execution_id'])
    op.create_index('idx_step_executions_step_id', 'step_executions', ['step_id'])
    op.create_index('idx_step_executions_status', 'step_executions', ['status'])
    
    # ==========================================================================
    # Conversation Tables
    # ==========================================================================
    
    # Conversations
    op.create_table(
        'conversations',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('user_id', sa.String(length=255), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_conversations_user_id', 'conversations', ['user_id'])
    op.create_index('idx_conversations_created_at', 'conversations', ['created_at'])
    op.create_index('idx_conversations_source', 'conversations', ['source'])
    
    # Messages
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
        sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['run_records.id']),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('idx_messages_created_at', 'messages', ['created_at'])
    op.create_index('idx_messages_run_id', 'messages', ['run_id'])
    
    # Chat Settings
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
    op.create_index('idx_chat_settings_user_id', 'chat_settings', ['user_id'], unique=True)
    
    # ==========================================================================
    # Tool Configuration Tables
    # ==========================================================================
    
    # Tool Configs
    op.create_table(
        'tool_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tool_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('tool_name', sa.String(length=120), nullable=False),
        sa.Column('scope', sa.String(length=20), nullable=False, server_default='user'),
        sa.Column('user_id', sa.String(length=255), nullable=True),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('config', postgresql.JSON(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tool_configs_tool_id', 'tool_configs', ['tool_id'])
    op.create_index('ix_tool_configs_tool_name', 'tool_configs', ['tool_name'])
    op.create_index('ix_tool_configs_user_id', 'tool_configs', ['user_id'])
    op.create_index('ix_tool_configs_agent_id', 'tool_configs', ['agent_id'])
    op.create_index('ix_tool_configs_scope', 'tool_configs', ['scope'])
    op.create_index('ix_tool_config_unique_scope', 'tool_configs', ['tool_id', 'scope', 'user_id', 'agent_id'], unique=True)
    
    # ==========================================================================
    # Agent Tasks Tables
    # ==========================================================================
    
    # Agent Tasks
    op.create_table(
        'agent_tasks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('user_id', sa.String(255), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('input_config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('trigger_type', sa.String(50), nullable=False),
        sa.Column('trigger_config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('delegation_token', sa.Text(), nullable=True),
        sa.Column('delegation_scopes', sa.JSON(), nullable=False, server_default='[]'),
        sa.Column('delegation_expires_at', sa.DateTime(), nullable=True),
        sa.Column('notification_config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('insights_config', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('output_saving_config', postgresql.JSON(astext_type=sa.Text()), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='active'),
        sa.Column('scheduler_job_id', sa.String(255), nullable=True),
        sa.Column('webhook_secret', sa.String(255), nullable=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('next_run_at', sa.DateTime(), nullable=True),
        sa.Column('run_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['last_run_id'], ['run_records.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_agent_tasks_user_id', 'agent_tasks', ['user_id'])
    op.create_index('idx_agent_tasks_agent_id', 'agent_tasks', ['agent_id'])
    op.create_index('ix_agent_tasks_workflow_id', 'agent_tasks', ['workflow_id'])
    op.create_index('idx_agent_tasks_status', 'agent_tasks', ['status'])
    op.create_index('idx_agent_tasks_trigger_type', 'agent_tasks', ['trigger_type'])
    op.create_index('idx_agent_tasks_next_run_at', 'agent_tasks', ['next_run_at'])
    
    # Task Executions
    op.create_table(
        'task_executions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('trigger_source', sa.String(50), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('input_data', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('output_data', sa.JSON(), nullable=True),
        sa.Column('output_summary', sa.Text(), nullable=True),
        sa.Column('notification_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('notification_error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_seconds', sa.Float(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['task_id'], ['agent_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['run_id'], ['run_records.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_task_executions_task_id', 'task_executions', ['task_id'])
    op.create_index('idx_task_executions_status', 'task_executions', ['status'])
    op.create_index('idx_task_executions_created_at', 'task_executions', ['created_at'])
    
    # Task Notifications
    op.create_table(
        'task_notifications',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('execution_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('channel', sa.String(50), nullable=False),
        sa.Column('recipient', sa.String(500), nullable=False),
        sa.Column('subject', sa.String(500), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('message_id', sa.String(500), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('delivered_at', sa.DateTime(), nullable=True),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_retry_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['task_id'], ['agent_tasks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['execution_id'], ['task_executions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_task_notifications_task_id', 'task_notifications', ['task_id'])
    op.create_index('idx_task_notifications_execution_id', 'task_notifications', ['execution_id'])
    op.create_index('idx_task_notifications_status', 'task_notifications', ['status'])
    op.create_index('idx_task_notifications_channel', 'task_notifications', ['channel'])
    op.create_index('idx_task_notifications_created_at', 'task_notifications', ['created_at'])


def downgrade() -> None:
    """Drop all tables in reverse order."""
    op.drop_table('task_notifications')
    op.drop_table('task_executions')
    op.drop_table('agent_tasks')
    op.drop_table('tool_configs')
    op.drop_table('chat_settings')
    op.drop_table('messages')
    op.drop_table('conversations')
    op.drop_table('step_executions')
    op.drop_table('workflow_executions')
    op.drop_table('dispatcher_decision_log')
    op.drop_table('token_grants')
    op.drop_table('run_records')
    op.drop_table('rag_documents')
    op.drop_table('rag_databases')
    op.drop_table('eval_definitions')
    op.drop_table('workflow_definitions')
    op.drop_table('tool_definitions')
    op.drop_table('agent_definitions')
