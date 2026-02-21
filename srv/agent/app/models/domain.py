import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

# Use JSON for SQLite compatibility, JSONB for PostgreSQL
try:
    from sqlalchemy.dialects.postgresql import JSONB as JSONType
except ImportError:
    JSONType = JSON  # type: ignore


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    # Return timezone-naive UTC datetime for PostgreSQL TIMESTAMP WITHOUT TIME ZONE
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(255))
    instructions: Mapped[str] = mapped_column(Text)
    tools: Mapped[dict] = mapped_column(JSON, default=dict)
    workflows: Mapped[Optional[dict]] = mapped_column(JSON, default=None)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_frontier_fallback: Mapped[bool] = mapped_column(
        Boolean, default=False,
        comment="Allow automatic fallback to frontier cloud model when context window is exceeded"
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<AgentDefinition(id={self.id}, name={self.name}, is_builtin={self.is_builtin}, version={self.version})>"


class ToolDefinition(Base):
    __tablename__ = "tool_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    schema: Mapped[dict] = mapped_column(JSON, default=dict)
    entrypoint: Mapped[str] = mapped_column(String(255), comment="registered adapter name")
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<ToolDefinition(id={self.id}, name={self.name}, is_builtin={self.is_builtin}, version={self.version})>"


class ToolConfig(Base):
    """
    Runtime configuration for tools (e.g., API keys, provider settings).
    
    Configuration hierarchy (highest priority first):
    1. User-level: scope='user', user_id=<user_id>, agent_id=NULL
    2. Agent-level: scope='agent', user_id=NULL, agent_id=<agent_id>
    3. System-level: scope='system', user_id=NULL, agent_id=NULL
    
    When looking up config, check user first, then agent, then system.
    """
    __tablename__ = "tool_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    tool_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, comment="Tool UUID (can be built-in)")
    tool_name: Mapped[str] = mapped_column(String(120), index=True, comment="Tool name for lookup")
    scope: Mapped[str] = mapped_column(String(20), default="user", comment="Config scope: system, agent, or user")
    user_id: Mapped[Optional[str]] = mapped_column(String(255), index=True, comment="User ID for user-scoped config")
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), index=True, comment="Agent ID for agent-scoped config")
    config: Mapped[dict] = mapped_column(JSON, default=dict, comment="Provider configuration (enabled, api_keys, etc.)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index('ix_tool_config_scope', 'tool_id', 'scope', 'user_id', 'agent_id', unique=True),
    )

    def __repr__(self) -> str:
        return f"<ToolConfig(tool_id={self.tool_id}, scope={self.scope}, user_id={self.user_id}, agent_id={self.agent_id})>"


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    trigger: Mapped[dict] = mapped_column(JSON, default=dict)  # NEW: Trigger configuration
    guardrails: Mapped[Optional[dict]] = mapped_column(JSON, default=None)  # NEW: Global guardrails
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<WorkflowDefinition(id={self.id}, name={self.name}, version={self.version})>"


class WorkflowExecution(Base):
    """Track workflow execution state and results"""
    __tablename__ = "workflow_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_definitions.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    # Status values: pending, running, completed, failed, timeout, awaiting_human, cancelled
    
    trigger_source: Mapped[str] = mapped_column(String(255))  # manual, cron, webhook, event
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    
    # Current execution state
    current_step_id: Mapped[Optional[str]] = mapped_column(String(255))
    step_outputs: Mapped[dict] = mapped_column(JSON, default=dict)  # Results from each step
    
    # Usage tracking (aggregated across all steps)
    usage_requests: Mapped[int] = mapped_column(Integer, default=0)
    usage_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_dollars: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    
    # Error tracking
    error: Mapped[Optional[str]] = mapped_column(Text)
    failed_step_id: Mapped[Optional[str]] = mapped_column(String(255))
    
    # Human-in-loop state
    awaiting_approval_data: Mapped[Optional[dict]] = mapped_column(JSON)
    
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    
    # Relationships
    workflow: Mapped["WorkflowDefinition"] = relationship("WorkflowDefinition", lazy="selectin")
    steps: Mapped[list["StepExecution"]] = relationship(
        "StepExecution", back_populates="execution", cascade="all, delete-orphan"
    )
    
    __table_args__ = (
        Index('idx_workflow_executions_workflow_id', 'workflow_id'),
        Index('idx_workflow_executions_status', 'status'),
        Index('idx_workflow_executions_created_at', 'created_at'),
    )
    
    def __repr__(self) -> str:
        return f"<WorkflowExecution(id={self.id}, workflow_id={self.workflow_id}, status={self.status})>"


