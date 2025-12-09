---
title: Role-Based Access Control (RBAC) and Group Permissions
created: 2025-11-24
updated: 2025-11-24
status: proposed
category: architecture
---

# RBAC and Group Permissions Architecture

## Requirements

### Document Ownership Model

Every document has:
1. **Owner** (user who uploaded it)
2. **Visibility** (personal or group)
3. **Group** (optional, if shared with a group)

### Access Rules

| Scenario | Owner | Group Member | Other User | Result |
|----------|-------|--------------|------------|--------|
| Personal doc | ✅ | ❌ | ❌ | Owner only |
| Group doc | ✅ | ✅ | ❌ | Owner + group members |
| User leaves group | ✅ | ❌ | ❌ | Owner only (even if doc was group) |
| User joins group | ✅ | ✅ (new docs only) | ❌ | Access to new docs |

### Test Scenarios

1. ✅ User uploads personal document → only they can access
2. ✅ User uploads group document → owner + group members can access
3. ❌ User tries to access another user's personal document → denied
4. ❌ User tries to access group document they're not in → denied
5. ✅ User accesses group document they're in → allowed
6. ❌ User leaves group → loses access to group documents (even ones they created)
7. ✅ User who created group document can always access (as owner)
8. ❌ User tries to upload document to group they're not in → denied

## Data Model

### Database Schema

```sql
-- Users table (from AI Portal)
CREATE TABLE users (
    id UUID PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255),
    role VARCHAR(50) DEFAULT 'user', -- admin, user, guest
    created_at TIMESTAMP DEFAULT NOW()
);

-- Groups table
CREATE TABLE groups (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Group memberships
CREATE TABLE group_memberships (
    id UUID PRIMARY KEY,
    group_id UUID REFERENCES groups(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(50) DEFAULT 'member', -- owner, admin, member
    joined_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(group_id, user_id)
);

-- Document permissions (in ingestion_files table)
ALTER TABLE ingestion_files ADD COLUMN owner_id UUID NOT NULL;
ALTER TABLE ingestion_files ADD COLUMN visibility VARCHAR(20) DEFAULT 'personal'; -- personal, group
ALTER TABLE ingestion_files ADD COLUMN group_id UUID REFERENCES groups(id);
ALTER TABLE ingestion_files ADD CONSTRAINT check_group_visibility 
    CHECK ((visibility = 'group' AND group_id IS NOT NULL) OR (visibility = 'personal' AND group_id IS NULL));

-- Indexes for performance
CREATE INDEX idx_ingestion_files_owner ON ingestion_files(owner_id);
CREATE INDEX idx_ingestion_files_group ON ingestion_files(group_id);
CREATE INDEX idx_group_memberships_user ON group_memberships(user_id);
CREATE INDEX idx_group_memberships_group ON group_memberships(group_id);
```

### JWT Claims

```json
{
  "sub": "user-uuid",
  "email": "user@example.com",
  "role": "user",
  "groups": [
    {
      "id": "group-uuid-1",
      "name": "Finance",
      "role": "member"
    },
    {
      "id": "group-uuid-2", 
      "name": "Engineering",
      "role": "admin"
    }
  ]
}
```

## Implementation

### 1. Upload with Group Permission

```python
# srv/ingest/src/api/routes/upload.py

@router.post("/upload")
async def upload_file(
    file: UploadFile,
    visibility: str = Form("personal"),  # personal or group
    group_id: Optional[str] = Form(None),
    request: Request = None
):
    user_id = request.state.user_id
    user_groups = request.state.user_groups  # From JWT
    
    # Validate group access
    if visibility == "group":
        if not group_id:
            raise HTTPException(400, "group_id required for group visibility")
        
        # Check user is member of group
        user_group_ids = [g["id"] for g in user_groups]
        if group_id not in user_group_ids:
            raise HTTPException(403, "User is not a member of this group")
    
    # Store document with permissions
    file_id = str(uuid.uuid4())
    
    async with postgres_service.pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO ingestion_files 
            (file_id, user_id, owner_id, filename, visibility, group_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, file_id, user_id, user_id, file.filename, visibility, group_id, "pending")
    
    return {"file_id": file_id, "visibility": visibility, "group_id": group_id}
```

### 2. Access Control Check

