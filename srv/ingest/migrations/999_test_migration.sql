-- Test migration to verify Ansible migration system works
-- Created: 2025-12-04

-- Create a simple test table if not exists
CREATE TABLE IF NOT EXISTS migration_test (
    id SERIAL PRIMARY KEY,
    migration_name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP DEFAULT NOW()
);

-- Insert a row to prove this migration ran
INSERT INTO migration_test (migration_name) 
VALUES ('999_test_migration - ' || NOW()::TEXT);

-- Log that we ran
DO $$ 
BEGIN 
    RAISE NOTICE 'Test migration 999_test_migration executed successfully at %', NOW();
END $$;

