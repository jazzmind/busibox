# Data Model: Local LLM Infrastructure Platform

**Feature**: 001-create-an-initial  
**Created**: 2025-10-14  
**Status**: Complete

This document defines the data entities, schemas, relationships, and validation rules for the busibox platform.

## Overview

The busibox platform uses three primary data stores:

1. **PostgreSQL**: Relational data (users, roles, file metadata, chunks, job logs)
2. **MinIO**: Object storage (actual file content, S3-compatible)
3. **Milvus**: Vector database (embeddings for semantic search)
4. **Redis**: Ephemeral data (job queue via Streams, session cache)

##Entity-Relationship Diagram

```
User ──< UserRole >── Role
 │
 └──< File ──< Chunk ──< Embedding (Milvus)
         │         │
         │         └─< IngestionJob (Redis + PG log)
         │
         └── MinIO Object (S3 bucket)
```

---

## Entities

### 1. User (PostgreSQL)

Represents a person or service account accessing the platform.

**Table**: `users`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique user identifier |
| username | VARCHAR(255) | UNIQUE, NOT NULL | Login username |
| email | VARCHAR(255) | UNIQUE, NOT NULL | Email address (validated format) |
| password_hash | VARCHAR(255) | NOT NULL | Bcrypt hashed password |
| is_active | BOOLEAN | DEFAULT true | Account active status |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Account creation time |
| updated_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Last update time |

**Indexes**:
```sql
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_active ON users(is_active) WHERE is_active = true;
```

**Validation Rules**:
- Email must match regex: `^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`
- Username must be 3-50 characters, alphanumeric plus underscore/hyphen
- Password must be hashed with bcrypt (cost factor 12)

**Relationships**:
- → UserRole (many-to-many through join table)
- → File (one-to-many as owner)

---

### 2. Role (PostgreSQL)

Defines a permission set that can be assigned to users.

**Table**: `roles`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique role identifier |
| name | VARCHAR(100) | UNIQUE, NOT NULL | Role name (e.g., "admin", "user", "readonly") |
| permissions | JSONB | NOT NULL | Permission flags as JSON object |
| description | TEXT | NULL | Human-readable description |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Role creation time |

**Permissions Structure** (JSONB):
```json
{
  "file": {
    "upload": true,
    "read": true,
    "delete": true
  },
  "search": {
    "query": true
  },
  "agent": {
    "invoke": true
  },
  "admin": {
    "manage_users": false,
    "manage_roles": false
  }
}
```

**Default Roles**:
```sql
-- Admin role
INSERT INTO roles (name, permissions, description) VALUES (
  'admin',
  '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": true, "manage_roles": true}}',
  'Full platform access including user management'
);

-- Standard user role
INSERT INTO roles (name, permissions, description) VALUES (
  'user',
  '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}',
  'Standard user with file upload, search, and agent access'
);

-- Read-only role
INSERT INTO roles (name, permissions, description) VALUES (
  'readonly',
  '{"file": {"upload": false, "read": true, "delete": false}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}',
  'Read-only access for searching and viewing files'
);
```

**Relationships**:
- → UserRole (many-to-many through join table)

---

### 3. UserRole (PostgreSQL)

Join table linking users to their assigned roles.

**Table**: `user_roles`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| user_id | UUID | FOREIGN KEY REFERENCES users(id) ON DELETE CASCADE | User identifier |
| role_id | UUID | FOREIGN KEY REFERENCES roles(id) ON DELETE CASCADE | Role identifier |
| assigned_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Assignment timestamp |

**Primary Key**: `(user_id, role_id)`

**Indexes**:
```sql
CREATE INDEX idx_user_roles_user ON user_roles(user_id);
CREATE INDEX idx_user_roles_role ON user_roles(role_id);
```

---

### 4. File (PostgreSQL + MinIO)

Represents an uploaded document with metadata in PostgreSQL and content in MinIO.

**Table**: `files`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique file identifier |
| owner_id | UUID | FOREIGN KEY REFERENCES users(id) ON DELETE CASCADE | User who uploaded file |
| filename | VARCHAR(500) | NOT NULL | Original filename |
| content_type | VARCHAR(100) | NOT NULL | MIME type (application/pdf, text/plain, etc.) |
| size_bytes | BIGINT | NOT NULL, CHECK (size_bytes <= 104857600) | File size in bytes (max 100MB) |
| bucket | VARCHAR(100) | NOT NULL | MinIO bucket name |
| object_key | VARCHAR(500) | NOT NULL, UNIQUE | S3 object key (path in bucket) |
| status | VARCHAR(50) | NOT NULL, DEFAULT 'pending' | Processing status |
| uploaded_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Upload timestamp |
| indexed_at | TIMESTAMP | NULL | When embeddings were completed |
| error_message | TEXT | NULL | Error details if status = 'failed' |

