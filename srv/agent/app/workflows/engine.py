"""
Workflow execution engine for multi-step agent orchestration.

Supports:
- Sequential step execution
- Simple conditional branching
- State persistence between steps
- Error handling and recovery
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import RunRecord, WorkflowDefinition
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token

logger = logging.getLogger(__name__)


class WorkflowExecutionError(Exception):
    """Raised when workflow execution fails."""
    pass


def _resolve_value(value: Any, context: Dict[str, Any]) -> Any:
    """
    Resolve a value that may contain JSONPath-like references.
    
    Args:
        value: Value to resolve (may be string starting with '$.')
        context: Execution context with step outputs
        
    Returns:
        Resolved value
        
    Examples:
        _resolve_value("$.input.path", {"input": {"path": "/doc.pdf"}}) -> "/doc.pdf"
        _resolve_value("literal", {}) -> "literal"
    """
    if isinstance(value, str) and value.startswith("$."):
        # Simple JSONPath resolution: $.step_id.field
        parts = value[2:].split(".")
        result = context
        for part in parts:
            if isinstance(result, dict):
                result = result.get(part)
            else:
                return None
        return result
    return value


def _resolve_args(args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve all arguments in a dict, handling JSONPath references.
    
    Args:
        args: Arguments dict (may contain $.references)
        context: Execution context
        
    Returns:
        Dict with resolved values
    """
    resolved = {}
    for key, value in args.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_args(value, context)
        elif isinstance(value, list):
            resolved[key] = [_resolve_value(v, context) for v in value]
        else:
            resolved[key] = _resolve_value(value, context)
    return resolved


