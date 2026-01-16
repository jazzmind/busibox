# Security Enhancement Plan: Encryption at Rest

**Created**: 2026-01-15  
**Status**: Planning  
**Priority**: High  
**Owner**: Security Team

---

## Executive Summary

This plan addresses critical security gaps in our data-at-rest protection. Currently:
- **PostgreSQL**: RLS enforced but data stored in plaintext
- **Milvus**: Plaintext document chunks stored in `text` field, accessible without authentication
- **MinIO**: Files stored unencrypted with path-based access

**Goal**: Implement envelope encryption for all sensitive data, remove plaintext from Milvus, and evaluate embedding security against inversion attacks.

---

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         CURRENT STATE (VULNERABLE)                       │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │  PostgreSQL │     │   Milvus    │     │    MinIO    │                │
│  │             │     │             │     │             │                │
│  │ • Metadata  │     │ • Vectors   │     │ • Files     │                │
│  │ • Chunks    │◄───►│ • TEXT(!)   │     │ • Markdown  │                │
│  │ • Keywords  │     │ • user_id   │     │ • Images    │                │
│  │             │     │             │     │             │                │
│  │ [PLAINTEXT] │     │ [PLAINTEXT] │     │ [PLAINTEXT] │                │
│  └─────────────┘     └─────────────┘     └─────────────┘                │
│        ▲                   ▲                   ▲                        │
│        │                   │                   │                        │
│        └───────────────────┴───────────────────┘                        │
│                            │                                            │
│                    All data accessible to:                              │
│                    • Database admins                                    │
│                    • Backup theft                                       │
│                    • Physical access                                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TARGET STATE (SECURE)                            │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌───────────────────────────────────────────────────────────┐          │
│  │                    AuthZ Service                           │          │
│  │  ┌─────────────────────────────────────────────────────┐  │          │
│  │  │         Envelope Encryption Service                  │  │          │
│  │  │                                                      │  │          │
│  │  │  AUTHZ_MASTER_KEY (env) ──► KEKs (per role/user)    │  │          │
│  │  │                                ▼                     │  │          │
│  │  │                            DEKs (per document)       │  │          │
│  │  │                                ▼                     │  │          │
│  │  │                          AES-256-GCM                 │  │          │
│  │  └─────────────────────────────────────────────────────┘  │          │
│  └───────────────────────────────────────────────────────────┘          │
│                              │                                           │
│                              ▼                                           │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐                │
│  │  PostgreSQL │     │   Milvus    │     │    MinIO    │                │
│  │             │     │             │     │             │                │
│  │ • Metadata  │     │ • Vectors   │     │ • Files     │                │
│  │ • Encrypted │     │ • file_id   │     │ [ENCRYPTED] │                │
│  │   chunks    │     │ • chunk_idx │     │             │                │
│  │ • DEK refs  │     │             │     │             │                │
│  │             │     │ [NO TEXT!]  │     │             │                │
│  │ [ENCRYPTED] │     │             │     │             │                │
│  └─────────────┘     └─────────────┘     └─────────────┘                │
│                                                                          │
│  Security: Data unreadable without user's KEK access                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 1: Consolidate Encryption in AuthZ

### 1.1 Current State (Already Implemented)

The `EnvelopeEncryptionService` already exists in `srv/authz/src/services/encryption.py`:

```python
# Key hierarchy already implemented:
# Master Key (from AUTHZ_MASTER_KEY env) 
#   └── KEKs (per role/user, stored encrypted in authz_key_encryption_keys)
#       └── DEKs (per document, wrapped in authz_wrapped_data_keys)
```

**Database tables exist:**
- `authz_key_encryption_keys` - KEKs for roles/users
- `authz_wrapped_data_keys` - DEKs wrapped with KEKs

**Status**: ✅ Infrastructure exists but is not activated

### 1.2 Required Additions

#### 1.2.1 Add Encryption API Endpoints to AuthZ

Create new routes in `srv/authz/src/routes/encryption.py`:

```python
# POST /encryption/keks - Create KEK for role/user
# GET /encryption/keks/{owner_type}/{owner_id} - Get KEK for owner
# POST /encryption/deks - Wrap a new DEK for a document
# GET /encryption/deks/{file_id} - Get wrapped DEK for user's access
# POST /encryption/decrypt - Decrypt data (internal only)
```

#### 1.2.2 Auto-create KEKs on Role/User Creation

