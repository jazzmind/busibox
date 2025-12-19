"""Initial schema

Revision ID: 001
Revises: 
Create Date: 2025-12-10 20:53:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_definitions_name'), 'agent_definitions', ['name'], unique=True)

    # Tool Definitions
    op.create_table(
        'tool_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('schema', sa.JSON(), nullable=False),
        sa.Column('entrypoint', sa.String(length=255), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_tool_definitions_name'), 'tool_definitions', ['name'], unique=True)

    # Workflow Definitions
    op.create_table(
        'workflow_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('steps', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_workflow_definitions_name'), 'workflow_definitions', ['name'], unique=True)

    # Eval Definitions
    op.create_table(
        'eval_definitions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_eval_definitions_name'), 'eval_definitions', ['name'], unique=True)

    # RAG Databases
    op.create_table(
        'rag_databases',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('config', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rag_databases_name'), 'rag_databases', ['name'], unique=True)

    # RAG Documents
    op.create_table(
        'rag_documents',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('rag_database_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('path', sa.String(length=255), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['rag_database_id'], ['rag_databases.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_rag_documents_rag_database_id'), 'rag_documents', ['rag_database_id'], unique=False)

    # Run Records
    op.create_table(
        'run_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('agent_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('workflow_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('input', sa.JSON(), nullable=False),
        sa.Column('output', sa.JSON(), nullable=True),
        sa.Column('events', sa.JSON(), nullable=False),
        sa.Column('created_by', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    # Token Grants
    op.create_table(
        'token_grants',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('subject', sa.String(length=255), nullable=False),
        sa.Column('scopes', sa.JSON(), nullable=False),
        sa.Column('token', sa.Text(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_token_grants_subject'), 'token_grants', ['subject'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_token_grants_subject'), table_name='token_grants')
    op.drop_table('token_grants')
    op.drop_table('run_records')
    op.drop_index(op.f('ix_rag_documents_rag_database_id'), table_name='rag_documents')
    op.drop_table('rag_documents')
    op.drop_index(op.f('ix_rag_databases_name'), table_name='rag_databases')
    op.drop_table('rag_databases')
    op.drop_index(op.f('ix_eval_definitions_name'), table_name='eval_definitions')
    op.drop_table('eval_definitions')
    op.drop_index(op.f('ix_workflow_definitions_name'), table_name='workflow_definitions')
    op.drop_table('workflow_definitions')
    op.drop_index(op.f('ix_tool_definitions_name'), table_name='tool_definitions')
    op.drop_table('tool_definitions')
    op.drop_index(op.f('ix_agent_definitions_name'), table_name='agent_definitions')
    op.drop_table('agent_definitions')









