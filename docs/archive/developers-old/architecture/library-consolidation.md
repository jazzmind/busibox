---
title: "Library Management Consolidation"
category: "developer"
order: 23
description: "Consolidation of library management from AI Portal into ingest-api"
published: true
---

# Library Management Consolidation

**Status**: Implemented (Phase 1-4, Zero Trust integrated)  
**Created**: 2026-01-21  
**Updated**: 2026-01-21  
**Category**: Architecture

## Overview

This document describes the consolidation of library management from AI Portal into ingest-api. This change eliminates cross-service dependencies for file operations and simplifies the architecture.

## Background

### Previous Architecture

Library management was split between two services:

- **AI Portal** (Prisma/PostgreSQL): Owned `Library`, `Document`, `LibraryTagCache` tables
- **Ingest-API** (PostgreSQL): Owned `ingestion_files`, `ingestion_status`, `document_roles` tables

This caused several problems:

1. **Cross-service dependency**: Ingest-API had to call AI Portal to resolve folder names to library IDs
2. **Network failures**: If AI Portal was unavailable, file operations would fail
3. **Data duplication**: Document metadata existed in both databases
4. **Circular dependencies**: Services depended on each other in ways that complicated deployment

### New Architecture

Library management now lives in ingest-api:

```
┌─────────────────┐      ┌─────────────────┐
│   Agent API     │      │   AI Portal     │
│   (tasks)       │      │   (UI)          │
└────────┬────────┘      └────────┬────────┘
         │                        │
         │                        │ (proxy only)
         ▼                        ▼
┌─────────────────────────────────────────┐
│            Ingest API                    │
│  - File ingestion                        │
│  - Library management  ◄─── NEW          │
│  - File serving                          │
│  - Document processing                   │
└────────────────┬────────────────────────┘
                 │
                 ▼
         ┌───────────────┐
         │   Files DB    │
         │  (PostgreSQL) │
         └───────────────┘
```

## Implementation

### Phase 1: Database Schema (Completed)

Added library tables to ingest DB in `srv/ingest/src/schema.py`:

```sql
-- Libraries table
CREATE TABLE IF NOT EXISTS libraries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    is_personal BOOLEAN DEFAULT false,
    user_id UUID,  -- Only set for personal libraries
    library_type VARCHAR(20),  -- 'DOCS', 'RESEARCH', 'TASKS'
    created_by UUID NOT NULL,
    deleted_at TIMESTAMP,  -- Soft delete
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(user_id, library_type)
);

-- Library tag cache
CREATE TABLE IF NOT EXISTS library_tag_cache (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    library_id UUID UNIQUE REFERENCES libraries(id) ON DELETE CASCADE,
    version INTEGER DEFAULT 1,
    groups JSONB,
    generated_at TIMESTAMP DEFAULT NOW()
);

-- Migration: Add library_id to ingestion_files
ALTER TABLE ingestion_files 
ADD COLUMN IF NOT EXISTS library_id UUID REFERENCES libraries(id) ON DELETE SET NULL;
```

### Phase 2: Library Service (Completed)

Created `srv/ingest/src/api/services/library_service.py` with:

- `get_or_create_personal_library(user_id, library_type)` - Auto-creates DOCS/RESEARCH/TASKS
- `get_library_by_folder(user_id, folder_name)` - Resolves folder aliases
- `list_user_libraries(user_id, include_shared)` - Lists accessible libraries
- `create_library(name, created_by, ...)` - Creates shared libraries
- `update_library(library_id, name)` - Updates library name
- `delete_library(library_id, soft_delete)` - Soft or hard delete
- `ensure_all_personal_libraries(user_id)` - Creates all personal library types

### Phase 3: API Routes (Completed)

Created `srv/ingest/src/api/routes/libraries.py` with endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/libraries` | List user's libraries |
| POST | `/libraries` | Create a library |
| GET | `/libraries/{id}` | Get library by ID |
| GET | `/libraries/by-folder?folder=name` | Resolve folder to library |
| PUT | `/libraries/{id}` | Update library |
| DELETE | `/libraries/{id}` | Delete library |
| POST | `/libraries/ensure-personal` | Ensure all personal libraries exist |

### Phase 3b: Content Route Update (Completed)

Updated `srv/ingest/src/api/routes/content.py` to use local library resolution instead of calling AI Portal.

Before:
```python
# Called AI Portal API
library_id = await _resolve_library_from_folder(
    folder=body.folder,
    auth_header=auth_header,
    config=config,
)
```

After:
```python
# Uses local LibraryService
library_id = await _resolve_library_from_folder(
    folder=body.folder,
    user_id=user_id,
    pg_service=pg_service,
)
```

## Folder Name Mapping

The following folder names are supported for personal libraries:

| Folder Name | Library Type | Library Name |
|-------------|--------------|--------------|
| `personal`, `personal-docs`, `docs` | DOCS | Personal |
| `personal-research`, `research` | RESEARCH | Research |
| `personal-tasks`, `tasks` | TASKS | Tasks |

## Migration Plan

### Current State (Phase 4 Complete)

- Ingest-API manages libraries independently
- AI Portal proxies `/libraries/by-folder` to ingest-api
- AI Portal syncs library creation to ingest-api
- Migration script available for bulk data transfer
- Both systems can operate independently

### Phase 4: Data Migration & Proxy (Completed)

#### Automatic Migration on Startup

The ingest-api container automatically migrates libraries from AI Portal on startup.

**How it works:**
1. On container startup, after schema is applied, the migration service runs
2. It connects to AI Portal's database (if configured)
3. Imports libraries that don't already exist in ingest-api (idempotent)
4. Updates `library_id` in `ingestion_files` based on AI Portal's Document table

**Configuration:**
Set these environment variables for the ingest-api container:
```bash
AI_PORTAL_DB_HOST=postgres
AI_PORTAL_DB_PORT=5432
AI_PORTAL_DB_NAME=ai_portal
AI_PORTAL_DB_USER=busibox_user
AI_PORTAL_DB_PASSWORD=devpassword
```

If not configured, migration is skipped and libraries are created on-demand.

#### Manual Migration Script

For manual migration or debugging, a script is also available:

```bash
# Dry run to see what would be migrated
python scripts/migrations/migrate_libraries_to_ingest.py --dry-run --verbose

