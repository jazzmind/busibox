-- Rollback Migration 011: Web Search Providers Table
-- Description: Remove web_search_providers table and related objects

-- Drop trigger
DROP TRIGGER IF EXISTS trigger_update_web_search_providers_updated_at ON web_search_providers;

-- Drop function
DROP FUNCTION IF EXISTS update_web_search_providers_updated_at();

-- Drop indexes
DROP INDEX IF EXISTS idx_web_search_providers_default;
DROP INDEX IF EXISTS idx_web_search_providers_enabled;
DROP INDEX IF EXISTS idx_web_search_providers_provider;

-- Drop table
DROP TABLE IF EXISTS web_search_providers;

-- Remove migration record
DELETE FROM ansible_migrations WHERE version = 11;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Rollback 011: web_search_providers table removed successfully';
END $$;