```python
# srv/ingest/src/api/middleware/access_control.py

async def check_document_access(
    file_id: str,
    user_id: str,
    user_groups: List[dict],
    postgres_service: PostgresService
) -> bool:
    """
    Check if user has access to document.
    
    Access granted if:
    1. User is the owner, OR
    2. Document is group-visible AND user is member of that group
    """
    async with postgres_service.pool.acquire() as conn:
        doc = await conn.fetchrow("""
            SELECT owner_id, visibility, group_id
            FROM ingestion_files
            WHERE file_id = $1
        """, uuid.UUID(file_id))
        
        if not doc:
            return False
        
        # Owner always has access
        if str(doc["owner_id"]) == user_id:
            return True
        
        # Check group access
        if doc["visibility"] == "group" and doc["group_id"]:
            user_group_ids = [g["id"] for g in user_groups]
            if str(doc["group_id"]) in user_group_ids:
                return True
        
        return False


def require_document_access(func):
    """Decorator to enforce document access control."""
    async def wrapper(fileId: str, request: Request, *args, **kwargs):
        user_id = request.state.user_id
        user_groups = request.state.user_groups
        
        config = Config().to_dict()
        postgres_service = PostgresService(config)
        postgres_service.connect()
        
        try:
            has_access = await check_document_access(
                fileId, user_id, user_groups, postgres_service
            )
            
            if not has_access:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": "Access denied to this document"}
                )
            
            return await func(fileId, request, *args, **kwargs)
        finally:
            postgres_service.close()
    
    return wrapper


# Usage
@router.get("/{fileId}")
@require_document_access
async def get_file_metadata(fileId: str, request: Request):
    # User has been verified to have access
    ...
```

### 3. List Documents (Filtered by Access)

```python
@router.get("/files")
async def list_files(request: Request, page: int = 1, page_size: int = 50):
    user_id = request.state.user_id
    user_groups = request.state.user_groups
    user_group_ids = [g["id"] for g in user_groups]
    
    async with postgres_service.pool.acquire() as conn:
        # Get documents user can access
        docs = await conn.fetch("""
            SELECT file_id, filename, visibility, group_id, owner_id, status, created_at
            FROM ingestion_files
            WHERE 
                -- User is owner
                owner_id = $1
                OR
                -- Document is group-visible and user is in group
                (visibility = 'group' AND group_id = ANY($2))
            ORDER BY created_at DESC
            LIMIT $3 OFFSET $4
        """, 
        uuid.UUID(user_id),
        [uuid.UUID(gid) for gid in user_group_ids],
        page_size,
        (page - 1) * page_size
        )
    
    return {"documents": [dict(doc) for doc in docs]}
```

## Comprehensive Test Suite

