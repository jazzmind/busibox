"""
Processing History Service

Tracks detailed processing steps, errors, and timing for document ingestion.
Provides visibility into what happened during processing.
"""

import time
from typing import Optional, Dict, Any
import structlog

logger = structlog.get_logger()


class ProcessingHistoryService:
    """Service for logging processing history."""
    
    def __init__(self, config: dict):
        """Initialize with database configuration."""
        self.config = config
        self.pool = None
    
    def connect(self):
        """Establish connection pool to database."""
        if self.pool is None:
            import psycopg2.pool
            try:
                self.pool = psycopg2.pool.SimpleConnectionPool(
                    1, 10,
                    host=self.config.get("postgres_host"),
                    port=self.config.get("postgres_port", 5432),
                    database=self.config.get("files_db"),
                    user=self.config.get("postgres_user"),
                    password=self.config.get("postgres_password"),
                )
                logger.info("ProcessingHistoryService connected to PostgreSQL")
            except Exception as e:
                logger.error("Failed to connect ProcessingHistoryService", error=str(e))
                raise
    
    def disconnect(self):
        """Close the connection pool."""
        if self.pool:
            self.pool.closeall()
            self.pool = None
            logger.info("ProcessingHistoryService disconnected")
    
    def _get_connection(self):
        """Get a connection from the pool."""
        if not self.pool:
            logger.error("ProcessingHistoryService not connected")
            return None
        return self.pool.getconn()
    
    def _return_connection(self, conn):
        """Return a connection to the pool."""
        if self.pool and conn:
            self.pool.putconn(conn)
    
    def log_step(
        self,
        file_id: str,
        stage: str,
        step_name: str,
        status: str,
        message: Optional[str] = None,
        error_message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
        started_at: Optional[float] = None,
    ):
        """
        Log a processing step.
        
        Args:
            file_id: File identifier
            stage: Processing stage (queued, parsing, etc.)
            step_name: Name of the step (e.g., "download_from_minio", "extract_text")
            status: Step status (started, completed, failed, skipped)
            message: Success message or description
            error_message: Error message if failed
            metadata: Additional metadata (dict)
            duration_ms: Duration in milliseconds
            started_at: Start timestamp (for calculating duration)
        """
        if not self.pool:
            logger.warning("ProcessingHistoryService not connected, skipping log")
            return
            
        try:
            conn = self._get_connection()
            if not conn:
                return
            try:
                with conn.cursor() as cur:
                    # Calculate duration if started_at provided and not already set
                    if started_at and not duration_ms:
                        duration_ms = int((time.time() - started_at) * 1000)
                    
                    import json
                    metadata_json = json.dumps(metadata) if metadata else '{}'
                    
                    cur.execute("""
                        INSERT INTO processing_history (
                            file_id, stage, step_name, status, message, 
                            error_message, metadata, duration_ms,
                            completed_at
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s,
                            CASE WHEN %s IN ('completed', 'failed', 'skipped') THEN NOW() ELSE NULL END
                        )
                    """, (
                        file_id,
                        stage,
                        step_name,
                        status,
                        message,
                        error_message,
                        metadata_json,
                        duration_ms,
                        status,
                    ))
                    conn.commit()
                    
                    logger.debug(
                        "Logged processing step",
                        file_id=file_id,
                        stage=stage,
                        step_name=step_name,
                        status=status,
                    )
            finally:
                self._return_connection(conn)
        except Exception as e:
            logger.warning(
                "Failed to log processing history (non-fatal)",
                file_id=file_id,
                stage=stage,
                step_name=step_name,
                error=str(e),
            )
    
    def get_history(self, file_id: str):
        """Get processing history for a file."""
        if not self.pool:
            logger.warning("ProcessingHistoryService not connected")
            return []
            
        conn = self._get_connection()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        id, stage, step_name, status, message, error_message,
                        metadata, duration_ms, started_at, completed_at, created_at
                    FROM processing_history
                    WHERE file_id = %s
                    ORDER BY created_at ASC
                """, (file_id,))
                
                rows = cur.fetchall()
                return [
                    {
                        "id": str(row[0]),
                        "stage": row[1],
                        "stepName": row[2],
                        "status": row[3],
                        "message": row[4],
                        "errorMessage": row[5],
                        "metadata": row[6],
                        "durationMs": row[7],
                        "startedAt": row[8].isoformat() if row[8] else None,
                        "completedAt": row[9].isoformat() if row[9] else None,
                        "createdAt": row[10].isoformat() if row[10] else None,
                    }
                    for row in rows
                ]
        finally:
            self._return_connection(conn)

