"""
Delegate-to-Agent Tool.

Allows one agent to invoke another agent synchronously and receive its output.
This is the core primitive for multi-agent orchestration patterns such as the
Chief of Staff → specialist-agent delegation pattern.

Usage by an agent:
    result = delegate_to_agent(
        ctx,
        agent_id="<uuid>",            # preferred: exact agent UUID
        agent_name="briefing-agent",  # alternative: looked up by name
        prompt="Summarise today's calendar events...",
        context={"date": "2026-06-01"},
        tier="simple",                 # simple/complex/batch
    )
"""

import logging
import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input / output schemas
# ---------------------------------------------------------------------------

class DelegateInput(BaseModel):
    """Input schema for the delegate_to_agent tool."""

    agent_id: Optional[str] = Field(
        None,
        description="UUID of the agent to invoke. Takes priority over agent_name.",
    )
    agent_name: Optional[str] = Field(
        None,
        description=(
            "Name of the agent to invoke (e.g. 'briefing-agent'). "
            "Used when agent_id is not provided."
        ),
    )
    prompt: str = Field(
        ...,
        description="The task or question to send to the delegated agent.",
    )
    context: Optional[Dict[str, Any]] = Field(
        None,
        description="Optional key/value context that is merged into the agent payload.",
    )
    tier: str = Field(
        "simple",
        description="Execution tier: 'simple' (30 s), 'complex' (5 min), or 'batch' (30 min).",
    )


class DelegateOutput(BaseModel):
    """Output schema for the delegate_to_agent tool."""

    success: bool = Field(..., description="Whether the delegation completed successfully.")
    run_id: Optional[str] = Field(None, description="ID of the created run record.")
    agent_id: Optional[str] = Field(None, description="ID of the agent that was invoked.")
    agent_name: Optional[str] = Field(None, description="Name of the agent that was invoked.")
    output: Optional[Any] = Field(None, description="The agent's output (text or structured).")
    status: Optional[str] = Field(None, description="Final run status.")
    error: Optional[str] = Field(None, description="Error message if delegation failed.")


# ---------------------------------------------------------------------------
# Main tool function
# ---------------------------------------------------------------------------

