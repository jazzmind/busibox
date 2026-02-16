-- Migration 013: Deployment Tables for Deploy-API
-- Created: 2026-02-10
-- Description: Add tables for deployment configuration, GitHub connections,
--              deployment history, app secrets, and GitHub releases.
--              These tables are owned by deploy-api and replace the busibox-portal
--              Prisma-managed deployment models.

-- Enable UUID extension (idempotent)
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- GitHub Connections (per-user GitHub OAuth credentials)
-- ============================================================================

CREATE TABLE IF NOT EXISTS github_connections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(255) NOT NULL,
    access_token TEXT NOT NULL,          -- encrypted
    refresh_token TEXT,                   -- encrypted
    token_expires_at TIMESTAMPTZ,
    github_user_id VARCHAR(255) NOT NULL,
    github_username VARCHAR(255) NOT NULL,
    scopes TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_github_connections_user_id UNIQUE (user_id)
);

CREATE INDEX IF NOT EXISTS idx_github_connections_user_id ON github_connections(user_id);
CREATE INDEX IF NOT EXISTS idx_github_connections_github_user_id ON github_connections(github_user_id);

-- ============================================================================
-- Deployment Configs (per-app deployment configuration)
-- ============================================================================

CREATE TABLE IF NOT EXISTS deployment_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    app_id VARCHAR(255) NOT NULL,
    github_connection_id UUID NOT NULL REFERENCES github_connections(id) ON DELETE CASCADE,
    github_repo_owner VARCHAR(255) NOT NULL,
    github_repo_name VARCHAR(255) NOT NULL,
    github_branch VARCHAR(255) NOT NULL DEFAULT 'main',
    deploy_path VARCHAR(512) NOT NULL,
    port INTEGER NOT NULL,
    health_endpoint VARCHAR(255) NOT NULL DEFAULT '/api/health',
    build_command VARCHAR(512),
    start_command VARCHAR(512),
    auto_deploy_enabled BOOLEAN NOT NULL DEFAULT false,
    staging_enabled BOOLEAN NOT NULL DEFAULT false,
    staging_port INTEGER,
    staging_path VARCHAR(512),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_deployment_configs_app_id UNIQUE (app_id)
);

CREATE INDEX IF NOT EXISTS idx_deployment_configs_app_id ON deployment_configs(app_id);
CREATE INDEX IF NOT EXISTS idx_deployment_configs_github_connection_id ON deployment_configs(github_connection_id);

-- ============================================================================
-- Deployments (deployment history)
-- ============================================================================

CREATE TABLE IF NOT EXISTS deployments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_config_id UUID NOT NULL REFERENCES deployment_configs(id) ON DELETE CASCADE,
    environment VARCHAR(20) NOT NULL DEFAULT 'PRODUCTION',
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    deployment_type VARCHAR(20) NOT NULL DEFAULT 'RELEASE',
    release_tag VARCHAR(255),
    release_id VARCHAR(255),
    commit_sha VARCHAR(255),
    deployed_by VARCHAR(255) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    error_message TEXT,
    logs TEXT,
    previous_deployment_id UUID REFERENCES deployments(id),
    is_rollback BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_deployments_deployment_config_id ON deployments(deployment_config_id);
CREATE INDEX IF NOT EXISTS idx_deployments_deployed_by ON deployments(deployed_by);
CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status);
CREATE INDEX IF NOT EXISTS idx_deployments_environment ON deployments(environment);
CREATE INDEX IF NOT EXISTS idx_deployments_started_at ON deployments(started_at DESC);

-- ============================================================================
-- App Secrets (encrypted env vars per deployment config)
-- ============================================================================

CREATE TABLE IF NOT EXISTS app_secrets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_config_id UUID NOT NULL REFERENCES deployment_configs(id) ON DELETE CASCADE,
    key VARCHAR(255) NOT NULL,
    encrypted_value TEXT NOT NULL,
    type VARCHAR(20) NOT NULL DEFAULT 'CUSTOM',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_app_secrets_config_key UNIQUE (deployment_config_id, key)
);