```python
# tests/integration/test_rbac_permissions.py

import pytest
import uuid
from httpx import AsyncClient


class TestRBACPermissions:
    """Test role-based access control and group permissions"""
    
    @pytest.fixture
    async def setup_users_and_groups(self, postgres_service):
        """Create test users and groups"""
        # Create users
        alice_id = str(uuid.uuid4())
        bob_id = str(uuid.uuid4())
        charlie_id = str(uuid.uuid4())
        
        async with postgres_service.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (id, email, name, role)
                VALUES 
                    ($1, 'alice@example.com', 'Alice', 'user'),
                    ($2, 'bob@example.com', 'Bob', 'user'),
                    ($3, 'charlie@example.com', 'Charlie', 'user')
            """, uuid.UUID(alice_id), uuid.UUID(bob_id), uuid.UUID(charlie_id))
            
            # Create groups
            finance_group_id = str(uuid.uuid4())
            engineering_group_id = str(uuid.uuid4())
            
            await conn.execute("""
                INSERT INTO groups (id, name, created_by)
                VALUES 
                    ($1, 'Finance', $2),
                    ($3, 'Engineering', $4)
            """, 
            uuid.UUID(finance_group_id), uuid.UUID(alice_id),
            uuid.UUID(engineering_group_id), uuid.UUID(bob_id))
            
            # Add memberships
            # Alice: Finance (owner), Engineering (member)
            # Bob: Engineering (owner)
            # Charlie: Finance (member)
            await conn.execute("""
                INSERT INTO group_memberships (id, group_id, user_id, role)
                VALUES 
                    ($1, $2, $3, 'owner'),
                    ($4, $5, $6, 'member'),
                    ($7, $8, $9, 'owner'),
                    ($10, $11, $12, 'member')
            """,
            uuid.uuid4(), uuid.UUID(finance_group_id), uuid.UUID(alice_id),
            uuid.uuid4(), uuid.UUID(engineering_group_id), uuid.UUID(alice_id),
            uuid.uuid4(), uuid.UUID(engineering_group_id), uuid.UUID(bob_id),
            uuid.uuid4(), uuid.UUID(finance_group_id), uuid.UUID(charlie_id))
        
        return {
            "alice": {"id": alice_id, "groups": [finance_group_id, engineering_group_id]},
            "bob": {"id": bob_id, "groups": [engineering_group_id]},
            "charlie": {"id": charlie_id, "groups": [finance_group_id]},
            "finance_group": finance_group_id,
            "engineering_group": engineering_group_id
        }
    
    
    @pytest.mark.asyncio
    async def test_personal_document_owner_only(self, async_client, setup_users_and_groups):
        """Test 1: User uploads personal document → only they can access"""
        users = setup_users_and_groups
        
        # Alice uploads personal document
        alice_client = create_client_with_jwt(users["alice"]["id"], users["alice"]["groups"])
        response = await alice_client.post(
            "/upload",
            files={"file": ("test.pdf", b"content", "application/pdf")},
            data={"visibility": "personal"}
        )
        assert response.status_code == 200
        file_id = response.json()["file_id"]
        
        # Alice can access
        response = await alice_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        
        # Bob cannot access
        bob_client = create_client_with_jwt(users["bob"]["id"], users["bob"]["groups"])
        response = await bob_client.get(f"/files/{file_id}")
        assert response.status_code == 403
        assert "Access denied" in response.json()["error"]
    
    
    @pytest.mark.asyncio
    async def test_group_document_members_can_access(self, async_client, setup_users_and_groups):
        """Test 2: User uploads group document → owner + group members can access"""
        users = setup_users_and_groups
        
        # Alice uploads document to Finance group
        alice_client = create_client_with_jwt(users["alice"]["id"], users["alice"]["groups"])
        response = await alice_client.post(
            "/upload",
            files={"file": ("finance-report.pdf", b"content", "application/pdf")},
            data={"visibility": "group", "group_id": users["finance_group"]}
        )
        assert response.status_code == 200
        file_id = response.json()["file_id"]
        
        # Alice (owner) can access
        response = await alice_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        
        # Charlie (Finance member) can access
        charlie_client = create_client_with_jwt(users["charlie"]["id"], users["charlie"]["groups"])
        response = await charlie_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        
        # Bob (not in Finance) cannot access
        bob_client = create_client_with_jwt(users["bob"]["id"], users["bob"]["groups"])
        response = await bob_client.get(f"/files/{file_id}")
        assert response.status_code == 403
    
    
    @pytest.mark.asyncio
    async def test_cannot_upload_to_group_not_member(self, async_client, setup_users_and_groups):
        """Test 3: User tries to upload to group they're not in → denied"""
        users = setup_users_and_groups
        
        # Bob tries to upload to Finance group (he's not a member)
        bob_client = create_client_with_jwt(users["bob"]["id"], users["bob"]["groups"])
        response = await bob_client.post(
            "/upload",
            files={"file": ("test.pdf", b"content", "application/pdf")},
            data={"visibility": "group", "group_id": users["finance_group"]}
        )
        assert response.status_code == 403
        assert "not a member" in response.json()["error"]
    
    
    @pytest.mark.asyncio
    async def test_user_leaves_group_loses_access(self, async_client, setup_users_and_groups, postgres_service):
        """Test 4: User leaves group → loses access to group documents"""
        users = setup_users_and_groups
        
        # Alice uploads document to Finance group
        alice_client = create_client_with_jwt(users["alice"]["id"], users["alice"]["groups"])
        response = await alice_client.post(
            "/upload",
            files={"file": ("report.pdf", b"content", "application/pdf")},
            data={"visibility": "group", "group_id": users["finance_group"]}
        )
        file_id = response.json()["file_id"]
        
        # Charlie can access (member of Finance)
        charlie_client = create_client_with_jwt(users["charlie"]["id"], users["charlie"]["groups"])
        response = await charlie_client.get(f"/files/{file_id}")
        assert response.status_code == 200
        
        # Charlie leaves Finance group
        async with postgres_service.pool.acquire() as conn:
            await conn.execute("""
                DELETE FROM group_memberships
                WHERE user_id = $1 AND group_id = $2
            """, uuid.UUID(users["charlie"]["id"]), uuid.UUID(users["finance_group"]))
        
        # Charlie can no longer access (not in Finance anymore)
        charlie_client_no_groups = create_client_with_jwt(users["charlie"]["id"], [])
        response = await charlie_client_no_groups.get(f"/files/{file_id}")
        assert response.status_code == 403
        
        # Alice (owner) can still access
        response = await alice_client.get(f"/files/{file_id}")
        assert response.status_code == 200
    
    
    @pytest.mark.asyncio
    async def test_list_documents_filtered_by_access(self, async_client, setup_users_and_groups):
        """Test 5: List documents returns only accessible documents"""
        users = setup_users_and_groups
        
        alice_client = create_client_with_jwt(users["alice"]["id"], users["alice"]["groups"])
        bob_client = create_client_with_jwt(users["bob"]["id"], users["bob"]["groups"])
        
        # Alice uploads personal doc
        response = await alice_client.post(
            "/upload",
            files={"file": ("alice-personal.pdf", b"content", "application/pdf")},
            data={"visibility": "personal"}
        )
        alice_personal_id = response.json()["file_id"]
        
        # Alice uploads Finance group doc
        response = await alice_client.post(
            "/upload",
            files={"file": ("finance-doc.pdf", b"content", "application/pdf")},
            data={"visibility": "group", "group_id": users["finance_group"]}
        )
        finance_doc_id = response.json()["file_id"]
        
        # Bob uploads Engineering group doc
        response = await bob_client.post(
            "/upload",
            files={"file": ("eng-doc.pdf", b"content", "application/pdf")},
            data={"visibility": "group", "group_id": users["engineering_group"]}
        )
        eng_doc_id = response.json()["file_id"]
        
        # Alice lists documents - should see her personal + both group docs
        response = await alice_client.get("/files")
        assert response.status_code == 200
        alice_docs = {doc["file_id"] for doc in response.json()["documents"]}
        assert alice_personal_id in alice_docs
        assert finance_doc_id in alice_docs
        assert eng_doc_id in alice_docs  # Alice is in Engineering
        
        # Bob lists documents - should see only Engineering doc
        response = await bob_client.get("/files")
        assert response.status_code == 200
        bob_docs = {doc["file_id"] for doc in response.json()["documents"]}
        assert alice_personal_id not in bob_docs
        assert finance_doc_id not in bob_docs  # Bob not in Finance
        assert eng_doc_id in bob_docs
    
    
    @pytest.mark.asyncio
    async def test_search_respects_permissions(self, async_client, setup_users_and_groups):
        """Test 6: Search only returns documents user can access"""
        users = setup_users_and_groups
        
        alice_client = create_client_with_jwt(users["alice"]["id"], users["alice"]["groups"])
        bob_client = create_client_with_jwt(users["bob"]["id"], users["bob"]["groups"])
        
        # Alice uploads Finance doc with "quarterly report" content
        # Bob uploads Engineering doc with "quarterly report" content
        # Both contain same search term
        
        # Alice searches - should only see Finance doc
        response = await alice_client.post("/search", json={"query": "quarterly report"})
        alice_results = response.json()["results"]
        # Verify only Finance doc in results
        
        # Bob searches - should only see Engineering doc
        response = await bob_client.post("/search", json={"query": "quarterly report"})
        bob_results = response.json()["results"]
        # Verify only Engineering doc in results


def create_client_with_jwt(user_id: str, group_ids: List[str]) -> AsyncClient:
    """Create test client with JWT for specific user and groups"""
    jwt_token = create_test_jwt(
        user_id=user_id,
        groups=[{"id": gid, "name": f"Group-{gid[:8]}", "role": "member"} for gid in group_ids]
    )
    
    from api.main import app
    client = AsyncClient(app=app, base_url="http://test")
    client.headers.update({"Authorization": f"Bearer {jwt_token}"})
    return client
```

