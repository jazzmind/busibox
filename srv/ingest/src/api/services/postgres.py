"""
PostgreSQL service for API layer.

Handles database operations for file metadata, status tracking, and role management.
"""

import asyncio
import uuid
from typing import Dict, List, Optional
from contextlib import asynccontextmanager

import asyncpg
import structlog

from api.middleware.jwt_auth import set_rls_session_vars

logger = structlog.get_logger()


class PostgresService:
    """Service for PostgreSQL operations."""
    
    def __init__(self, config: dict, request=None):
        """Initialize PostgreSQL connection pool."""
        self.config = config
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", 5432)
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "postgres")
        self.password = config.get("postgres_password", "")
        self.request = request
        
        self.pool: Optional[asyncpg.Pool] = None
        self._pool_loop: Optional[asyncio.AbstractEventLoop] = None
        self._document_roles_ready: bool = False
        self._connect_lock: Optional[asyncio.Lock] = None
    
    async def connect(self):
        """Create connection pool."""
        current_loop = asyncio.get_running_loop()
        
        # Check if we need to reconnect due to event loop change
        if self.pool and self._pool_loop and self._pool_loop != current_loop:
            logger.warning("Event loop changed, closing old pool and reconnecting")
            try:
                # Try to close the old pool, but don't wait too long
                await asyncio.wait_for(self.pool.close(), timeout=1.0)
            except Exception as e:
                logger.warning("Failed to close old pool", error=str(e))
            self.pool = None
            self._pool_loop = None
        
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                min_size=2,
                max_size=10,
            )
            self._pool_loop = current_loop
            logger.info("PostgreSQL connection pool created")
            # Ensure required tables exist (idempotent)
            await self.ensure_schema()

    async def ensure_schema(self) -> None:
        """
        Create ingest tables if missing.
        
        Uses the shared SchemaManager pattern for idempotent schema creation.
        The schema is defined in schema.py and applied on every startup.
        """
        if not self.pool:
            return
        
        # Import schema from parent directory
        import sys
        from pathlib import Path
        schema_path = Path(__file__).parent.parent.parent
        if str(schema_path) not in sys.path:
            sys.path.insert(0, str(schema_path))
        
        from schema import get_ingest_schema
        
        schema = get_ingest_schema()
        async with self.pool.acquire() as conn:
            await schema.apply(conn)
            
            # Apply additional migrations from the migrations directory
            # These are idempotent (use IF NOT EXISTS, ADD COLUMN IF NOT EXISTS, etc.)
            await self._apply_migrations(conn)
        
        logger.info("Ingest schema ensured")
        self._document_roles_ready = True
    
    async def _apply_migrations(self, conn) -> None:
        """
        Apply migration SQL files from the migrations directory.
        
        Migrations should be idempotent (use IF NOT EXISTS, etc.) so they can
        be safely re-run on every startup.
        """
        import os
        from pathlib import Path
        
        # Find migrations directory relative to this file
        # When deployed: /opt/ingest/migrations/ or /app/migrations/
        # When local: srv/ingest/migrations/
        possible_paths = [
            Path(__file__).parent.parent.parent.parent / "migrations",  # From src/api/services/ -> srv/ingest/migrations
            Path("/opt/ingest/migrations"),  # Deployed path
            Path("/app/migrations"),  # Docker path
        ]
        
        migrations_dir = None
        for path in possible_paths:
            if path.exists() and path.is_dir():
                migrations_dir = path
                break
        
        if not migrations_dir:
            logger.debug("No migrations directory found, skipping migrations")
            return
        
        # Order of migrations matters - apply in specific order
        migration_order = [
            "add_markdown_storage.sql",
            "add_multi_flow_support.sql",
            "add_cleanup_stage.sql",
            "add_processing_history.sql",
            "add_rbac_schema.sql",
            "add_rls_policies.sql",
            "003_security_model.sql",
        ]
        
        for migration_file in migration_order:
            migration_path = migrations_dir / migration_file
            if migration_path.exists():
                try:
                    sql = migration_path.read_text()
                    
                    # Execute the entire migration file at once
                    # asyncpg supports multi-statement execution
                    try:
                        await conn.execute(sql)
                        logger.debug(f"Applied migration: {migration_file}")
                    except Exception as e:
                        error_str = str(e).lower()
                        # Ignore "already exists" and "does not exist" errors - migration already applied
                        if "already exists" in error_str or "does not exist" in error_str:
                            logger.debug(f"Migration {migration_file} already applied (or partially applied)")
                        else:
                            logger.warning(f"Migration {migration_file} had errors: {str(e)[:200]}")
                except Exception as e:
                    logger.warning(f"Failed to read migration {migration_file}: {e}")

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
                file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
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
        if self.pool:
            try:
                await self.pool.close()
            except RuntimeError as e:
                # Handle "Event loop is closed" during test teardown
                if "Event loop is closed" in str(e):
                    logger.warning("Could not close pool - event loop already closed")
                else:
                    raise
            self.pool = None
            self._pool_loop = None
            logger.info("PostgreSQL connection pool closed")

    async def _apply_rls(self, conn, request=None):
        """Set RLS session variables for this connection if request is provided."""
        req = request or self.request
        if not req:
            return
        await set_rls_session_vars(conn, req)

    def _get_lock(self) -> asyncio.Lock:
        """Get or create a lock for the current event loop."""
        current_loop = asyncio.get_running_loop()
        if self._connect_lock is None or self._pool_loop != current_loop:
            self._connect_lock = asyncio.Lock()
        return self._connect_lock

    @asynccontextmanager
    async def acquire(self, request=None):
        """
        Get a connection from the pool and apply RLS context for the current request.

        NOTE: The previous implementation mistakenly recursed into itself and
        exhausted the call stack. This version correctly pulls from the pool.
        
        Handles event loop changes (common in testing) by reconnecting if needed.
        
        Args:
            request: FastAPI Request object for RLS context (optional)
        """
        # Ensure we're connected in the current event loop
        current_loop = asyncio.get_running_loop()
        if not self.pool or self._pool_loop != current_loop:
            async with self._get_lock():
                # Double-check after acquiring lock
                if not self.pool or self._pool_loop != current_loop:
                    await self.connect()

        async with self.pool.acquire() as conn:
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
                FROM ingestion_files
                WHERE content_hash = $1
                AND has_markdown = true
                AND chunk_count > 0
                AND file_id IN (
                    SELECT file_id FROM ingestion_status
                    WHERE stage = 'completed'
                )
                ORDER BY created_at DESC
                LIMIT 1
            """, content_hash)
            
            if row:
                # Found a valid duplicate - also clean up any incomplete ones
                # to prevent database bloat
                await conn.execute("""
                    DELETE FROM ingestion_files
                    WHERE content_hash = $1
                    AND file_id != $2
                    AND (
                        has_markdown = false
                        OR chunk_count = 0
                        OR chunk_count IS NULL
                        OR file_id NOT IN (
                            SELECT file_id FROM ingestion_status
                            WHERE stage = 'completed'
                        )
                    )
                """, content_hash, row["file_id"])
                
                return {
                    "file_id": str(row["file_id"]),
                    "chunk_count": row["chunk_count"],
                    "vector_count": row["vector_count"],
                    "processing_duration_seconds": row["processing_duration_seconds"],
                }
            
            # No valid duplicate found - clean up ALL incomplete files with this hash
            # so the new upload can proceed cleanly
            deleted = await conn.execute("""
                DELETE FROM ingestion_files
                WHERE content_hash = $1
                AND (
                    has_markdown = false
                    OR chunk_count = 0
                    OR chunk_count IS NULL
                    OR file_id NOT IN (
                        SELECT file_id FROM ingestion_status
                        WHERE stage = 'completed'
                    )
                )
            """, content_hash)
            
            if deleted and deleted != "DELETE 0":
                logger.info(
                    "Cleaned up incomplete duplicates",
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
    ) -> str:
        """
        Create file record in ingestion_files table with role-based access control.
        
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
        
        Returns:
            file_id
        """
        import json
        async with self.acquire(request) as conn:
            await self._ensure_document_roles(conn)
            # Start transaction
            async with conn.transaction():
                # Create file record with owner_id and visibility
                await conn.execute("""
                    INSERT INTO ingestion_files (
                        file_id, user_id, owner_id, filename, original_filename,
                        mime_type, size_bytes, storage_path, content_hash,
                        metadata, permissions, visibility
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
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
                )
                
                # Create initial status record
                await conn.execute("""
                    INSERT INTO ingestion_status (
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
    ):
        """
        Update a document's visibility and its document_roles atomically.
        """
        if visibility not in ("personal", "shared"):
            raise ValueError("visibility must be 'personal' or 'shared'")

        async with self.acquire() as conn:
            await self._ensure_document_roles(conn)
            async with conn.transaction():
                # Update visibility
                await conn.execute(
                    """
                    UPDATE ingestion_files
                    SET visibility = $2, updated_at = NOW()
                    WHERE file_id = $1
                    """,
                    uuid.UUID(file_id),
                    visibility,
                )

                # Clear existing roles
                await conn.execute(
                    "DELETE FROM document_roles WHERE file_id = $1",
                    uuid.UUID(file_id),
                )

                # Insert new roles for shared
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

    async def insert_audit(
        self,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: Optional[str],
        details: dict,
    ):
        """Insert audit record."""
        async with self.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs (actor_id, action, resource_type, resource_id, details)
                VALUES ($1, $2, $3, $4, $5)
                """,
                uuid.UUID(actor_id),
                action,
                resource_type,
                uuid.UUID(resource_id) if resource_id else None,
                details,
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
        """Update ingestion status for a file."""
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
                UPDATE ingestion_status
                SET {', '.join(fields)}
                WHERE file_id = $1
            """
            
            await conn.execute(query, *params)

    async def delete_file(self, file_id: str) -> None:
        """Delete a file record and all associated data."""
        async with self.acquire() as conn:
            # The CASCADE on foreign keys will delete status and chunks automatically
            await conn.execute(
                "DELETE FROM ingestion_files WHERE file_id = $1",
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
                FROM ingestion_files f
                LEFT JOIN ingestion_status s ON f.file_id = s.file_id
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
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(existing_file_id))
            
            if not existing:
                raise ValueError(f"Existing file {existing_file_id} not found")
            
            # Create new file record with same content_hash AND markdown/image paths
            await conn.execute("""
                INSERT INTO ingestion_files (
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
                INSERT INTO ingestion_status (
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
                UPDATE ingestion_files
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