**Status Enum**:
- `pending`: Uploaded, awaiting processing
- `processing`: Currently being ingested
- `indexed`: Successfully processed and searchable
- `failed`: Processing failed (see error_message)

**Indexes**:
```sql
CREATE INDEX idx_files_owner ON files(owner_id);
CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_uploaded ON files(uploaded_at DESC);
CREATE INDEX idx_files_object_key ON files(bucket, object_key);
```

**Row-Level Security (RLS)**:
```sql
ALTER TABLE files ENABLE ROW LEVEL SECURITY;

-- Users can see own files
CREATE POLICY files_owner_policy ON files
  FOR ALL
  USING (owner_id = current_user_id());

-- Admins can see all files
CREATE POLICY files_admin_policy ON files
  FOR ALL
  USING (current_user_has_permission('admin.manage_users'));
```

**MinIO Object**:
- Bucket: `documents` (default)
- Object key format: `{user_id}/{file_id}/{filename}`
- Metadata tags: `user-id`, `file-id`, `content-type`

**Validation Rules**:
- Size must be ≤ 100MB (104,857,600 bytes)
- Content type must be in allowed list: `application/pdf`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, `text/plain`, `text/markdown`
- Filename must not contain path traversal characters (`..`, `/`, `\`)

**Relationships**:
- → User (owner)
- → Chunk (one-to-many)
- → IngestionJob (one-to-many)

---

### 5. Chunk (PostgreSQL)

Represents a text segment extracted from a file for embedding.

**Table**: `chunks`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY, DEFAULT gen_random_uuid() | Unique chunk identifier |
| file_id | UUID | FOREIGN KEY REFERENCES files(id) ON DELETE CASCADE | Parent file |
| chunk_index | INTEGER | NOT NULL | Position in file (0-based) |
| content | TEXT | NOT NULL, CHECK (length(content) > 0) | Chunk text content |
| token_count | INTEGER | NOT NULL, CHECK (token_count <= 2000) | Approximate token count |
| start_char | INTEGER | NULL | Character offset in original file |
| end_char | INTEGER | NULL | End character offset |
| created_at | TIMESTAMP | DEFAULT CURRENT_TIMESTAMP | Chunk creation time |

**Unique Constraint**: `(file_id, chunk_index)` - No duplicate chunks per file

**Indexes**:
```sql
CREATE INDEX idx_chunks_file ON chunks(file_id);
CREATE INDEX idx_chunks_file_index ON chunks(file_id, chunk_index);
```

**Row-Level Security (RLS)**:
```sql
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- Inherit permissions from parent file
CREATE POLICY chunks_inherit_file_policy ON chunks
  FOR ALL
  USING (
    file_id IN (
      SELECT id FROM files WHERE owner_id = current_user_id()
    )
  );
```

**Validation Rules**:
- Content must be non-empty
- Token count must be ≤ 2000 (safety limit, typical chunks ~512 tokens)
- chunk_index must be unique within file_id

**Relationships**:
- → File (parent)
- → Embedding (one-to-one in Milvus, linked by chunk.id)

---

### 6. Embedding (Milvus Collection)

Vector representation of a chunk's semantic meaning, stored in Milvus.

**Collection**: `document_embeddings`

**Schema**:
```python
from pymilvus import FieldSchema, CollectionSchema, DataType

fields = [
    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=36),  # Chunk UUID
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=768),  # Embedding vector
    FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=36),  # For filtering
    FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, max_length=36),  # Same as id (redundant for clarity)
    FieldSchema(name="model_name", dtype=DataType.VARCHAR, max_length=100),  # e.g., "sentence-transformers/all-MiniLM-L6-v2"
    FieldSchema(name="created_at", dtype=DataType.INT64),  # Unix timestamp
]

