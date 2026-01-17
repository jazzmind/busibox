"""
Ingest Service Database Schema Definition.

This module defines the database schema for the ingest service using the
shared SchemaManager pattern. The schema is applied idempotently on every
service startup.

Usage:
    from schema import get_ingest_schema
    
    schema = get_ingest_schema()
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
                await conn.execute(sql)


def get_ingest_schema() -> SchemaManager:
    """
    Build and return the ingest service schema definition.
    
    Returns:
        SchemaManager configured with all ingest tables and indexes.
    """
    schema = SchemaManager()
    
    # ==========================================================================
    # Extensions
    # ==========================================================================
    schema.add_extension("pgcrypto")
    
    # ==========================================================================
    # Core Tables
    # ==========================================================================
    
    # Main ingestion_files table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS ingestion_files (
            file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            owner_id UUID,
            filename VARCHAR(255) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            size_bytes BIGINT NOT NULL,
            storage_path TEXT NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            document_type VARCHAR(50),
            primary_language VARCHAR(10),
            detected_languages VARCHAR(10)[],
            classification_confidence REAL CHECK (classification_confidence >= 0 AND classification_confidence <= 1),
            chunk_count INTEGER DEFAULT 0,
            vector_count INTEGER DEFAULT 0,
            processing_duration_seconds INTEGER,
            extracted_title VARCHAR(500),
            extracted_author VARCHAR(255),
            extracted_date DATE,
            extracted_keywords TEXT[],
            metadata JSONB DEFAULT '{}',
            permissions JSONB NOT NULL DEFAULT '{"visibility": "private"}',
            visibility VARCHAR(20) DEFAULT 'personal',
            has_markdown BOOLEAN DEFAULT false,
            markdown_path VARCHAR(512),
            images_path VARCHAR(512),
            image_count INTEGER DEFAULT 0,
            processing_strategies JSONB DEFAULT '[]'::jsonb,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Ingestion status table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS ingestion_status (
            file_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
            chunks_processed INTEGER,
            total_chunks INTEGER,
            pages_processed INTEGER,
            total_pages INTEGER,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Ingestion chunks table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS ingestion_chunks (
            chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_offset INTEGER,
            token_count INTEGER,
            page_number INTEGER,
            section_heading VARCHAR(500),
            processing_strategy VARCHAR(50) DEFAULT 'simple',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (file_id, chunk_index)
        )
    """)
    
    # Document roles table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS document_roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            role_id UUID NOT NULL,
            role_name VARCHAR(100) NOT NULL,
            added_at TIMESTAMP DEFAULT NOW(),
            added_by UUID,
            UNIQUE(file_id, role_id)
        )
    """)
    
    # Groups table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            description TEXT,
            created_by UUID NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    # Group memberships table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS group_memberships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id UUID NOT NULL,
            role VARCHAR(50) DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )
    """)
    
    # Processing history table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS processing_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'started',
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP,
            duration_ms INTEGER,
            details JSONB DEFAULT '{}',
            error_message TEXT
        )
    """)
    
    # Processing strategy results table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS processing_strategy_results (
            result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            processing_strategy VARCHAR(50) NOT NULL,
            success BOOLEAN NOT NULL DEFAULT false,
            text_length INTEGER,
            chunk_count INTEGER,
            embedding_count INTEGER,
            visual_embedding_count INTEGER,
            processing_time_seconds NUMERIC(10,3),
            error_message TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(file_id, processing_strategy)
        )
    """)
    
    # ==========================================================================
    # Indexes
    # ==========================================================================
    
    # ingestion_files indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_user_id ON ingestion_files(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_owner ON ingestion_files(owner_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_content_hash ON ingestion_files(content_hash)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_document_type ON ingestion_files(document_type)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_created_at ON ingestion_files(created_at DESC)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_files_visibility ON ingestion_files(visibility)")
    
    # ingestion_status indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_status_stage ON ingestion_status(stage)")
    
    # ingestion_chunks indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_file_id ON ingestion_chunks(file_id)")
    
    # document_roles indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_document_roles_file ON document_roles(file_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_document_roles_role ON document_roles(role_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_document_roles_name ON document_roles(role_name)")
    
    # groups indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups(created_by)")
    
    # group_memberships indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_group_memberships_user ON group_memberships(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_group_memberships_group ON group_memberships(group_id)")
    
    # processing_history indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_processing_history_file_id ON processing_history(file_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_processing_history_stage ON processing_history(stage)")
    
    # processing_strategy_results indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_strategy_results_file ON processing_strategy_results(file_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_strategy_results_strategy ON processing_strategy_results(processing_strategy)")
    
    # ==========================================================================
    # Migrations (Backfill and column additions)
    # ==========================================================================
    
    # Backfill owner_id from user_id
    schema.add_migration("""
        UPDATE ingestion_files SET owner_id = user_id WHERE owner_id IS NULL
    """)
    
    # Add group_id column for group-shared documents
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'ingestion_files' AND column_name = 'group_id'
            ) THEN
                ALTER TABLE ingestion_files ADD COLUMN group_id UUID REFERENCES groups(id) ON DELETE SET NULL;
                CREATE INDEX IF NOT EXISTS idx_ingestion_files_group ON ingestion_files(group_id);
            END IF;
        END $$
    """)
    
    return schema