## Migration Path

### Phase 1: Add Schema
```sql
-- Run migration to add columns
ALTER TABLE ingestion_files ADD COLUMN owner_id UUID;
ALTER TABLE ingestion_files ADD COLUMN visibility VARCHAR(20) DEFAULT 'personal';
ALTER TABLE ingestion_files ADD COLUMN group_id UUID;

-- Backfill existing documents
UPDATE ingestion_files SET owner_id = user_id WHERE owner_id IS NULL;
```

### Phase 2: Implement Access Control
- Add `check_document_access()` function
- Add `@require_document_access` decorator
- Update all file routes

### Phase 3: Update Upload
- Add `visibility` and `group_id` parameters
- Validate group membership

### Phase 4: Update List/Search
- Filter by access permissions
- Update queries

### Phase 5: Add Tests
- Run comprehensive RBAC test suite
- Verify all scenarios pass

## Security Considerations

1. **Row-Level Security**: Consider PostgreSQL RLS policies
2. **Audit Logging**: Log all access attempts
3. **Group Hierarchy**: Future: support nested groups
4. **Time-Based Access**: Future: temporary group membership
5. **Document Sharing**: Future: share individual docs with users

## Performance

- **Indexes**: Critical for group membership lookups
- **Caching**: Cache user group memberships in JWT
- **Denormalization**: Consider caching group access in Redis