Hook into role and user creation to automatically generate KEKs:

```python
async def create_role(...):
    # Existing logic...
    
    # Generate KEK for new role
    kek = encryption_service.generate_kek()
    encrypted_kek = encryption_service.encrypt_kek(kek)
    await postgres.create_kek(
        owner_type="role",
        owner_id=role_id,
        encrypted_key=encrypted_kek,
    )
```

#### 1.2.3 Bootstrap System KEK

On startup, ensure a system-level KEK exists:

```python
async def ensure_system_kek():
    existing = await postgres.get_kek_for_owner("system", None)
    if not existing:
        kek = encryption_service.generate_kek()
        encrypted_kek = encryption_service.encrypt_kek(kek)
        await postgres.create_kek(
            owner_type="system",
            owner_id=None,
            encrypted_key=encrypted_kek,
        )
```

### 1.3 Tasks

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Create `/routes/encryption.py` with KEK/DEK endpoints | High | 2d | None |
| Add KEK auto-generation on role creation | High | 0.5d | encryption routes |
| Add KEK auto-generation on user creation | High | 0.5d | encryption routes |
| Bootstrap system KEK on startup | High | 0.5d | None |
| Add key rotation endpoint | Medium | 1d | encryption routes |
| Add integration tests | High | 1d | All above |

---

## Phase 2: Encrypt PostgreSQL Data at Rest

### 2.1 Strategy: Application-Layer Encryption with Native Decryption

We'll use PostgreSQL's `pgcrypto` extension for performance-critical decrypt operations while keeping key management in AuthZ.

**Why this approach:**
- Keys never leave AuthZ (zero-trust)
- Decryption can happen in SQL for JOIN performance
- RLS still works on non-encrypted columns (file_id, user_id, visibility)
- Bulk operations remain efficient

### 2.2 Schema Changes

#### 2.2.1 New Encrypted Columns

```sql
-- Add encrypted columns to ingestion_chunks
ALTER TABLE ingestion_chunks ADD COLUMN encrypted_text BYTEA;
ALTER TABLE ingestion_chunks ADD COLUMN encryption_nonce BYTEA;
ALTER TABLE ingestion_chunks ADD COLUMN dek_id UUID;

-- Add encrypted columns to ingestion_files (for sensitive metadata)
ALTER TABLE ingestion_files ADD COLUMN encrypted_keywords BYTEA;
ALTER TABLE ingestion_files ADD COLUMN encrypted_title BYTEA;
ALTER TABLE ingestion_files ADD COLUMN keywords_nonce BYTEA;
ALTER TABLE ingestion_files ADD COLUMN title_nonce BYTEA;
```

#### 2.2.2 DEK Reference Table (in busibox DB, not authz)

```sql
-- Local reference to DEKs (actual DEK stored in authz)
CREATE TABLE document_encryption (
    file_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id),
    dek_id UUID NOT NULL,  -- Reference to authz_wrapped_data_keys
    algorithm TEXT NOT NULL DEFAULT 'AES-256-GCM',
    encrypted_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
```

### 2.3 Stored Procedures for Decryption

```sql
-- Enable pgcrypto
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Decrypt function using session-provided DEK
CREATE OR REPLACE FUNCTION decrypt_text(
    encrypted_data BYTEA,
    nonce BYTEA,
    dek BYTEA
) RETURNS TEXT AS $$
BEGIN
    RETURN convert_from(
        pgp_sym_decrypt(
            encrypted_data,
            encode(dek, 'hex'),
            'cipher-algo=aes256'
        ),
        'UTF8'
    );
EXCEPTION WHEN OTHERS THEN
    RETURN NULL;  -- Return NULL on decryption failure
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- View that decrypts chunks using session DEK
CREATE OR REPLACE VIEW decrypted_chunks AS
SELECT 
    c.chunk_id,
    c.file_id,
    c.chunk_index,
    CASE 
        WHEN current_setting('app.dek', true) IS NOT NULL 
        THEN decrypt_text(c.encrypted_text, c.encryption_nonce, 
                         decode(current_setting('app.dek', true), 'hex'))
        ELSE NULL
    END as text,
    c.page_number,
    c.section_heading
FROM ingestion_chunks c;
```