class StepExecution(Base):
    """Track individual step execution within a workflow"""
    __tablename__ = "step_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workflow_executions.id", ondelete="CASCADE"), index=True
    )
    step_id: Mapped[str] = mapped_column(String(255), index=True)  # From workflow step definition
    status: Mapped[str] = mapped_column(String(50), default="pending")
    # Status values: pending, running, completed, failed, skipped
    
    # Step I/O
    input_data: Mapped[Optional[dict]] = mapped_column(JSON)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON)
    
    # Usage tracking (for this step only)
    usage_requests: Mapped[int] = mapped_column(Integer, default=0)
    usage_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_dollars: Mapped[float] = mapped_column(Float, default=0.0)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    
    # Error tracking
    error: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    
    # Relationships
    execution: Mapped["WorkflowExecution"] = relationship("WorkflowExecution", back_populates="steps")
    
    __table_args__ = (
        Index('idx_step_executions_execution_id', 'execution_id'),
        Index('idx_step_executions_step_id', 'step_id'),
        Index('idx_step_executions_status', 'status'),
    )
    
    def __repr__(self) -> str:
        return f"<StepExecution(id={self.id}, execution_id={self.execution_id}, step_id={self.step_id}, status={self.status})>"

class EvalDefinition(Base):
    __tablename__ = "eval_definitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<EvalDefinition(id={self.id}, name={self.name}, version={self.version})>"


class EvalDataset(Base):
    """A named collection of eval scenarios for testing agents."""
    __tablename__ = "eval_datasets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    # Optional: restrict this dataset to a specific agent
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    scenarios: Mapped[list["EvalScenario"]] = relationship(
        "EvalScenario", back_populates="dataset", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<EvalDataset(id={self.id}, name={self.name})>"


class EvalScenario(Base):
    """A single test case: a query with expected outcomes."""
    __tablename__ = "eval_scenarios"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_datasets.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    query: Mapped[str] = mapped_column(Text)
    # Optional expected outcomes for heuristic and LLM grading
    expected_agent: Mapped[Optional[str]] = mapped_column(String(120))
    expected_tools: Mapped[Optional[list]] = mapped_column(JSON)
    expected_output_contains: Mapped[Optional[list]] = mapped_column(JSON)
    expected_outcome: Mapped[Optional[str]] = mapped_column(Text)
    # Extra context injected with the query (e.g., conversation history)
    scenario_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    dataset: Mapped["EvalDataset"] = relationship("EvalDataset", back_populates="scenarios")

    __table_args__ = (
        Index("idx_eval_scenarios_dataset_id", "dataset_id"),
    )

    def __repr__(self) -> str:
        return f"<EvalScenario(id={self.id}, name={self.name}, dataset_id={self.dataset_id})>"


class EvalRun(Base):
    """Tracks a batch eval execution against a dataset."""
    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_datasets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[Optional[str]] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    # Status: pending, running, completed, failed
    scorers: Mapped[list] = mapped_column(JSON, default=list)  # list of scorer names
    model_override: Mapped[Optional[str]] = mapped_column(String(120))
    # Summary results
    total_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    passed_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    failed_scenarios: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[Optional[float]] = mapped_column(Float)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    dataset: Mapped[Optional["EvalDataset"]] = relationship("EvalDataset")
    scores: Mapped[list["EvalScore"]] = relationship(
        "EvalScore", back_populates="eval_run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_eval_runs_dataset_id", "dataset_id"),
        Index("idx_eval_runs_status", "status"),
        Index("idx_eval_runs_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<EvalRun(id={self.id}, status={self.status}, dataset_id={self.dataset_id})>"


class EvalScore(Base):
    """Persisted score result for a single agent response."""
    __tablename__ = "eval_scores"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    # Source: run_record, conversation message, or direct eval
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_records.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # Eval batch context
    eval_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scenario_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_scenarios.id", ondelete="SET NULL"), nullable=True
    )
    # Agent that produced the response
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    # Scoring metadata
    scorer_name: Mapped[str] = mapped_column(String(120), index=True)
    score: Mapped[float] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    # For LLM-as-judge: which model was used to grade
    grading_model: Mapped[Optional[str]] = mapped_column(String(120))
    # Online vs offline eval
    source: Mapped[str] = mapped_column(String(50), default="offline")
    # Source: "offline" (batch eval), "online" (production sampling)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    eval_run: Mapped[Optional["EvalRun"]] = relationship("EvalRun", back_populates="scores")

    __table_args__ = (
        Index("idx_eval_scores_agent_id", "agent_id"),
        Index("idx_eval_scores_scorer_name", "scorer_name"),
        Index("idx_eval_scores_eval_run_id", "eval_run_id"),
        Index("idx_eval_scores_created_at", "created_at"),
        Index("idx_eval_scores_source", "source"),
    )

    def __repr__(self) -> str:
        return f"<EvalScore(id={self.id}, scorer_name={self.scorer_name}, score={self.score}, passed={self.passed})>"


class RagDatabase(Base):
    __tablename__ = "rag_databases"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )


