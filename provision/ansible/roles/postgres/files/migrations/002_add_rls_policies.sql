-- Migration 002: Row-Level Security Policies
-- Created: 2025-10-14
-- Description: Add RLS policies for multi-tenant data isolation

-- ============================================================================
-- Helper Functions for RLS
-- ============================================================================

-- Function to get current user ID from session
CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID AS $$
BEGIN
    -- Application sets this via: SET app.current_user_id = '<uuid>';
    RETURN current_setting('app.current_user_id', true)::UUID;
EXCEPTION
    WHEN OTHERS THEN
        RETURN NULL;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to check if user has specific permission
CREATE OR REPLACE FUNCTION current_user_has_permission(perm TEXT) RETURNS BOOLEAN AS $$
DECLARE
    user_uuid UUID;
BEGIN
    user_uuid := current_user_id();
    
    IF user_uuid IS NULL THEN
        RETURN FALSE;
    END IF;
    
    -- Check if user has a role with the specified permission
    -- Permission format: "category.action" (e.g., "admin.manage_users")
    RETURN EXISTS (
        SELECT 1
        FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = user_uuid
        AND r.permissions @> jsonb_build_object(
            split_part(perm, '.', 1),
            jsonb_build_object(split_part(perm, '.', 2), true)
        )
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Function to check if user is owner of a file
CREATE OR REPLACE FUNCTION is_file_owner(file_uuid UUID) RETURNS BOOLEAN AS $$
DECLARE
    user_uuid UUID;
BEGIN
    user_uuid := current_user_id();
    
    IF user_uuid IS NULL THEN
        RETURN FALSE;
    END IF;
    
    RETURN EXISTS (
        SELECT 1
        FROM files
        WHERE id = file_uuid AND owner_id = user_uuid
    );
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- ============================================================================
-- Enable Row-Level Security
-- ============================================================================

-- Enable RLS on files table
ALTER TABLE files ENABLE ROW LEVEL SECURITY;

-- Enable RLS on chunks table
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- ============================================================================
-- Files Table Policies
-- ============================================================================

-- Policy 1: Users can see their own files
DROP POLICY IF EXISTS files_owner_policy ON files;
CREATE POLICY files_owner_policy ON files
    FOR ALL
    USING (owner_id = current_user_id());

-- Policy 2: Admins can see all files
DROP POLICY IF EXISTS files_admin_policy ON files;
CREATE POLICY files_admin_policy ON files
    FOR ALL
    USING (current_user_has_permission('admin.manage_users'));

-- Policy 3: Users with read permission can view files (for shared access - future)
-- Note: This is a placeholder for future shared file functionality
-- Currently, only owner and admin can access files

-- ============================================================================
-- Chunks Table Policies
-- ============================================================================

-- Policy 1: Chunks inherit permissions from parent file
-- Users can see chunks from files they own
DROP POLICY IF EXISTS chunks_owner_policy ON chunks;
CREATE POLICY chunks_owner_policy ON chunks
    FOR ALL
    USING (
        file_id IN (
            SELECT id FROM files WHERE owner_id = current_user_id()
        )
    );

-- Policy 2: Admins can see all chunks
DROP POLICY IF EXISTS chunks_admin_policy ON chunks;
CREATE POLICY chunks_admin_policy ON chunks
    FOR ALL
    USING (current_user_has_permission('admin.manage_users'));

-- ============================================================================
-- Usage Instructions
-- ============================================================================

-- To set the current user context in application code:
-- 
-- Python (psycopg2):
--   cursor.execute("SET app.current_user_id = %s", (user_id,))
--
-- SQL:
--   SET app.current_user_id = '123e4567-e89b-12d3-a456-426614174000';
--
-- To test RLS policies:
--   SET app.current_user_id = '<valid_user_uuid>';
--   SELECT * FROM files;  -- Should only see files owned by that user
--   
--   RESET app.current_user_id;
--   SELECT * FROM files;  -- Should see nothing (no user context)

-- ============================================================================
-- Record Migration
-- ============================================================================

INSERT INTO schema_migrations (version, name) VALUES (2, 'add_rls_policies')
ON CONFLICT (version) DO NOTHING;

-- ============================================================================
-- Verification Queries (for manual testing)
-- ============================================================================

-- Uncomment to verify RLS is enabled
-- SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' AND rowsecurity = true;
-- SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual FROM pg_policies WHERE schemaname = 'public';

