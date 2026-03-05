"""
Error Handler for Worker

Classifies errors as transient or permanent and manages retry logic.
"""

import asyncio
from typing import Optional

import redis as redis_sync
import structlog

from services.postgres_service import FileDeletedError

logger = structlog.get_logger()


class ErrorHandler:
    """Handles error classification and retry logic for job processing."""
    
    def __init__(self, config: dict, postgres_service, redis_client):
        """
        Initialize error handler.
        
        Args:
            config: Configuration dictionary
            postgres_service: PostgreSQL service instance
            redis_client: Redis client for requeuing jobs
        """
        self.config = config
        self.postgres_service = postgres_service
        self.redis_client = redis_client
        self.max_retries = config.get("max_retries", 3)
        base_stream = config.get("stream_name", "jobs:data")
        self.stream_name = f"{base_stream}:high"
    
    def is_transient_error(self, error: Exception) -> bool:
        """
        Determine if error is transient (should retry) or permanent.
        
        Transient errors:
        - Network timeouts
        - Service unavailable (503, 502)
        - Connection errors
        - Rate limiting (429)
        - Temporary service failures
        
        Permanent errors:
        - Corrupted files
        - Unsupported formats
        - Invalid data
        - Authentication failures (401)
        - Permission errors (403)
        
        Args:
            error: Exception to classify
            
        Returns:
            True if error is transient, False if permanent
        """
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        # Transient errors
        transient_indicators = [
            "timeout",
            "connection",
            "unavailable",
            "temporary",
            "retry",
            "rate limit",
            "429",
            "502",
            "503",
            "504",
            "network",
            "socket",
            "refused",
        ]
        
        if any(indicator in error_str for indicator in transient_indicators):
            return True
        
        # Permanent errors
        permanent_indicators = [
            "corrupted",
            "invalid",
            "unsupported",
            "format",
            "malformed",
            "parse error",
            "401",
            "403",
            "404",  # File not found is permanent
            "valueerror",
            "typeerror",
            "not found in database",  # Orphaned job - file never existed in DB
            "not present in table",   # Foreign key violation - orphaned job
            "nul (0x00)",             # PostgreSQL rejects NUL bytes in text
            "cannot contain nul",     # Alternate phrasing
        ]
        
        if any(indicator in error_str for indicator in permanent_indicators):
            return False
        
        # Check error type
        transient_types = (
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
            redis_sync.ConnectionError,
            redis_sync.TimeoutError,
        )
        
        if isinstance(error, transient_types):
            return True
        
        # Default: assume transient for unknown errors (safer to retry)
        return True
    
    def get_retry_count(self, file_id: str) -> int:
        """
        Get current retry count for a file.
        
        Args:
            file_id: File identifier
            
        Returns:
            Current retry count
        """
        try:
            conn = self.postgres_service._get_connection()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT retry_count FROM data_status WHERE file_id = %s",
                        (file_id,),
                    )
                    result = cur.fetchone()
                    return result[0] if result else 0
            finally:
                self.postgres_service._return_connection(conn)
        except Exception as e:
            logger.warning(
                "Failed to get retry count",
                file_id=file_id,
                error=str(e),
            )
            return 0
    
    def should_retry(self, file_id: str, error: Exception, job_retry_count: int = 0) -> bool:
        """
        Determine if job should be retried.
        
        Args:
            file_id: File identifier
            error: Exception that occurred
            job_retry_count: Retry count from job data (fallback if DB unavailable)
            
        Returns:
            True if should retry, False otherwise
        """
        if not self.is_transient_error(error):
            return False
        
        # Get retry count from DB, but use job_retry_count as fallback
        # This prevents infinite retry loops when the file doesn't exist in DB
        db_retry_count = self.get_retry_count(file_id)
        retry_count = max(db_retry_count, job_retry_count)
        
        return retry_count < self.max_retries
    
    def requeue_job(
        self,
        job_id: str,
        file_id: str,
        job_data: dict,
        error: Exception,
        rls_context=None,
    ) -> bool:
        """
        Requeue a job for retry.
        
        Args:
            job_id: Original job ID
            file_id: File identifier
            job_data: Original job data
            error: Exception that caused retry
            rls_context: Optional RLS context for database access
            
        Returns:
            True if requeued successfully
        """
        try:
            # Get retry count from job data (more reliable for orphaned jobs)
            # Fall back to DB if job data doesn't have it
            job_retry_count = int(job_data.get("retry_count", 0))
            db_retry_count = self.get_retry_count(file_id)
            retry_count = max(job_retry_count, db_retry_count)
            new_retry_count = retry_count + 1
            
            # Update status to queued with retry info
            self.postgres_service.update_status(
                file_id=file_id,
                stage="queued",
                progress=0,
                error_message=f"Transient error (retry {new_retry_count}/{self.max_retries}): {str(error)}",
                retry_count=new_retry_count,
                request=rls_context,
            )
            
            # Re-add to Redis stream with maxlen to prevent unbounded growth
            self.redis_client.xadd(
                self.stream_name,
                {
                    "file_id": job_data.get("file_id"),
                    "user_id": job_data.get("user_id"),
                    "storage_path": job_data.get("storage_path"),
                    "mime_type": job_data.get("mime_type"),
                    "original_filename": job_data.get("original_filename", ""),
                    "processing_config": job_data.get("processing_config", ""),
                    "visibility": job_data.get("visibility", "personal"),
                    "role_ids": job_data.get("role_ids", ""),
                    "retry_count": str(new_retry_count),
                },
                maxlen=10000,  # Limit stream to 10k messages
            )
            
            logger.info(
                "Job re-queued for retry",
                file_id=file_id,
                retry_count=new_retry_count,
                error=str(error),
            )
            return True
            
        except FileDeletedError:
            logger.info(
                "File deleted before requeue — skipping retry",
                file_id=file_id,
            )
            return False
        except Exception as requeue_error:
            logger.error(
                "Failed to re-queue job",
                file_id=file_id,
                error=str(requeue_error),
            )
            return False
    
    def mark_failed(
        self,
        file_id: str,
        error: Exception,
        retry_count: Optional[int] = None,
        rls_context=None,
    ):
        """
        Mark job as permanently failed.
        
        Args:
            file_id: File identifier
            error: Exception that caused failure
            retry_count: Number of retries attempted
            rls_context: Optional RLS context for database access
        """
        if retry_count is None:
            retry_count = self.get_retry_count(file_id)
        
        is_transient = self.is_transient_error(error)
        
        if is_transient:
            error_msg = f"Max retries ({self.max_retries}) exceeded: {str(error)}"
        else:
            error_msg = f"Permanent error: {str(error)}"
        
        try:
            self.postgres_service.update_status(
                file_id=file_id,
                stage="failed",
                progress=0,
                error_message=error_msg,
                retry_count=retry_count,
                request=rls_context,
            )
        except FileDeletedError:
            logger.info(
                "File deleted before failure could be recorded — skipping",
                file_id=file_id,
            )
            return
        
        logger.error(
            "Job marked as failed",
            file_id=file_id,
            error_type=type(error).__name__,
            error=str(error),
            is_transient=is_transient,
            retry_count=retry_count,
            max_retries=self.max_retries,
        )

