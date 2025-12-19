"""
Version isolation service for snapshot-based definition capture.

Provides snapshot capture of agent, tool, and workflow definitions at run start time
to ensure running agents are immune to definition updates during execution.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.domain import AgentDefinition, ToolDefinition, WorkflowDefinition


async def capture_definition_snapshot(
    agent_id: uuid.UUID,
    workflow_id: Optional[uuid.UUID],
    session: AsyncSession
) -> dict:
    """
    Capture a snapshot of agent, tool, and workflow definitions at run start time.
    
    This snapshot is stored in run_record.definition_snapshot and ensures that
    running agents use the same definitions even if they are updated during execution.
    
    Args:
        agent_id: ID of the agent being executed
        workflow_id: Optional ID of the workflow being executed
        session: Database session
        
    Returns:
        Dictionary containing:
        - agent: Agent definition snapshot
        - tools: List of tool definition snapshots
        - workflow: Workflow definition snapshot (if workflow_id provided)
        
    Raises:
        ValueError: If agent or workflow not found
    """
    snapshot = {}
    
    # Capture agent definition
    agent_stmt = select(AgentDefinition).where(
        AgentDefinition.id == agent_id,
        AgentDefinition.is_active == True
    )
    agent_result = await session.execute(agent_stmt)
    agent = agent_result.scalar_one_or_none()
    
    if not agent:
        raise ValueError(f"Agent {agent_id} not found or inactive")
    
    snapshot["agent"] = {
        "id": str(agent.id),
        "name": agent.name,
        "display_name": agent.display_name,
        "description": agent.description,
        "model": agent.model,
        "instructions": agent.instructions,
        "tools": agent.tools,
        "workflow": agent.workflow,
        "scopes": agent.scopes,
        "version": agent.version,
        "is_builtin": agent.is_builtin,
        "created_by": agent.created_by,
    }
    
    # Capture tool definitions referenced by agent
    tool_names = agent.tools.get("names", []) if isinstance(agent.tools, dict) else []
    if tool_names:
        tools_stmt = select(ToolDefinition).where(
            ToolDefinition.name.in_(tool_names),
            ToolDefinition.is_active == True
        )
        tools_result = await session.execute(tools_stmt)
        tools = tools_result.scalars().all()
        
        snapshot["tools"] = [
            {
                "id": str(tool.id),
                "name": tool.name,
                "description": tool.description,
                "schema": tool.schema,
                "entrypoint": tool.entrypoint,
                "scopes": tool.scopes,
                "version": tool.version,
                "is_builtin": tool.is_builtin,
                "created_by": tool.created_by,
            }
            for tool in tools
        ]
    else:
        snapshot["tools"] = []
    
    # Capture workflow definition if specified
    if workflow_id:
        workflow_stmt = select(WorkflowDefinition).where(
            WorkflowDefinition.id == workflow_id,
            WorkflowDefinition.is_active == True
        )
        workflow_result = await session.execute(workflow_stmt)
        workflow = workflow_result.scalar_one_or_none()
        
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found or inactive")
        
        snapshot["workflow"] = {
            "id": str(workflow.id),
            "name": workflow.name,
            "description": workflow.description,
            "steps": workflow.steps,
            "version": workflow.version,
            "created_by": workflow.created_by,
        }
    else:
        snapshot["workflow"] = None
    
    return snapshot


async def validate_snapshot_compatibility(
    run_snapshot: dict,
    current_agent_id: uuid.UUID,
    current_workflow_id: Optional[uuid.UUID],
    session: AsyncSession
) -> tuple[bool, Optional[str]]:
    """
    Validate that a run's snapshot is compatible with current definitions.
    
    Used for workflow resume to ensure definitions haven't changed significantly
    since the original run.
    
    Args:
        run_snapshot: Snapshot from original run
        current_agent_id: Current agent ID
        current_workflow_id: Current workflow ID (if any)
        session: Database session
        
    Returns:
        Tuple of (is_compatible, error_message)
        - is_compatible: True if snapshot matches current definitions
        - error_message: Description of incompatibility (if any)
    """
    try:
        current_snapshot = await capture_definition_snapshot(
            current_agent_id,
            current_workflow_id,
            session
        )
    except ValueError as e:
        return False, str(e)
    
    # Check agent version
    if run_snapshot.get("agent", {}).get("version") != current_snapshot.get("agent", {}).get("version"):
        return False, "Agent definition has been updated since original run"
    
    # Check workflow version (if workflow used)
    if current_workflow_id:
        run_workflow_version = run_snapshot.get("workflow", {}).get("version") if run_snapshot.get("workflow") else None
        current_workflow_version = current_snapshot.get("workflow", {}).get("version") if current_snapshot.get("workflow") else None
        
        if run_workflow_version != current_workflow_version:
            return False, "Workflow definition has been updated since original run"
    
    # Check tool versions
    run_tools = {tool["name"]: tool["version"] for tool in run_snapshot.get("tools", [])}
    current_tools = {tool["name"]: tool["version"] for tool in current_snapshot.get("tools", [])}
    
    for tool_name, run_version in run_tools.items():
        if tool_name not in current_tools:
            return False, f"Tool '{tool_name}' no longer available"
        if run_version != current_tools[tool_name]:
            return False, f"Tool '{tool_name}' has been updated since original run"
    
    return True, None








