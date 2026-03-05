"""
PostgreSQL service for API layer.

Handles database operations for file metadata, status tracking, and role management.
Uses the shared AsyncPGPoolManager for connection pooling.
"""

import uuid
from typing import Dict, List, Optional
from contextlib import asynccontextmanager

import asyncpg
import structlog

from busibox_common import AsyncPGPoolManager, PoolConfig
from api.middleware.jwt_auth import set_rls_session_vars

logger = structlog.get_logger()


# Test mode header name
TEST_MODE_HEADER = "X-Test-Mode"


class PostgresService:
    """
    Data PostgreSQL service with domain-specific operations.
    
    Uses the shared AsyncPGPoolManager for connection pooling.
    """
    
    def __init__(self, config: dict, request=None, use_test_db: bool = False):
        """Initialize PostgreSQL connection pool.
        
        Args:
            config: Database configuration dictionary
            request: Optional FastAPI request object
            use_test_db: If True, use test database configuration
        """
        self.config = config
        self.request = request
        
        # Build pool config based on test mode
        if use_test_db and config.get("test_mode_enabled"):
            pool_config = PoolConfig(
                host=config.get("postgres_host", "postgres"),
                port=int(config.get("postgres_port", 5432)),
                database=config.get("test_postgres_db", "test_files"),
                user=config.get("test_postgres_user", "busibox_test_user"),
                password=config.get("test_postgres_password", "testpassword"),
            )
            self._is_test_db = True
        else:
            pool_config = PoolConfig(
                host=config.get("postgres_host", "postgres"),
                port=int(config.get("postgres_port", 5432)),
                database=config.get("postgres_db", "busibox"),
                user=config.get("postgres_user", "postgres"),
                password=config.get("postgres_password", ""),
            )
            self._is_test_db = False
        
        self._pool_manager = AsyncPGPoolManager(pool_config)
        self._document_roles_ready: bool = False
    
    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        """Access the underlying pool (for compatibility with existing code)."""
        return self._pool_manager.pool
    
    async def connect(self):
        """Create connection pool."""
        await self._pool_manager.connect()
        # Ensure required tables exist (idempotent)
        await self.ensure_schema()

    async def ensure_schema(self) -> None:
        """
        Create data tables if missing.
        
        Uses the shared SchemaManager pattern for idempotent schema creation.
        The schema is defined in schema.py and applied on every startup.
        """
        if not self._pool_manager.is_connected:
            return
        
        # Import schema from parent directory
        import sys
        from pathlib import Path
        schema_path = Path(__file__).parent.parent.parent
        if str(schema_path) not in sys.path:
            sys.path.insert(0, str(schema_path))
        
        from schema import get_data_schema
        
        schema = get_data_schema()
        async with self._pool_manager.acquire() as conn:
            await schema.apply(conn)
            
            # Note: All migrations have been collapsed into schema.py
            # No separate migration files are needed anymore
        
        logger.info("Data schema ensured")
        self._document_roles_ready = True

    async def _ensure_document_roles(self, conn):
        """
        Defensive: ensure document_roles table exists (for environments where
        migrations have not been applied yet). Uses IF NOT EXISTS so it is safe
        to run repeatedly.
        
        Note: This is now handled by ensure_schema() on connect(), but kept for
        backward compatibility with existing code paths.
        """
        if self._document_roles_ready:
            return

        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS document_roles (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                file_id UUID NOT NULL REFERENCES data_files(file_id) ON DELETE CASCADE,
                role_id UUID NOT NULL,
                role_name VARCHAR(100) NOT NULL,
                added_at TIMESTAMP DEFAULT NOW(),
                added_by UUID,
                UNIQUE(file_id, role_id)
            );
            CREATE INDEX IF NOT EXISTS idx_document_roles_file ON document_roles(file_id);
            CREATE INDEX IF NOT EXISTS idx_document_roles_role ON document_roles(role_id);
            CREATE INDEX IF NOT EXISTS idx_document_roles_name ON document_roles(role_name);
            """
        )
        self._document_roles_ready = True
    
    async def disconnect(self):
        """Close connection pool."""
        await self._pool_manager.disconnect()

    async def _apply_rls(self, conn, request=None):
        """Set RLS session variables for this connection if request is provided."""
        req = request or self.request
        if not req:
            return
        await set_rls_session_vars(conn, req)

    @asynccontextmanager
    async def acquire(self, request=None):
        """
        Get a connection from the pool and apply RLS context for the current request.

        Uses the shared AsyncPGPoolManager which handles event loop changes
        and ensures proper connection pooling.
        
        Args:
            request: FastAPI Request object for RLS context (optional)
        """
        async with self._pool_manager.acquire() as conn:
            # Use provided request or fall back to instance request (for backward compatibility)
            req = request or self.request
            if req:
                await self._apply_rls(conn, req)
            yield conn
    
    async def check_duplicate(self, content_hash: str, request=None) -> Optional[Dict]:
        """
        Check if file with same content hash already exists and is FULLY completed.
        Also cleans up incomplete/corrupt duplicates to prevent them from blocking
        future uploads.
        
        A file is considered fully completed when:
        - status.stage = 'completed'
        - has_markdown = true (for document types that generate markdown)
        - chunk_count > 0
        
        Args:
            content_hash: SHA-256 content hash to check
            request: Optional FastAPI Request for RLS context
        
        Returns:
            Existing file record if found, None otherwise
        """
        async with self.acquire(request) as conn:
            # First, find a fully completed duplicate
            row = await conn.fetchrow("""
                SELECT 
                    file_id,
                    chunk_count,
                    vector_count,
                    processing_duration_seconds
                FROM data_files
                WHERE content_hash = $1
                AND has_markdown = true
                AND chunk_count > 0
                AND file_id IN (
                    SELECT file_id FROM data_status
                    WHERE stage = 'completed'
                )
                ORDER BY created_at DESC
                LIMIT 1
            """, content_hash)
            
            if row:
                # Found a valid duplicate - clean up incomplete copies that are
                # old enough to be considered orphaned (>30min).  Recent records
                # may still be processing in the worker queue.
                await conn.execute("""
                    DELETE FROM data_files
                    WHERE content_hash = $1
                    AND file_id != $2
                    AND (
                        has_markdown = false
                        OR chunk_count = 0
                        OR chunk_count IS NULL
                        OR file_id NOT IN (
                            SELECT file_id FROM data_status
                            WHERE stage = 'completed'
                        )
                    )
                    AND created_at < NOW() - INTERVAL '30 minutes'
                """, content_hash, row["file_id"])
                
                return {
                    "file_id": str(row["file_id"]),
                    "chunk_count": row["chunk_count"],
                    "vector_count": row["vector_count"],
                    "processing_duration_seconds": row["processing_duration_seconds"],
                }
            
            # No valid duplicate found.
            # DO NOT aggressively delete incomplete records here — they may be
            # actively processing in the worker queue.  Only clean up records
            # that have been stuck for over 30 minutes (likely orphaned).
            deleted = await conn.execute("""
                DELETE FROM data_files
                WHERE content_hash = $1
                AND (
                    has_markdown = false
                    OR chunk_count = 0
                    OR chunk_count IS NULL
                    OR file_id NOT IN (
                        SELECT file_id FROM data_status
                        WHERE stage = 'completed'
                    )
                )
                AND created_at < NOW() - INTERVAL '30 minutes'
            """, content_hash)
            
            if deleted and deleted != "DELETE 0":
                logger.info(
                    "Cleaned up stale incomplete duplicates (>30min old)",
                    content_hash=content_hash[:16] + "...",
                    deleted=deleted,
                )
            
            return None
    
    async def create_file_record(
        self,
        file_id: str,
        user_id: str,
        filename: str,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        content_hash: str,
        metadata: Optional[Dict] = None,
        visibility: str = "personal",
        role_ids: Optional[List[str]] = None,
        request=None,
        library_id: Optional[str] = None,
        is_encrypted: bool = False,
    ) -> str:
        """
        Create file record in data_files table with role-based access control.
        
        Args:
            file_id: Unique file identifier
            user_id: Owner user ID
            filename: Display filename
            original_filename: Original uploaded filename
            mime_type: MIME type
            size_bytes: File size in bytes
            storage_path: MinIO storage path
            content_hash: SHA-256 content hash
            metadata: Optional metadata dict
            visibility: 'personal' (owner only) or 'shared' (role-based)
            role_ids: List of role IDs (required if visibility='shared')
            request: Optional FastAPI Request for RLS context
            library_id: Optional library ID to associate the file with
            is_encrypted: Whether the file content is encrypted at rest
        
        Returns:
            file_id
        """
        import json
        async with self.acquire(request) as conn:
            await self._ensure_document_roles(conn)
            
            # Debug: Check RLS session variable
            rls_user = await conn.fetchval("SELECT current_setting('app.user_id', true)")
            print(f"[pg_service] RLS app.user_id = {rls_user}")
            print(f"[pg_service] Inserting file: file_id={file_id}, owner_id={user_id}")
            
            # Start transaction
            async with conn.transaction():
                # Create file record with owner_id, visibility, and library_id
                lib_uuid = uuid.UUID(library_id) if library_id else None
                print(f"[pg_service] Inserting with library_id={library_id}")
                
                await conn.execute("""
                    INSERT INTO data_files (
                        file_id, user_id, owner_id, filename, original_filename,
                        mime_type, size_bytes, storage_path, content_hash,
                        metadata, permissions, visibility, library_id, is_encrypted
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                    uuid.UUID(file_id),
                    uuid.UUID(user_id),
                    uuid.UUID(user_id),  # owner_id = user_id
                    filename,
                    original_filename,
                    mime_type,
                    size_bytes,
                    storage_path,
                    content_hash,
                    json.dumps(metadata) if isinstance(metadata, dict) else (metadata or '{}'),
                    json.dumps({"visibility": visibility}),
                    visibility,
                    lib_uuid,  # library_id
                    is_encrypted,  # encryption status
                )
                
                # Create initial status record
                await conn.execute("""
                    INSERT INTO data_status (
                        file_id, stage, progress, started_at
                    ) VALUES ($1, $2, $3, NOW())
                """,
                    uuid.UUID(file_id),
                    "queued",
                    0,
                )
                
                # Add role assignments if shared
                if visibility == "shared" and role_ids:
                    for role_id in role_ids:
                        await conn.execute("""
                            INSERT INTO document_roles (
                                file_id, role_id, role_name, added_by
                            ) VALUES ($1, $2, $3, $4)
                        """,
                            uuid.UUID(file_id),
                            uuid.UUID(role_id),
                            f"Role-{role_id[:8]}",  # Placeholder name, will be updated by caller
                            uuid.UUID(user_id),
                        )
            
            logger.info(
                "File record created",
                file_id=file_id,
                user_id=user_id,
                visibility=visibility,
                role_count=len(role_ids) if role_ids else 0,
                content_hash=content_hash,
            )
            
            return file_id

    async def update_document_visibility_and_roles(
        self,
        file_id: str,
        visibility: str,
        role_ids: Optional[List[str]],
        actor_id: str,
        library_id: Optional[str] = None,
        request=None,
    ):
        """
        Update a document's visibility, library, and document_roles atomically.
        
        Requires a request with RLS context. The update policies use
        WITH CHECK (true), so the USING clause gates on the current row
        (ownership for personal, role access for shared) while allowing
        the new row values to be anything.
        """
        if visibility not in ("personal", "shared", "authenticated"):
            raise ValueError("visibility must be 'personal', 'shared', or 'authenticated'")

        async with self.acquire(request) as conn:
            await self._ensure_document_roles(conn)
            async with conn.transaction():
                # When changing to shared, insert roles BEFORE updating visibility.
                # PostgreSQL RLS checks SELECT visibility of the new row after UPDATE;
                # shared_docs_select requires a matching document_roles entry.
                if visibility == "shared" and role_ids:
                    for role_id in role_ids:
                        await conn.execute(
                            """
                            INSERT INTO document_roles (file_id, role_id, role_name, added_by)
                            VALUES ($1, $2, $3, $4)
                            ON CONFLICT (file_id, role_id) DO NOTHING
                            """,
                            uuid.UUID(file_id),
                            uuid.UUID(role_id),
                            f"Role-{role_id[:8]}",
                            uuid.UUID(actor_id),
                        )

                if library_id:
                    await conn.execute(
                        """
                        UPDATE data_files
                        SET visibility = $2, library_id = $3, updated_at = NOW()
                        WHERE file_id = $1
                        """,
                        uuid.UUID(file_id),
                        visibility,
                        uuid.UUID(library_id),
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE data_files
                        SET visibility = $2, updated_at = NOW()
                        WHERE file_id = $1
                        """,
                        uuid.UUID(file_id),
                        visibility,
                    )

                # Clean up old roles that don't match the new set
                if visibility == "shared" and role_ids:
                    role_uuids = [uuid.UUID(r) for r in role_ids]
                    await conn.execute(
                        "DELETE FROM document_roles WHERE file_id = $1 AND role_id != ALL($2)",
                        uuid.UUID(file_id),
                        role_uuids,
                    )
                elif visibility in ("personal", "authenticated"):
                    await conn.execute(
                        "DELETE FROM document_roles WHERE file_id = $1",
                        uuid.UUID(file_id),
                    )

    async def update_status(
        self,
        file_id: str,
        stage: str,
        progress: int = None,
        error_message: str = None,
        chunks_processed: int = None,
        total_chunks: int = None,
        pages_processed: int = None,
        total_pages: int = None,
    ) -> None:
        """Update data status for a file."""
        async with self.acquire() as conn:
            fields = ["stage = $2", "updated_at = NOW()"]
            params = [uuid.UUID(file_id), stage]
            param_num = 3
            
            if progress is not None:
                fields.append(f"progress = ${param_num}")
                params.append(progress)
                param_num += 1
            
            if error_message is not None:
                fields.append(f"error_message = ${param_num}")
                params.append(error_message)
                param_num += 1
            
            if chunks_processed is not None:
                fields.append(f"chunks_processed = ${param_num}")
                params.append(chunks_processed)
                param_num += 1
            
            if total_chunks is not None:
                fields.append(f"total_chunks = ${param_num}")
                params.append(total_chunks)
                param_num += 1
            
            if pages_processed is not None:
                fields.append(f"pages_processed = ${param_num}")
                params.append(pages_processed)
                param_num += 1
            
            if total_pages is not None:
                fields.append(f"total_pages = ${param_num}")
                params.append(total_pages)
                param_num += 1
            
            # Set completed_at if stage is completed or failed
            if stage in ("completed", "failed"):
                fields.append("completed_at = NOW()")
            
            query = f"""
                UPDATE data_status
                SET {', '.join(fields)}
                WHERE file_id = $1
            """
            
            await conn.execute(query, *params)

    async def delete_file(self, file_id: str) -> None:
        """Delete a file record and all associated data."""
        async with self.acquire() as conn:
            # The CASCADE on foreign keys will delete status and chunks automatically
            await conn.execute(
                "DELETE FROM data_files WHERE file_id = $1",
                uuid.UUID(file_id)
            )

    async def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        """Get file metadata by file_id."""
        async with self.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    f.file_id::text,
                    f.user_id::text,
                    f.filename,
                    f.original_filename,
                    f.mime_type,
                    f.size_bytes,
                    f.storage_path,
                    f.content_hash,
                    f.document_type,
                    f.primary_language,
                    f.detected_languages,
                    f.classification_confidence,
                    f.chunk_count,
                    f.vector_count,
                    f.processing_duration_seconds,
                    f.extracted_title,
                    f.extracted_author,
                    f.extracted_date,
                    f.extracted_keywords,
                    f.metadata,
                    f.permissions,
                    f.created_at,
                    f.updated_at,
                    s.stage,
                    s.progress,
                    s.error_message
                FROM data_files f
                LEFT JOIN data_status s ON f.file_id = s.file_id
                WHERE f.file_id = $1
            """, uuid.UUID(file_id))
            
            if row:
                return dict(row)
            return None
    
    async def reuse_vectors(
        self,
        new_file_id: str,
        existing_file_id: str,
        user_id: str,
        request=None,
    ):
        """
        Link new file to existing vectors (duplicate content).
        
        Creates a new file record linked to existing vectors via content_hash.
        Also copies markdown and image paths so the duplicate file has full functionality.
        
        Args:
            new_file_id: ID for the new file record
            existing_file_id: ID of the existing file to copy from
            user_id: User ID for the new file
            request: FastAPI Request for RLS context (required)
        """
        import json
        async with self.acquire(request) as conn:
            # Copy file record from existing file (including markdown/images)
            existing = await conn.fetchrow("""
                SELECT 
                    filename, original_filename, mime_type, size_bytes,
                    storage_path, content_hash, metadata, chunk_count, vector_count,
                    has_markdown, markdown_path, images_path, image_count
                FROM data_files
                WHERE file_id = $1
            """, uuid.UUID(existing_file_id))
            
            if not existing:
                raise ValueError(f"Existing file {existing_file_id} not found")
            
            # Create new file record with same content_hash AND markdown/image paths
            await conn.execute("""
                INSERT INTO data_files (
                    file_id, user_id, owner_id, filename, original_filename,
                    mime_type, size_bytes, storage_path, content_hash,
                    metadata, permissions, chunk_count, vector_count, visibility,
                    has_markdown, markdown_path, images_path, image_count
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18)
            """,
                uuid.UUID(new_file_id),
                uuid.UUID(user_id),
                uuid.UUID(user_id),  # owner_id = user_id
                existing["filename"],
                existing["original_filename"],
                existing["mime_type"],
                existing["size_bytes"],
                existing["storage_path"],
                existing["content_hash"],
                existing["metadata"],
                json.dumps({"visibility": "personal"}),
                existing.get("chunk_count", 0),
                existing.get("vector_count", 0),
                "personal",  # visibility
                existing.get("has_markdown", False),
                existing.get("markdown_path"),
                existing.get("images_path"),
                existing.get("image_count", 0),
            )
            
            # Create completed status
            await conn.execute("""
                INSERT INTO data_status (
                    file_id, stage, progress, completed_at
                ) VALUES ($1, $2, $3, NOW())
            """,
                uuid.UUID(new_file_id),
                "completed",
                100,
            )
            
            logger.info(
                "Vectors reused for duplicate",
                new_file_id=new_file_id,
                existing_file_id=existing_file_id,
                user_id=user_id,
                has_markdown=existing.get("has_markdown", False),
            )
    
    # ========================================================================
    # Role Management Methods
    # ========================================================================
    
    async def get_document_roles(self, file_id: str) -> List[Dict]:
        """
        Get all roles assigned to a document.
        
        Returns:
            List of role dictionaries with role_id, role_name, added_at, added_by
        """
        async with self.acquire() as conn:
            await self._ensure_document_roles(conn)
            rows = await conn.fetch("""
                SELECT 
                    role_id::text,
                    role_name,
                    added_at,
                    added_by::text
                FROM document_roles
                WHERE file_id = $1
                ORDER BY added_at
            """, uuid.UUID(file_id))
            
            return [dict(row) for row in rows]
    
    async def add_document_role(
        self,
        file_id: str,
        role_id: str,
        role_name: str,
        added_by: str,
    ) -> None:
        """
        Add a role to a document.
        
        Raises:
            Exception if role already assigned
        """
        async with self.acquire() as conn:
            await self._ensure_document_roles(conn)
            await conn.execute("""
                INSERT INTO document_roles (
                    file_id, role_id, role_name, added_by
                ) VALUES ($1, $2, $3, $4)
                ON CONFLICT (file_id, role_id) DO NOTHING
            """,
                uuid.UUID(file_id),
                uuid.UUID(role_id),
                role_name,
                uuid.UUID(added_by),
            )
            
            logger.info(
                "Role added to document",
                file_id=file_id,
                role_id=role_id,
                role_name=role_name,
            )
    
    async def remove_document_role(
        self,
        file_id: str,
        role_id: str,
    ) -> None:
        """
        Remove a role from a document.
        
        Note: The trigger will prevent removing the last role from a shared document.
        """
        async with self.acquire() as conn:
            await self._ensure_document_roles(conn)
            await conn.execute("""
                DELETE FROM document_roles
                WHERE file_id = $1 AND role_id = $2
            """,
                uuid.UUID(file_id),
                uuid.UUID(role_id),
            )
            
            logger.info(
                "Role removed from document",
                file_id=file_id,
                role_id=role_id,
            )
    
    async def update_file_visibility(
        self,
        file_id: str,
        visibility: str,
    ) -> None:
        """
        Update document visibility (personal/shared).
        """
        async with self.acquire() as conn:
            await conn.execute("""
                UPDATE data_files
                SET visibility = $2,
                    updated_at = NOW()
                WHERE file_id = $1
            """,
                uuid.UUID(file_id),
                visibility,
            )
            
            logger.info(
                "Document visibility updated",
                file_id=file_id,
                visibility=visibility,
            )
    
    # =========================================================================
    # Library Trigger Operations
    # =========================================================================
    
    async def create_library_trigger(
        self,
        library_id: str,
        name: str,
        created_by: str,
        trigger_type: str = "run_agent",
        agent_id: Optional[str] = None,
        prompt: Optional[str] = None,
        schema_document_id: Optional[str] = None,
        notification_config: Optional[Dict] = None,
        description: Optional[str] = None,
        delegation_token: Optional[str] = None,
        delegation_scopes: Optional[List] = None,
    ) -> Dict:
        """Create a library trigger that fires when docs complete in a library."""
        trigger_id = uuid.uuid4()
        import json
        async with self.acquire() as conn:
            await conn.execute("""
                INSERT INTO library_triggers (
                    id, library_id, name, description, trigger_type, agent_id, prompt,
                    schema_document_id, notification_config, is_active, created_by,
                    delegation_token, delegation_scopes
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10, $11, $12)
            """,
                trigger_id,
                uuid.UUID(library_id),
                name,
                description,
                trigger_type,
                uuid.UUID(agent_id) if agent_id else None,
                prompt,
                uuid.UUID(schema_document_id) if schema_document_id else None,
                json.dumps(notification_config) if notification_config is not None else None,
                uuid.UUID(created_by),
                delegation_token,
                json.dumps(delegation_scopes or []),
            )
            
            row = await conn.fetchrow(
                "SELECT * FROM library_triggers WHERE id = $1", trigger_id
            )
            
            logger.info(
                "Library trigger created",
                trigger_id=str(trigger_id),
                library_id=library_id,
                name=name,
            )
            return dict(row)
    
    async def list_library_triggers(
        self,
        library_id: str,
        active_only: bool = False,
    ) -> List[Dict]:
        """List triggers for a library."""
        async with self.acquire() as conn:
            query = "SELECT * FROM library_triggers WHERE library_id = $1"
            params = [uuid.UUID(library_id)]
            if active_only:
                query += " AND is_active = true"
            query += " ORDER BY created_at DESC"
            rows = await conn.fetch(query, *params)
            return [dict(r) for r in rows]
    
    async def get_library_trigger(self, trigger_id: str) -> Optional[Dict]:
        """Get a single library trigger by ID."""
        async with self.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM library_triggers WHERE id = $1",
                uuid.UUID(trigger_id),
            )
            return dict(row) if row else None
    
    async def update_library_trigger(
        self,
        trigger_id: str,
        **kwargs,
    ) -> Optional[Dict]:
        """Update a library trigger."""
        set_clauses = ["updated_at = NOW()"]
        params = []
        idx = 1
        
        allowed = {
            "is_active",
            "name",
            "description",
            "trigger_type",
            "prompt",
            "schema_document_id",
            "agent_id",
            "notification_config",
        }
        for key, value in kwargs.items():
            if key in allowed and value is not None:
                idx += 1
                if key in ("schema_document_id", "agent_id"):
                    set_clauses.append(f"{key} = ${idx}")
                    params.append(uuid.UUID(value) if value else None)
                elif key == "notification_config":
                    import json
                    set_clauses.append(f"{key} = ${idx}")
                    params.append(json.dumps(value))
                else:
                    set_clauses.append(f"{key} = ${idx}")
                    params.append(value)
        
        if len(set_clauses) == 1:
            # Nothing to update
            return await self.get_library_trigger(trigger_id)
        
        async with self.acquire() as conn:
            await conn.execute(
                f"UPDATE library_triggers SET {', '.join(set_clauses)} WHERE id = $1",
                uuid.UUID(trigger_id),
                *params,
            )
            return await self.get_library_trigger(trigger_id)
    
    async def delete_library_trigger(self, trigger_id: str) -> bool:
        """Delete a library trigger."""
        async with self.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM library_triggers WHERE id = $1",
                uuid.UUID(trigger_id),
            )
            deleted = result == "DELETE 1"
            if deleted:
                logger.info("Library trigger deleted", trigger_id=trigger_id)
            return deleted
    
    async def get_active_triggers_for_library(self, library_id: str) -> List[Dict]:
        """Get all active triggers for a library (used by worker on doc completion)."""
        async with self.acquire() as conn:
            rows = await conn.fetch("""
                SELECT lt.*, l.name as library_name
                FROM library_triggers lt
                JOIN libraries l ON l.id = lt.library_id
                WHERE lt.library_id = $1 AND lt.is_active = true
            """, uuid.UUID(library_id))
            return [dict(r) for r in rows]
    
    async def record_trigger_execution(
        self,
        trigger_id: str,
        error: Optional[str] = None,
    ) -> None:
        """Record that a trigger was executed (increment count, update timestamp)."""
        async with self.acquire() as conn:
            if error:
                await conn.execute("""
                    UPDATE library_triggers
                    SET execution_count = execution_count + 1,
                        last_execution_at = NOW(),
                        last_error = $2,
                        updated_at = NOW()
                    WHERE id = $1
                """, uuid.UUID(trigger_id), error)
            else:
                await conn.execute("""
                    UPDATE library_triggers
                    SET execution_count = execution_count + 1,
                        last_execution_at = NOW(),
                        last_error = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                """, uuid.UUID(trigger_id))
