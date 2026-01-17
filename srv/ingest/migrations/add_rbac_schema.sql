-- Migration: Add RBAC schema (groups, memberships, permissions)
-- Created: 2025-11-24
-- Description: Adds group-based access control to the ingestion system

BEGIN;

-- ============================================================================
-- GROUPS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS groups (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_by UUID NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups(created_by);

-- ============================================================================
-- GROUP MEMBERSHIPS TABLE
-- ============================================================================
CREATE TABLE IF NOT EXISTS group_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id UUID NOT NULL,
    role VARCHAR(50) DEFAULT 'member', -- owner, admin, member
    joined_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(group_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_group_memberships_user ON group_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_group_memberships_group ON group_memberships(group_id);

-- ============================================================================
-- ADD PERMISSION COLUMNS TO INGESTION_FILES
-- ============================================================================

-- Add owner_id column (separate from user_id for clarity)
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS owner_id UUID;

-- Add visibility column (personal or group)
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'personal';

-- Add group_id column (for group-shared documents)
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS group_id UUID REFERENCES groups(id) ON DELETE SET NULL;

-- Backfill owner_id from user_id for existing documents
UPDATE ingestion_files 
SET owner_id = user_id 
WHERE owner_id IS NULL;

-- Make owner_id NOT NULL after backfill
ALTER TABLE ingestion_files 
    ALTER COLUMN owner_id SET NOT NULL;

-- Add constraint: group documents must have group_id
-- Use DO block to check if constraint exists before adding
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname = 'check_group_visibility'
    ) THEN
        ALTER TABLE ingestion_files 
            ADD CONSTRAINT check_group_visibility 
            CHECK (
                (visibility = 'group' AND group_id IS NOT NULL) OR 
                (visibility = 'personal' AND group_id IS NULL)
            );
    END IF;
END $$;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_ingestion_files_owner ON ingestion_files(owner_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_files_group ON ingestion_files(group_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_files_visibility ON ingestion_files(visibility);

-- ============================================================================
-- COMMENTS FOR DOCUMENTATION
-- ============================================================================
COMMENT ON TABLE groups IS 'User groups for document sharing and access control';
COMMENT ON TABLE group_memberships IS 'User membership in groups';
COMMENT ON COLUMN ingestion_files.owner_id IS 'User who uploaded the document (always has access)';
COMMENT ON COLUMN ingestion_files.visibility IS 'Document visibility: personal (owner only) or group (owner + group members)';
COMMENT ON COLUMN ingestion_files.group_id IS 'Group that has access to this document (if visibility=group)';

COMMIT;

