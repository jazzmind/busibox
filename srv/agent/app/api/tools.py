"""
Tool CRUD API endpoints.

Provides:
- Individual tool retrieval by ID
- Tool updates with version increment
- Tool soft deletion with conflict detection
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
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


@router.get("/{tool_id}", response_model=ToolDefinitionRead)
async def get_tool(
    tool_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolDefinitionRead:
    """
    Get individual tool by ID.
    
    Returns:
        Tool definition if found and active
        
    Raises:
        HTTPException: 404 if tool not found or inactive
    """
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








