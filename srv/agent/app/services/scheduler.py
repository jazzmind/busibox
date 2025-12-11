import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.job import Job

from app.schemas.auth import Principal
from app.services.run_service import create_run
from app.services.token_service import get_or_exchange_token

logger = logging.getLogger(__name__)


class ScheduledJob:
    """Metadata for a scheduled job."""
    
    def __init__(
        self,
        job_id: str,
        agent_id: uuid.UUID,
        cron: str,
        principal_sub: str,
        next_run_time: Optional[datetime] = None,
    ):
        self.job_id = job_id
        self.agent_id = agent_id
        self.cron = cron
        self.principal_sub = principal_sub
        self.next_run_time = next_run_time


class RunScheduler:
    """
    Lightweight scheduler for long-running/cron agent tasks with token refresh.
    
    Features:
    - APScheduler-based cron scheduling
    - Automatic token refresh before execution
    - Job management (list, cancel)
    - Thread-safe operations
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._started = False
        self._job_metadata: Dict[str, ScheduledJob] = {}
    
    def _ensure_started(self) -> None:
        """Start scheduler if not already started."""
        if not self._started:
            self._scheduler.start()
            self._started = True
            logger.info("RunScheduler started")

    def schedule_agent_run(
        self,
        session_factory,
        principal: Principal,
        agent_id: uuid.UUID,
        payload: Dict[str, Any],
        scopes: list[str],
        purpose: str,
        cron: str,
        agent_tier: str = "simple",
    ) -> str:
        """
        Schedule a recurring agent run with automatic token refresh.
        
        Args:
            session_factory: Async session factory for database access
            principal: User principal for authentication
            agent_id: Agent to execute
            payload: Run input payload
            scopes: Required scopes for execution
            purpose: Purpose for token exchange
            cron: Cron expression (5 fields: minute hour day month day_of_week)
            agent_tier: Execution tier (simple/complex/batch)
            
        Returns:
            job_id: Unique identifier for the scheduled job
            
        Raises:
            ValueError: If cron expression is invalid
        """
        self._ensure_started()
        
        async def _job() -> None:
            """Job function with token pre-refresh."""
            try:
                async with session_factory() as session:  # type: ignore[call-arg]
                    # Pre-refresh token before execution to ensure it's valid
                    logger.info(
                        f"Scheduled job executing for agent {agent_id}, refreshing token for {principal.sub}"
                    )
                    await get_or_exchange_token(
                        session=session,
                        principal=principal,
                        scopes=scopes,
                        purpose=purpose,
                    )
                    
                    # Execute the agent run
                    run_record = await create_run(
                        session=session,
                        principal=principal,
                        agent_id=agent_id,
                        payload=payload,
                        scopes=scopes,
                        purpose=purpose,
                        agent_tier=agent_tier,
                    )
                    logger.info(
                        f"Scheduled job completed for agent {agent_id}, run {run_record.id} status: {run_record.status}"
                    )
            except Exception as e:
                logger.error(
                    f"Scheduled job failed for agent {agent_id}: {str(e)}",
                    exc_info=True,
                )

        # Parse cron and add job
        cron_kwargs = self._parse_cron(cron)
        job = self._scheduler.add_job(_job, trigger="cron", **cron_kwargs)
        
        # Store metadata
        job_metadata = ScheduledJob(
            job_id=job.id,
            agent_id=agent_id,
            cron=cron,
            principal_sub=principal.sub,
            next_run_time=job.next_run_time,
        )
        self._job_metadata[job.id] = job_metadata
        
        logger.info(
            f"Scheduled job {job.id} for agent {agent_id} with cron '{cron}', next run: {job.next_run_time}"
        )
        
        return job.id

    def cancel_job(self, job_id: str) -> bool:
        """
        Cancel a scheduled job.
        
        Args:
            job_id: Job identifier to cancel
            
        Returns:
            True if job was cancelled, False if job not found
        """
        try:
            self._scheduler.remove_job(job_id)
            if job_id in self._job_metadata:
                del self._job_metadata[job_id]
            logger.info(f"Cancelled scheduled job {job_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to cancel job {job_id}: {str(e)}")
            return False
    
    def list_jobs(self) -> List[ScheduledJob]:
        """
        List all scheduled jobs with metadata.
        
        Returns:
            List of ScheduledJob metadata objects
        """
        jobs = []
        for job_id, metadata in self._job_metadata.items():
            # Update next_run_time from scheduler
            apscheduler_job = self._scheduler.get_job(job_id)
            if apscheduler_job:
                metadata.next_run_time = apscheduler_job.next_run_time
                jobs.append(metadata)
        return jobs
    
    def get_job(self, job_id: str) -> Optional[ScheduledJob]:
        """
        Get metadata for a specific job.
        
        Args:
            job_id: Job identifier
            
        Returns:
            ScheduledJob metadata or None if not found
        """
        metadata = self._job_metadata.get(job_id)
        if metadata:
            # Update next_run_time from scheduler
            apscheduler_job = self._scheduler.get_job(job_id)
            if apscheduler_job:
                metadata.next_run_time = apscheduler_job.next_run_time
        return metadata
    
    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown the scheduler.
        
        Args:
            wait: Whether to wait for running jobs to complete
        """
        if self._started:
            self._scheduler.shutdown(wait=wait)
            self._started = False
            logger.info("RunScheduler shut down")

    @staticmethod
    def _parse_cron(cron: str) -> Dict[str, Any]:
        """
        Parse cron expression into APScheduler kwargs.
        
        Args:
            cron: Cron expression (5 fields: minute hour day month day_of_week)
            
        Returns:
            Dictionary of cron trigger kwargs
            
        Raises:
            ValueError: If cron expression is invalid
        """
        fields = cron.strip().split()
        if len(fields) != 5:
            raise ValueError(
                f"cron string must have 5 fields (minute hour day month day_of_week), got {len(fields)}"
            )
        minute, hour, day, month, day_of_week = fields
        return {
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "day_of_week": day_of_week,
        }


run_scheduler = RunScheduler()
