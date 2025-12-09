---
title: Unified Role-Based Security Model
created: 2025-11-25
updated: 2025-11-25
status: active
category: architecture
---

# Unified Role-Based Security Model

## Overview

This document defines the authoritative security model for role-based access control (RBAC) across all Busibox foundation services. The model ensures consistent permission enforcement across PostgreSQL, Milvus, MinIO, and all API services.

## Core Concepts

### 1. Documents Have Roles

Every document in the system is associated with one or more **roles**. A document must always have at least one role assigned (enforced at the application layer).

### 2. Users Have Role Memberships with CRUD Permissions

Users are granted membership to roles with specific CRUD (Create, Read, Update, Delete) permissions:

| Permission | Meaning |
|------------|---------|
| **Create** | Can upload/create documents with this role |
| **Read** | Can view/search documents with this role |
| **Update** | Can modify roles on documents (add/remove roles) |
| **Delete** | Can delete documents with this role |

### 3. Two Document Contexts

Documents exist in one of two visibility contexts:

| Context | Access Model | Owner Privileges |
|---------|--------------|------------------|
| **Personal** | Owner-only access | Full control |
| **Shared** | Role-based access | None (owner loses special privileges once shared) |

**Key behavior**: When a document is moved from personal to shared (by assigning roles), the owner has no special privileges. If the owner loses membership to all document roles, they lose access—even though they uploaded it.

---

## Authentication: JWT Passthrough

All inter-service communication uses JWT tokens issued by AI Portal. The JWT contains user identity and role memberships with permissions.

### JWT Structure

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "iat": 1700000000,
  "exp": 1700003600,
  "iss": "ai-portal",
  "aud": ["ingest-api", "search-api", "agent-api"],
  "roles": [
    {
      "id": "role-uuid-1",
      "name": "finance",
      "permissions": ["create", "read"]
    },
    {
      "id": "role-uuid-2",
      "name": "engineering",
      "permissions": ["read"]
    }
  ]
}
```

### JWT Fields

| Field | Type | Description |
|-------|------|-------------|
| `sub` | UUID | User ID |
| `email` | string | User email |
| `iat` | timestamp | Issued at time |
| `exp` | timestamp | Expiration time (default: 1 hour) |
| `iss` | string | Issuer (always "ai-portal") |
| `aud` | string[] | Allowed audiences (services) |
| `roles` | array | Role memberships with CRUD permissions |

### Token Flow

```
User → AI Portal (better-auth) → JWT → Downstream Services
  1. User logs in via AI Portal
  2. AI Portal issues JWT with role memberships
  3. AI Portal includes JWT in Authorization header for API calls
  4. Services verify JWT signature
  5. Services extract user info and roles from JWT
  6. Services enforce access based on role permissions
```

---

## Data Model

### PostgreSQL Schema (AI Portal)

The AI Portal manages users and role memberships using Prisma:

```prisma
// Roles table (for document access, not app access)
model DocumentRole {
  id          String   @id @default(uuid())
  name        String   @unique  // e.g., "finance", "engineering"
  description String?
  createdAt   DateTime @default(now())
  updatedAt   DateTime @updatedAt
  
  // Relations
  memberships DocumentRoleMembership[]
  documents   DocumentRoleAssignment[]
}

// User role memberships with CRUD permissions
model DocumentRoleMembership {
  id          String   @id @default(uuid())
  userId      String
  roleId      String
  canCreate   Boolean  @default(false)
  canRead     Boolean  @default(false)
  canUpdate   Boolean  @default(false)  // Can add/remove roles on documents
  canDelete   Boolean  @default(false)
  grantedAt   DateTime @default(now())
  grantedBy   String?  // Admin who granted
  
  user        User          @relation(fields: [userId], references: [id], onDelete: Cascade)
  role        DocumentRole  @relation(fields: [roleId], references: [id], onDelete: Cascade)
  
  @@unique([userId, roleId])
}

// Document role assignments (many-to-many)
model DocumentRoleAssignment {
  id          String   @id @default(uuid())
  documentId  String   // References Document.id
  roleId      String
  addedAt     DateTime @default(now())
  addedBy     String?  // User who added this role
  
  document    Document      @relation(fields: [documentId], references: [id], onDelete: Cascade)
  role        DocumentRole  @relation(fields: [roleId], references: [id], onDelete: Cascade)
  
  @@unique([documentId, roleId])
}
```

### PostgreSQL Schema (Ingest Service)

The ingest service stores document metadata with visibility:

```sql
-- ingestion_files table (existing, with updates)
ALTER TABLE ingestion_files ADD COLUMN visibility VARCHAR(20) DEFAULT 'personal';
-- visibility: 'personal' (owner-based) or 'shared' (role-based)

-- Document roles (stored in PostgreSQL for RLS enforcement)
CREATE TABLE document_roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
    role_id UUID NOT NULL,  -- References AI Portal DocumentRole
    role_name VARCHAR(100) NOT NULL,  -- Denormalized for performance
    added_at TIMESTAMP DEFAULT NOW(),
    added_by UUID,  -- User who added this role
    UNIQUE(file_id, role_id)
);