### 2.4 Encryption Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      DOCUMENT INGESTION FLOW                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. Worker receives document for ingestion                               │
│     └── user_id, role_ids known                                         │
│                                                                          │
│  2. Worker requests DEK from AuthZ                                       │
│     POST /encryption/deks                                                │
│     {                                                                    │
│       "file_id": "...",                                                  │
│       "owner_type": "personal" | "role",                                │
│       "owner_id": "user_id" | "role_id"                                 │
│     }                                                                    │
│     └── AuthZ generates DEK, wraps with owner's KEK(s)                  │
│     └── Returns: { "dek": "base64...", "dek_id": "..." }                │
│                                                                          │
│  3. Worker encrypts chunks with DEK                                      │
│     └── Each chunk: encrypted_text = AES-256-GCM(text, dek)             │
│     └── Store nonce with each encrypted chunk                           │
│                                                                          │
│  4. Worker stores encrypted chunks in PostgreSQL                         │
│     └── encrypted_text, encryption_nonce, dek_id                        │
│                                                                          │
│  5. Worker stores vectors in Milvus (NO TEXT!)                          │
│     └── Only: file_id, chunk_index, embeddings, page_number             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.5 Decryption Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        SEARCH/RETRIEVE FLOW                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. User searches via API (JWT contains user_id, role_ids)              │
│                                                                          │
│  2. Milvus returns: file_id, chunk_index, score                         │
│     └── NO plaintext (text field removed from Milvus)                   │
│                                                                          │
│  3. API needs to enrich with text:                                       │
│     a. For each unique file_id, request DEK from AuthZ                  │
│        GET /encryption/deks/{file_id}                                   │
│        └── AuthZ checks user has access (via role/user KEK)             │
│        └── Returns unwrapped DEK (or 403 if no access)                  │
│                                                                          │
│     b. Set DEK in PostgreSQL session                                    │
│        SET app.dek = '{dek_hex}'                                        │
│                                                                          │
│     c. Query decrypted_chunks view (RLS + decryption)                   │
│        SELECT text FROM decrypted_chunks                                │
│        WHERE file_id = ... AND chunk_index IN (...)                     │
│                                                                          │
│  4. Return enriched results with decrypted text                         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.6 Tasks

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Add schema migration for encrypted columns | High | 0.5d | None |
| Create pgcrypto decrypt stored procedures | High | 1d | Schema |
| Create decrypted_chunks view | High | 0.5d | Stored procs |
| Modify ingest worker to encrypt chunks | High | 2d | Phase 1 |
| Modify search API to fetch DEKs and decrypt | High | 2d | Phase 1 |
| Migration script for existing data | Medium | 2d | All above |
| Performance testing | High | 1d | All above |

---

## Phase 3: Remove Plaintext from Milvus

### 3.1 Current Milvus Schema

```python
# Current (INSECURE)
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="chunk_index", dtype=DataType.INT32),
    FieldSchema(name="page_number", dtype=DataType.INT32),
    FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="modality", dtype=DataType.VARCHAR, max_length=20),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),  # REMOVE THIS
    FieldSchema(name="text_dense", dtype=DataType.FLOAT_VECTOR, dim=1024),
    FieldSchema(name="text_sparse", dtype=DataType.SPARSE_FLOAT_VECTOR),
    FieldSchema(name="metadata", dtype=DataType.JSON),
]
```

### 3.2 New Milvus Schema

```python
# New (SECURE - text removed)
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="file_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="chunk_index", dtype=DataType.INT32),
    FieldSchema(name="page_number", dtype=DataType.INT32),
    FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="modality", dtype=DataType.VARCHAR, max_length=20),
    # NO TEXT FIELD - retrieve from PostgreSQL
    FieldSchema(name="text_dense", dtype=DataType.FLOAT_VECTOR, dim=1024),
    FieldSchema(name="text_sparse", dtype=DataType.SPARSE_FLOAT_VECTOR),
    # Reduced metadata - no content hashes or section headings
    FieldSchema(name="metadata", dtype=DataType.JSON),  # Only: language
]
```

### 3.3 BM25 Keyword Search Consideration

**Problem**: Milvus BM25 requires the `text` field for sparse vector generation.

**Solutions**:

1. **Pre-compute sparse vectors at ingestion time** (Recommended)
   - Generate sparse vectors in Python before insertion
   - Store only the sparse vectors, not the source text
   - Milvus can still search on sparse vectors

2. **Move keyword search to PostgreSQL**
   - Use PostgreSQL full-text search on encrypted-then-decrypted text
   - Less efficient but more secure

