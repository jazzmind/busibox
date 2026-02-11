-- Rollback Migration 013: Deployment Tables
-- Created: 2026-02-10

-- Drop triggers
DROP TRIGGER IF EXISTS trigger_github_connections_updated_at ON github_connections;
DROP TRIGGER IF EXISTS trigger_deployment_configs_updated_at ON deployment_configs;
DROP TRIGGER IF EXISTS trigger_app_secrets_updated_at ON app_secrets;
DROP TRIGGER IF EXISTS trigger_app_databases_updated_at ON app_databases;

-- Drop function (only if no other triggers use it)
DROP FUNCTION IF EXISTS update_deployment_updated_at();

-- Drop tables in dependency order
DROP TABLE IF EXISTS app_databases;
DROP TABLE IF EXISTS github_releases;
DROP TABLE IF EXISTS app_secrets;
DROP TABLE IF EXISTS deployments;
DROP TABLE IF EXISTS deployment_configs;
DROP TABLE IF EXISTS github_connections;

-- Remove migration record
DELETE FROM ansible_migrations WHERE version = 13;

DO $$
BEGIN
    RAISE NOTICE 'Migration 013 rolled back successfully';
END $$;
