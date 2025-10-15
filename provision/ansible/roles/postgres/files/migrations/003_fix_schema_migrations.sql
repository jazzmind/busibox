-- Migration 003: Fix schema_migrations table structure
-- Created: 2025-10-15
-- Description: Drop old schema_migrations table to allow application to create its own

-- Drop the old schema_migrations table that Ansible created
-- The application will create it with the correct schema:
--   id VARCHAR(255) PRIMARY KEY
--   name VARCHAR(255) NOT NULL
--   executed_at TIMESTAMP DEFAULT NOW()
--   checksum VARCHAR(64) NOT NULL

DROP TABLE IF EXISTS schema_migrations CASCADE;

-- Record this migration in ansible_migrations
INSERT INTO ansible_migrations (version, name, applied_at)
VALUES (3, 'fix_schema_migrations', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