schema = CollectionSchema(fields=fields, description="Document chunk embeddings")
```

**Index**:
```python
index_params = {
    "index_type": "HNSW",
    "metric_type": "L2",  # Euclidean distance (or "IP" for inner product)
    "params": {"M": 16, "efConstruction": 256}
}
```

**Dimension**: 768 (default for `sentence-transformers/all-MiniLM-L6-v2`)
- Can be configured based on selected embedding model
- Common dimensions: 384 (MiniLM), 768 (BERT-base), 1024 (BERT-large)

**Validation Rules**:
- Vector dimension must match collection schema (768)
- id must correspond to existing chunk in PostgreSQL
- model_name must be non-empty

**Relationships**:
- Linked to Chunk by `id` (chunk UUID)
- Linked to File by `file_id` for permission filtering

---

### 7. IngestionJob (Redis Streams + PostgreSQL Log)

Represents an asynchronous file processing job.

**Redis Stream**: `jobs:ingestion`

**Stream Entry Format**:
```json
{
  "job_id": "uuid",
  "file_id": "uuid",
  "bucket": "documents",
  "object_key": "user_id/file_id/filename.pdf",
  "created_at": "2025-10-14T12:34:56Z"
}
```

**Consumer Group**: `workers`

**PostgreSQL Table** (job log): `ingestion_jobs`

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | UUID | PRIMARY KEY | Job identifier (matches Redis stream message ID) |
| file_id | UUID | FOREIGN KEY REFERENCES files(id) ON DELETE CASCADE | File being processed |
| status | VARCHAR(50) | NOT NULL, DEFAULT 'queued' | Job status |
| started_at | TIMESTAMP | NULL | When worker started processing |
| completed_at | TIMESTAMP | NULL | When job finished (success or failure) |
| error_message | TEXT | NULL | Error details if failed |
| retry_count | INTEGER | DEFAULT 0 | Number of retry attempts |
| worker_id | VARCHAR(100) | NULL | Worker that processed job |

**Status Transitions**:
```
queued → processing → completed (success)
queued → processing → failed (error, can retry)
failed → queued (manual retry or automatic retry logic)
```

**Indexes**:
```sql
CREATE INDEX idx_ingestion_jobs_file ON ingestion_jobs(file_id);
CREATE INDEX idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX idx_ingestion_jobs_created ON ingestion_jobs(started_at DESC);
```

**Validation Rules**:
- Status must be one of: `queued`, `processing`, `completed`, `failed`
- completed_at must be NULL if status is `queued` or `processing`
- error_message must be NULL if status is `completed`
- retry_count must be ≥ 0

**Relationships**:
- → File (parent)

---

### 8. LLMProvider (Configuration Only)

**Not stored in database**—defined in configuration files.

**Configuration File**: `/etc/litellm/config.yaml`

**Structure**:
```yaml
model_list:
  - model_name: llama2-7b
    litellm_params:
      model: ollama/llama2
      api_base: http://10.96.200.30:11434
      api_key: dummy
    metadata:
      provider: ollama
      status: active
```

**Fields**:
- `model_name`: Display name for API requests
- `litellm_params.model`: Provider-specific model identifier
- `litellm_params.api_base`: Provider endpoint URL
- `metadata.provider`: Provider type (ollama, vllm, custom)
- `metadata.status`: active | inactive

**Discovery**: liteLLM `/models` endpoint returns available models

---

### 9. Agent (Future Feature - Configuration)

**To be detailed when agent functionality is implemented.**

Placeholder structure:

**Table**: `agents` (future)

| Column | Type | Description |
|--------|------|-------------|
| id | UUID | PRIMARY KEY |
| name | VARCHAR(200) | Agent name |
| workflow_definition | JSONB | Workflow steps/logic |
| permissions | JSONB | Permission scope |
| created_by | UUID | FK to users |
| created_at | TIMESTAMP | Creation time |

---

### 10. Application (nginx Configuration)

**Not stored in database**—defined in nginx config files.

**Configuration**: `/etc/nginx/sites-available/apps.conf`

**Structure**:
```nginx
server {
    listen 80;
    server_name apps.busibox.local;

    location /app1/ {
        proxy_pass http://localhost:3010/;
        proxy_set_header X-User-Id $http_x_user_id;
    }

    location /app2/ {
        proxy_pass http://localhost:3011/;
        auth_request /auth/verify;
    }
}
```

**Metadata** (tracked manually or in future DB):
- Application name
- URL path prefix
- Upstream port
- Authentication requirement (boolean)

---

## Data Migration Scripts

### Migration 001: Initial Schema

**File**: `provision/ansible/roles/postgres/files/migrations/001_initial_schema.sql`

```sql
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Users table
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Roles table
CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) UNIQUE NOT NULL,
    permissions JSONB NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- User-Role join table
CREATE TABLE user_roles (
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID REFERENCES roles(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role_id)
);

-- Files table
CREATE TABLE files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(500) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes <= 104857600),
    bucket VARCHAR(100) NOT NULL,
    object_key VARCHAR(500) NOT NULL UNIQUE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    indexed_at TIMESTAMP,
    error_message TEXT,
    CHECK (status IN ('pending', 'processing', 'indexed', 'failed'))
);

