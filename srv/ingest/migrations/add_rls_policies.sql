-- Migration: Add Row-Level Security (RLS) policies
-- Created: 2025-11-24
-- Description: Enforces database-level access control for multi-tenancy

BEGIN;

-- ============================================================================
-- ENABLE ROW-LEVEL SECURITY
-- ============================================================================

-- Enable RLS on ingestion_files table
ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;

-- Enable RLS on chunks table (inherits permissions from ingestion_files)
ALTER TABLE ingestion_chunks ENABLE ROW LEVEL SECURITY;

-- Enable RLS on processing_history table
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- DROP EXISTING POLICIES (for idempotency)
-- ============================================================================

DROP POLICY IF EXISTS ingestion_files_owner_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_group_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_insert ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_update ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_delete ON ingestion_files;

DROP POLICY IF EXISTS chunks_select ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_insert ON ingestion_chunks;

DROP POLICY IF EXISTS processing_history_select ON processing_history;

-- ============================================================================
-- INGESTION_FILES POLICIES
-- ============================================================================

-- Policy: Users can see their own documents
CREATE POLICY ingestion_files_owner_select ON ingestion_files
    FOR SELECT
    USING (
        owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- Policy: Users can see group documents they have access to
CREATE POLICY ingestion_files_group_select ON ingestion_files
    FOR SELECT
    USING (
        visibility = 'group' 
        AND group_id IN (
            SELECT unnest(
                string_to_array(
                    COALESCE(current_setting('app.user_groups', true), ''),
                    ','
                )
            )::uuid
        )
    );

-- Policy: Users can only insert documents they own
CREATE POLICY ingestion_files_insert ON ingestion_files
    FOR INSERT
    WITH CHECK (
        owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
        AND (
            -- Personal documents: no group check needed
            (visibility = 'personal' AND group_id IS NULL)
            OR
            -- Group documents: user must be in the group
            (
                visibility = 'group' 
                AND group_id IN (
                    SELECT unnest(
                        string_to_array(
                            COALESCE(current_setting('app.user_groups', true), ''),
                            ','
                        )
                    )::uuid
                )
            )
        )
    );

-- Policy: Users can only update their own documents
CREATE POLICY ingestion_files_update ON ingestion_files
    FOR UPDATE
    USING (
        owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    )
    WITH CHECK (
        owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- Policy: Users can only delete their own documents
CREATE POLICY ingestion_files_delete ON ingestion_files
    FOR DELETE
    USING (
        owner_id = COALESCE(
            current_setting('app.user_id', true)::uuid,
            '00000000-0000-0000-0000-000000000000'::uuid
        )
    );

-- ============================================================================
-- CHUNKS POLICIES
-- ============================================================================

-- Policy: Users can see chunks from documents they can access
-- (RLS on ingestion_files automatically filters the subquery)
CREATE POLICY chunks_select ON ingestion_chunks
    FOR SELECT
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS policies on ingestion_files automatically apply here
        )
    );

-- Policy: System can insert chunks (during processing)
-- Chunks are inserted by worker with elevated privileges
CREATE POLICY chunks_insert ON ingestion_chunks
    FOR INSERT
    WITH CHECK (true);

-- ============================================================================
-- PROCESSING_HISTORY POLICIES
-- ============================================================================

-- Policy: Users can see processing history for their documents
CREATE POLICY processing_history_select ON processing_history
    FOR SELECT
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS policies on ingestion_files automatically apply here
        )
    );

-- ============================================================================
-- GRANT PRIVILEGES
-- ============================================================================

-- Ensure application user has necessary privileges
GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_files TO busibox_user;
GRANT SELECT, INSERT ON ingestion_chunks TO busibox_user;
GRANT SELECT, INSERT ON processing_history TO busibox_user;
GRANT SELECT ON groups TO busibox_user;
GRANT SELECT ON group_memberships TO busibox_user;

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================

COMMENT ON POLICY ingestion_files_owner_select ON ingestion_files IS 
    'Users can SELECT their own documents (owner_id matches app.user_id session variable)';

COMMENT ON POLICY ingestion_files_group_select ON ingestion_files IS 
    'Users can SELECT group documents if they are members (group_id in app.user_groups session variable)';

COMMENT ON POLICY ingestion_files_insert ON ingestion_files IS 
    'Users can INSERT documents they own, and only to groups they are members of';

COMMENT ON POLICY ingestion_files_update ON ingestion_files IS 
    'Users can UPDATE only their own documents';

COMMENT ON POLICY ingestion_files_delete ON ingestion_files IS 
    'Users can DELETE only their own documents';

COMMENT ON POLICY chunks_select ON ingestion_chunks IS 
    'Users can SELECT chunks from documents they have access to (via ingestion_files RLS)';

COMMENT ON POLICY processing_history_select ON processing_history IS 
    'Users can SELECT processing history for documents they have access to';

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (for testing)
-- ============================================================================

-- To test RLS, set session variables and query:
-- 
-- SET LOCAL app.user_id = 'your-user-uuid';
-- SET LOCAL app.user_groups = 'group-uuid-1,group-uuid-2';
-- SELECT * FROM ingestion_files;
-- 
-- You should only see documents you own or that belong to your groups.

