-- Migration: Add Row-Level Security (RLS) policies
-- Created: 2025-11-24
-- Updated: 2025-12-22 - Simplified to owner-based access only
-- Description: Enforces database-level access control for multi-tenancy
--
-- RLS Session Variables:
--   app.user_id - UUID of the current user (set by application)
--
-- Usage in application code:
--   SET LOCAL app.user_id = '<user-uuid>';
--   SELECT * FROM ingestion_files;  -- Only returns user's own documents

BEGIN;

-- ============================================================================
-- ENABLE ROW-LEVEL SECURITY
-- ============================================================================

-- Enable RLS on ingestion_files table
ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;

-- Force RLS to apply to table owner as well (important for testing)
ALTER TABLE ingestion_files FORCE ROW LEVEL SECURITY;

-- Enable RLS on chunks table (inherits permissions from ingestion_files)
ALTER TABLE ingestion_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingestion_chunks FORCE ROW LEVEL SECURITY;

-- Enable RLS on processing_history table
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history FORCE ROW LEVEL SECURITY;

-- ============================================================================
-- DROP EXISTING POLICIES (for idempotency)
-- ============================================================================

DROP POLICY IF EXISTS ingestion_files_owner_all ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_owner_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_group_select ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_insert ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_update ON ingestion_files;
DROP POLICY IF EXISTS ingestion_files_delete ON ingestion_files;

DROP POLICY IF EXISTS chunks_select ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_insert ON ingestion_chunks;
DROP POLICY IF EXISTS chunks_owner_all ON ingestion_chunks;

DROP POLICY IF EXISTS processing_history_select ON processing_history;
DROP POLICY IF EXISTS processing_history_owner_all ON processing_history;

-- ============================================================================
-- INGESTION_FILES POLICIES
-- ============================================================================

-- Policy: Users can only access their own documents
-- Handles NULL/empty app.user_id by comparing to a non-existent UUID
CREATE POLICY ingestion_files_owner_all ON ingestion_files
    FOR ALL
    USING (
        owner_id = CASE 
            WHEN current_setting('app.user_id', true) IS NULL 
                 OR current_setting('app.user_id', true) = '' 
            THEN '00000000-0000-0000-0000-000000000000'::uuid
            ELSE current_setting('app.user_id', true)::uuid
        END
    );

-- ============================================================================
-- CHUNKS POLICIES
-- ============================================================================

-- Policy: Users can see chunks from documents they own
-- (Inherits from ingestion_files via file_id foreign key)
CREATE POLICY chunks_owner_all ON ingestion_chunks
    FOR ALL
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS policies on ingestion_files automatically apply here
        )
    );

-- ============================================================================
-- PROCESSING_HISTORY POLICIES
-- ============================================================================

-- Policy: Users can see processing history for their documents
CREATE POLICY processing_history_owner_all ON processing_history
    FOR ALL
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS policies on ingestion_files automatically apply here
        )
    );

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (for testing)
-- ============================================================================

-- To test RLS, set session variables and query:
-- 
-- SET LOCAL app.user_id = 'your-user-uuid';
-- SELECT * FROM ingestion_files;
-- 
-- You should only see documents you own.
--
-- Without setting app.user_id, you should see no documents.
