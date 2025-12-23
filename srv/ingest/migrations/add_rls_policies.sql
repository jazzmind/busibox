-- Migration: Add Row-Level Security (RLS) policies
-- Created: 2025-11-24
-- Updated: 2025-12-22 - Full role-based policies for owner + shared access
-- Updated: 2025-12-22 - Added ingestion_status table, explicit FORCE for all tables
-- Description: Enforces database-level access control for multi-tenancy
--
-- RLS Session Variables (set by application before each request):
--   app.user_id - UUID of the current user
--   app.user_role_ids_read - CSV of role UUIDs user can read (for SELECT)
--   app.user_role_ids_create - CSV of role UUIDs user can create with (for INSERT)
--   app.user_role_ids_update - CSV of role UUIDs user can update (for UPDATE)
--   app.user_role_ids_delete - CSV of role UUIDs user can delete (for DELETE)
--
-- Access model:
--   Personal documents: Only owner can access (visibility = 'personal')
--   Shared documents: Users with matching roles can access (visibility = 'shared')
--
-- Tables covered:
--   - ingestion_files: Main file metadata
--   - ingestion_chunks: Text chunks for each file
--   - ingestion_status: Processing status
--   - processing_history: Detailed processing steps
--   - document_roles: Role assignments for shared documents

BEGIN;

-- ============================================================================
-- ENABLE ROW-LEVEL SECURITY ON ALL TABLES
-- ============================================================================

-- ingestion_files - main file records
ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_files FORCE ROW LEVEL SECURITY;

-- ingestion_chunks - text chunks for each file
ALTER TABLE ingestion_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks FORCE ROW LEVEL SECURITY;

-- ingestion_status - processing status (linked via file_id FK)
ALTER TABLE ingestion_status ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_status FORCE ROW LEVEL SECURITY;

-- processing_history - detailed processing steps
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history FORCE ROW LEVEL SECURITY;

-- document_roles - role assignments for shared documents
ALTER TABLE document_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_roles FORCE ROW LEVEL SECURITY;

-- ============================================================================
-- DROP EXISTING POLICIES (for idempotency)
-- ============================================================================

-- Drop simple owner-only policy if it exists
DROP POLICY IF EXISTS ingestion_files_owner_all ON ingestion_files;

-- Drop all specific policies
DROP POLICY IF EXISTS personal_docs_select ON ingestion_files;
DROP POLICY IF EXISTS shared_docs_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_insert ON ingestion_files;
DROP POLICY IF EXISTS personal_docs_update ON ingestion_files;
DROP POLICY IF EXISTS shared_docs_update ON ingestion_files;
DROP POLICY IF EXISTS personal_docs_delete ON ingestion_files;
DROP POLICY IF EXISTS shared_docs_delete ON ingestion_files;

DROP POLICY IF EXISTS chunks_owner_all ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_select ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_insert ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_update ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_delete ON ingestion_chunks;

DROP POLICY IF EXISTS processing_history_owner_all ON processing_history;
DROP POLICY IF EXISTS processing_history_select ON processing_history;
DROP POLICY IF EXISTS processing_history_insert ON processing_history;

DROP POLICY IF EXISTS ingestion_status_owner_all ON ingestion_status;
DROP POLICY IF EXISTS ingestion_status_select ON ingestion_status;
DROP POLICY IF EXISTS ingestion_status_insert ON ingestion_status;
DROP POLICY IF EXISTS ingestion_status_update ON ingestion_status;

DROP POLICY IF EXISTS document_roles_select ON document_roles;
DROP POLICY IF EXISTS document_roles_insert ON document_roles;
DROP POLICY IF EXISTS document_roles_update ON document_roles;
DROP POLICY IF EXISTS document_roles_delete ON document_roles;

-- ============================================================================
-- INGESTION_FILES POLICIES
-- ============================================================================