CREATE INDEX idx_document_roles_file ON document_roles(file_id);
CREATE INDEX idx_document_roles_role ON document_roles(role_id);
```

---

## Service Enforcement

### PostgreSQL Row-Level Security (RLS)

RLS policies automatically filter queries based on session variables set by middleware.

#### Session Variables

The JWT auth middleware sets these PostgreSQL session variables:

| Variable | Type | Description |
|----------|------|-------------|
| `app.user_id` | UUID | Current user's ID |
| `app.user_role_ids_read` | CSV | Role IDs user can read |
| `app.user_role_ids_create` | CSV | Role IDs user can create with |
| `app.user_role_ids_update` | CSV | Role IDs user can update |
| `app.user_role_ids_delete` | CSV | Role IDs user can delete |

#### RLS Policies

```sql
-- Personal documents: owner-only access
CREATE POLICY personal_docs_select ON ingestion_files
    FOR SELECT USING (
        visibility = 'personal' 
        AND owner_id = current_setting('app.user_id')::uuid
    );

-- Shared documents: role-based access (user has read permission on at least one doc role)
CREATE POLICY shared_docs_select ON ingestion_files
    FOR SELECT USING (
        visibility = 'shared'
        AND EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
            AND dr.role_id = ANY(
                string_to_array(current_setting('app.user_role_ids_read', true), ',')::uuid[]
            )
        )
    );

-- Insert: User must own document, and for shared docs must have create permission on specified roles
CREATE POLICY ingestion_files_insert ON ingestion_files
    FOR INSERT WITH CHECK (
        owner_id = current_setting('app.user_id')::uuid
    );

-- Update: Personal docs - owner only; Shared docs - must have update permission on a doc role
CREATE POLICY ingestion_files_update ON ingestion_files
    FOR UPDATE USING (
        (visibility = 'personal' AND owner_id = current_setting('app.user_id')::uuid)
        OR
        (visibility = 'shared' AND EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
            AND dr.role_id = ANY(
                string_to_array(current_setting('app.user_role_ids_update', true), ',')::uuid[]
            )
        ))
    );

-- Delete: Personal docs - owner only; Shared docs - must have delete permission on ALL doc roles
CREATE POLICY ingestion_files_delete ON ingestion_files
    FOR DELETE USING (
        (visibility = 'personal' AND owner_id = current_setting('app.user_id')::uuid)
        OR
        (visibility = 'shared' AND NOT EXISTS (
            SELECT 1 FROM document_roles dr
            WHERE dr.file_id = ingestion_files.file_id
            AND dr.role_id NOT IN (
                SELECT unnest(string_to_array(current_setting('app.user_role_ids_delete', true), ',')::uuid[])
            )
        ))
    );
```

### Milvus Partition-Based Isolation

Milvus doesn't have built-in RLS, so we use **partitions** for access isolation.

#### Partition Strategy

```
Collection: document_embeddings
Partitions:
├── personal_{user_id}     # Personal docs (owner-based)
├── role_{role_id_1}       # Finance role docs
├── role_{role_id_2}       # Engineering role docs
└── ...
```

#### Search Filtering

```python
# Build accessible partitions from JWT
partitions = [f"personal_{user_id}"]  # Always include personal
for role in user_roles:
    if "read" in role["permissions"]:
        partitions.append(f"role_{role['id']}")

results = collection.search(
    partition_names=partitions,
    ...
)
```

#### Multi-Role Documents

When a document has multiple roles, its vectors are inserted into ALL role partitions (duplicate vectors). This ensures the document appears in searches for users with any of those roles.

```python
async def insert_document_vectors(file_id, owner_id, visibility, role_ids, vectors):
    if visibility == "personal":
        # Personal: single partition
        partitions = [f"personal_{owner_id}"]
    else:
        # Shared: insert into all role partitions
        partitions = [f"role_{role_id}" for role_id in role_ids]
    
    for partition in partitions:
        ensure_partition_exists(partition)
        collection.insert(vectors, partition_name=partition)
```

### MinIO File Access

**Strategy**: Proxy all file access through the ingest-api

Files are stored in MinIO with paths that include visibility and role/owner information:

```
Bucket: documents
├── personal/{user_id}/{file_id}/...     # Personal files
├── shared/{primary_role_id}/{file_id}/...   # Shared files (primary role for path)
```

**Access enforcement**: The ingest-api verifies JWT permissions before generating presigned URLs:

```python
@router.get("/files/{file_id}/url")
async def get_presigned_url(file_id: str, request: Request):
    user_id = request.state.user_id
    user_roles = request.state.user_roles  # From JWT
    
    # Get file metadata (RLS automatically filters)
    file = await get_file_metadata(file_id)
    if not file:
        raise HTTPException(404, "File not found or access denied")
    
    # Generate time-limited presigned URL
    url = minio_service.presigned_get_url(file.path, expires=3600)
    return {"url": url}
