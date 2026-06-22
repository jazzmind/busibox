"""
Trigger Task Run Tool.

Allows an agent to asynchronously trigger an immediate run of an agent task.
Designed for self-continuation patterns where an agent processes a batch of work
and fires the next batch before finishing.

Multiple server-side guardrails prevent runaway loops:
  - Hard max continuation depth (default 50, configurable per task)
  - Cooldown window (default 10s) to prevent rapid re-triggering
  - Self-only restriction: agents can only trigger their own task by default
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from app.agents.core import BusiboxDeps

logger = logging.getLogger(__name__)

# Guardrail constants
MAX_CONTINUATION_DEPTH = 50   # Hard limit on self-continuation chain length
COOLDOWN_SECONDS = 10          # Minimum seconds between consecutive continuation triggers


class TriggerTaskInput(BaseModel):
    """Input for triggering an agent task run."""
    task_id: str = Field(description="UUID of the agent task to trigger. Must match your own task_id from context.")
    reason: str = Field(default="", description="Why the continuation is needed (logged for observability).")
    input_override: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional overrides merged into the task prompt/input_config for the next run.",
    )


class TriggerTaskOutput(BaseModel):
    """Result of triggering a task run."""
    success: bool
    execution_id: Optional[str] = None
    continuation_depth: int = 0
    message: str


async def trigger_task_run(
    ctx: RunContext[BusiboxDeps],
    task_id: str,
    reason: str = "",
    input_override: Optional[Dict[str, Any]] = None,
) -> TriggerTaskOutput:
    """
    Asynchronously trigger an immediate run of an agent task.

    Use this at the end of your batch to continue processing if more work remains.
    The run is fire-and-forget — this tool returns immediately, before the next run starts.

    Guardrails (server-enforced, cannot be bypassed):
    - Max 50 continuation depth per chain
    - 10-second cooldown between triggers for the same task
    - Can only trigger your own task (the task_id from your execution context)
    """
    from app.db.session import SessionLocal
    from app.models.domain import AgentTask, TaskExecution
    from app.services.task_service import create_task_execution
    from app.schemas.auth import Principal
    from sqlalchemy import select
    from sqlalchemy import func

    input_override = input_override or {}

    # Read execution context
    meta = ctx.deps.metadata or {}
    context_task_id = meta.get("task_id")
    current_depth = int(meta.get("continuation_depth", 0))
    next_depth = current_depth + 1

    logger.info(
        f"[DEBUG-fce93e][B] trigger_task_run CALLED: "
        f"task_id={task_id}, context_task_id={context_task_id}, "
        f"depth={current_depth}->{next_depth}, reason={reason!r}"
    )

    # --- Guardrail 1: self-only restriction ---
    # If context_task_id is known (injected via metadata), enforce self-only.
    # If None (e.g. first deployment before metadata fix, or direct invocation),
    # allow but log a warning so we can trace any abuse.
    if context_task_id is not None and context_task_id != task_id:
        return TriggerTaskOutput(
            success=False,
            continuation_depth=current_depth,
            message=(
                f"Cross-task triggering is not allowed. "
                f"Your task_id={context_task_id}, requested task_id={task_id}."
            ),
        )
    if context_task_id is None:
        logger.warning(
            f"[trigger_task_run] context task_id is None — "
            f"proceeding with requested task_id={task_id} (no self-only enforcement). "
            f"Ensure metadata is properly injected in run_service.py."
        )

    # --- Guardrail 2: hard max depth ---
    if next_depth > MAX_CONTINUATION_DEPTH:
        return TriggerTaskOutput(
            success=False,
            continuation_depth=current_depth,
            message=(
                f"Max continuation depth ({MAX_CONTINUATION_DEPTH}) reached. "
                f"This prevents runaway loops. Collection will resume on the next scheduled run."
            ),
        )

    try:
        async with SessionLocal() as session:
            # Load task
            stmt = select(AgentTask).where(AgentTask.id == uuid.UUID(task_id))
            result = await session.execute(stmt)
            task = result.scalar_one_or_none()

            if not task:
                return TriggerTaskOutput(
                    success=False,
                    continuation_depth=current_depth,
                    message=f"Task {task_id} not found.",
                )

            if task.status != "active":
                return TriggerTaskOutput(
                    success=False,
                    continuation_depth=current_depth,
                    message=f"Task {task_id} is not active (status={task.status}).",
                )

            # --- Guardrail 3: per-task configurable max (stored in trigger_config) ---
            task_max_depth = (task.trigger_config or {}).get("max_continuations", MAX_CONTINUATION_DEPTH)
            if next_depth > int(task_max_depth):
                return TriggerTaskOutput(
                    success=False,
                    continuation_depth=current_depth,
                    message=(
                        f"Task-level max continuations ({task_max_depth}) reached. "
                        f"Adjust trigger_config.max_continuations to increase."
                    ),
                )

            # --- Guardrail 4: cooldown window ---
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=COOLDOWN_SECONDS)
            recent_stmt = (
                select(func.count())
                .select_from(TaskExecution)
                .where(
                    TaskExecution.task_id == uuid.UUID(task_id),
                    TaskExecution.trigger_source == "continuation",
                    TaskExecution.created_at >= cutoff,
                )
            )
            recent_count_result = await session.execute(recent_stmt)
            recent_count = recent_count_result.scalar() or 0

            if recent_count > 0:
                return TriggerTaskOutput(
                    success=False,
                    continuation_depth=current_depth,
                    message=(
                        f"Cooldown active: a continuation was already triggered within the last "
                        f"{COOLDOWN_SECONDS}s. Preventing rapid re-triggering."
                    ),
                )

            # Build continuation payload, normalizing to JSON-safe types.
            # task.input_config is read from JSONB and asyncpg may decode ISO
            # date strings as datetime objects; round-tripping through JSON
            # converts them back to strings before the INSERT.
            raw_payload: Dict[str, Any] = {
                "prompt": task.prompt,
                **(task.input_config or {}),
                **input_override,
                "_task_id": task_id,
                "_continuation_depth": next_depth,
            }
            continuation_payload = json.loads(
                json.dumps(raw_payload, default=str)
            )

            logger.info(
                f"[DEBUG-fce93e][B] trigger_task_run continuation_payload built ok, "
                f"task={task_id}, depth={next_depth}"
            )

            # Create execution record with trigger_source="continuation"
            execution = await create_task_execution(
                session=session,
                task=task,
                trigger_source="continuation",
                input_data=continuation_payload,
            )

            await session.commit()

            logger.info(
                f"[trigger_task_run] Queued continuation: task={task_id}, "
                f"depth={next_depth}/{task_max_depth}, reason={reason!r}, "
                f"execution={execution.id}"
            )

            # Fire and forget — import here to avoid circular imports
            from app.api.webhooks import _execute_task_in_background
            asyncio.create_task(
                _execute_task_in_background(
                    task=task,
                    execution_id=execution.id,
                    input_data=continuation_payload,
                )
            )

            return TriggerTaskOutput(
                success=True,
                execution_id=str(execution.id),
                continuation_depth=next_depth,
                message=(
                    f"Continuation queued at depth {next_depth}/{task_max_depth}. "
                    f"Reason: {reason or 'not specified'}."
                ),
            )

    except Exception as e:
        logger.exception(f"[trigger_task_run] Failed to queue continuation for task {task_id}: {e}")
        logger.info(f"[DEBUG-fce93e][B] trigger_task_run EXCEPTION: {e}")
        return TriggerTaskOutput(
            success=False,
            continuation_depth=current_depth,
            message=f"Failed to queue continuation: {e}",
        )


# Pre-built Tool object for registration in builtin_tools.py
trigger_task_run_tool = Tool(
    trigger_task_run,
    takes_ctx=True,
    name="trigger_task_run",
    description=(
        "Asynchronously trigger an immediate run of your own agent task. "
        "Use this at the end of a batch to continue processing when more work remains. "
        "Fire-and-forget — returns immediately. "
        "Server enforces max 50 continuation depth and a 10-second cooldown to prevent loops."
    ),
)
