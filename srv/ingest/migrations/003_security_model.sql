-- Migration: Unified Role-Based Security Model
-- Created: 2025-11-25
-- Description: Implements role-based access control with CRUD permissions per role
-- 
-- Key changes:
-- 1. Replaces groups with document_roles table
-- 2. Updates RLS policies for CRUD-based permissions
-- 3. Adds visibility column for personal vs shared documents
--
-- Session variables set by JWT middleware:
-- - app.user_id: Current user UUID
-- - app.user_role_ids_read: CSV of role UUIDs user can read
-- - app.user_role_ids_create: CSV of role UUIDs user can create with
-- - app.user_role_ids_update: CSV of role UUIDs user can update
-- - app.user_role_ids_delete: CSV of role UUIDs user can delete

BEGIN;

-- ============================================================================
-- DROP OLD POLICIES (for clean migration)
-- ============================================================================

DROP POLICY IF EXISTS ingestion_files_owner_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_group_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_insert ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_update ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_delete ON ingestion_files;
DROP POLICY IF EXISTS personal_docs_select ON ingestion_files;
DROP POLICY IF EXISTS shared_docs_select ON ingestion_files;

DROP POLICY IF EXISTS chunks_select ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_insert ON ingestion_chunks;

DROP POLICY IF EXISTS processing_history_select ON processing_history;

-- ============================================================================
-- DOCUMENT ROLES TABLE
-- ============================================================================

-- Stores which roles have access to each document
CREATE TABLE IF NOT EXISTS document_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    role_id UUID NOT NULL,  -- References AI Portal DocumentRole table
    role_name VARCHAR(100) NOT NULL,  -- Denormalized for performance and logging
    added_at TIMESTAMP DEFAULT NOW(),
    added_by UUID,  -- User who added this role assignment
    UNIQUE(file_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_document_roles_file ON document_roles(file_id);
CREATE INDEX IF NOT EXISTS idx_document_roles_role ON document_roles(role_id);
CREATE INDEX IF NOT EXISTS idx_document_roles_name ON document_roles(role_name);

COMMENT ON TABLE document_roles IS 'Many-to-many relationship between documents and access roles';
COMMENT ON COLUMN document_roles.role_id IS 'UUID of role from AI Portal DocumentRole table';
COMMENT ON COLUMN document_roles.role_name IS 'Denormalized role name for performance';

-- ============================================================================
-- UPDATE INGESTION_FILES TABLE
-- ============================================================================

-- Add owner_id column if not exists (separate from user_id for clarity)
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS owner_id UUID;

-- Add visibility column (personal or shared)
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'personal';

-- Backfill owner_id from user_id for existing documents
UPDATE ingestion_files 
SET owner_id = user_id 
WHERE owner_id IS NULL;

-- Make owner_id NOT NULL after backfill
ALTER TABLE ingestion_files 
    ALTER COLUMN owner_id SET NOT NULL;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_ingestion_files_owner ON ingestion_files(owner_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_files_visibility ON ingestion_files(visibility);

-- Add constraint: visibility must be 'personal' or 'shared'
ALTER TABLE ingestion_files DROP CONSTRAINT IF EXISTS check_visibility;
ALTER TABLE ingestion_files ADD CONSTRAINT check_visibility 
    CHECK (visibility IN ('personal', 'shared'));

COMMENT ON COLUMN ingestion_files.owner_id IS 'User who uploaded the document (no special privileges for shared docs)';
COMMENT ON COLUMN ingestion_files.visibility IS 'Access model: personal (owner only) or shared (role-based)';

-- ============================================================================
-- MIGRATE OLD GROUP DATA (if exists)
-- ============================================================================

-- If there's an old groups/group_id column, migrate the data
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns 
               WHERE table_name = 'ingestion_files' AND column_name = 'group_id') THEN
        -- Migrate group documents to shared visibility
        UPDATE ingestion_files 
        SET visibility = 'shared' 
        WHERE group_id IS NOT NULL AND visibility = 'personal';
        
        -- Note: Manual migration needed to create document_roles entries
        -- from the old group_memberships table
        RAISE NOTICE 'Migration: Found group_id column. Shared visibility set for group documents.';
        RAISE NOTICE 'Manual step: Create document_roles entries from old group data.';
    END IF;
END $$;

-- ============================================================================
-- ENABLE ROW-LEVEL SECURITY
-- ============================================================================

ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_roles ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- INGESTION_FILES RLS POLICIES
-- ============================================================================

-- SELECT: Personal documents - owner only
CREATE POLICY personal_docs_select ON ingestion_files
    FOR SELECT
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
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
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- UPDATE: Personal docs - owner only; Shared docs - update permission on a document role
CREATE POLICY personal_docs_update ON ingestion_files
    FOR UPDATE
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    )
    WITH CHECK (
        visibility = 'personal'
        AND owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

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

-- DELETE: Personal docs - owner only; Shared docs - delete permission on ALL document roles
CREATE POLICY personal_docs_delete ON ingestion_files
    FOR DELETE
    USING (
        visibility = 'personal' 
        AND owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

CREATE POLICY shared_docs_delete ON ingestion_files
    FOR DELETE
    USING (
        visibility = 'shared'
        -- User must have delete permission on ALL roles assigned to this document
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
        -- And document has at least one role (safety check)
        AND EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
        )
    );

-- ============================================================================
-- DOCUMENT_ROLES RLS POLICIES
-- ============================================================================

-- SELECT: Same as document access (inherit from ingestion_files)
CREATE POLICY document_roles_select ON document_roles
    FOR SELECT
    USING (
        file_id IN (SELECT file_id FROM ingestion_files)
    );

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
        -- And user can access the document
        AND file_id IN (SELECT file_id FROM ingestion_files)
    );

