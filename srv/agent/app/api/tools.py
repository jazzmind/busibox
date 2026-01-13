"""
Tool CRUD API endpoints.

Provides:
- Individual tool retrieval by ID
- Tool updates with version increment
- Tool soft deletion with conflict detection
- Tool testing in sandbox mode
"""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import AgentDefinition, ToolDefinition
from app.schemas.auth import Principal
from app.schemas.definitions import ToolDefinitionRead, ToolDefinitionUpdate

router = APIRouter(prefix="/agents/tools", tags=["tools"])
logger = get_logger(__name__)


# Tool test schemas
class ToolTestRequest(BaseModel):
    """Request schema for testing a tool."""
    input: Dict[str, Any] = Field(description="Input parameters for the tool")


class ToolTestResult(BaseModel):
    """Result from tool test execution."""
    success: bool = Field(description="Whether the test execution succeeded")
    output: Optional[Dict[str, Any]] = Field(default=None, description="Tool output if successful")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    execution_time_ms: int = Field(description="Execution time in milliseconds")
    tool_name: str = Field(description="Name of the executed tool")
    input_used: Dict[str, Any] = Field(description="Input parameters that were used")


@router.get("/{tool_id}", response_model=ToolDefinitionRead)
async def get_tool(
    tool_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolDefinitionRead:
    """
    Get individual tool by ID.
    
    Checks both built-in tools and database tools.
    
    Returns:
        Tool definition if found and active
        
    Raises:
        HTTPException: 404 if tool not found or inactive
    """
    from app.services.builtin_tools import get_builtin_tool_by_id
    
    # First check built-in tools
    builtin_tool = get_builtin_tool_by_id(tool_id)
    if builtin_tool:
        logger.info(
            "builtin_tool_retrieved",
            tool_id=str(tool_id),
            tool_name=builtin_tool.name,
            user_id=principal.sub
        )
        return builtin_tool
    
    # Then check database
    tool = await session.get(ToolDefinition, tool_id)
    
    if not tool or not tool.is_active:
        logger.warning(
            "tool_not_found",
            tool_id=str(tool_id),
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Tool not found")
    
    logger.info(
        "tool_retrieved",
        tool_id=str(tool_id),
        tool_name=tool.name,
        user_id=principal.sub
    )
    
    return ToolDefinitionRead.model_validate(tool)


@router.put("/{tool_id}", response_model=ToolDefinitionRead)
async def update_tool(
    tool_id: uuid.UUID,
    payload: ToolDefinitionUpdate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolDefinitionRead:
    """
    Update a custom tool definition.
    
    Built-in tools cannot be modified (returns 403).
    Version number increments automatically.
    
    Args:
        tool_id: Tool UUID
        payload: Fields to update
        principal: Authenticated user
        session: Database session
        
    Returns:
        Updated tool definition
        
    Raises:
        HTTPException: 
            - 404 if tool not found
            - 403 if tool is built-in
            - 400 if update validation fails
    """
    tool = await session.get(ToolDefinition, tool_id)
    
    if not tool or not tool.is_active:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check if tool is built-in
    if tool.is_builtin:
        logger.warning(
            "builtin_tool_modification_attempt",
            tool_id=str(tool_id),
            tool_name=tool.name,
            user_id=principal.sub
        )
        raise HTTPException(
            status_code=403,
            detail="Cannot modify built-in tools. Built-in resources can only be modified via code deployment."
        )
    
    # Check ownership (only creator can update)
    if tool.created_by and tool.created_by != principal.sub:
        logger.warning(
            "tool_update_unauthorized",
            tool_id=str(tool_id),
            tool_name=tool.name,
            owner=tool.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Update fields
    update_data = payload.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(tool, key, value)
    
    # Increment version and update timestamp
    tool.version += 1
    tool.updated_at = datetime.now(timezone.utc)
    
    await session.commit()
    await session.refresh(tool)
    
    logger.info(
        "tool_updated",
        tool_id=str(tool_id),
        tool_name=tool.name,
        new_version=tool.version,
        user_id=principal.sub,
        updated_fields=list(update_data.keys())
    )
    
    return ToolDefinitionRead.model_validate(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> None:
    """
    Soft-delete a custom tool.
    
    Built-in tools cannot be deleted.
    Tools in use by active agents cannot be deleted.
    
    Args:
        tool_id: Tool UUID
        principal: Authenticated user
        session: Database session
        
    Raises:
        HTTPException:
            - 404 if tool not found
            - 403 if tool is built-in
            - 409 if tool is in use by active agents
    """
    tool = await session.get(ToolDefinition, tool_id)
    
    if not tool or not tool.is_active:
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check if tool is built-in
    if tool.is_builtin:
        logger.warning(
            "builtin_tool_deletion_attempt",
            tool_id=str(tool_id),
            tool_name=tool.name,
            user_id=principal.sub
        )
        raise HTTPException(
            status_code=403,
            detail="Cannot delete built-in tools"
        )
    
    # Check ownership
    if tool.created_by and tool.created_by != principal.sub:
        logger.warning(
            "tool_delete_unauthorized",
            tool_id=str(tool_id),
            tool_name=tool.name,
            owner=tool.created_by,
            user_id=principal.sub
        )
        raise HTTPException(status_code=404, detail="Tool not found")
    
    # Check if tool is in use by active agents
    stmt = select(AgentDefinition).where(
        AgentDefinition.is_active == True,
    )
    result = await session.execute(stmt)
    agents = result.scalars().all()
    
    agents_using_tool = []
    for agent in agents:
        tool_names = agent.tools.get("names", []) if isinstance(agent.tools, dict) else []
        if tool.name in tool_names:
            agents_using_tool.append({
                "id": str(agent.id),
                "name": agent.name
            })
    
    if agents_using_tool:
        logger.warning(
            "tool_in_use",
            tool_id=str(tool_id),
            tool_name=tool.name,
            user_id=principal.sub,
            agents_count=len(agents_using_tool)
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "resource_in_use",
                "message": "Tool is in use by active agents",
                "agents": agents_using_tool
            }
        )
    
    # Soft delete
    tool.is_active = False
    await session.commit()
    
    logger.info(
        "tool_deleted",
        tool_id=str(tool_id),
        tool_name=tool.name,
        user_id=principal.sub
    )


async def _get_tool_executor(tool_name: str):
    """
    Get the executor function for a built-in tool.
    Uses the builtin_tools service for consistent tool discovery.
    """
    from app.services.builtin_tools import get_tool_executor
    return get_tool_executor(tool_name)


@router.post("/{tool_id}/test", response_model=ToolTestResult)
async def test_tool(
    tool_id: uuid.UUID,
    payload: ToolTestRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolTestResult:
    """
    Execute a tool in test/sandbox mode with provided input.
    
    This endpoint allows testing tool functionality with sample inputs.
    Built-in tools are executed with live results but in a controlled context.
    
    Args:
        tool_id: Tool UUID
        payload: Input parameters for the tool
        principal: Authenticated user
        session: Database session
        
    Returns:
        ToolTestResult with execution output, timing, and status
        
    Raises:
        HTTPException: 
            - 404 if tool not found
            - 400 if tool doesn't support testing
            - 500 if execution fails
    """
    import time
    from app.services.builtin_tools import get_builtin_tool_by_id
    
    # First check built-in tools
    builtin_tool = get_builtin_tool_by_id(tool_id)
    
    if builtin_tool:
        tool_name = builtin_tool.name
        tool_is_builtin = True
    else:
        # Then check database
        tool = await session.get(ToolDefinition, tool_id)
        
        if not tool or not tool.is_active:
            logger.warning(
                "tool_test_not_found",
                tool_id=str(tool_id),
                user_id=principal.sub
            )
            raise HTTPException(status_code=404, detail="Tool not found")
        
        tool_name = tool.name
        tool_is_builtin = tool.is_builtin
    
    logger.info(
        "tool_test_started",
        tool_id=str(tool_id),
        tool_name=tool_name,
        user_id=principal.sub,
        input_keys=list(payload.input.keys())
    )
    
    start_time = time.time()
    
    try:
        # Get the executor for this tool
        executor = await _get_tool_executor(tool_name)
        
        if not executor:
            # Tool exists in DB but doesn't have a built-in executor
            logger.warning(
                "tool_test_no_executor",
                tool_id=str(tool_id),
                tool_name=tool_name,
                user_id=principal.sub
            )
            raise HTTPException(
                status_code=400,
                detail=f"Tool '{tool_name}' does not support direct testing. Only built-in tools can be tested."
            )
        
        # Execute the tool with provided input
        result = await executor(**payload.input)
        
        end_time = time.time()
        execution_time_ms = int((end_time - start_time) * 1000)
        
        # Convert result to dict
        if hasattr(result, 'model_dump'):
            output = result.model_dump()
        elif hasattr(result, 'dict'):
            output = result.dict()
        else:
            output = {"result": str(result)}
        
        logger.info(
            "tool_test_completed",
            tool_id=str(tool_id),
            tool_name=tool_name,
            user_id=principal.sub,
            execution_time_ms=execution_time_ms,
            success=True
        )
        
        return ToolTestResult(
            success=True,
            output=output,
            error=None,
            execution_time_ms=execution_time_ms,
            tool_name=tool_name,
            input_used=payload.input
        )
        
    except HTTPException:
        raise
        
    except Exception as e:
        end_time = time.time()
        execution_time_ms = int((end_time - start_time) * 1000)
        
        logger.error(
            "tool_test_failed",
            tool_id=str(tool_id),
            tool_name=tool_name,
            user_id=principal.sub,
            error=str(e),
            execution_time_ms=execution_time_ms
        )
        
        return ToolTestResult(
            success=False,
            output=None,
            error=str(e),
            execution_time_ms=execution_time_ms,
            tool_name=tool_name,
            input_used=payload.input
        )








