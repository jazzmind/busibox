-- Rollback Migration 010: Data Service Schema
-- Created: 2025-11-05
-- Description: Rollback data service tables and associated objects

BEGIN;

-- Drop triggers
DROP TRIGGER IF EXISTS trigger_notify_status_update ON data_status;
DROP TRIGGER IF EXISTS trigger_update_data_file_timestamp ON data_status;

-- Drop functions
DROP FUNCTION IF EXISTS notify_status_update();
DROP FUNCTION IF EXISTS update_data_file_timestamp();

-- Drop tables (cascades to dependent objects)
DROP TABLE IF EXISTS data_chunks CASCADE;
DROP TABLE IF EXISTS data_status CASCADE;
DROP TABLE IF EXISTS data_files CASCADE;

-- Remove migration record
DELETE FROM ansible_migrations WHERE version = 10;

COMMIT;

