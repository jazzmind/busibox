-- Migration: Add Row-Level Security (RLS) policies
-- Created: 2025-11-24
-- Updated: 2025-12-22 - Full role-based policies for owner + shared access
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

BEGIN;

-- ============================================================================
-- ENABLE ROW-LEVEL SECURITY
-- ============================================================================

ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_files FORCE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks FORCE ROW LEVEL SECURITY;
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history FORCE ROW LEVEL SECURITY;
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

DROP POLICY IF EXISTS processing_history_owner_all ON processing_history;
DROP POLICY IF EXISTS processing_history_select ON processing_history;
DROP POLICY IF EXISTS processing_history_insert ON processing_history;

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

-- SELECT: Inherit from ingestion_files (can see chunks for docs they can access)
CREATE POLICY chunks_select ON ingestion_chunks
    FOR SELECT
    USING (file_id IN (SELECT file_id FROM ingestion_files));

-- INSERT: System/worker can insert (no user restriction)
CREATE POLICY chunks_insert ON ingestion_chunks
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- PROCESSING_HISTORY POLICIES
-- ============================================================================

-- SELECT: Inherit from ingestion_files
CREATE POLICY processing_history_select ON processing_history
    FOR SELECT
    USING (file_id IN (SELECT file_id FROM ingestion_files));

-- INSERT: System/worker can insert
CREATE POLICY processing_history_insert ON processing_history
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- DOCUMENT_ROLES POLICIES
-- ============================================================================

-- SELECT: Same as document access (inherit from ingestion_files)
CREATE POLICY document_roles_select ON document_roles
    FOR SELECT
    USING (file_id IN (SELECT file_id FROM ingestion_files));

-- INSERT: User must have create permission on the role being assigned
CREATE POLICY document_roles_insert ON document_roles
    FOR INSERT
    WITH CHECK (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_create', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
        AND file_id IN (SELECT file_id FROM ingestion_files)
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

-- DELETE: User must have update permission on the role being removed
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
