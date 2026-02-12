"""
Agent Service Database Schema Definition.

This module defines the complete database schema for the agent service.
The schema is applied idempotently on every service startup.

All tables and indexes are defined here. No separate migration files needed.

Usage:
    from schema import get_agent_schema
    
    schema = get_agent_schema()
    async with pool.acquire() as conn:
        await schema.apply(conn)
"""

import sys
from pathlib import Path

# Add shared library to path (when deployed: /srv/shared)
_shared_paths = [
    Path(__file__).parent.parent.parent / "shared",  # Local dev: srv/shared
    Path("/srv/shared"),  # Deployed
]
for _path in _shared_paths:
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from busibox_common import SchemaManager
except ImportError:
    # Fallback: define minimal SchemaManager inline if shared lib not available
    class SchemaManager:
        def __init__(self):
            self._sql_statements = []
        
        def add_extension(self, name: str) -> "SchemaManager":
            self._sql_statements.append(f'CREATE EXTENSION IF NOT EXISTS "{name}";')
            return self
        
        def add_table(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        def add_index(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        def add_migration(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        async def apply(self, conn) -> None:
            for sql in self._sql_statements:
                try:
                    await conn.execute(sql)
                except Exception as e:
                    error_str = str(e).lower()
                    if "already exists" in error_str or "does not exist" in error_str:
                        pass
                    else:
                        raise


def get_agent_schema() -> SchemaManager:
    """
    Build and return the complete agent service schema definition.
    
    Returns:
        SchemaManager configured with all agent tables and indexes.
    """
    schema = SchemaManager()
    
    # ==========================================================================
    # Extensions
    # ==========================================================================
    schema.add_extension("pgcrypto")
    
    # ==========================================================================
    # Core Definition Tables
    # ==========================================================================
    
    # Agent Definitions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS agent_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            display_name VARCHAR(255),
            description TEXT,
            model VARCHAR(255) NOT NULL,
            instructions TEXT NOT NULL,
            tools JSON NOT NULL,
            workflows JSON,
            scopes JSON NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            is_builtin BOOLEAN NOT NULL DEFAULT false,
            allow_frontier_fallback BOOLEAN NOT NULL DEFAULT false,
            created_by VARCHAR(255),
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Tool Definitions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS tool_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            schema JSON NOT NULL,
            entrypoint VARCHAR(255) NOT NULL,
            scopes JSON NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            is_builtin BOOLEAN NOT NULL DEFAULT false,
            created_by VARCHAR(255),
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Workflow Definitions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            steps JSON NOT NULL,
            trigger JSON NOT NULL DEFAULT '{}',
            guardrails JSON,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_by VARCHAR(255),
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Eval Definitions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS eval_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            config JSON NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_by VARCHAR(255),
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # RAG Tables
    # ==========================================================================
    
    # RAG Databases
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS rag_databases (
            id UUID PRIMARY KEY,
            name VARCHAR(120) NOT NULL,
            description TEXT,
            config JSON NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # RAG Documents
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS rag_documents (
            id UUID PRIMARY KEY,
            rag_database_id UUID NOT NULL REFERENCES rag_databases(id) ON DELETE CASCADE,
            path VARCHAR(255) NOT NULL,
            metadata JSON NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # Run Records and Execution Tables
    # ==========================================================================
    
    # Run Records
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS run_records (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL,
            workflow_id UUID,
            status VARCHAR(50) NOT NULL,
            input JSON NOT NULL,
            output JSON,
            events JSON NOT NULL DEFAULT '[]',
            definition_snapshot JSONB,
            parent_run_id UUID REFERENCES run_records(id) ON DELETE SET NULL,
            resume_from_step VARCHAR(255),
            workflow_state JSONB,
            created_by VARCHAR(255),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Token Grants
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS token_grants (
            id UUID PRIMARY KEY,
            subject VARCHAR(255) NOT NULL,
            scopes JSON NOT NULL,
            token TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Dispatcher Decision Log
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS dispatcher_decision_log (
            id UUID PRIMARY KEY,
            query_text VARCHAR(1000) NOT NULL,
            selected_tools TEXT[] NOT NULL,
            selected_agents TEXT[] NOT NULL,
            confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
            reasoning TEXT NOT NULL,
            alternatives TEXT[] NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            request_id VARCHAR(255) NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # Workflow Execution Tables
    # ==========================================================================
    
    # Workflow Executions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS workflow_executions (
            id UUID PRIMARY KEY,
            workflow_id UUID NOT NULL REFERENCES workflow_definitions(id) ON DELETE CASCADE,
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            trigger_source VARCHAR(255) NOT NULL,
            input_data JSON NOT NULL DEFAULT '{}',
            current_step_id VARCHAR(255),
            step_outputs JSON NOT NULL DEFAULT '{}',
            usage_requests INTEGER NOT NULL DEFAULT 0,
            usage_input_tokens INTEGER NOT NULL DEFAULT 0,
            usage_output_tokens INTEGER NOT NULL DEFAULT 0,
            usage_tool_calls INTEGER NOT NULL DEFAULT 0,
            estimated_cost_dollars REAL NOT NULL DEFAULT 0.0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_seconds REAL,
            error TEXT,
            failed_step_id VARCHAR(255),
            awaiting_approval_data JSON,
            created_by VARCHAR(255),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Step Executions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS step_executions (
            id UUID PRIMARY KEY,
            execution_id UUID NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
            step_id VARCHAR(255) NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            input_data JSON,
            output_data JSON,
            usage_requests INTEGER NOT NULL DEFAULT 0,
            usage_input_tokens INTEGER NOT NULL DEFAULT 0,
            usage_output_tokens INTEGER NOT NULL DEFAULT 0,
            usage_tool_calls INTEGER NOT NULL DEFAULT 0,
            estimated_cost_dollars REAL NOT NULL DEFAULT 0.0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_seconds REAL,
            error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # Conversation Tables
    # ==========================================================================
    
    # Conversations
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS conversations (
            id UUID PRIMARY KEY,
            title VARCHAR(255) NOT NULL,
            user_id VARCHAR(255) NOT NULL,
            source VARCHAR(50),
            model VARCHAR(255),
            is_private BOOLEAN NOT NULL DEFAULT false,
            agent_id VARCHAR(255),
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP
        )
    """)
    
    # Messages
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role VARCHAR(50) NOT NULL,
            content TEXT NOT NULL,
            attachments JSON,
            metadata JSON,
            run_id UUID REFERENCES run_records(id),
            routing_decision JSON,
            tool_calls JSON,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Conversation Shares
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS conversation_shares (
            id UUID PRIMARY KEY,
            conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_id VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'viewer',
            shared_by VARCHAR(255) NOT NULL,
            shared_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Chat Attachments
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS chat_attachments (
            id UUID PRIMARY KEY,
            message_id UUID REFERENCES messages(id) ON DELETE CASCADE,
            filename VARCHAR(500) NOT NULL,
            file_url TEXT NOT NULL,
            mime_type VARCHAR(255),
            size_bytes BIGINT,
            added_to_library BOOLEAN NOT NULL DEFAULT false,
            library_document_id VARCHAR(255),
            parsed_content TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)

    # Chat Settings
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            id UUID PRIMARY KEY,
            user_id VARCHAR(255) NOT NULL UNIQUE,
            enabled_tools TEXT[],
            enabled_agents UUID[],
            model VARCHAR(255),
            temperature REAL NOT NULL DEFAULT 0.7,
            max_tokens INTEGER NOT NULL DEFAULT 2000,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP
        )
    """)
    
    # ==========================================================================
    # Tool Configuration Tables
    # ==========================================================================
    
    # Tool Configs
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS tool_configs (
            id UUID PRIMARY KEY,
            tool_id UUID NOT NULL,
            tool_name VARCHAR(120) NOT NULL,
            scope VARCHAR(20) NOT NULL DEFAULT 'user',
            user_id VARCHAR(255),
            agent_id UUID,
            config JSON NOT NULL DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP
        )
    """)
    
    # ==========================================================================
    # Agent Tasks Tables
    # ==========================================================================
    
    # Agent Tasks
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS agent_tasks (
            id UUID PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            user_id VARCHAR(255) NOT NULL,
            agent_id UUID,
            workflow_id UUID,
            prompt TEXT NOT NULL,
            input_config JSON NOT NULL DEFAULT '{}',
            trigger_type VARCHAR(50) NOT NULL,
            trigger_config JSON NOT NULL DEFAULT '{}',
            delegation_token TEXT,
            delegation_scopes JSON NOT NULL DEFAULT '[]',
            delegation_expires_at TIMESTAMP,
            notification_config JSON NOT NULL DEFAULT '{}',
            insights_config JSON NOT NULL DEFAULT '{}',
            output_saving_config JSON,
            status VARCHAR(50) NOT NULL DEFAULT 'active',
            scheduler_job_id VARCHAR(255),
            webhook_secret VARCHAR(255),
            last_run_at TIMESTAMP,
            last_run_id UUID REFERENCES run_records(id) ON DELETE SET NULL,
            next_run_at TIMESTAMP,
            run_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Task Executions
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS task_executions (
            id UUID PRIMARY KEY,
            task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
            run_id UUID REFERENCES run_records(id) ON DELETE SET NULL,
            trigger_source VARCHAR(50) NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            input_data JSON NOT NULL DEFAULT '{}',
            output_data JSON,
            output_summary TEXT,
            notification_sent BOOLEAN NOT NULL DEFAULT false,
            notification_error TEXT,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            duration_seconds REAL,
            error TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Task Notifications
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS task_notifications (
            id UUID PRIMARY KEY,
            task_id UUID NOT NULL REFERENCES agent_tasks(id) ON DELETE CASCADE,
            execution_id UUID NOT NULL REFERENCES task_executions(id) ON DELETE CASCADE,
            channel VARCHAR(50) NOT NULL,
            recipient VARCHAR(500) NOT NULL,
            subject VARCHAR(500) NOT NULL,
            body TEXT NOT NULL,
            status VARCHAR(50) NOT NULL DEFAULT 'pending',
            message_id VARCHAR(500),
            sent_at TIMESTAMP,
            delivered_at TIMESTAMP,
            read_at TIMESTAMP,
            error TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_retry_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # Indexes
    # ==========================================================================
    
    # agent_definitions indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_definitions_name ON agent_definitions(name)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_definitions_builtin_created ON agent_definitions(is_builtin, created_by) WHERE is_active = true")
    
    # tool_definitions indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_tool_definitions_name ON tool_definitions(name)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_tool_definitions_builtin_created ON tool_definitions(is_builtin, created_by) WHERE is_active = true")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_tool_definitions_name_active ON tool_definitions(name) WHERE is_active = true")
    
    # workflow_definitions indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_workflow_definitions_name ON workflow_definitions(name)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_workflow_definitions_created_by ON workflow_definitions(created_by) WHERE is_active = true")
    
    # eval_definitions indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_eval_definitions_name ON eval_definitions(name)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_eval_definitions_created_by ON eval_definitions(created_by) WHERE is_active = true")
    
    # rag_databases indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_rag_databases_name ON rag_databases(name)")
    
    # rag_documents indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_rag_documents_rag_database_id ON rag_documents(rag_database_id)")
    
    # run_records indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_run_records_parent ON run_records(parent_run_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_run_records_snapshot ON run_records USING gin(definition_snapshot)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_run_records_workflow_state ON run_records USING gin(workflow_state)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_run_records_agent ON run_records(agent_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_run_records_created ON run_records(created_at)")
    
    # token_grants indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_token_grants_subject ON token_grants(subject)")
    
    # dispatcher_decision_log indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_dispatcher_log_user_timestamp ON dispatcher_decision_log(user_id, timestamp DESC)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_dispatcher_log_confidence ON dispatcher_decision_log(confidence)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_dispatcher_log_timestamp ON dispatcher_decision_log(timestamp DESC)")
    
    # workflow_executions indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_workflow_executions_workflow_id ON workflow_executions(workflow_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_workflow_executions_status ON workflow_executions(status)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_workflow_executions_created_at ON workflow_executions(created_at)")
    
    # step_executions indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_step_executions_execution_id ON step_executions(execution_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_step_executions_step_id ON step_executions(step_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_step_executions_status ON step_executions(status)")
    
    # conversations indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations(created_at)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversations_source ON conversations(source)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversations_agent_id ON conversations(agent_id)")
    
    # messages indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_messages_run_id ON messages(run_id)")

    # conversation_shares indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversation_shares_conv ON conversation_shares(conversation_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_conversation_shares_user ON conversation_shares(user_id)")
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_shares_conv_user ON conversation_shares(conversation_id, user_id)")

    # chat_attachments indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_chat_attachments_message ON chat_attachments(message_id)")

    # chat_settings indexes
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_settings_user_id ON chat_settings(user_id)")
    
    # tool_configs indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_tool_configs_tool_id ON tool_configs(tool_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_tool_configs_tool_name ON tool_configs(tool_name)")
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_tool_configs_user_id ON tool_configs(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_tool_configs_agent_id ON tool_configs(agent_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_tool_configs_scope ON tool_configs(scope)")
    schema.add_index("CREATE UNIQUE INDEX IF NOT EXISTS ix_tool_config_unique_scope ON tool_configs(tool_id, scope, user_id, agent_id)")
    
    # agent_tasks indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_tasks_user_id ON agent_tasks(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_tasks_agent_id ON agent_tasks(agent_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS ix_agent_tasks_workflow_id ON agent_tasks(workflow_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_tasks_status ON agent_tasks(status)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_tasks_trigger_type ON agent_tasks(trigger_type)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_agent_tasks_next_run_at ON agent_tasks(next_run_at)")
    
    # task_executions indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_executions_task_id ON task_executions(task_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_executions_status ON task_executions(status)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_executions_created_at ON task_executions(created_at)")
    
    # task_notifications indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_notifications_task_id ON task_notifications(task_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_notifications_execution_id ON task_notifications(execution_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_notifications_status ON task_notifications(status)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_notifications_channel ON task_notifications(channel)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_task_notifications_created_at ON task_notifications(created_at)")
    
    # ==========================================================================
    # Grants
    # ==========================================================================
    
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON agent_definitions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON tool_definitions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_definitions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON eval_definitions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON rag_databases TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON rag_documents TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON run_records TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON token_grants TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON dispatcher_decision_log TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_executions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON step_executions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON conversations TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON conversation_shares TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON chat_attachments TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON chat_settings TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON tool_configs TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON agent_tasks TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON task_executions TO busibox_user")
    schema.add_migration("GRANT SELECT, INSERT, UPDATE, DELETE ON task_notifications TO busibox_user")
    
    return schema
