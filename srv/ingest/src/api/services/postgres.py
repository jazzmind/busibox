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
        self.host = config.get("postgres_host", "10.96.200.26")
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
                metadata or {},
                {"visibility": "private"},
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
        async with self.pool.acquire() as conn:
            # Copy file record from existing file
            existing = await conn.fetchrow("""
                SELECT 
                    filename, original_filename, mime_type, size_bytes,
                    storage_path, content_hash, metadata
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
                {"visibility": "private"},
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