class RagDocument(Base):
    __tablename__ = "rag_documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    rag_database_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rag_databases.id", ondelete="CASCADE"), index=True
    )
    path: Mapped[str] = mapped_column(String(255))
    doc_metadata: Mapped[dict] = mapped_column("metadata", JSON, default=dict)  # Use column name 'metadata' but attribute 'doc_metadata'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )

    database: Mapped[RagDatabase] = relationship("RagDatabase", lazy="selectin")


class RunRecord(Base):
    __tablename__ = "run_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(50), default="pending")
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[Optional[dict]] = mapped_column(JSON)
    events: Mapped[list] = mapped_column(JSON, default=list)
    definition_snapshot: Mapped[Optional[dict]] = mapped_column(JSONType)
    parent_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_records.id", ondelete="SET NULL")
    )
    resume_from_step: Mapped[Optional[str]] = mapped_column(String(255))
    workflow_state: Mapped[Optional[dict]] = mapped_column(JSONType)
    created_by: Mapped[Optional[str]] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_now, onupdate=_now
    )
    
    def __repr__(self) -> str:
        return f"<RunRecord(id={self.id}, agent_id={self.agent_id}, status={self.status}, parent_run_id={self.parent_run_id})>"


class TokenGrant(Base):
    __tablename__ = "token_grants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    subject: Mapped[str] = mapped_column(String(255), index=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    token: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Conversation(Base):
    """Chat conversation between user and agents"""
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, index=True,
        comment="App/client that created this conversation (e.g., 'busibox-portal', 'busibox-agents')"
    )
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan"
    )
    shares: Mapped[list["ConversationShare"]] = relationship(
        "ConversationShare", back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_conversations_user_id', 'user_id'),
        Index('idx_conversations_created_at', 'created_at'),
        Index('idx_conversations_source', 'source'),
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, user_id={self.user_id}, title={self.title})>"


class Message(Base):
    """Individual message in a conversation"""
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 'user', 'assistant', 'system'
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True,
        comment="Additional metadata: web_search_results, doc_search_results, used_insight_ids"
    )
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('run_records.id'), nullable=True
    )
    routing_decision: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    # Relationships
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
    run: Mapped[Optional["RunRecord"]] = relationship("RunRecord")
    chat_attachments: Mapped[list["ChatAttachment"]] = relationship(
        "ChatAttachment", back_populates="message", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_messages_conversation_id', 'conversation_id'),
        Index('idx_messages_created_at', 'created_at'),
        Index('idx_messages_run_id', 'run_id'),
    )

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, conversation_id={self.conversation_id}, role={self.role})>"


