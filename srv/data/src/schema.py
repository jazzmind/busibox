"""
Data Service Database Schema Definition.

This module defines the complete database schema for the data service.
The schema is applied idempotently on every service startup.

All tables, indexes, RLS policies, and functions are defined here.
No separate migration files are needed.

Usage:
    from schema import get_data_schema
    
    schema = get_data_schema()
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
        
        def add_rls(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        def add_function(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        async def apply(self, conn) -> None:
            for sql in self._sql_statements:
                try:
                    await conn.execute(sql)
                except Exception as e:
                    error_str = str(e).lower()
                    # Ignore idempotent errors
                    if "already exists" in error_str or "does not exist" in error_str:
                        pass
                    else:
                        raise


def get_data_schema() -> SchemaManager:
    """
    Build and return the complete data service schema definition.
    
    Returns:
        SchemaManager configured with all tables, indexes, RLS policies, and functions.
    """
    schema = SchemaManager()
    
    # ==========================================================================
    # Extensions
    # ==========================================================================
    schema.add_extension("pgcrypto")
    
    # ==========================================================================
    # Core Tables
    # ==========================================================================
    
    # Groups table (must come before data_files for foreign key)
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
    
    # Libraries table (must come before data_files for foreign key)
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS libraries (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            is_personal BOOLEAN DEFAULT false,
            user_id UUID,
            library_type VARCHAR(20),
            created_by UUID NOT NULL,
            deleted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(user_id, library_type)
        )
    """)
    
    # Main data_files table - supports both file documents and structured data
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS data_files (
            file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            owner_id UUID NOT NULL,
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
            visibility VARCHAR(20) DEFAULT 'personal' CHECK (visibility IN ('personal', 'shared', 'group')),
            has_markdown BOOLEAN DEFAULT false,
            markdown_path VARCHAR(512),
            images_path VARCHAR(512),
            image_count INTEGER DEFAULT 0,
            processing_strategies JSONB DEFAULT '[]'::jsonb,
            group_id UUID REFERENCES groups(id) ON DELETE SET NULL,
            library_id UUID REFERENCES libraries(id) ON DELETE SET NULL,
            is_encrypted BOOLEAN DEFAULT false,
            -- Structured data support (doc_type = 'data')
            doc_type VARCHAR(20) DEFAULT 'file' CHECK (doc_type IN ('file', 'data')),
            data_schema JSONB,
            data_content JSONB DEFAULT '[]'::jsonb,
            data_indexes JSONB,
            data_version INTEGER DEFAULT 1,
            data_record_count INTEGER DEFAULT 0,
            data_modified_at TIMESTAMP,
            -- Timestamps
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    # Data status table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS data_status (
            file_id UUID PRIMARY KEY REFERENCES data_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL DEFAULT 'queued' CHECK (stage IN (
                'queued', 'parsing', 'classifying', 'extracting_metadata',
                'chunking', 'cleanup', 'embedding', 'indexing', 'completed', 'failed'
            )),
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
    
    # Data chunks table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS data_chunks (
            chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
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
    
    # Document roles table - role-based access control
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS document_roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
            role_id UUID NOT NULL,
            role_name VARCHAR(100) NOT NULL,
            added_at TIMESTAMP DEFAULT NOW(),
            added_by UUID,
            UNIQUE(file_id, role_id)
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
            file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL,
            step_name VARCHAR(100),
            status VARCHAR(20) NOT NULL DEFAULT 'started' CHECK (status IN ('started', 'completed', 'failed', 'skipped')),
            message TEXT,
            error_message TEXT,
            metadata JSONB DEFAULT '{}',
            duration_ms INTEGER,
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    
    # Processing strategy results table
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS processing_strategy_results (
            result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
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
    # Library Management Tables
    # ==========================================================================
    
    # Library tag cache
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS library_tag_cache (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            library_id UUID UNIQUE REFERENCES libraries(id) ON DELETE CASCADE,
            version INTEGER DEFAULT 1,
            groups JSONB,
            generated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    # ==========================================================================
    # Structured Data Tables
    # ==========================================================================
    
    # Data document cache tracking
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS data_document_cache (
            document_id UUID PRIMARY KEY REFERENCES data_files(file_id) ON DELETE CASCADE,
            redis_key VARCHAR(255) NOT NULL UNIQUE,
            cached_at TIMESTAMP DEFAULT NOW(),
            last_accessed TIMESTAMP DEFAULT NOW(),
            access_count INTEGER DEFAULT 0,
            dirty BOOLEAN DEFAULT FALSE,
            dirty_since TIMESTAMP,
            flush_scheduled_at TIMESTAMP,
            cache_size_bytes INTEGER
        )
    """)
    
    # Data record history - audit log
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS data_record_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
            record_id VARCHAR(255) NOT NULL,
            operation VARCHAR(20) NOT NULL CHECK (operation IN ('insert', 'update', 'delete')),
            old_data JSONB,
            new_data JSONB,
            changed_by UUID,
            changed_at TIMESTAMP DEFAULT NOW(),
            batch_id UUID
        )
    """)
    
    # ==========================================================================
    # Migrations - Add columns that may be missing from older table versions
    # ==========================================================================
    # CREATE TABLE IF NOT EXISTS is a no-op when the table already exists,
    # so any columns added after initial deployment must be handled here.
    
    # owner_id was added to data_files schema but existing tables may lack it
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'owner_id'
            ) THEN
                ALTER TABLE data_files ADD COLUMN owner_id UUID;
                -- Backfill from user_id for existing rows
                UPDATE data_files SET owner_id = user_id WHERE owner_id IS NULL;
                -- Now make it NOT NULL
                ALTER TABLE data_files ALTER COLUMN owner_id SET NOT NULL;
            END IF;
        END $$;
    """)
    
    # visibility column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'visibility'
            ) THEN
                ALTER TABLE data_files ADD COLUMN visibility VARCHAR(20) DEFAULT 'personal' CHECK (visibility IN ('personal', 'shared', 'group'));
            END IF;
        END $$;
    """)
    
    # doc_type column migration (structured data support)
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'doc_type'
            ) THEN
                ALTER TABLE data_files ADD COLUMN doc_type VARCHAR(20) DEFAULT 'file' CHECK (doc_type IN ('file', 'data'));
            END IF;
        END $$;
    """)
    
    # data_schema column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'data_schema'
            ) THEN
                ALTER TABLE data_files ADD COLUMN data_schema JSONB;
                ALTER TABLE data_files ADD COLUMN data_content JSONB DEFAULT '[]'::jsonb;
                ALTER TABLE data_files ADD COLUMN data_indexes JSONB;
                ALTER TABLE data_files ADD COLUMN data_version INTEGER DEFAULT 1;
                ALTER TABLE data_files ADD COLUMN data_record_count INTEGER DEFAULT 0;
                ALTER TABLE data_files ADD COLUMN data_modified_at TIMESTAMP;
            END IF;
        END $$;
    """)
    
    # has_markdown column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'has_markdown'
            ) THEN
                ALTER TABLE data_files ADD COLUMN has_markdown BOOLEAN DEFAULT false;
                ALTER TABLE data_files ADD COLUMN markdown_path VARCHAR(512);
                ALTER TABLE data_files ADD COLUMN images_path VARCHAR(512);
                ALTER TABLE data_files ADD COLUMN image_count INTEGER DEFAULT 0;
            END IF;
        END $$;
    """)
    
    # processing_strategies column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'processing_strategies'
            ) THEN
                ALTER TABLE data_files ADD COLUMN processing_strategies JSONB DEFAULT '[]'::jsonb;
            END IF;
        END $$;
    """)
    
    # is_encrypted column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'is_encrypted'
            ) THEN
                ALTER TABLE data_files ADD COLUMN is_encrypted BOOLEAN DEFAULT false;
            END IF;
        END $$;
    """)
    
    # library_id column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'library_id'
            ) THEN
                ALTER TABLE data_files ADD COLUMN library_id UUID REFERENCES libraries(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)
    
    # group_id column migration
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'data_files' AND column_name = 'group_id'
            ) THEN
                ALTER TABLE data_files ADD COLUMN group_id UUID REFERENCES groups(id) ON DELETE SET NULL;
            END IF;
        END $$;
    """)
    
    # ==========================================================================
    # Indexes
    # ==========================================================================
    
    # data_files indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_user_id ON data_files(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_owner ON data_files(owner_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_content_hash ON data_files(content_hash)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_document_type ON data_files(document_type)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_created_at ON data_files(created_at DESC)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_visibility ON data_files(visibility)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_doc_type ON data_files(doc_type)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_group ON data_files(group_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_library ON data_files(library_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_has_markdown ON data_files(has_markdown)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_files_encrypted ON data_files(is_encrypted)")
    
    # data_status indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_status_stage ON data_status(stage)")
    
    # data_chunks indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_chunks_file_id ON data_chunks(file_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON data_chunks(processing_strategy)")
    
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
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_processing_history_started_at ON processing_history(started_at DESC)")
    
    # processing_strategy_results indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_strategy_results_file ON processing_strategy_results(file_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_strategy_results_strategy ON processing_strategy_results(processing_strategy)")
    
    # libraries indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_libraries_user_id ON libraries(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_libraries_is_personal ON libraries(is_personal)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_libraries_created_by ON libraries(created_by)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_libraries_deleted_at ON libraries(deleted_at)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_libraries_type ON libraries(library_type)")
    
    # library_tag_cache indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_library_tag_cache_library ON library_tag_cache(library_id)")
    
    # data_document_cache indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_cache_dirty ON data_document_cache(dirty) WHERE dirty = TRUE")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_cache_last_accessed ON data_document_cache(last_accessed)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_data_cache_flush ON data_document_cache(flush_scheduled_at) WHERE flush_scheduled_at IS NOT NULL")
    
    # data_record_history indexes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_record_history_document ON data_record_history(document_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_record_history_record ON data_record_history(document_id, record_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_record_history_time ON data_record_history(changed_at DESC)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_record_history_batch ON data_record_history(batch_id) WHERE batch_id IS NOT NULL")
    
    # ==========================================================================
    # Row-Level Security (RLS)
    # ==========================================================================
    
    # Enable RLS on all tables
    schema.add_rls("ALTER TABLE data_files ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_files FORCE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_chunks ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_chunks FORCE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_status ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_status FORCE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE processing_history FORCE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE document_roles ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE document_roles FORCE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_document_cache ENABLE ROW LEVEL SECURITY")
    schema.add_rls("ALTER TABLE data_record_history ENABLE ROW LEVEL SECURITY")
    
    # Drop existing policies for idempotency
    schema.add_rls("DROP POLICY IF EXISTS personal_docs_select ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS shared_docs_select ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS data_files_insert ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS personal_docs_update ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS shared_docs_update ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS personal_docs_delete ON data_files")
    schema.add_rls("DROP POLICY IF EXISTS shared_docs_delete ON data_files")
    
    # DATA_FILES POLICIES
    schema.add_rls("""
        CREATE POLICY personal_docs_select ON data_files FOR SELECT USING (
            visibility = 'personal' 
            AND owner_id = COALESCE(
                NULLIF(current_setting('app.user_id', true), '')::uuid,
                '00000000-0000-0000-0000-000000000000'::uuid
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY shared_docs_select ON data_files FOR SELECT USING (
            visibility = 'shared'
            AND EXISTS (
                SELECT 1 FROM document_roles dr
                WHERE dr.file_id = data_files.file_id
                AND dr.role_id = ANY(
                    COALESCE(
                        string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[],
                        ARRAY[]::uuid[]
                    )
                )
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY data_files_insert ON data_files FOR INSERT WITH CHECK (
            owner_id = COALESCE(
                NULLIF(current_setting('app.user_id', true), '')::uuid,
                '00000000-0000-0000-0000-000000000000'::uuid
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY personal_docs_update ON data_files FOR UPDATE USING (
            visibility = 'personal' 
            AND owner_id = COALESCE(
                NULLIF(current_setting('app.user_id', true), '')::uuid,
                '00000000-0000-0000-0000-000000000000'::uuid
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY shared_docs_update ON data_files FOR UPDATE USING (
            visibility = 'shared'
            AND EXISTS (
                SELECT 1 FROM document_roles dr
                WHERE dr.file_id = data_files.file_id
                AND dr.role_id = ANY(
                    COALESCE(
                        string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                        ARRAY[]::uuid[]
                    )
                )
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY personal_docs_delete ON data_files FOR DELETE USING (
            visibility = 'personal' 
            AND owner_id = COALESCE(
                NULLIF(current_setting('app.user_id', true), '')::uuid,
                '00000000-0000-0000-0000-000000000000'::uuid
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY shared_docs_delete ON data_files FOR DELETE USING (
            visibility = 'shared'
            AND NOT EXISTS (
                SELECT 1 FROM document_roles dr
                WHERE dr.file_id = data_files.file_id
                AND dr.role_id NOT IN (
                    SELECT unnest(
                        COALESCE(
                            string_to_array(current_setting('app.user_role_ids_delete', true), ',')::uuid[],
                            ARRAY[]::uuid[]
                        )
                    )
                )
            )
            AND EXISTS (
                SELECT 1 FROM document_roles dr
                WHERE dr.file_id = data_files.file_id
            )
        )
    """)
    
    # CHUNKS POLICIES
    schema.add_rls("DROP POLICY IF EXISTS chunks_select ON data_chunks")
    schema.add_rls("DROP POLICY IF EXISTS chunks_insert ON data_chunks")
    schema.add_rls("DROP POLICY IF EXISTS chunks_update ON data_chunks")
    schema.add_rls("DROP POLICY IF EXISTS chunks_delete ON data_chunks")
    
    schema.add_rls("""
        CREATE POLICY chunks_select ON data_chunks FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM data_files f
                WHERE f.file_id = data_chunks.file_id
                AND (
                    (f.visibility = 'personal' AND f.owner_id = COALESCE(NULLIF(current_setting('app.user_id', true), '')::uuid, '00000000-0000-0000-0000-000000000000'::uuid))
                    OR
                    (f.visibility = 'shared' AND EXISTS (
                        SELECT 1 FROM document_roles dr
                        WHERE dr.file_id = f.file_id
                        AND dr.role_id = ANY(COALESCE(string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[], ARRAY[]::uuid[]))
                    ))
                )
            )
        )
    """)
    
    schema.add_rls("CREATE POLICY chunks_insert ON data_chunks FOR INSERT WITH CHECK (true)")
    schema.add_rls("CREATE POLICY chunks_update ON data_chunks FOR UPDATE USING (true)")
    schema.add_rls("CREATE POLICY chunks_delete ON data_chunks FOR DELETE USING (true)")
    
    # DATA_STATUS POLICIES
    schema.add_rls("DROP POLICY IF EXISTS data_status_select ON data_status")
    schema.add_rls("DROP POLICY IF EXISTS data_status_insert ON data_status")
    schema.add_rls("DROP POLICY IF EXISTS data_status_update ON data_status")
    
    schema.add_rls("""
        CREATE POLICY data_status_select ON data_status FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM data_files f
                WHERE f.file_id = data_status.file_id
                AND (
                    (f.visibility = 'personal' AND f.owner_id = COALESCE(NULLIF(current_setting('app.user_id', true), '')::uuid, '00000000-0000-0000-0000-000000000000'::uuid))
                    OR
                    (f.visibility = 'shared' AND EXISTS (
                        SELECT 1 FROM document_roles dr
                        WHERE dr.file_id = f.file_id
                        AND dr.role_id = ANY(COALESCE(string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[], ARRAY[]::uuid[]))
                    ))
                )
            )
        )
    """)
    
    schema.add_rls("CREATE POLICY data_status_insert ON data_status FOR INSERT WITH CHECK (true)")
    schema.add_rls("CREATE POLICY data_status_update ON data_status FOR UPDATE USING (true)")
    
    # PROCESSING_HISTORY POLICIES
    schema.add_rls("DROP POLICY IF EXISTS processing_history_select ON processing_history")
    schema.add_rls("DROP POLICY IF EXISTS processing_history_insert ON processing_history")
    
    schema.add_rls("""
        CREATE POLICY processing_history_select ON processing_history FOR SELECT USING (
            EXISTS (
                SELECT 1 FROM data_files f
                WHERE f.file_id = processing_history.file_id
                AND (
                    (f.visibility = 'personal' AND f.owner_id = COALESCE(NULLIF(current_setting('app.user_id', true), '')::uuid, '00000000-0000-0000-0000-000000000000'::uuid))
                    OR
                    (f.visibility = 'shared' AND EXISTS (
                        SELECT 1 FROM document_roles dr
                        WHERE dr.file_id = f.file_id
                        AND dr.role_id = ANY(COALESCE(string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[], ARRAY[]::uuid[]))
                    ))
                )
            )
        )
    """)
    
    schema.add_rls("CREATE POLICY processing_history_insert ON processing_history FOR INSERT WITH CHECK (true)")
    
    # DOCUMENT_ROLES POLICIES
    schema.add_rls("DROP POLICY IF EXISTS document_roles_select ON document_roles")
    schema.add_rls("DROP POLICY IF EXISTS document_roles_insert ON document_roles")
    schema.add_rls("DROP POLICY IF EXISTS document_roles_update ON document_roles")
    schema.add_rls("DROP POLICY IF EXISTS document_roles_delete ON document_roles")
    
    schema.add_rls("""
        CREATE POLICY document_roles_select ON document_roles FOR SELECT USING (
            role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY document_roles_insert ON document_roles FOR INSERT WITH CHECK (
            role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_create', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY document_roles_update ON document_roles FOR UPDATE USING (
            role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    """)
    
    schema.add_rls("""
        CREATE POLICY document_roles_delete ON document_roles FOR DELETE USING (
            role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    """)
    
    # DATA_DOCUMENT_CACHE POLICIES
    schema.add_rls("DROP POLICY IF EXISTS data_cache_select ON data_document_cache")
    schema.add_rls("DROP POLICY IF EXISTS data_cache_modify ON data_document_cache")
    
    schema.add_rls("""
        CREATE POLICY data_cache_select ON data_document_cache FOR SELECT USING (
            document_id IN (SELECT file_id FROM data_files)
        )
    """)
    
    schema.add_rls("CREATE POLICY data_cache_modify ON data_document_cache FOR ALL USING (true) WITH CHECK (true)")
    
    # DATA_RECORD_HISTORY POLICIES
    schema.add_rls("DROP POLICY IF EXISTS record_history_select ON data_record_history")
    schema.add_rls("DROP POLICY IF EXISTS record_history_insert ON data_record_history")
    
    schema.add_rls("""
        CREATE POLICY record_history_select ON data_record_history FOR SELECT USING (
            document_id IN (SELECT file_id FROM data_files)
        )
    """)
    
    schema.add_rls("CREATE POLICY record_history_insert ON data_record_history FOR INSERT WITH CHECK (true)")
    
    # ==========================================================================
    # Helper Functions
    # ==========================================================================
    
    # Function to check shared document has at least one role
    schema.add_function("""
        CREATE OR REPLACE FUNCTION check_document_has_roles()
        RETURNS TRIGGER AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM document_roles 
                    WHERE file_id = OLD.file_id 
                    AND id != OLD.id
                ) THEN
                    IF EXISTS (
                        SELECT 1 FROM data_files 
                        WHERE file_id = OLD.file_id 
                        AND visibility = 'shared'
                    ) THEN
                        RAISE EXCEPTION 'Cannot remove last role from shared document.';
                    END IF;
                END IF;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
    """)
    
    schema.add_function("""
        DROP TRIGGER IF EXISTS ensure_document_has_roles ON document_roles
    """)
    
    schema.add_function("""
        CREATE TRIGGER ensure_document_has_roles
            BEFORE DELETE ON document_roles
            FOR EACH ROW
            EXECUTE FUNCTION check_document_has_roles()
    """)
    
    # ==========================================================================
    # Grants - Give busibox_user access to all tables
    # ==========================================================================
    
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON data_files TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON data_chunks TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE ON data_status TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT ON processing_history TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON document_roles TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON groups TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON group_memberships TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON processing_strategy_results TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON libraries TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON library_tag_cache TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT, UPDATE, DELETE ON data_document_cache TO busibox_user")
    schema.add_function("GRANT SELECT, INSERT ON data_record_history TO busibox_user")
    
    return schema
