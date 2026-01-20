"""
Scheduler Service for Agent Tasks and Runs.

Provides scheduling capabilities for:
- Agent runs (original functionality)
- Agent tasks with persistent scheduling

Features:
- APScheduler-based cron scheduling
- Automatic token refresh before execution
- Job management (list, cancel)
- Task-specific scheduling with notifications and insights
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid as uuid_module

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.job import Job
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.schemas.auth import Principal
from app.services.run_service import create_run
from app.services.token_service import get_or_exchange_token

logger = logging.getLogger(__name__)


class ScheduledJob:
    """Metadata for a scheduled job."""
    
    def __init__(
        self,
        job_id: str,
        agent_id: uuid_module.UUID,
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
        agent_id: uuid_module.UUID,
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

    # =========================================================================
    # Task Scheduling - For Agent Tasks with insights and notifications
    # =========================================================================
    
    def schedule_task(
        self,
        task_id: uuid_module.UUID,
        cron: str,
        executor: Callable,
    ) -> str:
        """
        Schedule a task for cron-based execution.
        
        This is used by the task executor to schedule tasks from the database.
        The executor callback handles the actual task execution.
        
        Args:
            task_id: Task UUID
            cron: Cron expression
            executor: Async function to call when triggered
            
        Returns:
            job_id: Unique identifier for the scheduled job
        """
        self._ensure_started()
        
        cron_kwargs = self._parse_cron(cron)
        job = self._scheduler.add_job(
            executor,
            trigger="cron",
            id=f"task_{task_id}",
            **cron_kwargs,
            replace_existing=True,
        )
        
        logger.info(
            f"Scheduled task {task_id} with cron '{cron}', next run: {job.next_run_time}"
        )
        
        return job.id
    
    def schedule_task_one_time(
        self,
        task_id: uuid_module.UUID,
        run_at: datetime,
        executor: Callable,
    ) -> str:
        """
        Schedule a one-time task execution.
        
        Args:
            task_id: Task UUID
            run_at: When to run the task
            executor: Async function to call when triggered
            
        Returns:
            job_id: Unique identifier for the scheduled job
        """
        self._ensure_started()
        
        # Ensure timezone-aware
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        
        job = self._scheduler.add_job(
            executor,
            trigger="date",
            run_date=run_at,
            id=f"task_{task_id}",
            replace_existing=True,
        )
        
        logger.info(
            f"Scheduled one-time task {task_id} for {run_at}"
        )
        
        return job.id
    
    def cancel_task(self, task_id: uuid_module.UUID) -> bool:
        """
        Cancel a scheduled task.
        
        Args:
            task_id: Task UUID
            
        Returns:
            True if cancelled, False if not found
        """
        job_id = f"task_{task_id}"
        try:
            self._scheduler.remove_job(job_id)
            logger.info(f"Cancelled scheduled task {task_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to cancel task {task_id}: {e}")
            return False
    
    def reschedule_task(
        self,
        task_id: uuid_module.UUID,
        cron: str,
    ) -> bool:
        """
        Reschedule a task with a new cron expression.
        
        Args:
            task_id: Task UUID
            cron: New cron expression
            
        Returns:
            True if rescheduled, False if not found
        """
        job_id = f"task_{task_id}"
        try:
            cron_kwargs = self._parse_cron(cron)
            self._scheduler.reschedule_job(
                job_id,
                trigger="cron",
                **cron_kwargs,
            )
            logger.info(f"Rescheduled task {task_id} with cron '{cron}'")
            return True
        except Exception as e:
            logger.warning(f"Failed to reschedule task {task_id}: {e}")
            return False
    
    def get_task_next_run(self, task_id: uuid_module.UUID) -> Optional[datetime]:
        """
        Get the next run time for a task.
        
        Args:
            task_id: Task UUID
            
        Returns:
            Next run datetime or None if not scheduled
        """
        job_id = f"task_{task_id}"
        job = self._scheduler.get_job(job_id)
        if job:
            return job.next_run_time
        return None
    
    def is_task_scheduled(self, task_id: uuid_module.UUID) -> bool:
        """Check if a task is currently scheduled."""
        job_id = f"task_{task_id}"
        return self._scheduler.get_job(job_id) is not None


run_scheduler = RunScheduler()


class TaskSchedulerService:
    """
    High-level service for managing task schedules.
    
    Handles the lifecycle of task scheduling including:
    - Restoring schedules on startup
    - Creating/updating/cancelling task schedules
    - Executing tasks with insights and notifications
    """
    
    def __init__(self, scheduler: RunScheduler):
        self.scheduler = scheduler
        self._task_executors: Dict[str, Callable] = {}
    
    async def restore_task_schedules(self, session_factory):
        """
        Restore task schedules from the database on startup.
        
        This should be called during application initialization to
        re-register all active cron tasks with the scheduler.
        
        Args:
            session_factory: Async session factory
        """
        from app.models.domain import AgentTask
        from sqlalchemy import select
        
        logger.info("Restoring task schedules from database...")
        
        async with session_factory() as session:
            # Get all active cron tasks
            stmt = select(AgentTask).where(
                AgentTask.status == "active",
                AgentTask.trigger_type == "cron",
            )
            result = await session.execute(stmt)
            tasks = result.scalars().all()
            
            restored = 0
            for task in tasks:
                try:
                    cron = task.trigger_config.get("cron")
                    if cron:
                        # Create executor for this task
                        executor = self._create_task_executor(
                            task_id=task.id,
                            session_factory=session_factory,
                        )
                        self.scheduler.schedule_task(
                            task_id=task.id,
                            cron=cron,
                            executor=executor,
                        )
                        restored += 1
                except Exception as e:
                    logger.error(f"Failed to restore schedule for task {task.id}: {e}")
            
            logger.info(f"Restored {restored} task schedules")
    
    def _create_task_executor(
        self,
        task_id: uuid_module.UUID,
        session_factory,
    ) -> Callable:
        """
        Create an executor function for a task.
        
        The executor handles:
        - Token refresh
        - Insights injection
        - Agent execution
        - Result storage as insight
        - Notification sending
        """
        async def execute():
            from app.models.domain import AgentTask
            from app.services.task_service import (
                create_task_execution,
                update_task_execution,
                update_task_after_execution,
            )
            from app.services.run_service import create_run
            from app.schemas.auth import Principal
            from sqlalchemy import select
            
            logger.info(f"Executing scheduled task {task_id}")
            
            try:
                async with session_factory() as session:
                    # Get task
                    stmt = select(AgentTask).where(AgentTask.id == task_id)
                    result = await session.execute(stmt)
                    task = result.scalar_one_or_none()
                    
                    if not task:
                        logger.error(f"Task {task_id} not found")
                        return
                    
                    if task.status != "active":
                        logger.info(f"Task {task_id} is not active, skipping")
                        return
                    
                    # Create execution record
                    execution = await create_task_execution(
                        session=session,
                        task=task,
                        trigger_source="cron",
                    )
                    
                    try:
                        # Create a principal for the task owner using the delegation token
                        principal = Principal(
                            sub=task.user_id,
                            scopes=task.delegation_scopes or [],
                            token=task.delegation_token,  # Use stored delegation token
                        )
                        
                        # Build the payload from task configuration
                        payload = {
                            "prompt": task.prompt,
                            **(task.input_config or {}),
                        }
                        
                        # Execute the agent
                        run_record = await create_run(
                            session=session,
                            principal=principal,
                            agent_id=task.agent_id,
                            payload=payload,
                            scopes=task.delegation_scopes or [],
                            purpose="task-execution",
                            agent_tier="simple",  # Could be configurable per task
                        )
                        
                        # Update execution with run result
                        output_summary = None
                        if run_record.output:
                            if isinstance(run_record.output, dict):
                                output_summary = run_record.output.get("summary") or str(run_record.output)[:500]
                            else:
                                output_summary = str(run_record.output)[:500]
                        
                        success = run_record.status == "completed"
                        
                        await update_task_execution(
                            session=session,
                            execution_id=execution.id,
                            run_id=run_record.id,
                            status=run_record.status,
                            output_summary=output_summary,
                            error=run_record.output.get("error") if isinstance(run_record.output, dict) and not success else None,
                        )
                        
                        await update_task_after_execution(
                            session=session,
                            task_id=task_id,
                            execution=execution,
                            success=success,
                        )
                        
                        logger.info(f"Task {task_id} execution completed with run {run_record.id}, status: {run_record.status}")
                        
                    except Exception as e:
                        logger.error(f"Task {task_id} execution failed: {e}", exc_info=True)
                        await update_task_execution(
                            session=session,
                            execution_id=execution.id,
                            status="failed",
                            error=str(e),
                        )
                        await update_task_after_execution(
                            session=session,
                            task_id=task_id,
                            execution=execution,
                            success=False,
                        )
            
            except Exception as e:
                logger.error(f"Task {task_id} executor error: {e}", exc_info=True)
        
        return execute
    
    def schedule_task(
        self,
        task_id: uuid_module.UUID,
        cron: str,
        session_factory,
    ) -> str:
        """Schedule a task with cron expression."""
        executor = self._create_task_executor(task_id, session_factory)
        return self.scheduler.schedule_task(task_id, cron, executor)
    
    def schedule_task_one_time(
        self,
        task_id: uuid_module.UUID,
        run_at: datetime,
        session_factory,
    ) -> str:
        """Schedule a one-time task."""
        executor = self._create_task_executor(task_id, session_factory)
        return self.scheduler.schedule_task_one_time(task_id, run_at, executor)
    
    def cancel_task(self, task_id: uuid_module.UUID) -> bool:
        """Cancel a scheduled task."""
        return self.scheduler.cancel_task(task_id)


# Global task scheduler service instance
task_scheduler = TaskSchedulerService(run_scheduler)