class ConversationShare(Base):
    """Conversation sharing with role-based access"""
    __tablename__ = "conversation_shares"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), default='viewer', nullable=False)
    shared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    shared_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="shares")

    __table_args__ = (
        Index('idx_conversation_shares_conversation_id', 'conversation_id'),
        Index('idx_conversation_shares_user_id', 'user_id'),
        Index('uq_conversation_shares_conv_user', 'conversation_id', 'user_id', unique=True),
    )


class ChatAttachment(Base):
    """File attachment in chat messages"""
    __tablename__ = "chat_attachments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey('messages.id', ondelete='CASCADE'), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_url: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    added_to_library: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    library_document_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    parsed_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    message: Mapped[Optional["Message"]] = relationship("Message", back_populates="chat_attachments")

    __table_args__ = (
        Index('idx_chat_attachments_message_id', 'message_id'),
    )


class ChatSettings(Base):
    """User chat preferences"""
    __tablename__ = "chat_settings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    enabled_tools: Mapped[list] = mapped_column(ARRAY(String), default=list)
    enabled_agents: Mapped[list] = mapped_column(ARRAY(UUID(as_uuid=True)), default=list)
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    def __repr__(self) -> str:
        return f"<ChatSettings(id={self.id}, user_id={self.user_id})>"


class AgentTask(Base):
    """
    Event-driven agent task with pre-authorized execution.
    
    Tasks can be triggered by:
    - Cron schedules (hourly, daily, custom cron expressions)
    - Webhooks (incoming events from external services)
    - One-time datetime triggers
    
    Each task has a delegation token for autonomous execution on behalf of the user.
    """
    __tablename__ = "agent_tasks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    
    # Target agent or workflow (no FK - agents/workflows may be built-in from code or from DB)
    # Either agent_id or workflow_id should be set, not both
    agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=True,
        comment="Agent ID (may be built-in from code or from agent_definitions table)"
    )
    workflow_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), index=True, nullable=True,
        comment="Workflow ID (may be built-in from code or from workflow_definitions table)"
    )
    
    # Task prompt/input
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    input_config: Mapped[dict] = mapped_column(JSON, default=dict, comment="Additional input parameters")
    
    # Library trigger fields
    library_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, index=True,
        comment="Library ID for library-triggered tasks"
    )
    schema_document_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True,
        comment="Data document ID containing the extraction schema"
    )
    
    # Trigger configuration
    trigger_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="Trigger type: cron, webhook, one_time, library"
    )
    trigger_config: Mapped[dict] = mapped_column(
        JSON, default=dict,
        comment="Trigger-specific config: {cron: '0 * * * *'} or {webhook_secret: '...'}"
    )
    
    # Pre-authorized delegation token for autonomous execution
    delegation_token: Mapped[Optional[str]] = mapped_column(Text, comment="Encrypted delegation token")
    delegation_scopes: Mapped[list] = mapped_column(JSON, default=list, comment="Scopes granted to the task")
    delegation_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    # Notification configuration
    notification_config: Mapped[dict] = mapped_column(
        JSON, default=dict,
        comment="Notification settings: {channel: 'email', recipient: '...', include_summary: true}"
    )
    
    # Task insights/memory configuration
    insights_config: Mapped[dict] = mapped_column(
        JSON, default=dict,
        comment="Insights settings: {enabled: true, max_insights: 50, purge_after_days: 30}"
    )
    
    # Output saving configuration
    output_saving_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True,
        comment="Output saving settings: {enabled: false, tags: ['tag1'], library_type: 'TASKS'}"
    )
    
    # Execution state
    status: Mapped[str] = mapped_column(
        String(50), default="active", index=True,
        comment="Status: active, paused, completed, failed, expired"
    )
    scheduler_job_id: Mapped[Optional[str]] = mapped_column(
        String(255), comment="APScheduler job ID for cron tasks"
    )
    webhook_secret: Mapped[Optional[str]] = mapped_column(
        String(255), comment="Secret for webhook validation"
    )
    
    # Execution tracking
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_records.id", ondelete="SET NULL")
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    
    # Relationships
    # Note: No relationship to AgentDefinition since agents may be built-in from code
    last_run: Mapped[Optional["RunRecord"]] = relationship("RunRecord", lazy="selectin")
    executions: Mapped[list["TaskExecution"]] = relationship(
        "TaskExecution", back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_agent_tasks_user_id', 'user_id'),
        Index('idx_agent_tasks_agent_id', 'agent_id'),
        Index('idx_agent_tasks_status', 'status'),
        Index('idx_agent_tasks_trigger_type', 'trigger_type'),
        Index('idx_agent_tasks_next_run_at', 'next_run_at'),
    )

    def __repr__(self) -> str:
        return f"<AgentTask(id={self.id}, name={self.name}, user_id={self.user_id}, status={self.status})>"