**Recommendation**: Option 1 - pre-compute sparse vectors. The ingest worker already generates embeddings; add sparse vector generation there.

### 3.4 Tasks

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Update Milvus collection schema (remove text) | High | 0.5d | None |
| Modify ingest worker to pre-compute sparse vectors | High | 1d | None |
| Update search API to fetch text from PostgreSQL | High | 1d | Phase 2 |
| Migration: Create new collection, migrate vectors | Medium | 1d | All above |
| Remove old collection | Low | 0.5d | Migration verified |

---

## Phase 4: Embedding Security Assessment

### 4.1 Threat Model: Embedding Inversion Attacks

**What are embedding inversion attacks?**

Research shows that dense vector embeddings can be "inverted" to reconstruct the original text with varying degrees of accuracy. Attackers use:

1. **Optimization-based inversion**: Find text whose embedding is similar to target
2. **Learning-based decoders**: Train models on embedding-text pairs to reconstruct text

**Risk Assessment for Busibox:**

| Attack Vector | Our Exposure | Risk Level |
|---------------|--------------|------------|
| Direct embedding access | Milvus accessible on internal network | **HIGH** |
| Embedding + inversion model | Pre-trained models exist for common embedders | **MEDIUM** |
| Cross-tenant leakage | Partitioning mitigates but not perfect | **MEDIUM** |
| Query probing | Requires API access with valid JWT | **LOW** |

### 4.2 Current Embedding Model

We use `bge-large-en-v1.5` (1024 dimensions) which is:
- A popular model with known embedding space characteristics
- Potentially vulnerable to inversion attacks trained on similar models
- High-dimensional (harder to invert than low-dim)

### 4.3 Mitigation Strategies

#### 4.3.1 Defense in Depth (Recommended - Low Effort)

Even without modifying embeddings, we can reduce risk:

| Defense | Implementation | Effort |
|---------|----------------|--------|
| Network isolation | Milvus only accessible from app containers | Low |
| Remove text from Milvus | Already planned in Phase 3 | Medium |
| Audit logging | Log all Milvus queries | Low |
| Rate limiting | Limit query volume per user | Low |

#### 4.3.2 Embedding Perturbation (Optional - Medium Effort)

Add noise to embeddings to reduce inversion accuracy while maintaining search quality:

```python
def perturb_embedding(embedding: np.ndarray, epsilon: float = 0.05) -> np.ndarray:
    """
    Add calibrated noise to embedding.
    
    Based on EntroGuard research - entropy-driven perturbation.
    epsilon controls privacy-utility tradeoff.
    """
    noise = np.random.normal(0, epsilon, embedding.shape)
    perturbed = embedding + noise
    # Re-normalize to unit length
    return perturbed / np.linalg.norm(perturbed)
```

**Trade-offs:**
- **Privacy gain**: ~8x reduction in inversion success (per EntroGuard)
- **Utility loss**: ~2-5% reduction in retrieval accuracy
- **Complexity**: Need to tune epsilon per use case

#### 4.3.3 Encrypted Vector Search (Future - High Effort)

Homomorphic encryption or secure enclaves would allow search on encrypted vectors:

- **Pros**: Vectors never exposed in plaintext
- **Cons**: Significant performance overhead (10-100x slower)
- **Status**: Emerging technology, not production-ready

### 4.4 Recommendation

For Busibox, implement **Defense in Depth (4.3.1)** immediately, with **Embedding Perturbation (4.3.2)** as a Phase 5 enhancement if handling highly sensitive data.

### 4.5 Tasks

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Network isolation for Milvus | High | 0.5d | None |
| Audit logging for Milvus queries | Medium | 1d | None |
| Rate limiting per user | Medium | 1d | None |
| Research perturbation impact on our model | Low | 2d | None |
| Implement optional perturbation | Low | 2d | Research |

---

## Phase 5: MinIO File Encryption

### 5.1 Strategy

Encrypt files before upload to MinIO using the same envelope encryption scheme.

### 5.2 Flow

```
Upload:
1. Generate DEK (or reuse document DEK)
2. Encrypt file with DEK: encrypted_content = AES-256-GCM(file_bytes, dek)
3. Upload encrypted content to MinIO
4. Store wrapped DEK in authz

Download:
1. Fetch wrapped DEK from authz (validates user access)
2. Download encrypted content from MinIO
3. Decrypt: file_bytes = AES-256-GCM.decrypt(encrypted_content, dek)
4. Return to user
```

### 5.3 Tasks