-- Chunks table
CREATE TABLE chunks (
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

-- Ingestion jobs table
CREATE TABLE ingestion_jobs (
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

-- Schema migrations tracking
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert initial migration record
INSERT INTO schema_migrations (version, name) VALUES (1, 'initial_schema');

-- Create indexes
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_active ON users(is_active) WHERE is_active = true;

CREATE INDEX idx_user_roles_user ON user_roles(user_id);
CREATE INDEX idx_user_roles_role ON user_roles(role_id);

CREATE INDEX idx_files_owner ON files(owner_id);
CREATE INDEX idx_files_status ON files(status);
CREATE INDEX idx_files_uploaded ON files(uploaded_at DESC);
CREATE INDEX idx_files_object_key ON files(bucket, object_key);

CREATE INDEX idx_chunks_file ON chunks(file_id);
CREATE INDEX idx_chunks_file_index ON chunks(file_id, chunk_index);

CREATE INDEX idx_ingestion_jobs_file ON ingestion_jobs(file_id);
CREATE INDEX idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX idx_ingestion_jobs_created ON ingestion_jobs(started_at DESC);

-- Insert default roles
INSERT INTO roles (name, permissions, description) VALUES
  ('admin', '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": true, "manage_roles": true}}', 'Full platform access'),
  ('user', '{"file": {"upload": true, "read": true, "delete": true}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}', 'Standard user'),
  ('readonly', '{"file": {"upload": false, "read": true, "delete": false}, "search": {"query": true}, "agent": {"invoke": true}, "admin": {"manage_users": false, "manage_roles": false}}', 'Read-only access');
```

**Rollback**: `provision/ansible/roles/postgres/files/migrations/001_rollback.sql`

```sql
DROP TABLE IF EXISTS ingestion_jobs;
DROP TABLE IF EXISTS chunks;
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS user_roles;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS schema_migrations;
```

### Migration 002: Row-Level Security

**File**: `provision/ansible/roles/postgres/files/migrations/002_add_rls_policies.sql`

```sql
-- Enable RLS on files table
ALTER TABLE files ENABLE ROW LEVEL SECURITY;

-- Helper function to get current user ID from session
CREATE OR REPLACE FUNCTION current_user_id() RETURNS UUID AS $$
BEGIN
    RETURN current_setting('app.current_user_id', true)::UUID;
END;
$$ LANGUAGE plpgsql;

-- Helper function to check permissions
CREATE OR REPLACE FUNCTION current_user_has_permission(perm TEXT) RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS (
        SELECT 1 FROM user_roles ur
        JOIN roles r ON r.id = ur.role_id
        WHERE ur.user_id = current_user_id()
        AND r.permissions @> jsonb_build_object(split_part(perm, '.', 1), jsonb_build_object(split_part(perm, '.', 2), true))
    );
END;
$$ LANGUAGE plpgsql;

-- Policy: users see own files
CREATE POLICY files_owner_policy ON files
  FOR ALL
  USING (owner_id = current_user_id());

-- Policy: admins see all files
CREATE POLICY files_admin_policy ON files
  FOR ALL
  USING (current_user_has_permission('admin.manage_users'));

-- Enable RLS on chunks table
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- Policy: chunks inherit file permissions
CREATE POLICY chunks_inherit_file_policy ON chunks
  FOR ALL
  USING (
    file_id IN (
      SELECT id FROM files WHERE owner_id = current_user_id()
    )
  );

-- Record migration
INSERT INTO schema_migrations (version, name) VALUES (2, 'add_rls_policies');
```

**Rollback**: `provision/ansible/roles/postgres/files/migrations/002_rollback.sql`

```sql
DROP POLICY IF EXISTS files_owner_policy ON files;
DROP POLICY IF EXISTS files_admin_policy ON files;
DROP POLICY IF EXISTS chunks_inherit_file_policy ON chunks;

ALTER TABLE files DISABLE ROW LEVEL SECURITY;
ALTER TABLE chunks DISABLE ROW LEVEL SECURITY;

DROP FUNCTION IF EXISTS current_user_id();
DROP FUNCTION IF EXISTS current_user_has_permission(TEXT);

DELETE FROM schema_migrations WHERE version = 2;
```

---

## Summary

**Total Entities**: 10 (7 database tables, 2 configuration entities, 1 future)

**Storage Distribution**:
- PostgreSQL: 7 tables (users, roles, user_roles, files, chunks, ingestion_jobs, schema_migrations)
- MinIO: File objects (referenced by files table)
- Milvus: 1 collection (document_embeddings, linked to chunks)
- Redis: 1 stream (jobs:ingestion, logged to ingestion_jobs table)

**Key Relationships**:
- Users ↔ Roles (many-to-many)
- Users → Files (one-to-many)
- Files → Chunks (one-to-many)
- Chunks ↔ Embeddings (one-to-one, cross-system)
- Files → IngestionJobs (one-to-many)

**Security**:
- Row-Level Security (RLS) on files and chunks
- Permission checking via roles JSONB
- User-owned data isolation

**Migrations**:
- 001: Initial schema
- 002: Row-Level Security policies

All entities and migrations align with Constitution principles (Infrastructure as Code, Security Isolation, Simplicity).

