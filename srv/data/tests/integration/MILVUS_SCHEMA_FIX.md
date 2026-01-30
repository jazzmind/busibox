# Milvus Schema Fix Required

## Problem

The integration tests are failing because the existing `document_embeddings` collection in Milvus has an **incompatible schema**.

### Current Schema (WRONG)
```
Collection: document_embeddings
Fields:
  - id (VARCHAR, primary)
  - vector (FLOAT_VECTOR)
  - file_id (VARCHAR)
  - chunk_id (VARCHAR)
  - model_name (VARCHAR)
  - created_at (INT64)
```

### Required Schema (CORRECT)
```
Collection: documents
Fields:
  - id (VARCHAR, primary)
  - file_id (VARCHAR)
  - chunk_index (INT64)
  - page_number (INT64)
  - modality (VARCHAR)
  - text (VARCHAR) - for BM25 sparse vector generation
  - text_dense (FLOAT_VECTOR, 1536 dims)
  - text_sparse (SPARSE_FLOAT_VECTOR) - auto-generated
  - page_vectors (FLOAT_VECTOR, 16384 dims) - for ColPali
  - user_id (VARCHAR)
  - metadata (JSON)
```

## Solution

### 1. Run Migration on Test Environment

Deploy the Milvus role to run the migration:

```bash
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus
```

The migration script will:
1. Check for old `document_embeddings` collection
2. Drop it if schema is incompatible (⚠️ **DATA LOSS**)
3. Run `hybrid_schema.py` to create new `documents` collection

### 2. Environment Configuration

Ensure `.env` has:
```bash
MILVUS_HOST=10.96.201.204
MILVUS_PORT=19530
MILVUS_COLLECTION=documents
```

### 3. Verify Schema

After migration, verify the schema:

```bash
ssh root@10.96.201.204
/opt/milvus-tools/bin/python << 'EOF'
from pymilvus import connections, Collection
connections.connect('default', host='localhost', port='19530')
coll = Collection('documents')
print('Schema fields:')
for field in coll.schema.fields:
    print(f'  - {field.name}: {field.dtype}')
connections.disconnect('default')
EOF
```

### 4. Run Tests

Once schema is fixed, integration tests should pass:

```bash
cd srv/ingest
python -m pytest tests/integration/test_services.py::test_milvus_service -v
```

## TDD Principle

The **tests define the contract**. The `MilvusService` code is correct - it implements the hybrid search schema as designed. The infrastructure (Milvus collection) needs to match the test expectations.

## Files Changed

1. **Migration Script**: `provision/ansible/roles/milvus/files/migrate_schema.py`
   - Checks and drops incompatible collection
   
2. **Ansible Playbook**: `provision/ansible/roles/milvus/tasks/main.yml`
   - Runs migration before schema creation
   
3. **Config**: `srv/ingest/src/shared/config.py`
   - Default collection name is now `documents`

## Next Steps

1. User deploys migration to test environment
2. Verify schema is correct
3. Re-run integration tests
4. Deploy to production when ready