-- UPDATE: User must have update permission on both old and new roles
CREATE POLICY document_roles_update ON document_roles
    FOR UPDATE
    USING (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    )
    WITH CHECK (
        role_id = ANY(
            COALESCE(
                string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[],
                ARRAY[]::uuid[]
            )
        )
    );

-- DELETE: User must have update permission on the role being removed
-- (Using update permission for role removal, not delete)
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

-- ============================================================================
-- CHUNKS RLS POLICIES
-- ============================================================================

-- SELECT: Inherit from ingestion_files (can see chunks for docs they can access)
CREATE POLICY chunks_select ON ingestion_chunks
    FOR SELECT
    USING (
        file_id IN (SELECT file_id FROM ingestion_files)
    );

-- INSERT: System/worker can insert (no user restriction)
CREATE POLICY chunks_insert ON ingestion_chunks
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- PROCESSING_HISTORY RLS POLICIES
-- ============================================================================

-- SELECT: Inherit from ingestion_files
CREATE POLICY processing_history_select ON processing_history
    FOR SELECT
    USING (
        file_id IN (SELECT file_id FROM ingestion_files)
    );

-- INSERT: System/worker can insert
CREATE POLICY processing_history_insert ON processing_history
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- GRANT PRIVILEGES
-- ============================================================================

GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_files TO busibox_user;
GRANT SELECT, INSERT ON ingestion_chunks TO busibox_user;
GRANT SELECT, INSERT ON processing_history TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON document_roles TO busibox_user;

-- ============================================================================
-- HELPER FUNCTION: Check minimum role requirement
-- ============================================================================

CREATE OR REPLACE FUNCTION check_document_has_roles()
RETURNS TRIGGER AS $$
BEGIN
    -- Only check on DELETE
    IF TG_OP = 'DELETE' THEN
        -- Check if this would leave the document with no roles
        IF NOT EXISTS (
            SELECT 1 FROM document_roles 
            WHERE file_id = OLD.file_id 
            AND id != OLD.id
        ) THEN
            -- Check if document is shared (personal docs don't need roles)
            IF EXISTS (
                SELECT 1 FROM ingestion_files 
                WHERE file_id = OLD.file_id 
                AND visibility = 'shared'
            ) THEN
                RAISE EXCEPTION 'Cannot remove last role from shared document. Document must have at least one role.';
            END IF;
        END IF;
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ensure_document_has_roles ON document_roles;
CREATE TRIGGER ensure_document_has_roles
    BEFORE DELETE ON document_roles
    FOR EACH ROW
    EXECUTE FUNCTION check_document_has_roles();

-- ============================================================================
-- DOCUMENTATION
-- ============================================================================

COMMENT ON POLICY personal_docs_select ON ingestion_files IS 
    'Users can SELECT their personal documents (owner_id matches app.user_id)';

COMMENT ON POLICY shared_docs_select ON ingestion_files IS 
    'Users can SELECT shared documents if they have read permission on at least one document role';

COMMENT ON POLICY ingestion_files_insert ON ingestion_files IS 
    'Users can INSERT documents they own (owner_id = app.user_id)';

COMMENT ON POLICY personal_docs_update ON ingestion_files IS 
    'Users can UPDATE their personal documents';

COMMENT ON POLICY shared_docs_update ON ingestion_files IS 
    'Users can UPDATE shared documents if they have update permission on at least one document role';

COMMENT ON POLICY personal_docs_delete ON ingestion_files IS 
    'Users can DELETE their personal documents';

COMMENT ON POLICY shared_docs_delete ON ingestion_files IS 
    'Users can DELETE shared documents only if they have delete permission on ALL document roles';

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (for testing)
-- ============================================================================

-- To test RLS, set session variables and query:
-- 
-- SET LOCAL app.user_id = 'your-user-uuid';
-- SET LOCAL app.user_role_ids_read = 'role-uuid-1,role-uuid-2';
-- SET LOCAL app.user_role_ids_create = 'role-uuid-1';
-- SET LOCAL app.user_role_ids_update = 'role-uuid-1';
-- SET LOCAL app.user_role_ids_delete = 'role-uuid-1';
-- SELECT * FROM ingestion_files;
-- 
-- You should only see:
-- 1. Personal documents you own
-- 2. Shared documents where you have read permission on at least one role