# Run migration
python scripts/migrations/migrate_libraries_to_ingest.py --verbose
```

Environment variables:
- `AI_PORTAL_DB_URL` or individual `AI_PORTAL_DB_*` variables
- `INGEST_DB_URL` or individual `INGEST_DB_*` variables

#### AI Portal Updates

**`getUserLibraries`**:
- Exchanges session JWT for ingest-api access token (Zero Trust)
- Calls `GET /libraries` endpoint on ingest-api
- Document counts included in response

**`getLibraryDocuments`**:
- Exchanges session JWT for ingest-api access token (Zero Trust)
- Calls `GET /libraries/{id}/documents` endpoint on ingest-api
- No fallback to local Prisma (data is in ingest-api)

#### Ingest-API Updates

**GET /libraries**:
- Requires JWT authentication (`require_ingest_read` dependency)
- Returns libraries with document counts

**GET /libraries/{id}/documents**:
- Requires JWT authentication (`require_ingest_read` dependency)  
- Uses `LibraryService.acquire_with_rls()` for RLS-protected queries
- RLS session variables set via `busibox_common.auth.set_rls_session_vars`

**RLS Integration**:
```python
# LibraryService uses busibox_common for RLS
from busibox_common.auth import set_rls_session_vars, WorkerRLSContext

@asynccontextmanager
async def acquire_with_rls(self, request):
    """Get connection with RLS session variables set."""
    async with self.pool.acquire() as conn:
        await set_rls_session_vars(conn, request)
        yield conn
```

### Phase 5: Zero Trust Integration (Completed)

AI Portal now queries ingest-api for all library data:

1. **`getUserLibraries`**: Calls `GET /libraries` on ingest-api with token exchange
2. **`getLibraryDocuments`**: Calls `GET /libraries/{id}/documents` with token exchange
3. **RLS enforcement**: LibraryService uses `busibox_common.auth.set_rls_session_vars`
4. **No fallbacks**: AI Portal no longer falls back to local Prisma for document queries

**Token Exchange Flow:**
```
AI Portal → exchangeWithSubjectToken(sessionJwt, 'ingest-api') → AuthZ Service
                                                                    ↓
AI Portal ← accessToken (audience: ingest-api) ←─────────────────────┘
    ↓
Ingest-API (validates JWT, extracts user_id, sets RLS session vars)
    ↓
PostgreSQL (RLS policies filter by app.user_id)
```

### Future Work (Phase 6)

1. **Schema Cleanup**: Remove Library/Document models from AI Portal Prisma schema
2. **Service Rename**: Rename `ingest-api` to `files-api`
3. **Write Path Migration**: Route library creation/updates through ingest-api

## Testing

Integration tests are in `srv/ingest/tests/integration/test_libraries.py`:

- Personal library auto-creation
- Folder name resolution
- Library CRUD operations
- Authorization enforcement
- Idempotency checks

Run tests:
```bash
make test-local SERVICE=ingest
```

## API Examples

### Resolve folder to library

```bash
curl -X GET "http://ingest-api:8002/libraries/by-folder?folder=personal-tasks" \
  -H "Authorization: Bearer $TOKEN"
```

Response:
```json
{
  "data": {
    "library": {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "name": "Tasks",
      "isPersonal": true,
      "userId": "user-123",
      "libraryType": "TASKS",
      "createdAt": "2026-01-21T00:00:00.000Z"
    }
  }
}
```

### List libraries

```bash
curl -X GET "http://ingest-api:8002/libraries" \
  -H "Authorization: Bearer $TOKEN"
```

### Create shared library

```bash
curl -X POST "http://ingest-api:8002/libraries" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Team Documents", "is_personal": false}'
```

## Related Documentation

- [Ingest Service Architecture](./ingest-service.md)
- [Document Libraries](../guides/document-libraries.md)
- [Agent Task Output](../guides/agent-tasks.md)
