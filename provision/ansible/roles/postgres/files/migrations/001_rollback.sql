-- Migration 001 Rollback: Initial Schema
-- Created: 2025-10-14
-- Description: Rollback initial database schema

-- WARNING: This will delete all data in these tables!
-- Only run this if you need to completely reset the database.

-- Drop tables in reverse dependency order
DROP TABLE IF EXISTS data_jobs;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS user_roles;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS roles;

-- Remove migration record
DELETE FROM schema_migrations WHERE version = 1;

-- Note: We keep schema_migrations table even on rollback
-- to maintain migration history. To fully reset, drop it manually:
-- DROP TABLE IF EXISTS schema_migrations;

-- Verification query (uncomment to verify rollback)
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;

