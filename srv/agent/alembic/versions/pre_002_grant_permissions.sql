-- Pre-migration script to grant necessary permissions
-- Run this as postgres superuser before running migration 002

-- Grant ALTER permission on all tables to the agent_server user
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO agent_server;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO agent_server;
GRANT ALL PRIVILEGES ON SCHEMA public TO agent_server;

-- Ensure agent_server can create tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO agent_server;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO agent_server;








