---
title: "Data API Migration Guide"
category: "administrator"
order: 25
description: "Migration from ingest-api to data-api with structured data documents"
published: true
---

# Data API Migration Guide

## Overview

This document describes the migration from `ingest-api` to `data-api`. The service has been expanded to support structured data documents in addition to file ingestion.

## What Changed

### New Capabilities

1. **Structured Data Documents** - Create and manage data documents similar to Notion/Coda databases
2. **SQL-like Query Engine** - Query records with filters, sorting, aggregations
3. **Redis Caching** - High-frequency access caching for data documents
4. **Agent Tools** - New tools for agents to manage persistent data

### API Changes

- Service renamed from `ingest-api` to `data-api` (v2.0.0)
- All existing `/upload`, `/files`, `/search` endpoints remain unchanged
- New `/data` endpoints added for structured data

### New Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/data` | GET | List data documents |
| `/data` | POST | Create data document |
| `/data/{id}` | GET | Get data document |
| `/data/{id}` | PUT | Update data document |
| `/data/{id}` | DELETE | Delete data document |
| `/data/{id}/records` | POST | Insert records |
| `/data/{id}/records` | PUT | Update records |
| `/data/{id}/records` | DELETE | Delete records |
| `/data/{id}/query` | POST | Query records |
| `/data/{id}/schema` | GET/PUT | Schema management |
| `/data/{id}/cache` | GET/POST/DELETE | Cache management |

## Database Migration

The migration adds new columns to `ingestion_files` and creates supporting tables:

```bash
# Migration file (srv/ingest was renamed to srv/data)
srv/data/migrations/004_structured_data.sql
```

**New columns in `ingestion_files`:**
- `doc_type` - Discriminator: 'file' or 'data'
- `data_schema` - Optional JSONB schema definition
- `data_content` - JSONB array of records
- `data_indexes` - Query optimization hints
- `data_version` - Optimistic locking version
- `data_record_count` - Cached record count
- `data_modified_at` - Last data modification timestamp

**New tables:**
- `data_document_cache` - Tracks Redis-cached documents
- `data_record_history` - Audit trail for record changes

## Deployment Steps

### Phase 1: Database Migration (Non-Breaking)

1. Apply the migration to add new columns:
   ```bash
   cd provision/ansible
   make deploy-postgres
   ```

   The migration is automatically applied by the service on startup.

### Phase 2: Deploy Updated Service

1. Deploy the updated ingest service:
   ```bash
   make deploy-ingest
   ```

2. Verify the new endpoints are available:
   ```bash
   curl http://ingest-lxc:8002/data
   ```

### Phase 3: Update Clients (Optional)

Update client code to use the new data endpoints if needed:
- `@jazzmind/busibox-app` - Add DataClient
- Agent tools - Already included in deployment

### Phase 4: Full Rename (Optional)

A comprehensive rename script is available when you're ready to fully transition:

```bash
# Dry run - shows what would be changed
./scripts/refactor/rename-ingest-to-data.sh

# Execute the rename
./scripts/refactor/rename-ingest-to-data.sh --execute
```

The script handles:
1. **Ansible Roles** - Copies `roles/ingest` to `roles/data`, renames templates
2. **Docker Compose** - Updates service names, hostnames, volumes
3. **Inventory Files** - Updates host groups and group_vars
4. **Scripts** - Updates service references in deployment scripts
5. **Proxmox/LXC** - Updates container names and IPs
6. **Documentation** - Updates references in markdown files

**After running the script:**
1. Review changes: `git diff`
2. Test locally
3. Update DNS entries on Proxmox host
4. Deploy to test environment first
5. Commit and push changes

## Rollback

The migration is additive and non-breaking. To rollback:

1. Deploy the previous service version
2. New columns/tables can be left in place (ignored by old code)

## Testing

### Verify File Operations

```bash
# Existing file upload should still work
curl -X POST http://ingest-lxc:8002/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test.pdf"
```

### Verify Data Operations

```bash
# Create a data document
curl -X POST http://ingest-lxc:8002/data \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Test Tasks",
    "schema": {
      "fields": {
        "name": {"type": "string", "required": true},
        "done": {"type": "boolean"}
      }
    },
    "initialRecords": [
      {"name": "Task 1", "done": false}
    ]
  }'

# Query records
curl -X POST http://ingest-lxc:8002/data/{document_id}/query \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "where": {"field": "done", "op": "eq", "value": false},
    "limit": 10
  }'
```

## Agent Tool Usage

Agents can now use these tools:
- `create_data_document` - Create a new data document
- `query_data` - Query records with filters
- `insert_records` - Add new records
- `update_records` - Modify existing records
- `delete_records` - Remove records
- `list_data_documents` - List available documents
- `get_data_document` - Get document details

Example agent interaction:
```
User: Create a task list and add "Review PR" as a pending task

Agent: [Calls create_data_document]
       [Calls insert_records]
       Done! Created "Task List" with 1 task.
```

## Security Notes

- Data documents use the same RLS policies as files
- Personal documents are owner-only access
- Shared documents use role-based access
- All existing authentication/authorization applies

## Support

For issues or questions:
1. Check service logs: `journalctl -u ingest-api`
2. Verify database migration: Check for `doc_type` column in `ingestion_files`
3. Test endpoints via Swagger UI: `http://ingest-lxc:8002/docs`
