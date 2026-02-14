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
    
    @asynccontextmanager
    async def acquire_with_rls(self, request):
        """
        Get a connection with RLS session variables set.
        
        Args:
            request: FastAPI Request object with user_id in state
        """
        user_id = getattr(request.state, "user_id", None)
        role_ids = getattr(request.state, "role_ids", [])
        
        logger.debug(
            "[RLS] Acquiring connection with RLS context",
            user_id=user_id,
            role_ids=role_ids,
            role_count=len(role_ids) if role_ids else 0,
        )
        
        async with self.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            yield conn
    
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
            visibility: 'personal' or 'shared'
            role_ids: Role IDs for shared documents
            library_id: Optional library to place document in
            enable_cache: Whether to enable Redis caching
            source_app: Optional app identifier (e.g., "status-report") for app data libraries
            
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
                            f"Role-{role_id[:8]}",
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
                hint="For 'shared' visibility, roleIds must be provided. For 'personal' visibility, ensure owner_id matches request user_id.",
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
            
            return self._row_to_document(row, include_records)
    
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
    # Record Operations
    # ========================================================================
    
    async def insert_records(
        self,
        request,
        document_id: str,
        records: List[Dict],
        validate: bool = True,
    ) -> Tuple[int, List[str]]:
        """
        Insert records into a data document.
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            records: List of record dicts to insert
            validate: Whether to validate against schema
            
        Returns:
            Tuple of (inserted count, list of new record IDs)
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            # Get current document
            row = await conn.fetchrow("""
                SELECT data_schema, data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            schema = json.loads(row["data_schema"]) if row["data_schema"] else None
            current_records = json.loads(row["data_content"] or "[]")
            
            # Process and validate new records
            new_ids = []
            for record in records:
                if "id" not in record:
                    record["id"] = str(uuid.uuid4())
                new_ids.append(record["id"])
                
                # Add auto fields
                record["_created_at"] = datetime.utcnow().isoformat()
                record["_created_by"] = user_id
                
                if validate and schema:
                    self._validate_record(schema, record)
                
                current_records.append(record)
            
            # Update document
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
            
            # Record history
            batch_id = str(uuid.uuid4())
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
        
        # Invalidate cache
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info(
            "Records inserted",
            document_id=document_id,
            count=len(records),
        )
        
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
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            updates: Field updates to apply
            where: Optional filter for which records to update
            validate: Whether to validate against schema
            
        Returns:
            Number of records updated
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            # Get current document
            row = await conn.fetchrow("""
                SELECT data_schema, data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            schema = json.loads(row["data_schema"]) if row["data_schema"] else None
            current_records = json.loads(row["data_content"] or "[]")
            
            # Apply updates
            updated_count = 0
            batch_id = str(uuid.uuid4())
            
            for i, record in enumerate(current_records):
                if where is None or self._record_matches_filter(record, where):
                    old_record = record.copy()
                    
                    # Apply updates
                    for key, value in updates.items():
                        record[key] = value
                    
                    # Add audit fields
                    record["_updated_at"] = datetime.utcnow().isoformat()
                    record["_updated_by"] = user_id
                    
                    # Validate if needed
                    if validate and schema:
                        self._validate_record(schema, record)
                    
                    current_records[i] = record
                    updated_count += 1
                    
                    # Record history
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
                # Update document
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
        
        # Invalidate cache
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info(
            "Records updated",
            document_id=document_id,
            count=updated_count,
        )
        
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
        
        Args:
            request: FastAPI Request for RLS context
            document_id: Document UUID
            where: Optional filter for which records to delete
            record_ids: Optional list of specific record IDs to delete
            
        Returns:
            Tuple of (number of records deleted, list of deleted record IDs)
        """
        user_id = getattr(request.state, "user_id", None)
        
        async with self.acquire_with_rls(request) as conn:
            # Get current document
            row = await conn.fetchrow("""
                SELECT data_content, data_version
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
                FOR UPDATE
            """, uuid.UUID(document_id))
            
            if not row:
                raise ValueError(f"Document {document_id} not found")
            
            current_records = json.loads(row["data_content"] or "[]")
            
            # Filter records to keep
            deleted_count = 0
            deleted_ids: List[str] = []
            kept_records = []
            batch_id = str(uuid.uuid4())
            
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
                    # Record history
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
                # Update document
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
        
        # Invalidate cache
        if self.cache_manager:
            await self.cache_manager.invalidate_document(document_id)
        
        logger.info(
            "Records deleted",
            document_id=document_id,
            count=deleted_count,
        )
        
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
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict]:
        """
        List data documents accessible to the user.
        
        Args:
            request: FastAPI Request for RLS context
            library_id: Optional filter by library
            visibility: Optional filter by visibility
            source_app: Optional filter by source app (e.g., "status-report")
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
            
            query += f" ORDER BY updated_at DESC LIMIT ${param_idx} OFFSET ${param_idx + 1}"
            params.extend([limit, offset])
            
            rows = await conn.fetch(query, *params)
            
            return [self._row_to_document(row, include_records=False) for row in rows]
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
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
