-- =============================================================================
-- Busibox Local Development Database Initialization
-- =============================================================================
--
-- This script runs when PostgreSQL container starts for the first time.
-- It creates all required databases and users for the Busibox platform.
--
-- =============================================================================

-- Create busibox_user if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'busibox_user') THEN
        CREATE ROLE busibox_user WITH LOGIN PASSWORD 'devpassword';
    END IF;
END
$$;

-- Grant permissions to busibox_user
ALTER ROLE busibox_user CREATEDB;

-- Create databases
-- Note: Each database must be created separately as CREATE DATABASE cannot be inside a transaction

-- Create busibox database (main database for shared data)
SELECT 'CREATE DATABASE busibox OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'busibox')\gexec

-- Create ai_portal database (for ai-portal Next.js app)
SELECT 'CREATE DATABASE ai_portal OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_portal')\gexec

-- Create agent_server database (for agent-api)
SELECT 'CREATE DATABASE agent_server OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_server')\gexec

-- Grant all privileges on databases
GRANT ALL PRIVILEGES ON DATABASE busibox TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE ai_portal TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE agent_server TO busibox_user;

-- =============================================================================
-- BUSIBOX DATABASE SETUP
-- =============================================================================
\c busibox

-- Grant schema permissions FIRST (PostgreSQL 15+ requires explicit grants)
GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create base schema (tables created by authz service on startup)
-- We just ensure the extensions and permissions are in place

-- Grant permissions for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- AGENT_SERVER DATABASE SETUP
-- =============================================================================
\c agent_server

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant permissions for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- AI_PORTAL DATABASE SETUP
-- =============================================================================
\c ai_portal

-- Grant schema permissions
GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant permissions for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- Return to busibox database for final message
\c busibox

-- Print success message
SELECT 'Busibox databases initialized successfully!' AS status;
