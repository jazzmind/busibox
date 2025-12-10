from datetime import timedelta
from typing import Any, Dict
import uuid

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.schemas.auth import Principal
from app.services.run_service import create_run


class RunScheduler:
    """
    Lightweight scheduler for long-running/cron agent tasks.
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler()
        self._started = False
    
    def _ensure_started(self) -> None:
        """Start scheduler if not already started."""
        if not self._started:
            self._scheduler.start()
            self._started = True

    def schedule_agent_run(
        self,
        session_factory,
        principal: Principal,
        agent_id: uuid.UUID,
        payload: Dict[str, Any],
        scopes: list[str],
        purpose: str,
        cron: str,
    ) -> None:
        self._ensure_started()  # Start scheduler on first use
        
        async def _job() -> None:
            async with session_factory() as session:  # type: ignore[call-arg]
                await create_run(
                    session=session,
                    principal=principal,
                    agent_id=agent_id,
                    payload=payload,
                    scopes=scopes,
                    purpose=purpose,
                )

        self._scheduler.add_job(_job, trigger="cron", **self._parse_cron(cron))

    @staticmethod
    def _parse_cron(cron: str) -> Dict[str, Any]:
        fields = cron.strip().split()
        if len(fields) != 5:
            raise ValueError("cron string must have 5 fields")
        minute, hour, day, month, day_of_week = fields
        return {
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month,
            "day_of_week": day_of_week,
        }


run_scheduler = RunScheduler()
