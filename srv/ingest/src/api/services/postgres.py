"""
PostgreSQL service for API layer.

Handles database operations for file metadata and status tracking.
"""

import uuid
from typing import Dict, Optional

import asyncpg
import structlog

logger = structlog.get_logger()


class PostgresService:
    """Service for PostgreSQL operations."""
    
    def __init__(self, config: dict):
        """Initialize PostgreSQL connection pool."""
        self.config = config
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", 5432)
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "postgres")
        self.password = config.get("postgres_password", "")
        
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create connection pool."""
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
            logger.info("PostgreSQL connection pool created")
    
    async def disconnect(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("PostgreSQL connection pool closed")
    
    async def check_duplicate(self, content_hash: str) -> Optional[Dict]:
        """
        Check if file with same content hash already exists and is completed.
        
        Returns:
            Existing file record if found, None otherwise
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT 
                    file_id,
                    chunk_count,
                    vector_count,
                    processing_duration_seconds
                FROM ingestion_files
                WHERE content_hash = $1
                AND file_id IN (
                    SELECT file_id FROM ingestion_status
                    WHERE stage = 'completed'
                )
                ORDER BY created_at DESC
                LIMIT 1
            """, content_hash)
            
            if row:
                return {
                    "file_id": str(row["file_id"]),
                    "chunk_count": row["chunk_count"],
                    "vector_count": row["vector_count"],
                    "processing_duration_seconds": row["processing_duration_seconds"],
                }
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
    ) -> str:
        """
        Create file record in ingestion_files table.
        
        Returns:
            file_id
        """
        import json
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO ingestion_files (
                    file_id, user_id, filename, original_filename,
                    mime_type, size_bytes, storage_path, content_hash,
                    metadata, permissions
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                uuid.UUID(file_id),
                uuid.UUID(user_id),
                filename,
                original_filename,
                mime_type,
                size_bytes,
                storage_path,
                content_hash,
                json.dumps(metadata) if isinstance(metadata, dict) else (metadata or '{}'),
                json.dumps({"visibility": "private"}),
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
            
            logger.info(
                "File record created",
                file_id=file_id,
                user_id=user_id,
                content_hash=content_hash,
            )
            
            return file_id
    
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
        async with self.pool.acquire() as conn:
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
        async with self.pool.acquire() as conn:
            # The CASCADE on foreign keys will delete status and chunks automatically
            await conn.execute(
                "DELETE FROM ingestion_files WHERE file_id = $1",
                uuid.UUID(file_id)
            )

    async def get_file_metadata(self, file_id: str) -> Optional[Dict]:
        """Get file metadata by file_id."""
        async with self.pool.acquire() as conn:
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
    ):
        """
        Link new file to existing vectors (duplicate content).
        
        Creates a new file record linked to existing vectors via content_hash.
        """
        import json
        async with self.pool.acquire() as conn:
            # Copy file record from existing file
            existing = await conn.fetchrow("""
                SELECT 
                    filename, original_filename, mime_type, size_bytes,
                    storage_path, content_hash, metadata, chunk_count, vector_count
                FROM ingestion_files
                WHERE file_id = $1
            """, uuid.UUID(existing_file_id))
            
            if not existing:
                raise ValueError(f"Existing file {existing_file_id} not found")
            
            # Create new file record with same content_hash
            await conn.execute("""
                INSERT INTO ingestion_files (
                    file_id, user_id, filename, original_filename,
                    mime_type, size_bytes, storage_path, content_hash,
                    metadata, permissions, chunk_count, vector_count
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            """,
                uuid.UUID(new_file_id),
                uuid.UUID(user_id),
                existing["filename"],
                existing["original_filename"],
                existing["mime_type"],
                existing["size_bytes"],
                existing["storage_path"],
                existing["content_hash"],
                existing["metadata"],
                json.dumps({"visibility": "private"}),
                existing.get("chunk_count", 0),
                existing.get("vector_count", 0),
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
            )
