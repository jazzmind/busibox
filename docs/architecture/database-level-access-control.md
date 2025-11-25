---
title: Database-Level Access Control (RLS)
created: 2025-11-24
updated: 2025-11-24
status: proposed
category: architecture
---

# Database-Level Access Control

## Problem

**Current State**: Access control only in API layer
- ❌ API checks permissions, but database queries can bypass them
- ❌ Direct database access bypasses security
- ❌ SQL injection or bugs can leak data
- ❌ Search queries can return unauthorized documents

**Required**: Permissions enforced at database level
- ✅ PostgreSQL Row-Level Security (RLS)
- ✅ Milvus partition-based isolation
- ✅ Impossible to retrieve records without permission
- ✅ Defense in depth

## Architecture

### Data Isolation Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│  - Validates JWT                                             │
│  - Extracts user_id + group_ids                             │
│  - Sets session variables                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    PostgreSQL (RLS)                          │
│  - Automatic filtering based on session variables            │
│  - SELECT: Only returns accessible rows                      │
│  - INSERT: Validates group membership                        │
│  - UPDATE/DELETE: Only owner's documents                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Milvus (Partitions)                       │
│  - Separate partition per group                              │
│  - Search filtered by user's partitions                      │
│  - Vectors physically isolated                               │
└─────────────────────────────────────────────────────────────┘
```

## PostgreSQL Row-Level Security

### 1. Enable RLS on Tables

```sql
-- Enable RLS on ingestion_files table
ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;

-- Enable RLS on chunks table
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- Enable RLS on processing_history table
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;
```

### 2. Create RLS Policies

```sql
-- ============================================================================
-- INGESTION_FILES POLICIES
-- ============================================================================

-- Policy: Users can see their own documents
CREATE POLICY ingestion_files_owner_select ON ingestion_files
    FOR SELECT
    USING (owner_id = current_setting('app.user_id')::uuid);

-- Policy: Users can see group documents they have access to
CREATE POLICY ingestion_files_group_select ON ingestion_files
    FOR SELECT
    USING (
        visibility = 'group' 
        AND group_id IN (
            SELECT unnest(string_to_array(current_setting('app.user_groups', true), ','))::uuid
        )
    );

-- Policy: Users can only insert documents they own
CREATE POLICY ingestion_files_insert ON ingestion_files
    FOR INSERT
    WITH CHECK (
        owner_id = current_setting('app.user_id')::uuid
        AND (
            -- Personal documents: no group check needed
            (visibility = 'personal' AND group_id IS NULL)
            OR
            -- Group documents: user must be in the group
            (
                visibility = 'group' 
                AND group_id IN (
                    SELECT unnest(string_to_array(current_setting('app.user_groups', true), ','))::uuid
                )
            )
        )
    );

-- Policy: Users can only update their own documents
CREATE POLICY ingestion_files_update ON ingestion_files
    FOR UPDATE
    USING (owner_id = current_setting('app.user_id')::uuid)
    WITH CHECK (owner_id = current_setting('app.user_id')::uuid);

-- Policy: Users can only delete their own documents
CREATE POLICY ingestion_files_delete ON ingestion_files
    FOR DELETE
    USING (owner_id = current_setting('app.user_id')::uuid);


-- ============================================================================
-- CHUNKS POLICIES
-- ============================================================================

-- Policy: Users can see chunks from documents they can access
CREATE POLICY chunks_select ON chunks
    FOR SELECT
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS on ingestion_files automatically filters this subquery
        )
    );

-- Policy: Only system can insert chunks (during processing)
-- Chunks are inserted by worker, not by users directly
CREATE POLICY chunks_insert ON chunks
    FOR INSERT
    WITH CHECK (true);  -- Worker has elevated privileges

-- Policy: Users cannot update or delete chunks directly
-- (Handled through document deletion cascade)


-- ============================================================================
-- PROCESSING_HISTORY POLICIES
-- ============================================================================