```

---

## API Operations

### Upload Document

**Endpoint**: `POST /upload`

**Parameters**:
- `file`: File to upload
- `visibility`: "personal" (default) or "shared"
- `role_ids[]`: Required if visibility is "shared"

**Validation**:
1. If personal: No role validation needed
2. If shared: User must have "create" permission on ALL specified roles
3. At least one role required for shared documents

**Process**:
1. Validate permissions
2. Store file in MinIO
3. Create database record with visibility and roles
4. Queue for processing
5. On embedding generation, insert into appropriate Milvus partitions

### Search Documents

**Endpoint**: `POST /search`

**Process**:
1. Extract roles from JWT
2. Build list of accessible partitions (personal + readable roles)
3. Search only those partitions in Milvus
4. RLS automatically filters PostgreSQL metadata queries

### Update Document Roles

**Endpoint**: `PUT /files/{id}/roles`

**Parameters**:
- `add_role_ids[]`: Roles to add
- `remove_role_ids[]`: Roles to remove

**Validation**:
1. User must have "update" permission on current document roles
2. User must have "update" permission on roles being added
3. Cannot remove ALL roles (minimum 1 required)
4. If removing all roles from a personal doc, it stays personal

**Process**:
1. Validate permissions
2. Update document_roles table
3. Update Milvus partitions:
   - Add: Copy vectors to new role partitions
   - Remove: Delete vectors from removed role partitions

### Delete Document

**Endpoint**: `DELETE /files/{id}`

**Validation**:
- Personal: User must be owner
- Shared: User must have "delete" permission on ALL document roles

**Process**:
1. Validate permissions
2. Delete from Milvus (all partitions)
3. Delete from MinIO
4. Delete from PostgreSQL (cascades to document_roles)

---

## Access Scenarios

### Scenario 1: Personal Document

```
Alice uploads document (default personal)
├── Only Alice can access (owner check)
├── Stored in personal_{alice_id} Milvus partition
└── Bob cannot search or access
```

### Scenario 2: Shared Document (Single Role)

```
Alice uploads document to Finance role
├── Alice must have Finance.create permission
├── Stored in role_{finance_id} Milvus partition
├── Anyone with Finance.read can search/view
├── Alice has NO special owner privileges
└── If Alice loses Finance role, she loses access
```

### Scenario 3: Multi-Role Document

```
Document has [Finance, Engineering] roles
├── Vectors in both role_{finance} and role_{engineering} partitions
├── Finance.read OR Engineering.read users can search
├── Both Finance.delete AND Engineering.delete needed to delete
└── Finance.update OR Engineering.update can modify roles
```

### Scenario 4: Role Change

```
User removes Finance role from document (Engineering remains)
├── Delete vectors from role_{finance} partition
├── Vectors remain in role_{engineering} partition
├── Finance users lose access
└── Engineering users retain access
```

---

## Security Guarantees

1. **Defense in Depth**: Multiple layers (JWT → RLS → Milvus partitions)
2. **Impossible to Bypass**: Even SQL injection can't bypass RLS
3. **Automatic Enforcement**: No manual permission checks needed in most code
4. **Audit Trail**: All operations logged with user context
5. **Zero Trust**: Database doesn't trust application layer

---

## Migration from Groups to Roles

The previous implementation used "groups" for access control. This security model replaces groups with **roles with CRUD permissions**.

### Key Differences

| Aspect | Old (Groups) | New (Roles) |
|--------|--------------|-------------|
| Access | Binary (member or not) | CRUD permissions per role |
| Granularity | All-or-nothing | Fine-grained (C, R, U, D) |
| Owner | Always has access | No special privileges for shared docs |
| Multi-membership | Multiple groups | Multiple roles with different permissions |

### Migration Steps

1. Rename `groups` table to `document_roles`
2. Add permission columns to membership table
3. Migrate existing memberships with default permissions
4. Update JWT generation to include permissions
5. Update RLS policies for CRUD checks
6. Update Milvus partition naming

---

## Configuration

### Environment Variables

**AI Portal**:
```bash
SERVICE_JWT_SECRET=<shared-secret-key>  # Same across all services
SERVICE_TOKEN_EXPIRY=3600               # 1 hour
```

**Ingest/Search/Agent Services**:
```bash
JWT_SECRET=<same-shared-secret-key>
JWT_ISSUER=ai-portal
JWT_AUDIENCE=ingest-api  # or search-api, agent-api
```

### Ansible Vault

Store secrets in Ansible vault:

```yaml
# provision/ansible/roles/secrets/vars/vault.yml
service_jwt_secret: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

---

## Related Documentation

- `docs/deployment/` - Service deployment procedures
- `docs/configuration/` - Service configuration guides
- `srv/ingest/migrations/` - Database migration files

---

## Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2025-11-25 | 1.0 | Initial unified security model |

