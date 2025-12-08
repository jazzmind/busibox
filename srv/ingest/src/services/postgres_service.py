"""
PostgreSQL service for metadata operations.

Handles file metadata, chunk storage, and status updates with NOTIFY for SSE.
"""

import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import psycopg2.pool
import structlog

from api.middleware.jwt_auth import set_rls_session_vars_sync

logger = structlog.get_logger()


class PostgresService:
    """Service for PostgreSQL operations."""
    
    def __init__(self, config: dict):
        """Initialize PostgreSQL service with configuration."""
        self.config = config
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", "5432")
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "postgres")
        self.password = config.get("postgres_password", "")
        
        self.pool: Optional[psycopg2.pool.ThreadedConnectionPool] = None
    
    def connect(self):
        """Create connection pool."""
        if self.pool:
            return
        
        try:
            self.pool = psycopg2.pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
            )
            logger.info("PostgreSQL connection pool created")
        except Exception as e:
            logger.error(
                "Failed to create PostgreSQL connection pool",
                error=str(e),
                exc_info=True,
            )
            raise
    
    def close(self):
        """Close database connections."""
        if self.pool:
            self.pool.closeall()
            self.pool = None
            logger.info("PostgreSQL connection pool closed")
    
    def _get_connection(self, request=None):
        """Get connection from pool and set RLS session variables."""
        if not self.pool:
            self.connect()
        conn = self.pool.getconn()
        if request:
            try:
                cursor = conn.cursor()
                set_rls_session_vars_sync(cursor, request)
                cursor.close()
            except Exception as exc:
                logger.error("Failed to set RLS session vars (sync)", error=str(exc))
        return conn
    
    def _return_connection(self, conn):
        """Return connection to pool."""
        if self.pool:
            self.pool.putconn(conn)

    @contextmanager
    def connection(self, request=None):
        """Context manager that applies RLS session vars."""
        conn = self._get_connection(request)
        try:
            yield conn
        finally:
            self._return_connection(conn)
    
    def update_status(
        self,
        file_id: str,
        stage: str,
        progress: int,
        chunks_processed: Optional[int] = None,
        total_chunks: Optional[int] = None,
        pages_processed: Optional[int] = None,
        total_pages: Optional[int] = None,
        error_message: Optional[str] = None,
        retry_count: Optional[int] = None,
    ):
        """
        Update ingestion status and send NOTIFY for SSE.
        
        Args:
            file_id: File identifier
            stage: Processing stage
            progress: Progress percentage (0-100)
            chunks_processed: Chunks processed so far
            total_chunks: Total chunks
            pages_processed: Pages processed so far
            total_pages: Total pages
            error_message: Error message if failed
            retry_count: Number of retry attempts (optional)
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                # Update status
                if retry_count is not None:
                    update_query = """
                        UPDATE ingestion_status
                        SET stage = %s,
                            progress = %s,
                            chunks_processed = %s,
                            total_chunks = %s,
                            pages_processed = %s,
                            total_pages = %s,
                            error_message = %s,
                            retry_count = %s,
                            updated_at = NOW()
                        WHERE file_id = %s
                    """
                    cur.execute(
                        update_query,
                        (
                            stage,
                            progress,
                            chunks_processed,
                            total_chunks,
                            pages_processed,
                            total_pages,
                            error_message,
                            retry_count,
                            file_id,  # Pass string directly, not UUID object
                        ),
                    )
                else:
                    update_query = """
                        UPDATE ingestion_status
                        SET stage = %s,
                            progress = %s,
                            chunks_processed = %s,
                            total_chunks = %s,
                            pages_processed = %s,
                            total_pages = %s,
                            error_message = %s,
                            updated_at = NOW()
                        WHERE file_id = %s
                    """
                    cur.execute(
                        update_query,
                        (
                            stage,
                            progress,
                            chunks_processed,
                            total_chunks,
                            pages_processed,
                            total_pages,
                            error_message,
                            file_id,  # Pass string directly, not UUID object
                        ),
                    )
                
                # Set started_at if queued -> parsing transition
                if stage == "parsing":
                    cur.execute(
                        "UPDATE ingestion_status SET started_at = NOW() WHERE file_id = %s AND started_at IS NULL",
                        (file_id,),  # Pass string directly, not UUID object
                    )
                
                # Set completed_at if completed or failed
                if stage in ["completed", "failed"]:
                    cur.execute(
                        "UPDATE ingestion_status SET completed_at = NOW() WHERE file_id = %s",
                        (file_id,),  # Pass string directly, not UUID object
                    )
                
                conn.commit()
                
                # Send NOTIFY (triggered automatically by database trigger, but we can also send explicitly)
                # The trigger in the database handles NOTIFY automatically
                
                logger.debug(
                    "Status updated",
                    file_id=file_id,
                    stage=stage,
                    progress=progress,
                )
        
        except Exception as e:
            conn.rollback()
            logger.error(
                "Failed to update status",
                file_id=file_id,
                stage=stage,
                error=str(e),
                exc_info=True,
            )
            raise
        finally:
            self._return_connection(conn)
    
    def insert_chunks(self, file_id: str, chunks: List[Dict]):
        """
        Insert chunk metadata into PostgreSQL.
        
        Args:
            file_id: File identifier
            chunks: List of chunk dictionaries with text, chunk_index, etc.
        """
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                insert_query = """
                    INSERT INTO ingestion_chunks (
                        file_id, chunk_index, text, char_offset,
                        token_count, page_number, section_heading, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (file_id, chunk_index) DO NOTHING
                """
                
                for chunk in chunks:
                    metadata = chunk.get("metadata", {})
                    cur.execute(
                        insert_query,
                        (
                            file_id,  # Pass string directly, not UUID object
                            chunk.get("chunk_index", 0),
                            chunk.get("text", ""),
                            chunk.get("char_offset"),
                            chunk.get("token_count", 0),
                            chunk.get("page_number"),
                            chunk.get("section_heading"),
                            json.dumps(metadata) if metadata else None,  # Convert dict to JSON string
                        ),
                    )
                
                conn.commit()
                
                logger.info(
                    "Chunks inserted into PostgreSQL",
                    file_id=file_id,
                    chunk_count=len(chunks),
                )
        
        except Exception as e:
            conn.rollback()
            logger.error(
                "Failed to insert chunks",
                file_id=file_id,
                error=str(e),
                exc_info=True,
            )
            raise
        finally:
            self._return_connection(conn)
    
    def update_file_metadata(
        self,
        file_id: str,
        document_type: Optional[str] = None,
        primary_language: Optional[str] = None,
        detected_languages: Optional[List[str]] = None,
        extracted_title: Optional[str] = None,
        extracted_author: Optional[str] = None,
        extracted_date: Optional[datetime] = None,
        extracted_keywords: Optional[List[str]] = None,
        chunk_count: Optional[int] = None,
        vector_count: Optional[int] = None,
        processing_duration_seconds: Optional[int] = None,
    ):
        """Update file metadata in ingestion_files table."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cur:
                update_fields = []
                update_values = []
                
                if document_type is not None:
                    update_fields.append("document_type = %s")
                    update_values.append(document_type)
                
                if primary_language is not None:
                    update_fields.append("primary_language = %s")
                    update_values.append(primary_language)
                
                if detected_languages is not None:
                    update_fields.append("detected_languages = %s")
                    update_values.append(detected_languages)
                
                if extracted_title is not None:
                    update_fields.append("extracted_title = %s")
                    update_values.append(extracted_title)
                
                if extracted_author is not None:
                    update_fields.append("extracted_author = %s")
                    update_values.append(extracted_author)
                
                if extracted_date is not None:
                    update_fields.append("extracted_date = %s")
                    update_values.append(extracted_date)
                
                if extracted_keywords is not None:
                    update_fields.append("extracted_keywords = %s")
                    update_values.append(extracted_keywords)
                
                if chunk_count is not None:
                    update_fields.append("chunk_count = %s")
                    update_values.append(chunk_count)
                
                if vector_count is not None:
                    update_fields.append("vector_count = %s")
                    update_values.append(vector_count)
                
                if processing_duration_seconds is not None:
                    update_fields.append("processing_duration_seconds = %s")
                    update_values.append(processing_duration_seconds)
                
                if update_fields:
                    update_fields.append("updated_at = NOW()")
                    update_values.append(file_id)  # Pass string directly, not UUID object
                    
                    update_query = f"""
                        UPDATE ingestion_files
                        SET {', '.join(update_fields)}
                        WHERE file_id = %s
                    """
                    
                    cur.execute(update_query, update_values)
                    conn.commit()
                    
                    logger.debug(
                        "File metadata updated",
                        file_id=file_id,
                        fields=len(update_fields) - 1,  # Exclude updated_at
                    )
        
        except Exception as e:
            conn.rollback()
            logger.error(
                "Failed to update file metadata",
                file_id=file_id,
                error=str(e),
                exc_info=True,
            )
            raise
        finally:
            self._return_connection(conn)