-- Policy: Users can see processing history for their documents
CREATE POLICY processing_history_select ON processing_history
    FOR SELECT
    USING (
        file_id IN (
            SELECT file_id FROM ingestion_files
            -- RLS on ingestion_files automatically filters
        )
    );
```

### 3. Set Session Variables

```python
# srv/ingest/src/api/middleware/jwt_auth.py

class JWTAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # ... JWT verification ...
        
        # Extract user info from JWT
        user_id = payload["sub"]
        user_groups = payload.get("groups", [])
        group_ids = [g["id"] for g in user_groups]
        
        # Store in request state
        request.state.user_id = user_id
        request.state.user_groups = user_groups
        
        # Set PostgreSQL session variables for RLS
        # These are used by RLS policies
        request.state.db_session_config = {
            "app.user_id": user_id,
            "app.user_groups": ",".join(group_ids)  # CSV for unnest
        }
        
        response = await call_next(request)
        return response
```

### 4. Apply Session Variables to Database Connections

```python
# srv/ingest/src/services/postgres_service.py

class PostgresService:
    def get_connection_with_rls(self, user_id: str, group_ids: List[str]):
        """
        Get database connection with RLS session variables set.
        This ensures all queries are automatically filtered by RLS policies.
        """
        conn = self.pool.getconn()
        
        # Set session variables for RLS
        cursor = conn.cursor()
        cursor.execute("SET LOCAL app.user_id = %s", (user_id,))
        cursor.execute("SET LOCAL app.user_groups = %s", (",".join(group_ids),))
        cursor.close()
        
        return conn
    
    def return_connection(self, conn):
        """Return connection to pool (session variables are reset)"""
        self.pool.putconn(conn)


# Usage in routes
@router.get("/{fileId}")
async def get_file_metadata(fileId: str, request: Request):
    user_id = request.state.user_id
    user_groups = [g["id"] for g in request.state.user_groups]
    
    postgres_service = PostgresService(config)
    postgres_service.connect()
    
    # Get connection with RLS enabled
    conn = postgres_service.get_connection_with_rls(user_id, user_groups)
    
    try:
        cursor = conn.cursor()
        
        # This query is automatically filtered by RLS policies
        # User can ONLY see documents they have access to
        cursor.execute("""
            SELECT file_id, filename, owner_id, visibility, group_id, status
            FROM ingestion_files
            WHERE file_id = %s
        """, (uuid.UUID(fileId),))
        
        row = cursor.fetchone()
        
        if not row:
            # Either doesn't exist OR user doesn't have access
            # RLS makes these indistinguishable (security feature)
            return JSONResponse(
                status_code=404,
                content={"error": "File not found"}
            )
        
        return JSONResponse(content=dict(row))
        
    finally:
        postgres_service.return_connection(conn)
```

### 5. Search with RLS

```python
@router.post("/search")
async def search_documents(request: Request, query: str, limit: int = 10):
    user_id = request.state.user_id
    user_groups = [g["id"] for g in request.state.user_groups]
    
    postgres_service = PostgresService(config)
    conn = postgres_service.get_connection_with_rls(user_id, user_groups)
    
    try:
        cursor = conn.cursor()
        
        # Get accessible file IDs
        # RLS automatically filters to only accessible documents
        cursor.execute("""
            SELECT file_id FROM ingestion_files
            WHERE status = 'completed'
        """)
        
        accessible_file_ids = [row[0] for row in cursor.fetchall()]
        
        # Search in Milvus (see next section)
        results = await search_milvus(
            query=query,
            file_ids=accessible_file_ids,  # Only search accessible docs
            limit=limit
        )
        
        return {"results": results}
        
    finally:
        postgres_service.return_connection(conn)
```

## Milvus Partition-Based Isolation

### Problem with Milvus

Milvus doesn't have built-in RLS like PostgreSQL. We need to implement isolation using **partitions**.

### Strategy: Partition per Group + Personal Partition

```
Collection: document_embeddings