class TaskExecution(Base):
    """Track individual task execution runs"""
    __tablename__ = "task_executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run_records.id", ondelete="SET NULL"), index=True
    )
    
    # Execution details
    trigger_source: Mapped[str] = mapped_column(String(50), comment="cron, webhook, manual")
    status: Mapped[str] = mapped_column(String(50), default="pending", index=True)
    # Status: pending, running, completed, failed, timeout
    
    # Input/Output
    input_data: Mapped[dict] = mapped_column(JSON, default=dict)
    output_data: Mapped[Optional[dict]] = mapped_column(JSON)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, comment="Summary for notifications")
    
    # Notification tracking
    notification_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    notification_error: Mapped[Optional[str]] = mapped_column(Text)
    
    # Timing
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float)
    
    # Error tracking
    error: Mapped[Optional[str]] = mapped_column(Text)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    
    # Relationships
    task: Mapped["AgentTask"] = relationship("AgentTask", back_populates="executions")
    run: Mapped[Optional["RunRecord"]] = relationship("RunRecord", lazy="selectin")
    notifications: Mapped[list["TaskNotification"]] = relationship(
        "TaskNotification", back_populates="execution", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index('idx_task_executions_task_id', 'task_id'),
        Index('idx_task_executions_status', 'status'),
        Index('idx_task_executions_created_at', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<TaskExecution(id={self.id}, task_id={self.task_id}, status={self.status})>"


class TaskNotification(Base):
    """
    Track notification delivery for task executions.
    
    Each notification has:
    - Delivery channel (email, slack, teams, webhook)
    - Delivery status (pending, sent, failed, delivered, read)
    - Error tracking if delivery fails
    """
    __tablename__ = "task_notifications"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_tasks.id", ondelete="CASCADE"), index=True
    )
    execution_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("task_executions.id", ondelete="CASCADE"), index=True
    )
    
    # Channel and recipient
    channel: Mapped[str] = mapped_column(String(50), nullable=False, comment="email, slack, teams, webhook")
    recipient: Mapped[str] = mapped_column(String(500), nullable=False, comment="Email address or webhook URL")
    
    # Content
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Delivery tracking
    status: Mapped[str] = mapped_column(
        String(50), default="pending", index=True,
        comment="pending, sent, failed, delivered, read, bounced"
    )
    message_id: Mapped[Optional[str]] = mapped_column(String(500), comment="External message ID from provider")
    
    # Timing
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    # Error tracking
    error: Mapped[Optional[str]] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    
    # Relationships
    task: Mapped["AgentTask"] = relationship("AgentTask", lazy="selectin")
    execution: Mapped["TaskExecution"] = relationship("TaskExecution", lazy="selectin")

    __table_args__ = (
        Index('idx_task_notifications_task_id', 'task_id'),
        Index('idx_task_notifications_execution_id', 'execution_id'),
        Index('idx_task_notifications_status', 'status'),
        Index('idx_task_notifications_channel', 'channel'),
        Index('idx_task_notifications_created_at', 'created_at'),
    )

    def __repr__(self) -> str:
        return f"<TaskNotification(id={self.id}, task_id={self.task_id}, channel={self.channel}, status={self.status})>"
