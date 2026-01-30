-- Rollback Migration 012: Config Table
-- Created: 2026-01-29

\c busibox

-- Drop trigger
DROP TRIGGER IF EXISTS trigger_update_config_updated_at ON config;

-- Drop function
DROP FUNCTION IF EXISTS update_config_updated_at();

-- Drop table
DROP TABLE IF EXISTS config;

-- Remove migration record
DELETE FROM ansible_migrations WHERE version = 12;

DO $$
BEGIN
    RAISE NOTICE 'Migration 012 rolled back successfully';
END $$;