| Task | Priority | Effort | Dependencies |
|------|----------|--------|--------------|
| Modify upload endpoint to encrypt before storage | Medium | 1d | Phase 1 |
| Modify download endpoint to decrypt after retrieval | Medium | 1d | Phase 1 |
| Update ingest worker for encrypted file handling | Medium | 1d | Phase 1 |
| Migration script for existing files | Low | 2d | All above |

---

## Implementation Timeline

```
Week 1-2: Phase 1 - AuthZ Encryption APIs
├── Encryption routes
├── KEK auto-generation
└── Integration tests

Week 3-4: Phase 2 - PostgreSQL Encryption
├── Schema changes
├── Stored procedures
├── Worker modifications
└── Search API updates

Week 5: Phase 3 - Milvus Text Removal
├── Schema update
├── Sparse vector pre-computation
└── Migration

Week 6: Phase 4 - Embedding Security
├── Network isolation
├── Audit logging
└── Rate limiting

Week 7: Phase 5 - MinIO Encryption
├── Upload/download encryption
└── Existing file migration

Week 8: Testing & Rollout
├── End-to-end testing
├── Performance benchmarks
├── Staged rollout
```

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Performance degradation | Medium | High | Benchmark each phase, optimize hot paths |
| Key management complexity | Medium | High | Automated KEK generation, clear documentation |
| Migration data loss | Low | Critical | Backup before migration, dry-run mode |
| Breaking existing integrations | Medium | Medium | Backward compatibility layer during transition |

---

## Success Criteria

1. **Data at rest**: All document text encrypted in PostgreSQL
2. **Milvus**: No plaintext content accessible
3. **MinIO**: All files encrypted before storage
4. **Key management**: KEKs automatically created for roles/users
5. **Performance**: < 20% latency increase on search operations
6. **Audit**: All encryption operations logged

---

## Appendix A: Key Hierarchy

```
AUTHZ_MASTER_KEY (environment variable)
│
├── decrypt
│
└── KEKs (stored encrypted in authz_key_encryption_keys)
    │
    ├── System KEK (owner_type='system', for internal operations)
    │
    ├── User KEKs (owner_type='user', owner_id=user_id)
    │   └── For personal documents
    │
    └── Role KEKs (owner_type='role', owner_id=role_id)
        └── For shared documents
            │
            └── DEKs (stored wrapped in authz_wrapped_data_keys)
                │
                └── Document content (encrypted in PostgreSQL/MinIO)
```

---

## Appendix B: Embedding Inversion Research

Key papers and resources:

1. **EntroGuard** (March 2025) - Entropy-driven perturbation for embedding privacy
   - Up to 8x reduction in inversion attack success
   - https://arxiv.org/abs/2503.12896

2. **TextCrafter** (2025) - Geometry-aware noise injection
   - PII-guided perturbation
   - https://arxiv.org/abs/2509.17302

3. **Eguard** - Projection-based embedding protection
   - ~95% reduction in token inversion
   - https://goatstack.ai/articles/2411.05034

4. **RAG-Thief** - Knowledge extraction from RAG systems
   - Query-based extraction attacks
   - https://arxiv.org/abs/2411.14110

---

## Appendix C: PostgreSQL pgcrypto Functions

```sql
-- AES-256-GCM encryption (requires pgcrypto)
-- Note: pgcrypto uses PGP format internally; for raw AES-GCM, 
-- consider using pgsodium extension instead

-- Alternative: Use pgsodium for better performance
CREATE EXTENSION IF NOT EXISTS pgsodium;

-- Encrypt with libsodium (faster than pgcrypto for GCM)
SELECT pgsodium.crypto_aead_det_encrypt(
    message := 'sensitive text'::bytea,
    additional := ''::bytea,
    key_id := (SELECT id FROM pgsodium.valid_key LIMIT 1),
    nonce := pgsodium.crypto_aead_det_noncegen()
);
```

---

## Review Checklist

- [ ] AuthZ encryption endpoints implemented
- [ ] KEK auto-generation working
- [ ] PostgreSQL schema migrated
- [ ] Stored procedures tested
- [ ] Ingest worker encrypting chunks
- [ ] Search API decrypting results
- [ ] Milvus text field removed
- [ ] Sparse vectors pre-computed
- [ ] Network isolation verified
- [ ] MinIO encryption active
- [ ] Performance benchmarks acceptable
- [ ] Documentation updated