async def delegate_to_agent(
    ctx: Any,  # RunContext[BusiboxDeps]
    prompt: str,
    agent_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    tier: str = "simple",
) -> DelegateOutput:
    """
    Invoke another agent synchronously and return its output.

    One of agent_id or agent_name must be supplied. The calling agent's
    principal is forwarded to the invoked agent so it runs with the same
    user identity and permissions.

    Args:
        ctx: Agent run context providing principal and deps.
        prompt: Task or question to send to the delegated agent.
        agent_id: UUID of the target agent (preferred).
        agent_name: Name of the target agent (fallback).
        context: Extra key/value pairs merged into the payload.
        tier: Execution tier (simple/complex/batch).

    Returns:
        DelegateOutput with the agent's response or an error description.
    """
    if not agent_id and not agent_name:
        return DelegateOutput(
            success=False,
            error="Either agent_id or agent_name must be provided.",
        )

    logger.info(
        "Delegating to agent %s / %s (tier=%s)",
        agent_id or "<by name>",
        agent_name or "<by id>",
        tier,
    )

    try:
        from app.db.session import SessionLocal
        from app.models.domain import AgentDefinition
        from app.services.run_service import create_run
        from sqlalchemy import select

        # Resolve principal from context
        deps = ctx.deps if hasattr(ctx, "deps") else None
        if not deps or not deps.principal:
            return DelegateOutput(
                success=False,
                error="Authentication required — no principal in agent context.",
            )

        principal = deps.principal

        async with SessionLocal() as session:
            # Resolve agent UUID
            resolved_uuid: Optional[uuid.UUID] = None

            if agent_id:
                try:
                    resolved_uuid = uuid.UUID(agent_id)
                except ValueError:
                    # Treat as a name
                    agent_name = agent_id
                    agent_id = None

            if resolved_uuid is None and agent_name:
                stmt = select(AgentDefinition).where(AgentDefinition.name == agent_name)
                result = await session.execute(stmt)
                agent_def = result.scalar_one_or_none()
                if agent_def is None:
                    return DelegateOutput(
                        success=False,
                        error=f"Agent '{agent_name}' not found.",
                    )
                resolved_uuid = agent_def.id
                agent_name = agent_def.name

            if resolved_uuid is None:
                return DelegateOutput(
                    success=False,
                    error="Could not resolve agent identifier.",
                )

            # Fetch agent name for output (if we have uuid only)
            if agent_name is None:
                stmt2 = select(AgentDefinition).where(AgentDefinition.id == resolved_uuid)
                r2 = await session.execute(stmt2)
                adef = r2.scalar_one_or_none()
                agent_name = adef.name if adef else str(resolved_uuid)

            payload: Dict[str, Any] = {"prompt": prompt}
            if context:
                payload.update(context)

            run_record = await create_run(
                session=session,
                principal=principal,
                agent_id=resolved_uuid,
                payload=payload,
                scopes=[],
                purpose="agent-delegate",
                agent_tier=tier,
            )

        # Extract output
        output_data: Optional[Any] = None
        error_msg: Optional[str] = None

        if run_record.output:
            if "result" in run_record.output:
                output_data = run_record.output["result"]
            elif "data" in run_record.output:
                output_data = run_record.output["data"]
            elif "error" in run_record.output:
                error_msg = str(run_record.output["error"])
            else:
                output_data = run_record.output

        if run_record.status in {"failed", "timeout"} and not error_msg:
            error_msg = str((run_record.output or {}).get("error", "Delegation failed"))

        success = run_record.status not in {"failed", "timeout"} and error_msg is None

        return DelegateOutput(
            success=success,
            run_id=str(run_record.id),
            agent_id=str(resolved_uuid),
            agent_name=agent_name,
            output=output_data,
            status=run_record.status,
            error=error_msg,
        )

    except Exception as exc:
        logger.error("delegate_to_agent failed: %s", exc, exc_info=True)
        return DelegateOutput(
            success=False,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

DELEGATE_TOOL_SCHEMA = {
    "name": "delegate_to_agent",
    "description": (
        "Invoke another specialist agent and return its output. "
        "Use this to delegate sub-tasks to agents such as briefing-agent, "
        "scheduler-agent, or debrief-agent. The delegated agent runs with "
        "the same user identity. One of agent_id or agent_name is required."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "agent_id": {
                "type": "string",
                "description": "UUID of the target agent (preferred).",
            },
            "agent_name": {
                "type": "string",
                "description": "Name of the target agent (used when agent_id is not known).",
            },
            "prompt": {
                "type": "string",
                "description": "Task or question to send to the delegated agent.",
            },
            "context": {
                "type": "object",
                "description": "Optional key/value context merged into the agent payload.",
            },
            "tier": {
                "type": "string",
                "enum": ["simple", "complex", "batch"],
                "description": "Execution tier: simple (30 s), complex (5 min), batch (30 min).",
                "default": "simple",
            },
        },
        "required": ["prompt"],
    },
}


def register_delegate_tool() -> None:
    """Register the delegate_to_agent tool with the ToolRegistry."""
    try:
        from app.agents.base_agent import ToolRegistry

        ToolRegistry.register(
            name="delegate_to_agent",
            func=delegate_to_agent,
            output_type=DelegateOutput,
        )
        logger.info("Registered delegate_to_agent tool")
    except Exception as exc:
        logger.warning("Could not register delegate_to_agent tool: %s", exc)


# ---------------------------------------------------------------------------
# Pre-built PydanticAI Tool object (for agents that use tool_objects registry)
# ---------------------------------------------------------------------------

try:
    from pydantic_ai import Tool

    delegate_to_agent_tool = Tool(
        delegate_to_agent,
        takes_ctx=True,
        name="delegate_to_agent",
        description=DELEGATE_TOOL_SCHEMA["description"],
    )
except Exception:
    delegate_to_agent_tool = None  # type: ignore[assignment]
