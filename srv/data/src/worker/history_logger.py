"""
History Logger for Worker

Convenient wrapper around ProcessingHistoryService with common logging patterns.
"""

import time
import traceback
from typing import Optional, Dict, Any

import structlog

logger = structlog.get_logger()


class HistoryLogger:
    """Wrapper around ProcessingHistoryService with convenience methods."""
    
    def __init__(self, history_service):
        """
        Initialize history logger.
        
        Args:
            history_service: ProcessingHistoryService instance
        """
        self.service = history_service
    
    def log_step(
        self,
        file_id: str,
        stage: str,
        step_name: str,
        status: str,
        message: str = None,
        error_message: str = None,
        metadata: dict = None,
        started_at: float = None,
    ):
        """
        Log a processing step (passthrough to service with error handling).
        
        Args:
            file_id: File identifier
            stage: Processing stage
            step_name: Name of the step
            status: Step status (started, completed, failed, skipped)
            message: Success message
            error_message: Error message if failed
            metadata: Additional metadata
            started_at: Start timestamp for duration calculation
        """
        try:
            self.service.log_step(
                file_id=file_id,
                stage=stage,
                step_name=step_name,
                status=status,
                message=message,
                error_message=error_message,
                metadata=metadata,
                started_at=started_at,
            )
        except Exception as e:
            # Don't fail processing if history logging fails
            logger.warning(
                "Failed to log processing step",
                file_id=file_id,
                stage=stage,
                step_name=step_name,
                error=str(e),
            )
    
    def log_stage_start(
        self,
        file_id: str,
        stage: str,
        message: str = None,
        metadata: dict = None,
    ) -> float:
        """
        Log the start of a processing stage.
        
        Args:
            file_id: File identifier
            stage: Stage name
            message: Optional message
            metadata: Optional metadata
            
        Returns:
            Current timestamp for duration tracking
        """
        start_time = time.time()
        self.log_step(
            file_id=file_id,
            stage=stage,
            step_name="stage_start",
            status="started",
            message=message or f"Starting {stage} stage",
            metadata=metadata,
        )
        return start_time
    
    def log_stage_complete(
        self,
        file_id: str,
        stage: str,
        message: str = None,
        metadata: dict = None,
        started_at: float = None,
    ):
        """
        Log successful completion of a processing stage.
        
        Args:
            file_id: File identifier
            stage: Stage name
            message: Success message
            metadata: Result metadata
            started_at: Start timestamp for duration
        """
        self.log_step(
            file_id=file_id,
            stage=stage,
            step_name="stage_complete",
            status="completed",
            message=message or f"Completed {stage} stage",
            metadata=metadata,
            started_at=started_at,
        )
    
    def log_substep(
        self,
        file_id: str,
        stage: str,
        step_name: str,
        message: str = None,
        metadata: dict = None,
        started_at: float = None,
    ):
        """
        Log completion of a substep within a stage.
        
        Args:
            file_id: File identifier
            stage: Parent stage name
            step_name: Substep name
            message: Success message
            metadata: Step metadata
            started_at: Start timestamp for duration
        """
        self.log_step(
            file_id=file_id,
            stage=stage,
            step_name=step_name,
            status="completed",
            message=message,
            metadata=metadata,
            started_at=started_at,
        )
    
    def log_error(
        self,
        file_id: str,
        stage: str,
        step_name: str,
        error: Exception,
        metadata: dict = None,
        started_at: float = None,
    ):
        """
        Log an error that occurred during processing.
        
        Args:
            file_id: File identifier
            stage: Stage where error occurred
            step_name: Step that failed
            error: Exception that was raised
            metadata: Additional context
            started_at: Start timestamp
        """
        error_traceback = traceback.format_exc()
        
        error_metadata = {
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": error_traceback,
        }
        
        if metadata:
            error_metadata.update(metadata)
        
        self.log_step(
            file_id=file_id,
            stage=stage,
            step_name=step_name,
            status="failed",
            error_message=f"{type(error).__name__}: {str(error)}",
            metadata=error_metadata,
            started_at=started_at,
        )
    
    def log_skip(
        self,
        file_id: str,
        stage: str,
        step_name: str,
        reason: str,
        metadata: dict = None,
    ):
        """
        Log that a step was skipped.
        
        Args:
            file_id: File identifier
            stage: Stage name
            step_name: Step that was skipped
            reason: Why it was skipped
            metadata: Additional context
        """
        self.log_step(
            file_id=file_id,
            stage=stage,
            step_name=step_name,
            status="skipped",
            message=reason,
            metadata=metadata,
        )

