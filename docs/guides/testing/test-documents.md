---
created: 2025-12-09
updated: 2025-12-09
status: active
category: testing
---

# Test Document Strategy

## Overview

The test document system provides two sets of documents for different testing purposes:

1. **Simple Documents** - Text-based PDFs for RAG search and role permission testing
2. **Complex Documents** - Images, charts, and plans requiring GPU services

## Simple Test Documents (Default)

**Purpose**: Test RAG search, role-based access control, and basic document processing without GPU dependencies.

**Endpoint**: `POST /test-docs/seed`

**Documents**:
- `1706.03762.pdf` - "Attention is All You Need" (Transformer paper) - role: test-role-a
- `2005.14165.pdf` - "Retrieval-Augmented Generation" paper - role: test-role-b
- `2010.11929.pdf` - "Retrieval-Enhanced Transformer" paper - role: test-role-c

**Extraction Method**: pdfplumber (no GPU required)

**Use Cases**:
- Testing role-based document access
- Testing RAG search functionality
- Testing document ingestion pipeline
- CI/CD automated testing
- Test environment without GPU

**Example**:
```bash
curl -X POST http://10.96.201.206:8002/test-docs/seed \
  -H "Authorization: Bearer $TOKEN"
```

## Complex Test Documents (GPU Required)

**Purpose**: Test advanced extraction methods (Marker, ColPali) that require GPU services.

**Endpoint**: `POST /test-docs/seed-complex`

**Documents**:
- `image/cat.jpg` - Image requiring ColPali visual embeddings - role: test-role-a
- `doc08_us_bancorp_q4_2023_presentation/source.pdf` - Charts requiring Marker - role: test-role-b
- `doc1_ny_harbor/W912DS-10-B-0004-Plans.pdf` - CAD drawings requiring ColPali - role: test-role-c

**Extraction Methods**: Marker (GPU), ColPali (GPU)

**Use Cases**:
- Testing Marker PDF extraction with complex layouts
- Testing ColPali visual embeddings
- Testing multimodal search
- Production environment testing
- Performance benchmarking

**Example**:
```bash
curl -X POST http://10.96.201.206:8002/test-docs/seed-complex \
  -H "Authorization: Bearer $TOKEN"
```

## Environment Configuration

### Test Environment (No GPU)

```yaml
# provision/ansible/inventory/test/group_vars/all/00-main.yml
marker_enabled: false
marker_service_url: ""  # Don't call production Marker
```

**Use**: Simple documents only (`/test-docs/seed`)

### Production Environment (GPU Available)

```yaml
# provision/ansible/inventory/production/group_vars/all/00-main.yml
marker_enabled: true
marker_use_gpu: true
marker_gpu_device: cuda:0
```

**Use**: Both simple and complex documents

## Testing Workflow

### 1. Basic RAG Search & Roles Test (No GPU)

```bash
# Deploy to test environment
cd provision/ansible
make ingest INV=inventory/test

# Seed simple documents
make test-ingest

# Verify documents are searchable
curl -X POST http://10.96.201.206:8001/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "attention mechanism", "top_k": 5}'

# Verify role-based access
# (should only return documents matching user's roles)
```

### 2. Advanced Extraction Test (GPU Required)

```bash
# Deploy to production
cd provision/ansible
make ingest

# Seed complex documents
curl -X POST http://10.96.200.206:8002/test-docs/seed-complex \
  -H "Authorization: Bearer $TOKEN"

# Test visual search with ColPali
curl -X POST http://10.96.200.206:8001/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "cat", "top_k": 5, "use_visual": true}'
```

## Document State Persistence

Test document file IDs are persisted in `/srv/ingest/test-docs-state.json`:

```json
{
  "attention-paper": "file-id-123",
  "retrieval-paper": "file-id-456",
  "ret-paper": "file-id-789"
}
```

This allows:
- Idempotent seeding (re-seeding uses existing file IDs)
- Status checking without re-uploading
- Cleanup and re-seeding

## API Endpoints

### GET /test-docs/status

Get ingestion status for all test documents.

**Response**:
```json
{
  "documents": [
    {
      "id": "attention-paper",
      "name": "Attention is All You Need",
      "role": "test-role-a",
      "fileId": "file-id-123",
      "status": "completed",
      "hasTextEmbeddings": true,
      "hasVisualEmbeddings": false
    }
  ],
  "repoPath": "/srv/test-docs"
}
```

### POST /test-docs/seed

Seed simple test documents (text-based PDFs).

**Requirements**: 
- Authorization header
- pdfplumber extraction

**Response**:
```json
{
  "seeded": [
    {
      "id": "attention-paper",
      "name": "Attention is All You Need",
      "role": "test-role-a",
      "fileId": "file-id-123",
      "error": null
    }
  ]
}
```

### POST /test-docs/seed-complex

Seed complex test documents (images, charts, plans).

**Requirements**:
- Authorization header
- GPU services (Marker, ColPali)
- Production environment

**Response**: Same as `/test-docs/seed`

## Error Handling

### pdfplumber Errors

If a PDF has malformed structure:
- Skip problematic pages
- Log warning with page number
- Continue processing other pages
- Return partial text if available

### Missing GPU Services

If Marker/ColPali not available:
- Log warning about remote service failure
- Fall back to pdfplumber
- Continue processing (degraded mode)

### Authentication Errors

If calling production Marker from test:
- Disable `marker_service_url` in test inventory
- Use local pdfplumber only
- Avoid 401 auth errors

## Best Practices

1. **Use simple documents for most testing** - Faster, no GPU dependency
2. **Reserve complex documents for integration testing** - Only when GPU available
3. **Test role-based access with simple documents** - Same RBAC logic applies
4. **Use complex documents for performance benchmarking** - Realistic workload
5. **Keep test document count small** - 3 documents per set is sufficient

## Troubleshooting

### "IndexError: list index out of range" from pdfplumber

**Cause**: Malformed PDF structure

**Fix**: Already handled - pages are skipped, processing continues

### "401 Missing Authorization header" from Marker

**Cause**: Test environment calling production Marker service

**Fix**: Set `marker_service_url: ""` in test inventory

### Documents not searchable after seeding

**Check**:
1. Ingestion status: `GET /test-docs/status`
2. Worker logs: `journalctl -u ingest-worker -n 100`
3. Embedding generation: Check `hasTextEmbeddings` in status
4. Role assignment: Verify user has matching role

## Related Documentation

- [Testing Strategy](testing-strategy.md) - Overall testing approach
- [Extraction Test Targets](extraction-test-targets.md) - Detailed extraction testing
- [Test Environment](../deployment/test-environment.md) - Test environment setup


