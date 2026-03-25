"""
Data Service for structured data document management.

Provides CRUD operations for data documents - structured, queryable data storage
similar to Notion/Coda databases. Integrates with existing RLS security model.

Key features:
- Create/Read/Update/Delete data documents
- Insert/Update/Delete individual records within documents
- Optional schema validation
- Automatic record ID generation
- Version tracking for optimistic locking
- Integration with Redis cache for high-frequency access

RLS (Row-Level Security):
- Data documents use the same RLS policies as file documents
- All operations respect user's role-based permissions
"""

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
from contextlib import asynccontextmanager

import asyncpg
import structlog

from api.middleware.jwt_auth import set_rls_session_vars

logger = structlog.get_logger()


class DataService:
    """
    Service for structured data document operations.
    
    Data documents are stored in the data_files table with doc_type='data'.
    They share the same RLS policies as file documents, ensuring consistent
    security across all document types.
    """
    
    def __init__(self, pool: asyncpg.Pool, cache_manager=None):
        """
        Initialize the data service.
        
        Args:
            pool: AsyncPG connection pool
            cache_manager: Optional CacheManager for Redis caching
        """
        self.pool = pool
        self.cache_manager = cache_manager

    @staticmethod
    def _resolve_role_name(request, role_id: str, explicit_names: Optional[Dict[str, str]] = None) -> str:
        """Resolve a human-readable role name.

        Priority:
        1. explicit_names map (client-supplied, e.g. from admin UI)
        2. JWT roles on the request (works when caller holds the role)
        3. Fallback placeholder Role-{id[:8]}
        """
        if explicit_names and role_id in explicit_names:
            return explicit_names[role_id]

        user_context = getattr(request.state, "user_context", None)
        roles = getattr(user_context, "roles", []) if user_context else []
        for role in roles:
            rid = getattr(role, "id", None) or (role.get("id") if isinstance(role, dict) else None)
            if str(rid) == str(role_id):
                name = getattr(role, "name", None) or (role.get("name") if isinstance(role, dict) else None)
                if name:
                    return name
        return f"Role-{role_id[:8]}"
    
    @asynccontextmanager
    async def acquire_with_rls(self, request):
        """
        Get a connection with RLS session variables set.
        
        If the caller has data.admin scope, also sets app.is_admin='true'
        so admin RLS bypass policies activate automatically.  This ensures
        admin users (e.g. from busibox-admin) can see all documents
        regardless of which app-specific roles they hold.
        
        Args:
            request: FastAPI Request object with user_id in state
        """
        user_id = getattr(request.state, "user_id", None)
        role_ids = getattr(request.state, "role_ids", [])
        user_context = getattr(request.state, "user_context", None)
        is_admin = user_context and user_context.has_scope("data.admin")
        
        logger.debug(
            "[RLS] Acquiring connection with RLS context",
            user_id=user_id,
            role_ids=role_ids,
            role_count=len(role_ids) if role_ids else 0,
            is_admin=bool(is_admin),
        )
        
        async with self.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            if is_admin:
                await conn.execute("SET app.is_admin = 'true'")
            try:
                yield conn
            finally:
                if is_admin:
                    await conn.execute("SET app.is_admin = 'false'")
    
    # ========================================================================
    # Document CRUD Operations
    # ========================================================================
    
    async def create_document(
        self,
        request,
        name: str,
        schema: Optional[Dict] = None,
        initial_records: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None,
        visibility: str = "personal",
        role_ids: Optional[List[str]] = None,
        library_id: Optional[str] = None,
        enable_cache: bool = False,
        source_app: Optional[str] = None,
    ) -> Dict:
        """
        Create a new data document.
        
        Args:
            request: FastAPI Request for RLS context
            name: Display name for the document
            schema: Optional JSON schema definition
            initial_records: Optional list of initial records
            metadata: Optional document metadata
            visibility: 'personal', 'shared', or 'authenticated'
            role_ids: Role IDs for shared documents (optional for authenticated)
            library_id: Optional library to place document in
            enable_cache: Whether to enable Redis caching
            source_app: Optional app identifier (e.g., "busibox-projects") for app data libraries
            
        Returns:
            Created document record
        """
        user_id = getattr(request.state, "user_id", None)
        if not user_id:
            raise ValueError("User ID required")
        
        document_id = str(uuid.uuid4())
        
        # Process initial records - ensure each has an ID
        records = []
        if initial_records:
            for record in initial_records:
                if "id" not in record:
                    record["id"] = str(uuid.uuid4())
                # Validate against schema if provided
                if schema:
                    self._validate_record(schema, record)
                records.append(record)
        
        # Build metadata with sourceApp if provided
        doc_metadata = metadata.copy() if metadata else {}
        if source_app:
            doc_metadata["sourceApp"] = source_app
        
        effective_visibility = visibility or "personal"

        async with self.acquire_with_rls(request) as conn:
            async with conn.transaction():
                # Create the data document
                await conn.execute("""
                    INSERT INTO data_files (
                        file_id, user_id, owner_id, filename, original_filename,
                        mime_type, size_bytes, storage_path, content_hash,
                        metadata, visibility, doc_type, data_schema, data_content,
                        data_record_count, data_version, data_modified_at, library_id
                    ) VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9,
                        $10, $11, $12, $13, $14,
                        $15, $16, NOW(), $17
                    )
                """,
                    uuid.UUID(document_id),
                    uuid.UUID(user_id),
                    uuid.UUID(user_id),  # owner_id
                    name,
                    name,  # original_filename = name for data docs
                    "application/x-busibox-data",  # Custom MIME type
                    0,  # size_bytes - not applicable for data docs
                    f"data/{document_id}",  # Virtual storage path
                    f"data-{document_id}",  # Unique content hash
                    json.dumps(doc_metadata),
                    visibility,
                    "data",
                    json.dumps(schema) if schema else None,
                    json.dumps(records),
                    len(records),
                    1,  # Initial version
                    uuid.UUID(library_id) if library_id else None,
                )
                
                # Also insert initial records into data_records table
                if records and await self._use_records_table(conn, document_id):
                    for record in records:
                        record_uuid = uuid.UUID(record["id"]) if self._is_uuid(record["id"]) else uuid.uuid4()
                        await conn.execute("""
                            INSERT INTO data_records (
                                record_id, document_id, data, owner_id, created_by,
                                visibility, created_at, updated_at
                            ) VALUES ($1, $2, $3, $4, $5, 'inherit', NOW(), NOW())
                        """,
                            record_uuid,
                            uuid.UUID(document_id),
                            json.dumps(record),
                            uuid.UUID(user_id),
                            uuid.UUID(user_id),
                        )
                
                # Create completed status (data docs don't need processing)
                await conn.execute("""
                    INSERT INTO data_status (
                        file_id, stage, progress, completed_at
                    ) VALUES ($1, 'completed', 100, NOW())
                """, uuid.UUID(document_id))
                
                # Add role assignments if shared
                if visibility == "shared" and role_ids:
                    for role_id in role_ids:
                        await conn.execute("""
                            INSERT INTO document_roles (
                                file_id, role_id, role_name, added_by
                            ) VALUES ($1, $2, $3, $4)
                        """,
                            uuid.UUID(document_id),
                            uuid.UUID(role_id),
                            self._resolve_role_name(request, role_id),
                            uuid.UUID(user_id),
                        )
        
        # Optionally cache the document
        if enable_cache and self.cache_manager:
            await self.cache_manager.cache_document(document_id, {
                "schema": schema,
                "records": records,
                "version": 1,
            })
        
        logger.info(
            "[DATA] Document created successfully",
            document_id=document_id,
            name=name,
            record_count=len(records),
            visibility=visibility,
            owner_id=user_id,
            role_ids=role_ids,
        )
        
        # Fetch the created document - this will apply RLS
        result = await self.get_document(request, document_id)
        
        if result is None:
            logger.error(
                "[DATA] CRITICAL: Created document but RLS prevented retrieval!",
                document_id=document_id,
                visibility=visibility,
                owner_id=user_id,
                role_ids=role_ids,
                hint="For 'shared' visibility, roleIds must be provided. For 'personal' visibility, ensure owner_id matches request user_id. 'authenticated' visibility is readable by any logged-in user.",
            )
        
        return result
    
    async def get_document(
        self,
        request,
        document_id: str,
        include_records: bool = True,
    ) -> Optional[Dict]:
        """
        Get a data document by ID.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            include_records: Whether to include data_content in response
            
        Returns:
            Document record or None if not found
        """
        # Try cache first
        if self.cache_manager:
            cached = await self.cache_manager.get_document(document_id)
            if cached:
                # Still need to verify RLS access
                async with self.acquire_with_rls(request) as conn:
                    exists = await conn.fetchval(
                        "SELECT 1 FROM data_files WHERE file_id = $1 AND doc_type = 'data'",
                        uuid.UUID(document_id)
                    )
                    if exists:
                        return self._format_document(cached, include_records)
        
        user_id = getattr(request.state, "user_id", None)
        role_ids = getattr(request.state, "role_ids", [])
        
        async with self.acquire_with_rls(request) as conn:
            row = await conn.fetchrow("""
                SELECT 
                    file_id,
                    filename as name,
                    owner_id,
                    visibility,
                    metadata,
                    data_schema,
                    data_content,
                    data_record_count,
                    data_version,
                    data_modified_at,
                    library_id,
                    created_at,
                    updated_at
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
            """, uuid.UUID(document_id))
            
            if not row:
                # Check if document exists at all (bypass RLS with a simpler check)
                # This helps distinguish "doesn't exist" from "RLS blocked"
                exists_check = await conn.fetchval(
                    "SELECT visibility FROM data_files WHERE file_id = $1",
                    uuid.UUID(document_id)
                )
                if exists_check is not None:
                    logger.warning(
                        "[DATA] Document exists but RLS blocked access",
                        document_id=document_id,
                        document_visibility=exists_check,
                        request_user_id=user_id,
                        request_role_ids=role_ids,
                        hint="Check visibility and role assignments",
                    )
                else:
                    logger.debug(
                        "[DATA] Document not found",
                        document_id=document_id,
                    )
                return None
            
            logger.debug(
                "[DATA] Document retrieved successfully",
                document_id=document_id,
                name=row["name"],
                visibility=row["visibility"],
            )
            
            doc = self._row_to_document(row, include_records=False)
            
            if include_records:
                use_table = await self._use_records_table(conn, document_id)
                if use_table:
                    record_rows = await conn.fetch(
                        "SELECT data FROM data_records WHERE document_id = $1 ORDER BY ordinal, created_at",
                        uuid.UUID(document_id),
                    )
                    doc["records"] = [json.loads(r["data"]) for r in record_rows]
                    doc["recordCount"] = len(doc["records"])
                elif row.get("data_content"):
                    doc["records"] = json.loads(row["data_content"])
            
            return doc
    
    async def update_document(
        self,
        request,
        document_id: str,
        name: Optional[str] = None,
        schema: Optional[Dict] = None,
        metadata: Optional[Dict] = None,
        expected_version: Optional[int] = None,
    ) -> Dict:
        """
        Update a data document's metadata/schema.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            name: New name (optional)
            schema: New schema (optional)
            metadata: New metadata (optional)
            expected_version: For optimistic locking
            
        Returns:
            Updated document record
            
        Raises:
            ValueError: If version mismatch (optimistic lock failure)
        """
        async with self.acquire_with_rls(request) as conn:
            # Check current version if optimistic locking
            if expected_version is not None:
                current_version = await conn.fetchval(
                    "SELECT data_version FROM data_files WHERE file_id = $1 AND doc_type = 'data'",
                    uuid.UUID(document_id)
                )
                if current_version != expected_version:
                    raise ValueError(f"Version mismatch: expected {expected_version}, got {current_version}")
            
            # Build update query
            updates = ["data_version = data_version + 1", "updated_at = NOW()"]
            params = [uuid.UUID(document_id)]
            param_idx = 2
            
            if name is not None:
                updates.append(f"filename = ${param_idx}")
                updates.append(f"original_filename = ${param_idx}")
                params.append(name)
                param_idx += 1
            
            if schema is not None:
                updates.append(f"data_schema = ${param_idx}")
                params.append(json.dumps(schema))
                param_idx += 1
            
            if metadata is not None:
                updates.append(f"metadata = ${param_idx}")
                params.append(json.dumps(metadata))
                param_idx += 1
            
            await conn.execute(f"""
                UPDATE data_files
                SET {', '.join(updates)}
                WHERE file_id = $1 AND doc_type = 'data'
            """, *params)
        
        # Invalidate cache
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        return await self.get_document(request, document_id)
    
    async def delete_document(
        self,
        request,
        document_id: str,
    ) -> bool:
        """
        Delete a data document.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            
        Returns:
            True if deleted, False if not found
        """
        async with self.acquire_with_rls(request) as conn:
            result = await conn.execute(
                "DELETE FROM data_files WHERE file_id = $1 AND doc_type = 'data'",
                uuid.UUID(document_id)
            )
            deleted = result != "DELETE 0"
        
        if deleted and self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info("Data document deleted", document_id=document_id, deleted=deleted)
        return deleted
    
    # ========================================================================
    # Record Operations (data_records table with JSONB fallback)
    # ========================================================================

    async def _use_records_table(self, conn, document_id: str) -> bool:
        """Check if the data_records table exists and should be used."""
        try:
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'data_records'
                )
            """)
            return bool(exists)
        except Exception:
            return False

    async def _sync_record_count(self, conn, document_id: str) -> None:
        """Update data_record_count on data_files from data_records table."""
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM data_records WHERE document_id = $1",
            uuid.UUID(document_id),
        )
        await conn.execute("""
            UPDATE data_files
            SET data_record_count = $2,
                data_version = data_version + 1,
                data_modified_at = NOW(),
                updated_at = NOW()
            WHERE file_id = $1 AND doc_type = 'data'
        """, uuid.UUID(document_id), count)

    async def insert_records(
        self,
        request,
        document_id: str,
        records: List[Dict],
        validate: bool = True,
        record_visibility: Optional[str] = None,
        record_role_ids: Optional[List[str]] = None,
    ) -> Tuple[int, List[str]]:
        """
        Insert records into a data document.
        
        Uses data_records table (row-per-record with RLS) when available,
        falling back to data_content JSONB for backward compatibility.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            records: List of record dicts to insert
            validate: Whether to validate against schema
            record_visibility: Optional per-record visibility ('inherit', 'personal', 'shared')
            record_role_ids: Optional role IDs for records with visibility='shared'
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            use_table = await self._use_records_table(conn, document_id)
            
            row = await conn.fetchrow("""
                SELECT data_schema, data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            schema = json.loads(row["data_schema"]) if row["data_schema"] else None
            
            new_ids = []
            batch_id = str(uuid.uuid4())
            vis = record_visibility or "inherit"
            
            if use_table:
                for record in records:
                    if "id" not in record:
                        record["id"] = str(uuid.uuid4())
                    new_ids.append(record["id"])
                    
                    record["_created_at"] = datetime.utcnow().isoformat()
                    record["_created_by"] = user_id
                    
                    if validate and schema:
                        self._validate_record(schema, record)
                    
                    record_id = uuid.UUID(record["id"]) if self._is_uuid(record["id"]) else uuid.uuid4()
                    
                    await conn.execute("""
                        INSERT INTO data_records (
                            record_id, document_id, data, owner_id, created_by,
                            visibility, created_at, updated_at
                        ) VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                    """,
                        record_id,
                        uuid.UUID(document_id),
                        json.dumps(record),
                        uuid.UUID(user_id) if user_id else uuid.UUID('00000000-0000-0000-0000-000000000000'),
                        uuid.UUID(user_id) if user_id else None,
                        vis,
                    )
                    
                    if vis == "shared" and record_role_ids:
                        for role_id in record_role_ids:
                            await conn.execute("""
                                INSERT INTO record_roles (record_id, role_id, role_name, added_by)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (record_id, role_id) DO NOTHING
                            """,
                                record_id,
                                uuid.UUID(role_id),
                                self._resolve_role_name(request, role_id),
                                uuid.UUID(user_id) if user_id else None,
                            )
                    
                    await conn.execute("""
                        INSERT INTO data_record_history (
                            document_id, record_id, operation, new_data, changed_by, batch_id
                        ) VALUES ($1, $2, 'insert', $3, $4, $5)
                    """,
                        uuid.UUID(document_id),
                        record["id"],
                        json.dumps(record),
                        uuid.UUID(user_id) if user_id else None,
                        uuid.UUID(batch_id),
                    )
                
                await self._sync_record_count(conn, document_id)
            else:
                current_records = json.loads(row["data_content"] or "[]")
                
                for record in records:
                    if "id" not in record:
                        record["id"] = str(uuid.uuid4())
                    new_ids.append(record["id"])
                    
                    record["_created_at"] = datetime.utcnow().isoformat()
                    record["_created_by"] = user_id
                    
                    if validate and schema:
                        self._validate_record(schema, record)
                    
                    current_records.append(record)
                
                await conn.execute("""
                    UPDATE data_files
                    SET data_content = $2,
                        data_record_count = $3,
                        data_version = data_version + 1,
                        data_modified_at = NOW(),
                        updated_at = NOW()
                    WHERE file_id = $1 AND doc_type = 'data'
                """,
                    uuid.UUID(document_id),
                    json.dumps(current_records),
                    len(current_records),
                )
                
                for record in records:
                    await conn.execute("""
                        INSERT INTO data_record_history (
                            document_id, record_id, operation, new_data, changed_by, batch_id
                        ) VALUES ($1, $2, 'insert', $3, $4, $5)
                    """,
                        uuid.UUID(document_id),
                        record["id"],
                        json.dumps(record),
                        uuid.UUID(user_id) if user_id else None,
                        uuid.UUID(batch_id),
                    )
        
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info("Records inserted", document_id=document_id, count=len(records))
        return len(records), new_ids
    
    async def update_records(
        self,
        request,
        document_id: str,
        updates: Dict[str, Any],
        where: Optional[Dict] = None,
        validate: bool = True,
    ) -> int:
        """
        Update records in a data document.
        
        Uses data_records table when available, falling back to JSONB.
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            use_table = await self._use_records_table(conn, document_id)
            
            row = await conn.fetchrow("""
                SELECT data_schema, data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            schema = json.loads(row["data_schema"]) if row["data_schema"] else None
            batch_id = str(uuid.uuid4())
            updated_count = 0
            
            if use_table:
                record_rows = await conn.fetch(
                    "SELECT record_id, data FROM data_records WHERE document_id = $1",
                    uuid.UUID(document_id),
                )
                
                for rrow in record_rows:
                    record = json.loads(rrow["data"])
                    if where is None or self._record_matches_filter(record, where):
                        old_record = record.copy()
                        
                        for key, value in updates.items():
                            record[key] = value
                        
                        record["_updated_at"] = datetime.utcnow().isoformat()
                        record["_updated_by"] = user_id
                        
                        if validate and schema:
                            self._validate_record(schema, record)
                        
                        await conn.execute("""
                            UPDATE data_records
                            SET data = $2, updated_by = $3, updated_at = NOW()
                            WHERE record_id = $1
                        """,
                            rrow["record_id"],
                            json.dumps(record),
                            uuid.UUID(user_id) if user_id else None,
                        )
                        
                        updated_count += 1
                        
                        await conn.execute("""
                            INSERT INTO data_record_history (
                                document_id, record_id, operation, old_data, new_data, changed_by, batch_id
                            ) VALUES ($1, $2, 'update', $3, $4, $5, $6)
                        """,
                            uuid.UUID(document_id),
                            record.get("id", "unknown"),
                            json.dumps(old_record),
                            json.dumps(record),
                            uuid.UUID(user_id) if user_id else None,
                            uuid.UUID(batch_id),
                        )
                
                if updated_count > 0:
                    await conn.execute("""
                        UPDATE data_files
                        SET data_version = data_version + 1,
                            data_modified_at = NOW(),
                            updated_at = NOW()
                        WHERE file_id = $1 AND doc_type = 'data'
                    """, uuid.UUID(document_id))
            else:
                current_records = json.loads(row["data_content"] or "[]")
                
                for i, record in enumerate(current_records):
                    if where is None or self._record_matches_filter(record, where):
                        old_record = record.copy()
                        
                        for key, value in updates.items():
                            record[key] = value
                        
                        record["_updated_at"] = datetime.utcnow().isoformat()
                        record["_updated_by"] = user_id
                        
                        if validate and schema:
                            self._validate_record(schema, record)
                        
                        current_records[i] = record
                        updated_count += 1
                        
                        await conn.execute("""
                            INSERT INTO data_record_history (
                                document_id, record_id, operation, old_data, new_data, changed_by, batch_id
                            ) VALUES ($1, $2, 'update', $3, $4, $5, $6)
                        """,
                            uuid.UUID(document_id),
                            record.get("id", "unknown"),
                            json.dumps(old_record),
                            json.dumps(record),
                            uuid.UUID(user_id) if user_id else None,
                            uuid.UUID(batch_id),
                        )
                
                if updated_count > 0:
                    await conn.execute("""
                        UPDATE data_files
                        SET data_content = $2,
                            data_version = data_version + 1,
                            data_modified_at = NOW(),
                            updated_at = NOW()
                        WHERE file_id = $1 AND doc_type = 'data'
                    """,
                        uuid.UUID(document_id),
                        json.dumps(current_records),
                    )
        
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info("Records updated", document_id=document_id, count=updated_count)
        return updated_count
    
    async def delete_records(
        self,
        request,
        document_id: str,
        where: Optional[Dict] = None,
        record_ids: Optional[List[str]] = None,
    ) -> int:
        """
        Delete records from a data document.
        
        Uses data_records table when available, falling back to JSONB.
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            use_table = await self._use_records_table(conn, document_id)
            
            row = await conn.fetchrow("""
                SELECT data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            deleted_count = 0
            deleted_ids: List[str] = []
            batch_id = str(uuid.uuid4())
            
            if use_table:
                record_rows = await conn.fetch(
                    "SELECT record_id, data FROM data_records WHERE document_id = $1",
                    uuid.UUID(document_id),
                )
                
                for rrow in record_rows:
                    record = json.loads(rrow["data"])
                    should_delete = False
                    
                    if record_ids and record.get("id") in record_ids:
                        should_delete = True
                    elif where and self._record_matches_filter(record, where):
                        should_delete = True
                    
                    if should_delete:
                        rid = record.get("id")
                        if rid:
                            deleted_ids.append(rid)
                        
                        await conn.execute("""
                            INSERT INTO data_record_history (
                                document_id, record_id, operation, old_data, changed_by, batch_id
                            ) VALUES ($1, $2, 'delete', $3, $4, $5)
                        """,
                            uuid.UUID(document_id),
                            record.get("id", "unknown"),
                            json.dumps(record),
                            uuid.UUID(user_id) if user_id else None,
                            uuid.UUID(batch_id),
                        )
                        
                        await conn.execute(
                            "DELETE FROM data_records WHERE record_id = $1",
                            rrow["record_id"],
                        )
                        deleted_count += 1
                
                if deleted_count > 0:
                    await self._sync_record_count(conn, document_id)
            else:
                current_records = json.loads(row["data_content"] or "[]")
                kept_records = []
                
                for record in current_records:
                    should_delete = False
                    
                    if record_ids and record.get("id") in record_ids:
                        should_delete = True
                    elif where and self._record_matches_filter(record, where):
                        should_delete = True
                    
                    if should_delete:
                        deleted_count += 1
                        rid = record.get("id")
                        if rid:
                            deleted_ids.append(rid)
                        await conn.execute("""
                            INSERT INTO data_record_history (
                                document_id, record_id, operation, old_data, changed_by, batch_id
                            ) VALUES ($1, $2, 'delete', $3, $4, $5)
                        """,
                            uuid.UUID(document_id),
                            record.get("id", "unknown"),
                            json.dumps(record),
                            uuid.UUID(user_id) if user_id else None,
                            uuid.UUID(batch_id),
                        )
                    else:
                        kept_records.append(record)
                
                if deleted_count > 0:
                    await conn.execute("""
                        UPDATE data_files
                        SET data_content = $2,
                            data_record_count = $3,
                            data_version = data_version + 1,
                            data_modified_at = NOW(),
                            updated_at = NOW()
                        WHERE file_id = $1 AND doc_type = 'data'
                    """,
                        uuid.UUID(document_id),
                        json.dumps(kept_records),
                        len(kept_records),
                    )
        
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info("Records deleted", document_id=document_id, count=deleted_count)
        return deleted_count, deleted_ids
    
    # ========================================================================
    # Schema Operations
    # ========================================================================
    
    async def get_schema(
        self,
        request,
        document_id: str,
    ) -> Optional[Dict]:
        """
        Get the schema for a data document.
        
        Returns:
            Schema dict or None if no schema defined
        """
        async with self.acquire_with_rls(request) as conn:
            schema_json = await conn.fetchval("""
                SELECT data_schema
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
            """, uuid.UUID(document_id))
            
            return json.loads(schema_json) if schema_json else None
    
    async def update_schema(
        self,
        request,
        document_id: str,
        schema: Dict,
        validate_existing: bool = False,
    ) -> Dict:
        """
        Update the schema for a data document.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            schema: New schema definition
            validate_existing: Whether to validate existing records
            
        Returns:
            Updated document
            
        Raises:
            ValueError: If validate_existing and records don't match schema
        """
        async with self.acquire_with_rls(request) as conn:
            if validate_existing:
                # Get existing records and validate
                row = await conn.fetchrow("""
                    SELECT data_content
                    FROM data_files
                    WHERE file_id = $1 AND doc_type = 'data'
                """, uuid.UUID(document_id))
                
                if row and row["data_content"]:
                    records = json.loads(row["data_content"])
                    for i, record in enumerate(records):
                        try:
                            self._validate_record(schema, record)
                        except ValueError as e:
                            raise ValueError(f"Record {i} fails validation: {e}")
            
            await conn.execute("""
                UPDATE data_files
                SET data_schema = $2,
                    data_version = data_version + 1,
                    updated_at = NOW()
                WHERE file_id = $1 AND doc_type = 'data'
            """,
                uuid.UUID(document_id),
                json.dumps(schema),
            )
        
        return await self.get_document(request, document_id)
    
    # ========================================================================
    # List Operations
    # ========================================================================
    
    async def list_documents(
        self,
        request,
        library_id: Optional[str] = None,
        visibility: Optional[str] = None,
        source_app: Optional[str] = None,
        metadata_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """
        List data documents accessible to the user.
        
        Args:
            request: FastAPI Request for RLS context
            library_id: Optional filter by library
            visibility: Optional filter by visibility
            source_app: Optional filter by source app (e.g., "busibox-projects")
            metadata_type: Optional filter by metadata.type (e.g., "extraction_schema")
            limit: Max results (default 50)
            offset: Pagination offset
            
        Returns:
            List of document summaries (without full data_content)
        """
        async with self.acquire_with_rls(request) as conn:
            query = """
                SELECT 
                    file_id,
                    filename as name,
                    owner_id,
                    visibility,
                    metadata,
                    data_schema,
                    data_record_count,
                    data_version,
                    data_modified_at,
                    library_id,
                    created_at,
                    updated_at
                FROM data_files
                WHERE doc_type = 'data'
            """
            params = []
            param_idx = 1
            
            if library_id:
                query += f" AND library_id = ${param_idx}"
                params.append(uuid.UUID(library_id))
                param_idx += 1
            
            if visibility:
                query += f" AND visibility = ${param_idx}"
                params.append(visibility)
                param_idx += 1
            
            if source_app:
                # Filter by sourceApp stored in metadata JSONB
                query += f" AND metadata->>'sourceApp' = ${param_idx}"
                params.append(source_app)
                param_idx += 1

            if metadata_type:
                # Filter by metadata.type for specialized data docs (e.g., extraction schemas)
                query += f" AND metadata->>'type' = ${param_idx}"
                params.append(metadata_type)
                param_idx += 1
            
            query += f" ORDER BY updated_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
            params.extend([limit, offset])
            
            rows = await conn.fetch(query, *params)
            
            return [self._row_to_document(row, include_records=False) for row in rows]

    # ========================================================================
    # Role Management
    # ========================================================================

    async def get_document_roles(
        self,
        request,
        document_id: str,
    ) -> List[Dict]:
        """
        Get all role assignments for a document the requester can access.
        Enriches stored placeholder names (Role-{id[:8]}) with real names from JWT context.
        """
        async with self.acquire_with_rls(request) as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM data_files WHERE file_id = $1 AND doc_type = 'data'",
                uuid.UUID(document_id),
            )
            if not exists:
                raise ValueError(f"Document {document_id} not found")

            rows = await conn.fetch(
                """
                SELECT
                    role_id::text AS role_id,
                    role_name,
                    added_at,
                    added_by::text AS added_by
                FROM document_roles
                WHERE file_id = $1
                ORDER BY added_at
                """,
                uuid.UUID(document_id),
            )
            results = [dict(row) for row in rows]
            for role in results:
                stored_name = role.get("role_name", "")
                if stored_name.startswith("Role-") and len(stored_name) <= 14:
                    resolved = self._resolve_role_name(request, role["role_id"])
                    if not resolved.startswith("Role-"):
                        role["role_name"] = resolved
            return results

    async def set_document_roles(
        self,
        request,
        document_id: str,
        role_ids: List[str],
        visibility: Optional[str] = None,
        role_names: Optional[Dict[str, str]] = None,
    ) -> Dict:
        """
        Replace role assignments for a document and optionally update visibility.

        Safety: prevents the requesting user from removing their own access when
        the resulting visibility is "shared" — unless the caller is an admin or
        the document owner.

        To avoid the ``ensure_document_has_roles`` trigger blocking bulk deletes
        on shared documents, we temporarily set visibility to ``personal`` before
        clearing the old roles, then re-insert the new roles and set the final
        visibility.

        Args:
            role_names: Optional mapping of role_id -> human-readable name.
                        Used when the caller's JWT may not include the role
                        (e.g. admin assigning an app role they don't hold).
        """
        request_role_ids = {str(role_id) for role_id in (getattr(request.state, "role_ids", []) or [])}
        request_user_id = getattr(request.state, "user_id", None)
        normalized_role_ids = list(dict.fromkeys([str(role_id) for role_id in role_ids]))

        user_context = getattr(request.state, "user_context", None)
        caller_is_admin = user_context and user_context.has_scope("data.admin")

        logger.info(
            "set_document_roles called",
            document_id=document_id,
            caller_is_admin=bool(caller_is_admin),
            has_user_context=user_context is not None,
            scopes=list(user_context.scopes) if user_context else [],
            role_ids=normalized_role_ids,
            visibility=visibility,
        )

        if visibility is not None and visibility not in ("personal", "shared"):
            raise ValueError("visibility must be either 'personal' or 'shared'")

        # Admins use acquire_admin (sets app.is_admin at session level) so
        # the SELECT ... FOR UPDATE can see documents they don't own.
        acquire = self.acquire_admin if caller_is_admin else self.acquire_with_rls
        async with acquire(request) as conn:
            async with conn.transaction():
                # Reinforce admin flag inside the transaction to guarantee
                # it applies to all statements including INSERT into
                # document_roles (which has its own RLS policies).
                if caller_is_admin:
                    await conn.execute("SET LOCAL app.is_admin = 'true'")

                document_row = await conn.fetchrow(
                    """
                    SELECT visibility, user_id
                    FROM data_files
                    WHERE file_id = $1 AND doc_type = 'data'
                    FOR UPDATE
                    """,
                    uuid.UUID(document_id),
                )

                if not document_row:
                    raise ValueError(f"Document {document_id} not found")

                caller_is_owner = (
                    request_user_id
                    and document_row["user_id"]
                    and str(document_row["user_id"]) == str(request_user_id)
                )

                effective_visibility = visibility or document_row["visibility"]

                if effective_visibility == "shared":
                    if len(normalized_role_ids) == 0:
                        raise ValueError("Shared visibility requires at least one role ID")

                    if not caller_is_admin and not caller_is_owner:
                        if request_role_ids and request_role_ids.isdisjoint(set(normalized_role_ids)):
                            raise PermissionError(
                                "Role update would remove your own access to this document"
                            )

                # Owners may need to manipulate roles they don't hold.
                if not caller_is_admin and caller_is_owner:
                    await conn.execute("SET LOCAL app.is_admin = 'true'")

                # Temporarily set visibility to 'personal' so the
                # ensure_document_has_roles trigger won't block role deletion.
                if document_row["visibility"] == "shared":
                    await conn.execute(
                        """
                        UPDATE data_files
                        SET visibility = 'personal', updated_at = NOW()
                        WHERE file_id = $1 AND doc_type = 'data'
                        """,
                        uuid.UUID(document_id),
                    )

                await conn.execute(
                    "DELETE FROM document_roles WHERE file_id = $1",
                    uuid.UUID(document_id),
                )

                if effective_visibility == "shared":
                    for role_id in normalized_role_ids:
                        await conn.execute(
                            """
                            INSERT INTO document_roles (
                                file_id, role_id, role_name, added_by
                            ) VALUES ($1, $2, $3, $4)
                            ON CONFLICT (file_id, role_id) DO NOTHING
                            """,
                            uuid.UUID(document_id),
                            uuid.UUID(role_id),
                            self._resolve_role_name(request, role_id, role_names),
                            uuid.UUID(request_user_id) if request_user_id else None,
                        )

                await conn.execute(
                    """
                    UPDATE data_files
                    SET visibility = $2,
                        updated_at = NOW()
                    WHERE file_id = $1 AND doc_type = 'data'
                    """,
                    uuid.UUID(document_id),
                    effective_visibility,
                )

        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)

        roles = await self.get_document_roles(request, document_id)
        return {
            "documentId": document_id,
            "visibility": effective_visibility,
            "roleIds": [role["role_id"] for role in roles],
            "roles": roles,
        }
    
    # ========================================================================
    # Record-Level Role Management
    # ========================================================================

    async def get_record_roles(
        self,
        request,
        document_id: str,
        record_id: str,
    ) -> List[Dict]:
        """Get role assignments for a specific record."""
        async with self.acquire_with_rls(request) as conn:
            rec = await conn.fetchrow(
                "SELECT record_id FROM data_records WHERE document_id = $1 AND (data->>'id') = $2",
                uuid.UUID(document_id), record_id,
            )
            if not rec:
                raise ValueError(f"Record {record_id} not found in document {document_id}")
            
            rows = await conn.fetch("""
                SELECT role_id::text, role_name, added_at, added_by::text
                FROM record_roles
                WHERE record_id = $1
                ORDER BY added_at
            """, rec["record_id"])
            return [dict(r) for r in rows]

    async def set_record_visibility(
        self,
        request,
        document_id: str,
        record_id: str,
        visibility: str,
        role_ids: Optional[List[str]] = None,
    ) -> Dict:
        """
        Set visibility and roles for a specific record.
        
        Args:
            visibility: 'inherit', 'personal', or 'shared'
            role_ids: Required when visibility='shared'
        """
        user_id = getattr(request.state, "user_id", None)
        
        if visibility not in ("inherit", "personal", "shared"):
            raise ValueError("visibility must be 'inherit', 'personal', or 'shared'")
        if visibility == "shared" and not role_ids:
            raise ValueError("role_ids required for shared visibility")
        
        async with self.acquire_with_rls(request) as conn:
            rec = await conn.fetchrow(
                "SELECT record_id FROM data_records WHERE document_id = $1 AND (data->>'id') = $2",
                uuid.UUID(document_id), record_id,
            )
            if not rec:
                raise ValueError(f"Record {record_id} not found")
            
            pg_record_id = rec["record_id"]
            
            async with conn.transaction():
                # Insert roles BEFORE changing visibility to 'shared' because
                # PostgreSQL RLS SELECT policies on the new row require matching
                # record_roles to exist when visibility='shared'.
                if visibility == "shared" and role_ids:
                    for rid in role_ids:
                        await conn.execute("""
                            INSERT INTO record_roles (record_id, role_id, role_name, added_by)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (record_id, role_id) DO NOTHING
                        """,
                            pg_record_id,
                            uuid.UUID(rid),
                            f"Role-{rid[:8]}",
                            uuid.UUID(user_id) if user_id else None,
                        )
                
                await conn.execute(
                    "UPDATE data_records SET visibility = $2, updated_at = NOW() WHERE record_id = $1",
                    pg_record_id, visibility,
                )
                
                # Remove roles that are no longer needed
                if visibility != "shared":
                    await conn.execute(
                        "DELETE FROM record_roles WHERE record_id = $1",
                        pg_record_id,
                    )
        
        return {
            "recordId": record_id,
            "visibility": visibility,
            "roleIds": role_ids or [],
        }

    async def bulk_set_record_visibility(
        self,
        request,
        document_id: str,
        record_ids: List[str],
        visibility: str,
        role_ids: Optional[List[str]] = None,
    ) -> int:
        """Set visibility for multiple records in a document at once."""
        user_id = getattr(request.state, "user_id", None)
        
        if visibility not in ("inherit", "personal", "shared"):
            raise ValueError("visibility must be 'inherit', 'personal', or 'shared'")
        if visibility == "shared" and not role_ids:
            raise ValueError("role_ids required for shared visibility")
        
        updated = 0
        async with self.acquire_with_rls(request) as conn:
            async with conn.transaction():
                recs = await conn.fetch("""
                    SELECT record_id, data->>'id' AS rid
                    FROM data_records
                    WHERE document_id = $1 AND (data->>'id') = ANY($2)
                """, uuid.UUID(document_id), record_ids)
                
                for rec in recs:
                    # Insert roles BEFORE changing visibility (RLS requires
                    # record_roles to exist for shared visibility SELECT check)
                    if visibility == "shared" and role_ids:
                        for rid in role_ids:
                            await conn.execute("""
                                INSERT INTO record_roles (record_id, role_id, role_name, added_by)
                                VALUES ($1, $2, $3, $4)
                                ON CONFLICT (record_id, role_id) DO NOTHING
                            """,
                                rec["record_id"],
                                uuid.UUID(rid),
                                f"Role-{rid[:8]}",
                                uuid.UUID(user_id) if user_id else None,
                            )
                    
                    await conn.execute(
                        "UPDATE data_records SET visibility = $2, updated_at = NOW() WHERE record_id = $1",
                        rec["record_id"], visibility,
                    )
                    
                    if visibility != "shared":
                        await conn.execute(
                            "DELETE FROM record_roles WHERE record_id = $1",
                            rec["record_id"],
                        )
                    
                    updated += 1
        
        return updated

    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    @staticmethod
    def _is_uuid(value: str) -> bool:
        """Check if a string is a valid UUID."""
        try:
            uuid.UUID(value)
            return True
        except (ValueError, AttributeError):
            return False
    
    def _validate_record(self, schema: Dict, record: Dict) -> None:
        """
        Validate a record against a schema.
        
        Raises:
            ValueError: If validation fails
        """
        if not schema or "fields" not in schema:
            return
        
        fields = schema["fields"]
        
        for field_name, field_def in fields.items():
            value = record.get(field_name)
            field_type = field_def.get("type", "string")
            required = field_def.get("required", False)
            
            # Check required
            if required and value is None:
                raise ValueError(f"Required field '{field_name}' is missing")
            
            if value is None:
                continue
            
            # Type validation
            if field_type == "string" and not isinstance(value, str):
                raise ValueError(f"Field '{field_name}' must be a string")
            elif field_type == "integer" and not isinstance(value, int):
                raise ValueError(f"Field '{field_name}' must be an integer")
            elif field_type == "number" and not isinstance(value, (int, float)):
                raise ValueError(f"Field '{field_name}' must be a number")
            elif field_type == "boolean" and not isinstance(value, bool):
                raise ValueError(f"Field '{field_name}' must be a boolean")
            elif field_type == "array" and not isinstance(value, list):
                raise ValueError(f"Field '{field_name}' must be an array")
            elif field_type == "object" and not isinstance(value, dict):
                raise ValueError(f"Field '{field_name}' must be an object")
            elif field_type == "enum":
                allowed = field_def.get("values", [])
                if value not in allowed:
                    raise ValueError(f"Field '{field_name}' must be one of: {allowed}")
            
            # Range validation for numbers
            if field_type in ("integer", "number"):
                min_val = field_def.get("min")
                max_val = field_def.get("max")
                if min_val is not None and value < min_val:
                    raise ValueError(f"Field '{field_name}' must be >= {min_val}")
                if max_val is not None and value > max_val:
                    raise ValueError(f"Field '{field_name}' must be <= {max_val}")
    
    def _record_matches_filter(self, record: Dict, where: Dict) -> bool:
        """
        Check if a record matches a filter condition.
        
        Simple implementation - will be expanded by QueryEngine for complex queries.
        """
        # Handle AND conditions
        if "and" in where:
            return all(self._record_matches_filter(record, cond) for cond in where["and"])
        
        # Handle OR conditions
        if "or" in where:
            return any(self._record_matches_filter(record, cond) for cond in where["or"])
        
        # Handle NOT condition
        if "not" in where:
            return not self._record_matches_filter(record, where["not"])
        
        # Simple field condition
        field = where.get("field")
        op = where.get("op", "eq")
        value = where.get("value")
        
        if not field:
            return True
        
        record_value = record.get(field)
        
        if op == "eq":
            return record_value == value
        elif op == "ne":
            return record_value != value
        elif op == "gt":
            return record_value is not None and record_value > value
        elif op == "gte":
            return record_value is not None and record_value >= value
        elif op == "lt":
            return record_value is not None and record_value < value
        elif op == "lte":
            return record_value is not None and record_value <= value
        elif op == "in":
            return record_value in value
        elif op == "nin":
            return record_value not in value
        elif op == "contains":
            return value in record_value if isinstance(record_value, (str, list)) else False
        elif op == "startswith":
            return record_value.startswith(value) if isinstance(record_value, str) else False
        elif op == "endswith":
            return record_value.endswith(value) if isinstance(record_value, str) else False
        elif op == "isnull":
            return record_value is None if value else record_value is not None
        
        return False
    
    # ========================================================================
    # Admin Operations (bypass RLS via app.is_admin session variable)
    # ========================================================================

    @asynccontextmanager
    async def acquire_admin(self, request):
        """
        Get a connection with both RLS session variables and admin flag set.
        
        The app.is_admin='true' session variable activates admin_docs_select,
        admin_docs_delete, admin_docs_update, and admin_document_roles_*
        RLS policies, allowing full access to all documents and roles.
        
        Uses SET (not SET LOCAL) so the variable persists across nested
        transaction blocks opened by callers.
        
        Callers MUST verify the user has data.admin scope before using this.
        """
        async with self.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            await conn.execute("SET app.is_admin = 'true'")
            try:
                yield conn
            finally:
                await conn.execute("SET app.is_admin = 'false'")

    async def admin_list_all_documents(
        self,
        request,
        source_app: Optional[str] = None,
        visibility: Optional[str] = None,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict:
        """
        List all data documents with safe metadata only (no record contents).
        
        Returns only an explicit allowlist of non-sensitive columns.
        Requires data.admin scope (enforced by the route).
        """
        async with self.acquire_admin(request) as conn:
            where = "doc_type = 'data'"
            params: list = []
            param_idx = 1

            if source_app:
                where += f" AND metadata->>'sourceApp' = ${param_idx}"
                params.append(source_app)
                param_idx += 1

            if visibility:
                where += f" AND visibility = ${param_idx}"
                params.append(visibility)
                param_idx += 1

            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM data_files WHERE {where}", *params
            )

            rows = await conn.fetch(
                f"""
                SELECT 
                    file_id,
                    filename,
                    owner_id,
                    visibility,
                    metadata->>'sourceApp' AS source_app,
                    metadata->>'displayName' AS display_name,
                    data_record_count,
                    data_version,
                    created_at,
                    updated_at
                FROM data_files
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params, limit, offset,
            )

            documents = []
            for row in rows:
                documents.append({
                    "id": str(row["file_id"]),
                    "name": row["filename"],
                    "ownerId": str(row["owner_id"]),
                    "visibility": row["visibility"],
                    "sourceApp": row["source_app"],
                    "displayName": row["display_name"],
                    "recordCount": row["data_record_count"] or 0,
                    "version": row["data_version"] or 1,
                    "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                    "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
                })

            return {
                "documents": documents,
                "total": total or 0,
                "limit": limit,
                "offset": offset,
            }

    async def admin_delete_document(self, request, document_id: str) -> bool:
        """
        Delete a data document regardless of ownership/visibility.
        
        Uses cascading foreign keys to remove document_roles and data_records.
        Requires data.admin scope (enforced by the route).
        """
        async with self.acquire_admin(request) as conn:
            result = await conn.execute(
                "DELETE FROM data_files WHERE file_id = $1 AND doc_type = 'data'",
                uuid.UUID(document_id),
            )
            deleted = result == "DELETE 1"

        if deleted and self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)

        return deleted

    async def admin_list_document_records(
        self,
        request,
        document_id: str,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict:
        """
        List records for a document with metadata only (no data content).
        Requires data.admin scope (enforced by the route).
        """
        async with self.acquire_admin(request) as conn:
            total = await conn.fetchval(
                "SELECT COUNT(*) FROM data_records WHERE document_id = $1",
                uuid.UUID(document_id),
            )

            rows = await conn.fetch(
                """
                SELECT
                    record_id,
                    document_id,
                    owner_id,
                    visibility,
                    created_at,
                    updated_at
                FROM data_records
                WHERE document_id = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                uuid.UUID(document_id), limit, offset,
            )

            records = []
            for row in rows:
                records.append({
                    "recordId": str(row["record_id"]),
                    "documentId": str(row["document_id"]),
                    "ownerId": str(row["owner_id"]) if row["owner_id"] else None,
                    "visibility": row["visibility"],
                    "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                    "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
                })

            return {
                "records": records,
                "total": total or 0,
                "limit": limit,
                "offset": offset,
            }

    async def admin_delete_record(
        self, request, document_id: str, record_id: str
    ) -> bool:
        """
        Delete a single record regardless of ownership/visibility.
        Requires data.admin scope (enforced by the route).
        """
        async with self.acquire_admin(request) as conn:
            result = await conn.execute(
                "DELETE FROM data_records WHERE record_id = $1 AND document_id = $2",
                uuid.UUID(record_id),
                uuid.UUID(document_id),
            )
            return result == "DELETE 1"

    async def admin_get_document(
        self, request, document_id: str, include_records: bool = True
    ) -> Optional[Dict]:
        """
        Get a data document using admin context (bypasses RLS).
        Requires data.admin scope (enforced by the route).
        """
        async with self.acquire_admin(request) as conn:
            row = await conn.fetchrow("""
                SELECT
                    file_id,
                    filename as name,
                    owner_id,
                    visibility,
                    metadata,
                    data_schema,
                    data_content,
                    data_record_count,
                    data_version,
                    data_modified_at,
                    library_id,
                    created_at,
                    updated_at
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
            """, uuid.UUID(document_id))

            if not row:
                return None

            doc = self._row_to_document(row, include_records=False)

            if include_records:
                use_table = await self._use_records_table(conn, document_id)
                if use_table:
                    record_rows = await conn.fetch(
                        "SELECT data FROM data_records WHERE document_id = $1 ORDER BY ordinal, created_at",
                        uuid.UUID(document_id),
                    )
                    doc["records"] = [json.loads(r["data"]) for r in record_rows]
                    doc["recordCount"] = len(doc["records"])
                elif row.get("data_content"):
                    doc["records"] = json.loads(row["data_content"])

            return doc

    def _row_to_document(self, row: asyncpg.Record, include_records: bool = True) -> Dict:
        """
        Convert a database row to a document dict.
        """
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        
        doc = {
            "id": str(row["file_id"]),
            "name": row["name"],
            "ownerId": str(row["owner_id"]) if row["owner_id"] else None,
            "visibility": row["visibility"],
            "metadata": metadata,
            "schema": json.loads(row["data_schema"]) if row["data_schema"] else None,
            "recordCount": row["data_record_count"] or 0,
            "version": row["data_version"] or 1,
            "modifiedAt": row["data_modified_at"].isoformat() if row["data_modified_at"] else None,
            "libraryId": str(row["library_id"]) if row["library_id"] else None,
            "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
            "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        
        # Extract sourceApp from metadata if present
        if metadata.get("sourceApp"):
            doc["sourceApp"] = metadata["sourceApp"]
        
        if include_records and row.get("data_content"):
            doc["records"] = json.loads(row["data_content"])
        
        return doc
    
    def _format_document(self, cached_data: Dict, include_records: bool = True) -> Dict:
        """
        Format cached document data.
        """
        doc = {
            "id": cached_data.get("id"),
            "name": cached_data.get("name"),
            "schema": cached_data.get("schema"),
            "recordCount": len(cached_data.get("records", [])),
            "version": cached_data.get("version", 1),
        }
        
        if include_records:
            doc["records"] = cached_data.get("records", [])
        
        return doc