CREATE INDEX IF NOT EXISTS idx_app_secrets_deployment_config_id ON app_secrets(deployment_config_id);

-- ============================================================================
-- GitHub Releases (cached GitHub release info)
-- ============================================================================

CREATE TABLE IF NOT EXISTS github_releases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_config_id UUID NOT NULL REFERENCES deployment_configs(id) ON DELETE CASCADE,
    release_id VARCHAR(255) NOT NULL,
    tag_name VARCHAR(255) NOT NULL,
    release_name VARCHAR(512),
    body TEXT,
    commit_sha VARCHAR(255),
    published_at TIMESTAMPTZ NOT NULL,
    is_prerelease BOOLEAN NOT NULL DEFAULT false,
    is_draft BOOLEAN NOT NULL DEFAULT false,
    tarball_url VARCHAR(1024),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_github_releases_config_release UNIQUE (deployment_config_id, release_id)
);

CREATE INDEX IF NOT EXISTS idx_github_releases_deployment_config_id ON github_releases(deployment_config_id);
CREATE INDEX IF NOT EXISTS idx_github_releases_published_at ON github_releases(published_at DESC);

-- ============================================================================
-- App Databases (provisioned databases for apps)
-- ============================================================================

CREATE TABLE IF NOT EXISTS app_databases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deployment_config_id UUID NOT NULL REFERENCES deployment_configs(id) ON DELETE CASCADE,
    database_name VARCHAR(255) NOT NULL,
    database_user VARCHAR(255) NOT NULL,
    encrypted_password TEXT NOT NULL,
    host VARCHAR(255) NOT NULL DEFAULT 'postgres',
    port INTEGER NOT NULL DEFAULT 5432,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_app_databases_deployment_config_id UNIQUE (deployment_config_id),
    CONSTRAINT uq_app_databases_database_name UNIQUE (database_name)
);

CREATE INDEX IF NOT EXISTS idx_app_databases_deployment_config_id ON app_databases(deployment_config_id);
CREATE INDEX IF NOT EXISTS idx_app_databases_database_name ON app_databases(database_name);

-- ============================================================================
-- Updated_at triggers
-- ============================================================================

CREATE OR REPLACE FUNCTION update_deployment_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- github_connections
DROP TRIGGER IF EXISTS trigger_github_connections_updated_at ON github_connections;
CREATE TRIGGER trigger_github_connections_updated_at
    BEFORE UPDATE ON github_connections
    FOR EACH ROW
    EXECUTE FUNCTION update_deployment_updated_at();

-- deployment_configs
DROP TRIGGER IF EXISTS trigger_deployment_configs_updated_at ON deployment_configs;
CREATE TRIGGER trigger_deployment_configs_updated_at
    BEFORE UPDATE ON deployment_configs
    FOR EACH ROW
    EXECUTE FUNCTION update_deployment_updated_at();

-- app_secrets
DROP TRIGGER IF EXISTS trigger_app_secrets_updated_at ON app_secrets;
CREATE TRIGGER trigger_app_secrets_updated_at
    BEFORE UPDATE ON app_secrets
    FOR EACH ROW
    EXECUTE FUNCTION update_deployment_updated_at();

-- app_databases
DROP TRIGGER IF EXISTS trigger_app_databases_updated_at ON app_databases;
CREATE TRIGGER trigger_app_databases_updated_at
    BEFORE UPDATE ON app_databases
    FOR EACH ROW
    EXECUTE FUNCTION update_deployment_updated_at();

-- ============================================================================
-- Grants
-- ============================================================================

GRANT SELECT, INSERT, UPDATE, DELETE ON github_connections TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON deployment_configs TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON deployments TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON app_secrets TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON github_releases TO busibox_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON app_databases TO busibox_user;

-- ============================================================================
-- Migration record
-- ============================================================================

CREATE TABLE IF NOT EXISTS ansible_migrations (
    version INTEGER PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO ansible_migrations (version, name, applied_at)
VALUES (13, 'deployment_tables', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

DO $$
BEGIN
    RAISE NOTICE 'Migration 013: deployment tables created successfully';
END $$;
