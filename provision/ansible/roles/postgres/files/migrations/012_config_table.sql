-- Migration 012: Config Table for Runtime Configuration
-- Created: 2026-01-29
-- Description: Add config table for runtime configuration that can be changed after installation
-- This replaces runtime secrets in Ansible vault with database-stored configuration

-- Ensure we're using the correct database
\c busibox

-- Create config table
CREATE TABLE IF NOT EXISTS config (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    encrypted BOOLEAN DEFAULT false,
    category VARCHAR(50),  -- 'smtp', 'api_keys', 'oauth', 'email', 'feature_flags', etc.
    description TEXT,  -- Human-readable description of this config key
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create index on category for grouped lookups
CREATE INDEX IF NOT EXISTS idx_config_category ON config(category);

-- Create trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_config_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_update_config_updated_at ON config;
CREATE TRIGGER trigger_update_config_updated_at
    BEFORE UPDATE ON config
    FOR EACH ROW
    EXECUTE FUNCTION update_config_updated_at();

-- Grant permissions to busibox_user
GRANT SELECT, INSERT, UPDATE, DELETE ON config TO busibox_user;

-- Create migrations table if it doesn't exist
CREATE TABLE IF NOT EXISTS ansible_migrations (
    version INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Record migration
INSERT INTO ansible_migrations (version, name, applied_at)
VALUES (12, 'config_table', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

-- Success message
DO $$
BEGIN
    RAISE NOTICE 'Migration 012: config table created successfully';
END $$;