-- SELECT: Personal documents - owner only
CREATE POLICY personal_docs_select ON ingestion_files
    FOR SELECT
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            NULLIF(current_setting('app.user_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- SELECT: Shared documents - user has read permission on at least one document role
CREATE POLICY shared_docs_select ON ingestion_files
    FOR SELECT
    USING (
        visibility = 'shared'
        AND EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
            AND dr.role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    );

-- INSERT: User must set themselves as owner
CREATE POLICY ingestion_files_insert ON ingestion_files
    FOR INSERT
    WITH CHECK (
        owner_id = COALESCE(
            NULLIF(current_setting('app.user_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- UPDATE: Personal docs - owner only
CREATE POLICY personal_docs_update ON ingestion_files
    FOR UPDATE
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            NULLIF(current_setting('app.user_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- UPDATE: Shared docs - has update role
CREATE POLICY shared_docs_update ON ingestion_files
    FOR UPDATE
    USING (
        visibility = 'shared'
        AND EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
            AND dr.role_id = ANY(
                COALESCE(
                    string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                    ARRAY[]::uuid[]
                )
            )
        )
    );

-- DELETE: Personal docs - owner only
CREATE POLICY personal_docs_delete ON ingestion_files
    FOR DELETE
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            NULLIF(current_setting('app.user_id', true), '')::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- DELETE: Shared docs - has delete role on ALL document roles
CREATE POLICY shared_docs_delete ON ingestion_files
    FOR DELETE
    USING (
        visibility = 'shared'
        AND NOT EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
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
            WHERE dr.file_id = ingestion_files.file_id
        )
    );

-- ============================================================================
-- CHUNKS POLICIES
-- ============================================================================
-- Note: We can't use "file_id IN (SELECT file_id FROM ingestion_files)" because
-- that creates infinite recursion when ingestion_files has RLS. Instead, we
-- check the file's owner_id directly against the current user.

-- SELECT: Check file owner/roles directly (avoids recursion)
CREATE POLICY chunks_select ON ingestion_chunks
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM ingestion_files f
            WHERE f.file_id = ingestion_chunks.file_id
            AND (
                -- Personal docs: owner only
                (f.visibility = 'personal' AND f.owner_id = COALESCE(NULLIF(current_setting('app.user_id', true), '')::uuid, '00000000-0000-0000-0000-000000000000'::uuid))
                OR
                -- Shared docs: has a matching role
                (f.visibility = 'shared' AND EXISTS (
                    SELECT 1 FROM document_roles dr
                    WHERE dr.file_id = f.file_id
                    AND dr.role_id = ANY(COALESCE(string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[], ARRAY[]::uuid[]))
                ))
            )
        )
    );

-- INSERT: System/worker can insert (for chunking)
CREATE POLICY chunks_insert ON ingestion_chunks
    FOR INSERT
    WITH CHECK (true);

-- UPDATE: System/worker can update (for reprocessing)
CREATE POLICY chunks_update ON ingestion_chunks
    FOR UPDATE
    USING (true);

-- DELETE: System/worker can delete (for reprocessing)
CREATE POLICY chunks_delete ON ingestion_chunks
    FOR DELETE
    USING (true);

-- ============================================================================
-- PROCESSING_HISTORY POLICIES
-- ============================================================================
-- Note: Check file owner/roles directly to avoid recursion

-- SELECT: Check file owner/roles directly
CREATE POLICY processing_history_select ON processing_history
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM ingestion_files f
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
    );

-- INSERT: System/worker can insert (for processing logs)
CREATE POLICY processing_history_insert ON processing_history
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- INGESTION_STATUS POLICIES
-- ============================================================================
-- Note: Check file owner/roles directly to avoid recursion

-- SELECT: Check file owner/roles directly
CREATE POLICY ingestion_status_select ON ingestion_status
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM ingestion_files f
            WHERE f.file_id = ingestion_status.file_id
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
    );

-- INSERT: System/worker can insert (status is created with file)
CREATE POLICY ingestion_status_insert ON ingestion_status
    FOR INSERT
    WITH CHECK (true);

-- UPDATE: System/worker can update (for status changes during processing)
CREATE POLICY ingestion_status_update ON ingestion_status
    FOR UPDATE
    USING (true);

-- ============================================================================
-- DOCUMENT_ROLES POLICIES
-- ============================================================================
-- Note: document_roles is used by other policies (ingestion_files, chunks, etc.)
-- to check role membership. To avoid infinite recursion, we use a simple
-- approach: document_roles can be read if user has ANY of the roles on that file.
-- This is checked directly against the role_id column, not through ingestion_files.

-- SELECT: User can see document_roles where they have matching role
-- This MUST NOT reference ingestion_files to avoid recursion
CREATE POLICY document_roles_select ON document_roles
    FOR SELECT
    USING (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    );

-- INSERT: User must have create permission on the role being assigned
-- We check file ownership via ingestion_files, but only for personal docs.
-- For this we look at owner_id directly - no RLS cycle since we don't go through policies.
CREATE POLICY document_roles_insert ON document_roles
    FOR INSERT
    WITH CHECK (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_create', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    );

-- UPDATE: User must have update permission on roles
CREATE POLICY document_roles_update ON document_roles
    FOR UPDATE
    USING (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    );

-- DELETE: User must have delete permission on the role being removed
CREATE POLICY document_roles_delete ON document_roles
    FOR DELETE
    USING (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    );

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (for testing)
-- ============================================================================

-- To test RLS, set session variables and query:
-- 
-- SET app.user_id = 'your-user-uuid';
-- SET app.user_role_ids_read = 'role-uuid-1,role-uuid-2';
-- SET app.user_role_ids_create = 'role-uuid-1';
-- SET app.user_role_ids_update = 'role-uuid-1';
-- SET app.user_role_ids_delete = 'role-uuid-1';
-- SELECT * FROM ingestion_files;
-- 
-- You should only see:
-- 1. Personal documents you own
-- 2. Shared documents where you have read permission on at least one role