Partitions:
├── personal_{user_id}        # User's personal documents
├── group_{group_id_1}         # Finance group documents
├── group_{group_id_2}         # Engineering group documents
└── ...
```

### 1. Create Partitions on Demand

```python
# srv/ingest/src/services/milvus_service.py

class MilvusService:
    def get_partition_name(self, visibility: str, owner_id: str, group_id: Optional[str]) -> str:
        """
        Get partition name based on document visibility.
        
        Personal docs: personal_{owner_id}
        Group docs: group_{group_id}
        """
        if visibility == "personal":
            return f"personal_{owner_id}"
        elif visibility == "group" and group_id:
            return f"group_{group_id}"
        else:
            raise ValueError("Invalid visibility or missing group_id")
    
    def ensure_partition_exists(self, partition_name: str):
        """Create partition if it doesn't exist"""
        if not self.collection.has_partition(partition_name):
            self.collection.create_partition(partition_name)
            logger.info(f"Created Milvus partition: {partition_name}")
    
    async def insert_vectors(
        self,
        file_id: str,
        owner_id: str,
        visibility: str,
        group_id: Optional[str],
        vectors: List[List[float]],
        metadata: List[dict]
    ):
        """Insert vectors into appropriate partition"""
        
        # Determine partition
        partition_name = self.get_partition_name(visibility, owner_id, group_id)
        self.ensure_partition_exists(partition_name)
        
        # Insert into partition
        entities = [
            {"file_id": file_id, "vector": vec, "metadata": meta}
            for vec, meta in zip(vectors, metadata)
        ]
        
        self.collection.insert(
            entities,
            partition_name=partition_name
        )
        
        logger.info(
            f"Inserted {len(vectors)} vectors to partition {partition_name}",
            file_id=file_id
        )
```

### 2. Search Only Accessible Partitions

```python
async def search_milvus(
    query: str,
    user_id: str,
    user_groups: List[dict],
    limit: int = 10
) -> List[dict]:
    """
    Search Milvus with partition-based access control.
    Only searches partitions user has access to.
    """
    
    # Build list of accessible partitions
    accessible_partitions = []
    
    # 1. User's personal partition
    accessible_partitions.append(f"personal_{user_id}")
    
    # 2. All group partitions user is member of
    for group in user_groups:
        accessible_partitions.append(f"group_{group['id']}")
    
    # Generate query embedding
    query_vector = await generate_embedding(query)
    
    # Search ONLY in accessible partitions
    search_params = {
        "metric_type": "COSINE",
        "params": {"nprobe": 10}
    }
    
    results = milvus_service.collection.search(
        data=[query_vector],
        anns_field="vector",
        param=search_params,
        limit=limit,
        partition_names=accessible_partitions,  # KEY: Only search accessible partitions
        output_fields=["file_id", "chunk_id", "text"]
    )
    
    return results[0]  # First query results
```

### 3. Delete from Correct Partition

```python
async def delete_document_vectors(
    file_id: str,
    owner_id: str,
    visibility: str,
    group_id: Optional[str]
):
    """Delete all vectors for a document from correct partition"""
    
    partition_name = milvus_service.get_partition_name(visibility, owner_id, group_id)
    
    # Delete from specific partition
    milvus_service.collection.delete(
        expr=f"file_id == '{file_id}'",
        partition_name=partition_name
    )
    
    logger.info(f"Deleted vectors for {file_id} from partition {partition_name}")
```

### 4. Handle Group Membership Changes

```python
async def handle_user_removed_from_group(user_id: str, group_id: str):
    """
    When user leaves group, we DON'T move their documents.
    The documents stay in the group partition, but user loses access.
    
    This is handled by:
    1. PostgreSQL RLS: User can't query group documents anymore
    2. Milvus search: User's search won't include group partition
    """
    # No action needed - access is automatically revoked
    logger.info(f"User {user_id} removed from group {group_id} - access revoked")


