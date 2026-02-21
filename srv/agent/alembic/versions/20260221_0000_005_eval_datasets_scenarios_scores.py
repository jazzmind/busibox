"""Add eval_datasets, eval_scenarios, eval_runs, and eval_scores tables

Revision ID: eval_tables_005
Revises: task_library_fields_004
Create Date: 2026-02-21 00:00:00

Adds the full eval / observability schema:
- eval_datasets: named collections of test scenarios
- eval_scenarios: individual test cases with expected outcomes
- eval_runs: tracks a batch eval execution against a dataset
- eval_scores: persisted scoring results from heuristic and LLM-as-judge scorers
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'eval_tables_005'
down_revision: Union[str, None] = 'task_library_fields_004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create eval_datasets, eval_scenarios, eval_runs, and eval_scores tables."""

    # eval_datasets
    op.create_table(
        'eval_datasets',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(120), nullable=False, unique=True),
        sa.Column('description', sa.Text, nullable=True),
        sa.Column('agent_id', sa.String(255), nullable=True),
        sa.Column('tags', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_eval_datasets_name', 'eval_datasets', ['name'])
    op.create_index('ix_eval_datasets_agent_id', 'eval_datasets', ['agent_id'])

    # eval_scenarios
    op.create_table(
        'eval_scenarios',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dataset_id', UUID(as_uuid=True),
                  sa.ForeignKey('eval_datasets.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('query', sa.Text, nullable=False),
        sa.Column('expected_agent', sa.String(120), nullable=True),
        sa.Column('expected_tools', sa.JSON, nullable=True),
        sa.Column('expected_output_contains', sa.JSON, nullable=True),
        sa.Column('expected_outcome', sa.Text, nullable=True),
        sa.Column('scenario_metadata', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('tags', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('is_active', sa.Boolean, nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_eval_scenarios_dataset_id', 'eval_scenarios', ['dataset_id'])

    # eval_runs
    op.create_table(
        'eval_runs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('dataset_id', UUID(as_uuid=True),
                  sa.ForeignKey('eval_datasets.id', ondelete='SET NULL'), nullable=True),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('status', sa.String(50), nullable=False, server_default='pending'),
        sa.Column('scorers', sa.JSON, nullable=False, server_default='[]'),
        sa.Column('model_override', sa.String(120), nullable=True),
        sa.Column('total_scenarios', sa.Integer, nullable=False, server_default='0'),
        sa.Column('passed_scenarios', sa.Integer, nullable=False, server_default='0'),
        sa.Column('failed_scenarios', sa.Integer, nullable=False, server_default='0'),
        sa.Column('avg_score', sa.Float, nullable=True),
        sa.Column('duration_seconds', sa.Float, nullable=True),
        sa.Column('error', sa.Text, nullable=True),
        sa.Column('created_by', sa.String(255), nullable=True),
        sa.Column('started_at', sa.DateTime, nullable=True),
        sa.Column('completed_at', sa.DateTime, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_eval_runs_dataset_id', 'eval_runs', ['dataset_id'])
    op.create_index('idx_eval_runs_status', 'eval_runs', ['status'])
    op.create_index('idx_eval_runs_created_at', 'eval_runs', ['created_at'])

    # eval_scores
    op.create_table(
        'eval_scores',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('run_id', UUID(as_uuid=True),
                  sa.ForeignKey('run_records.id', ondelete='SET NULL'), nullable=True),
        sa.Column('conversation_id', UUID(as_uuid=True),
                  sa.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('message_id', UUID(as_uuid=True),
                  sa.ForeignKey('messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('eval_run_id', UUID(as_uuid=True),
                  sa.ForeignKey('eval_runs.id', ondelete='SET NULL'), nullable=True),
        sa.Column('scenario_id', UUID(as_uuid=True),
                  sa.ForeignKey('eval_scenarios.id', ondelete='SET NULL'), nullable=True),
        sa.Column('agent_id', sa.String(255), nullable=True),
        sa.Column('scorer_name', sa.String(120), nullable=False),
        sa.Column('score', sa.Float, nullable=False),
        sa.Column('passed', sa.Boolean, nullable=False),
        sa.Column('details', sa.JSON, nullable=False, server_default='{}'),
        sa.Column('grading_model', sa.String(120), nullable=True),
        sa.Column('source', sa.String(50), nullable=False, server_default='offline'),
        sa.Column('created_at', sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index('idx_eval_scores_agent_id', 'eval_scores', ['agent_id'])
    op.create_index('idx_eval_scores_scorer_name', 'eval_scores', ['scorer_name'])
    op.create_index('idx_eval_scores_eval_run_id', 'eval_scores', ['eval_run_id'])
    op.create_index('idx_eval_scores_created_at', 'eval_scores', ['created_at'])
    op.create_index('idx_eval_scores_source', 'eval_scores', ['source'])


def downgrade() -> None:
    """Drop eval tables in reverse dependency order."""
    op.drop_table('eval_scores')
    op.drop_table('eval_runs')
    op.drop_table('eval_scenarios')
    op.drop_table('eval_datasets')
