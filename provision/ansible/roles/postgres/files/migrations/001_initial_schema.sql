-- Migration 001: Initial Schema
-- Created: 2025-10-14
-- Description: Create initial database schema with users, roles, files, chunks, and ingestion jobs

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Schema migrations tracking table (must be first)
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ============================================================================
-- Users and Roles
-- ============================================================================

-- Roles table
CREATE TABLE IF NOT EXISTS roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,
    permissions JSONB NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User-Role join table
CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role_id)
);

-- ============================================================================
-- Files and Chunks
-- ============================================================================

-- Files table (renamed from uploads for consistency)
CREATE TABLE IF NOT EXISTS files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes <= 104857600), -- 100MB limit
    bucket VARCHAR(100) NOT NULL,
    object_key VARCHAR(500) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indexed_at TIMESTAMP,
    error_message TEXT,
    CHECK (status IN ('pending', 'processing', 'indexed', 'failed'))
);

-- Chunks table
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID REFERENCES files(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL CHECK (length(content) > 0),
    token_count INTEGER NOT NULL CHECK (token_count <= 2000),
    start_char INTEGER,
    end_char INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (file_id, chunk_index)
);

-- ============================================================================
-- Ingestion Jobs
-- ============================================================================

-- Ingestion jobs table (complements Redis Streams)
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY,
    file_id UUID REFERENCES files(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'queued',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    worker_id VARCHAR(100),
    CHECK (status IN ('queued', 'processing', 'completed', 'failed'))
);

-- ============================================================================
-- Indexes for Performance
-- ============================================================================

-- Users indexes
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(is_active) WHERE is_active = true;

-- User roles indexes
CREATE INDEX IF NOT EXISTS idx_user_roles_user ON user_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id);

-- Files indexes
CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_id);
CREATE INDEX IF NOT EXISTS idx_files_status ON files(status);
CREATE INDEX IF NOT EXISTS idx_files_uploaded ON files(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_files_object_key ON files(bucket, object_key);

-- Chunks indexes
CREATE INDEX IF NOT EXISTS idx_chunks_file ON chunks(file_id);
CREATE INDEX IF NOT EXISTS idx_chunks_file_index ON chunks(file_id, chunk_index);

-- Ingestion jobs indexes
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_file ON ingestion_jobs(file_id);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_started ON ingestion_jobs(started_at DESC);

-- ============================================================================
-- Default Roles
-- ============================================================================

-- Insert default roles with permissions
INSERT INTO roles (name, permissions, description) VALUES
    (
        'admin',
        '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": true, "manage_roles": true}}'::jsonb,
        'Full platform access including user management'
    ),
    (
        'user',
        '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}'::jsonb,
        'Standard user with file upload, search, and agent access'
    ),
    (
        'readonly',
        '{"file": {"upload": false, "read": true, "delete": false}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}'::jsonb,
        'Read-only access for searching and viewing files'
    )
ON CONFLICT (name) DO NOTHING;

-- ============================================================================
-- Record Migration
-- ============================================================================

INSERT INTO schema_migrations (version, name) VALUES (1, 'initial_schema')
ON CONFLICT (version) DO NOTHING;

-- ============================================================================
-- Verification Queries (for manual testing)
-- ============================================================================

-- Uncomment to verify migration
-- SELECT version, name, applied_at FROM schema_migrations ORDER BY version;
-- SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
-- SELECT indexname FROM pg_indexes WHERE schemaname = 'public' ORDER BY indexname;

