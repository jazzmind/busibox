-- Rollback Migration 010: Ingestion Service Schema
-- Created: 2025-11-05
-- Description: Rollback ingestion service tables and associated objects

BEGIN;

-- Drop triggers
DROP TRIGGER IF EXISTS trigger_notify_status_update ON ingestion_status;
DROP TRIGGER IF EXISTS trigger_update_ingestion_file_timestamp ON ingestion_status;

-- Drop functions
DROP FUNCTION IF EXISTS notify_status_update();
DROP FUNCTION IF EXISTS update_ingestion_file_timestamp();

-- Drop tables (cascades to dependent objects)
DROP TABLE IF EXISTS ingestion_chunks CASCADE;
DROP TABLE IF EXISTS ingestion_status CASCADE;
DROP TABLE IF EXISTS ingestion_files CASCADE;

-- Remove migration record
DELETE FROM ansible_migrations WHERE version = 10;

COMMIT;