async def handle_user_added_to_group(user_id: str, group_id: str):
    """
    When user joins group, they immediately get access to all group documents.
    
    This is handled by:
    1. PostgreSQL RLS: User can now query group documents
    2. Milvus search: User's search now includes group partition
    """
    # No action needed - access is automatically granted
    logger.info(f"User {user_id} added to group {group_id} - access granted")
```

## Worker Integration

### Update Worker to Use Partitions

```python
# srv/ingest/src/worker.py

class IngestionWorker:
    async def process_job(self, job_id: str, message_data: dict, trace_id: str):
        # ... existing processing ...
        
        # Get document metadata
        file_id = message_data["file_id"]
        owner_id = message_data["owner_id"]
        visibility = message_data.get("visibility", "personal")
        group_id = message_data.get("group_id")
        
        # ... chunking, embedding generation ...
        
        # Insert vectors into correct partition
        await self.milvus_service.insert_vectors(
            file_id=file_id,
            owner_id=owner_id,
            visibility=visibility,
            group_id=group_id,
            vectors=embeddings,
            metadata=chunk_metadata
        )
```

## Testing RLS

```python
# tests/integration/test_database_rls.py

class TestDatabaseRLS:
    """Test that database-level access control works"""
    
    @pytest.mark.asyncio
    async def test_rls_blocks_unauthorized_select(self, postgres_service):
        """Test: User cannot SELECT another user's document via SQL"""
        
        # Alice creates document
        alice_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        
        conn = postgres_service.get_connection_with_rls(alice_id, [])
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ingestion_files (file_id, owner_id, user_id, filename, visibility)
            VALUES (%s, %s, %s, %s, %s)
        """, (uuid.UUID(doc_id), uuid.UUID(alice_id), uuid.UUID(alice_id), "test.pdf", "personal"))
        conn.commit()
        postgres_service.return_connection(conn)
        
        # Bob tries to SELECT Alice's document
        bob_id = str(uuid.uuid4())
        conn = postgres_service.get_connection_with_rls(bob_id, [])
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM ingestion_files WHERE file_id = %s
        """, (uuid.UUID(doc_id),))
        
        result = cursor.fetchone()
        postgres_service.return_connection(conn)
        
        # RLS blocks Bob from seeing Alice's document
        assert result is None, "RLS failed: Bob can see Alice's document!"
    
    
    @pytest.mark.asyncio
    async def test_rls_allows_group_access(self, postgres_service):
        """Test: Group members can SELECT group documents"""
        
        alice_id = str(uuid.uuid4())
        bob_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        
        # Alice creates group document
        conn = postgres_service.get_connection_with_rls(alice_id, [group_id])
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ingestion_files 
            (file_id, owner_id, user_id, filename, visibility, group_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (uuid.UUID(doc_id), uuid.UUID(alice_id), uuid.UUID(alice_id), 
              "group-doc.pdf", "group", uuid.UUID(group_id)))
        conn.commit()
        postgres_service.return_connection(conn)
        
        # Bob (member of same group) can SELECT
        conn = postgres_service.get_connection_with_rls(bob_id, [group_id])
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM ingestion_files WHERE file_id = %s
        """, (uuid.UUID(doc_id),))
        
        result = cursor.fetchone()
        postgres_service.return_connection(conn)
        
        assert result is not None, "RLS failed: Bob cannot see group document!"
    
    
    @pytest.mark.asyncio
    async def test_rls_blocks_unauthorized_insert(self, postgres_service):
        """Test: User cannot INSERT document to group they're not in"""
        
        alice_id = str(uuid.uuid4())
        group_id = str(uuid.uuid4())  # Alice is NOT in this group
        doc_id = str(uuid.uuid4())
        
        # Alice tries to insert document to group she's not in
        conn = postgres_service.get_connection_with_rls(alice_id, [])  # No groups
        cursor = conn.cursor()
        
        with pytest.raises(Exception) as exc_info:
            cursor.execute("""
                INSERT INTO ingestion_files 
                (file_id, owner_id, user_id, filename, visibility, group_id)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (uuid.UUID(doc_id), uuid.UUID(alice_id), uuid.UUID(alice_id),
                  "test.pdf", "group", uuid.UUID(group_id)))
            conn.commit()
        
        postgres_service.return_connection(conn)
        
        # RLS blocks the insert
        assert "policy" in str(exc_info.value).lower(), "RLS failed to block unauthorized insert!"
    
    
    @pytest.mark.asyncio
    async def test_milvus_partition_isolation(self, milvus_service):
        """Test: Milvus search only returns results from accessible partitions"""
        
        alice_id = str(uuid.uuid4())
        bob_id = str(uuid.uuid4())
        finance_group = str(uuid.uuid4())
        
        # Alice uploads personal document
        await milvus_service.insert_vectors(
            file_id=str(uuid.uuid4()),
            owner_id=alice_id,
            visibility="personal",
            group_id=None,
            vectors=[[0.1, 0.2, 0.3]],
            metadata=[{"text": "Alice personal doc"}]
        )
        
        # Alice uploads Finance group document
        await milvus_service.insert_vectors(
            file_id=str(uuid.uuid4()),
            owner_id=alice_id,
            visibility="group",
            group_id=finance_group,
            vectors=[[0.4, 0.5, 0.6]],
            metadata=[{"text": "Finance group doc"}]
        )
        
        # Bob searches (not in Finance group)
        results = await search_milvus(
            query="document",
            user_id=bob_id,
            user_groups=[],  # Bob has no groups
            limit=10
        )
        
        # Bob should see NO results (can't access Alice's personal or Finance docs)
        assert len(results) == 0, "Milvus partition isolation failed!"
        
        # Alice searches
        results = await search_milvus(
            query="document",
            user_id=alice_id,
            user_groups=[{"id": finance_group}],
            limit=10
        )
        
        # Alice should see BOTH documents
        assert len(results) == 2, "Alice should see both documents!"
```

## Migration Script

```sql
-- migration: add_rls_policies.sql

BEGIN;

-- 1. Add owner_id column if not exists
ALTER TABLE ingestion_files 
    ADD COLUMN IF NOT EXISTS owner_id UUID,
    ADD COLUMN IF NOT EXISTS visibility VARCHAR(20) DEFAULT 'personal',
    ADD COLUMN IF NOT EXISTS group_id UUID;

-- 2. Backfill owner_id from user_id
UPDATE ingestion_files SET owner_id = user_id WHERE owner_id IS NULL;

-- 3. Make owner_id NOT NULL
ALTER TABLE ingestion_files ALTER COLUMN owner_id SET NOT NULL;

-- 4. Enable RLS
ALTER TABLE ingestion_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE processing_history ENABLE ROW LEVEL SECURITY;

-- 5. Create policies (see above)
-- ... all CREATE POLICY statements ...

-- 6. Grant privileges
GRANT SELECT, INSERT, UPDATE, DELETE ON ingestion_files TO busibox_user;
GRANT SELECT ON chunks TO busibox_user;
GRANT SELECT ON processing_history TO busibox_user;

COMMIT;
```

## Performance Considerations

1. **Indexes**: Critical for RLS performance
   ```sql
   CREATE INDEX idx_ingestion_files_owner ON ingestion_files(owner_id);
   CREATE INDEX idx_ingestion_files_group ON ingestion_files(group_id);
   CREATE INDEX idx_ingestion_files_visibility ON ingestion_files(visibility);
   ```

2. **Connection Pooling**: Reuse connections with same session variables

3. **Milvus Partitions**: Limit to reasonable number (< 1000 partitions)

4. **Partition Pruning**: Milvus only searches specified partitions (fast)

## Security Guarantees

With this implementation:

✅ **Impossible to bypass**: Even with SQL injection, RLS enforces access
✅ **Defense in depth**: Multiple layers (JWT → RLS → Partitions)
✅ **Audit trail**: All access logged at database level
✅ **Zero trust**: Database doesn't trust application layer
✅ **Automatic**: No manual permission checks needed in code

