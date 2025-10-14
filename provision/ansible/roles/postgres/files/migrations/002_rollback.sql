-- Migration 002 Rollback: Row-Level Security Policies
-- Created: 2025-10-14
-- Description: Rollback RLS policies and helper functions

-- ============================================================================
-- Drop Policies
-- ============================================================================

-- Drop files table policies
DROP POLICY IF EXISTS files_owner_policy ON files;
DROP POLICY IF EXISTS files_admin_policy ON files;

-- Drop chunks table policies
DROP POLICY IF EXISTS chunks_owner_policy ON chunks;
DROP POLICY IF EXISTS chunks_admin_policy ON chunks;

-- ============================================================================
-- Disable Row-Level Security
-- ============================================================================

ALTER TABLE files DISABLE ROW LEVEL SECURITY;
ALTER TABLE chunks DISABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Drop Helper Functions
-- ============================================================================

DROP FUNCTION IF EXISTS is_file_owner(UUID);
DROP FUNCTION IF EXISTS current_user_has_permission(TEXT);
DROP FUNCTION IF EXISTS current_user_id();

-- ============================================================================
-- Remove Migration Record
-- ============================================================================

DELETE FROM schema_migrations WHERE version = 2;

-- Verification query (uncomment to verify rollback)
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public';
-- SELECT proname FROM pg_proc WHERE proname LIKE 'current_user%';