def _add_workflow_event(
    run_record: RunRecord,
    step_id: str,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """
    Add a workflow step event to the run record.
    
    Args:
        run_record: Run record to update
        step_id: Workflow step identifier
        event_type: Event type (step_started, step_completed, step_failed)
        data: Optional event data
        error: Optional error message
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "step_id": step_id,
    }
    
    if data:
        event["data"] = data
    
    if error:
        event["error"] = error
    
    if not isinstance(run_record.events, list):
        run_record.events = []
    
    run_record.events.append(event)
    attributes.flag_modified(run_record, "events")


async def execute_workflow(
    session: AsyncSession,
    principal: Principal,
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    scopes: List[str],
    purpose: str,
) -> RunRecord:
    """
    Execute a multi-step workflow with state persistence.
    
    Args:
        session: Database session
        principal: User principal for authentication
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        scopes: Required scopes for execution
        purpose: Purpose for token exchange
        
    Returns:
        RunRecord with workflow execution results
        
    Raises:
        WorkflowExecutionError: If workflow execution fails
    """
    # Load workflow definition
    workflow = await session.get(WorkflowDefinition, workflow_id)
    if not workflow:
        raise WorkflowExecutionError(f"Workflow {workflow_id} not found")
    
    if not workflow.is_active:
        raise WorkflowExecutionError(f"Workflow {workflow.name} is not active")
    
    # Create run record
    run_record = RunRecord(
        workflow_id=workflow_id,
        status="running",
        input=input_data,
        output={},
        created_by=principal.sub,
    )
    session.add(run_record)
    await session.commit()
    await session.refresh(run_record)
    
    logger.info(
        f"Starting workflow {workflow.name} (ID: {workflow_id}), run {run_record.id}"
    )
    
    # Initialize execution context
    context = {
        "input": input_data,
        "workflow": {"id": str(workflow_id), "name": workflow.name},
    }
    
    try:
        # Get or exchange token for downstream services
        token_response = await get_or_exchange_token(
            session=session,
            principal=principal,
            scopes=scopes,
            purpose=purpose,
        )
        
        # Create Busibox client
        busibox_client = BusiboxClient(
            search_url="http://search-api:8001",
            ingest_url="http://ingest-api:8002",
            bearer_token=token_response.access_token,
        )
        
        # Execute steps sequentially
        for step in workflow.steps:
            step_id = step.get("id", "unknown")
            step_type = step.get("type")
            
            _add_workflow_event(
                run_record,
                step_id,
                "step_started",
                data={"type": step_type},
            )
            
            try:
                if step_type == "tool":
                    # Execute tool call
                    tool_name = step.get("tool")
                    args = step.get("args", {})
                    resolved_args = _resolve_args(args, context)
                    
                    logger.info(f"Executing tool {tool_name} in step {step_id}")
                    
                    # Call tool via Busibox client
                    if tool_name == "search":
                        result = await busibox_client.search(**resolved_args)
                    elif tool_name == "ingest":
                        result = await busibox_client.ingest_document(**resolved_args)
                    elif tool_name == "rag":
                        result = await busibox_client.rag_query(**resolved_args)
                    else:
                        raise WorkflowExecutionError(f"Unknown tool: {tool_name}")
                    
                    context[step_id] = result
                    
                elif step_type == "agent":
                    # Execute agent
                    agent_name = step.get("agent")
                    agent_input = step.get("input", "")
                    resolved_input = _resolve_value(agent_input, context)
                    
                    logger.info(f"Executing agent {agent_name} in step {step_id}")
                    
                    # Find agent by name (simplified - could be improved)
                    from sqlalchemy import select
                    from app.models.domain import AgentDefinition
                    
                    stmt = select(AgentDefinition).where(
                        AgentDefinition.name == agent_name,
                        AgentDefinition.is_active.is_(True),
                    )
                    result = await session.execute(stmt)
                    agent_def = result.scalars().first()
                    
                    if not agent_def:
                        raise WorkflowExecutionError(f"Agent {agent_name} not found")
                    
                    # Get agent from registry
                    agent = agent_registry.get(agent_def.id)
                    
                    # Execute agent
                    deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
                    agent_result = await agent.run(str(resolved_input), deps=deps)
                    
                    # Extract output
                    if hasattr(agent_result, "data") and hasattr(agent_result.data, "model_dump"):
                        output = agent_result.data.model_dump()
                    else:
                        output = {"result": str(agent_result)}
                    
                    context[step_id] = output
                    
                else:
                    raise WorkflowExecutionError(f"Unknown step type: {step_type}")
                
                _add_workflow_event(
                    run_record,
                    step_id,
                    "step_completed",
                    data={"output": context[step_id]},
                )
                
            except Exception as e:
                logger.error(f"Step {step_id} failed: {str(e)}", exc_info=True)
                _add_workflow_event(
                    run_record,
                    step_id,
                    "step_failed",
                    error=str(e),
                )
                raise WorkflowExecutionError(f"Step {step_id} failed: {str(e)}")
        
        # Workflow completed successfully
        run_record.status = "succeeded"
        run_record.output = {
            "workflow": workflow.name,
            "steps_completed": len(workflow.steps),
            "final_context": context,
        }
        
        logger.info(f"Workflow {workflow.name} completed successfully, run {run_record.id}")
        
    except WorkflowExecutionError as e:
        run_record.status = "failed"
        run_record.output = {"error": str(e), "workflow": workflow.name}
        logger.error(f"Workflow {workflow.name} failed: {str(e)}")
        
    except Exception as e:
        run_record.status = "failed"
        run_record.output = {"error": f"Unexpected error: {str(e)}", "workflow": workflow.name}
        logger.error(f"Workflow {workflow.name} failed unexpectedly: {str(e)}", exc_info=True)
    
    # Persist final state
    await session.commit()
    await session.refresh(run_record)
    
    return run_record


def validate_workflow_steps(steps: List[Dict[str, Any]]) -> None:
    """
    Validate workflow step definitions.
    
    Args:
        steps: List of step definitions
        
    Raises:
        ValueError: If validation fails
    """
    if not steps:
        raise ValueError("Workflow must have at least one step")
    
    step_ids = set()
    
    for i, step in enumerate(steps):
        # Check required fields
        if "id" not in step:
            raise ValueError(f"Step {i} missing required field: id")
        
        if "type" not in step:
            raise ValueError(f"Step {step.get('id', i)} missing required field: type")
        
        step_id = step["id"]
        step_type = step["type"]
        
        # Check for duplicate step IDs
        if step_id in step_ids:
            raise ValueError(f"Duplicate step ID: {step_id}")
        step_ids.add(step_id)
        
        # Validate step type
        if step_type not in ["tool", "agent"]:
            raise ValueError(f"Step {step_id} has invalid type: {step_type}")
        
        # Validate tool steps
        if step_type == "tool":
            if "tool" not in step:
                raise ValueError(f"Tool step {step_id} missing required field: tool")
        
        # Validate agent steps
        if step_type == "agent":
            if "agent" not in step:
                raise ValueError(f"Agent step {step_id} missing required field: agent")





