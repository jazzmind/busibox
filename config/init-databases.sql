-- =============================================================================
-- Busibox Local Development Database Initialization
-- =============================================================================
--
-- This script runs when PostgreSQL container starts for the first time.
-- It creates all required databases and users for the Busibox platform.
--
-- Architecture:
--   Production/Staging User: busibox_user
--     - Connects to: agent_server, authz, files, busibox, ai_portal
--   
--   Pytest Test User: busibox_test_user
--     - Connects to: agent_server, authz, files (SAME names, different owner)
--     - Provides complete isolation for automated tests
--
-- =============================================================================

-- =============================================================================
-- CREATE USERS
-- =============================================================================

-- Production/Staging user
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'busibox_user') THEN
        CREATE ROLE busibox_user WITH LOGIN PASSWORD 'devpassword';
    END IF;
END
$$;

-- Test user (for pytest isolation)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'busibox_test_user') THEN
        CREATE ROLE busibox_test_user WITH LOGIN PASSWORD 'testpassword';
    END IF;
END
$$;

-- Grant permissions to users
ALTER ROLE busibox_user CREATEDB;
ALTER ROLE busibox_test_user CREATEDB;

-- =============================================================================
-- PRODUCTION/STAGING DATABASES (owned by busibox_user)
-- =============================================================================

-- Create busibox database (main database for shared data)
SELECT 'CREATE DATABASE busibox OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'busibox')\gexec

-- Create ai_portal database (for ai-portal Next.js app)
SELECT 'CREATE DATABASE ai_portal OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'ai_portal')\gexec

-- Create agent_server database (for agent-api)
SELECT 'CREATE DATABASE agent_server OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agent_server')\gexec

-- Create authz database (for authz service)
SELECT 'CREATE DATABASE authz OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'authz')\gexec

-- Create files database (for ingest/search service)
SELECT 'CREATE DATABASE files OWNER busibox_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'files')\gexec

-- =============================================================================
-- PYTEST TEST DATABASES (owned by busibox_test_user)
-- Same table names as production, but completely isolated
-- =============================================================================

-- Create test_agent_server database
SELECT 'CREATE DATABASE test_agent_server OWNER busibox_test_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'test_agent_server')\gexec

-- Create test_authz database
SELECT 'CREATE DATABASE test_authz OWNER busibox_test_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'test_authz')\gexec

-- Create test_files database
SELECT 'CREATE DATABASE test_files OWNER busibox_test_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'test_files')\gexec

-- =============================================================================
-- GRANT PRIVILEGES
-- =============================================================================

-- Production databases
GRANT ALL PRIVILEGES ON DATABASE busibox TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE ai_portal TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE agent_server TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE authz TO busibox_user;
GRANT ALL PRIVILEGES ON DATABASE files TO busibox_user;

-- Test databases
GRANT ALL PRIVILEGES ON DATABASE test_agent_server TO busibox_test_user;
GRANT ALL PRIVILEGES ON DATABASE test_authz TO busibox_test_user;
GRANT ALL PRIVILEGES ON DATABASE test_files TO busibox_test_user;

-- =============================================================================
-- BUSIBOX DATABASE SETUP (production)
-- =============================================================================
\c busibox

GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- AGENT_SERVER DATABASE SETUP (production)
-- =============================================================================
\c agent_server

GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- AUTHZ DATABASE SETUP (production)
-- =============================================================================
\c authz

GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- FILES DATABASE SETUP (production)
-- =============================================================================
\c files

GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- AI_PORTAL DATABASE SETUP (production)
-- =============================================================================
\c ai_portal

GRANT ALL ON SCHEMA public TO busibox_user;
GRANT CREATE ON SCHEMA public TO busibox_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_user;

-- =============================================================================
-- TEST_AGENT_SERVER DATABASE SETUP (pytest)
-- =============================================================================
\c test_agent_server

GRANT ALL ON SCHEMA public TO busibox_test_user;
GRANT CREATE ON SCHEMA public TO busibox_test_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_test_user;

-- =============================================================================
-- TEST_AUTHZ DATABASE SETUP (pytest)
-- =============================================================================
\c test_authz

GRANT ALL ON SCHEMA public TO busibox_test_user;
GRANT CREATE ON SCHEMA public TO busibox_test_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_test_user;

-- =============================================================================
-- TEST_FILES DATABASE SETUP (pytest)
-- =============================================================================
\c test_files

GRANT ALL ON SCHEMA public TO busibox_test_user;
GRANT CREATE ON SCHEMA public TO busibox_test_user;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO busibox_test_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO busibox_test_user;

-- Return to busibox database for final message
\c busibox

-- Print success message
SELECT 'Busibox databases initialized successfully!' AS status;
