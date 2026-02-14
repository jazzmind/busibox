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

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    providers: Optional[Dict[str, Any]] = Field(
        default=None, 
        description="Provider configuration for multi-provider tools (e.g., web_search)"
    )


class ToolTestResult(BaseModel):
    """Result from tool test execution."""
    success: bool = Field(description="Whether the test execution succeeded")
    output: Optional[Dict[str, Any]] = Field(default=None, description="Tool output if successful")
    error: Optional[str] = Field(default=None, description="Error message if failed")
    execution_time_ms: int = Field(description="Execution time in milliseconds")
    tool_name: str = Field(description="Name of the executed tool")
    input_used: Dict[str, Any] = Field(description="Input parameters that were used")
    providers_used: Optional[list] = Field(default=None, description="Providers that returned results (for multi-provider tools)")


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
    update_data = payload.model_dump(exclude_unset=True, by_alias=True)
    for key, value in update_data.items():
        setattr(tool, key, value)
    
    # Increment version and update timestamp
    tool.version += 1
    tool.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    
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
        
        # Build kwargs for executor
        exec_kwargs = dict(payload.input)
        
        # For web_search tool, pass providers config if available
        if tool_name == "web_search" and payload.providers:
            exec_kwargs["providers"] = payload.providers
        
        # Check if tool requires RunContext (has ctx parameter)
        import inspect
        sig = inspect.signature(executor)
        requires_context = 'ctx' in sig.parameters
        
        if requires_context:
            # Tools like document_search need RunContext[BusiboxDeps]
            from app.agents.core import BusiboxDeps
            from app.clients.busibox import BusiboxClient
            from app.services.token_service import get_or_exchange_token
            
            # Determine required scopes based on tool
            # For now, search tools need search scopes
            # Note: scopes use dots (search.read) not colons (search:read)
            scopes = ["search.read"] if "search" in tool_name.lower() else []
            purpose = "search" if "search" in tool_name.lower() else "agent"
            
            # Get or exchange token for downstream service
            # This performs server-side token exchange using configured OAuth credentials
            token = await get_or_exchange_token(
                session=session,
                principal=principal,
                scopes=scopes,
                purpose=purpose
            )
            
            # Create BusiboxClient with the exchanged token
            busibox_client = BusiboxClient(access_token=token.access_token)
            
            # Create deps
            deps = BusiboxDeps(
                principal=principal,
                busibox_client=busibox_client
            )
            
            # Create a mock RunContext
            # Note: We can't create a real RunContext outside of pydantic-ai agent execution,
            # so we'll pass deps directly and let the tool access it via ctx.deps
            class MockRunContext:
                def __init__(self, deps):
                    self.deps = deps
            
            ctx = MockRunContext(deps)
            
            # Execute the tool with context as first argument
            result = await executor(ctx, **exec_kwargs)
        else:
            # Execute the tool without context (old style tools like web_search)
            result = await executor(**exec_kwargs)
        
        end_time = time.time()
        execution_time_ms = int((end_time - start_time) * 1000)
        
        # Convert result to dict
        if hasattr(result, 'model_dump'):
            output = result.model_dump()
        elif hasattr(result, 'dict'):
            output = result.dict()
        else:
            output = {"result": str(result)}
        
        # Extract providers_used if available (for web_search and similar tools)
        providers_used = None
        if hasattr(result, 'providers_used'):
            providers_used = result.providers_used
        elif isinstance(output, dict) and 'providers_used' in output:
            providers_used = output.get('providers_used')
        
        logger.info(
            "tool_test_completed",
            tool_id=str(tool_id),
            tool_name=tool_name,
            user_id=principal.sub,
            execution_time_ms=execution_time_ms,
            success=True,
            providers_used=providers_used
        )
        
        return ToolTestResult(
            success=True,
            output=output,
            error=None,
            execution_time_ms=execution_time_ms,
            tool_name=tool_name,
            input_used=payload.input,
            providers_used=providers_used
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


# ========== Tool Configuration Endpoints ==========

class ToolConfigRequest(BaseModel):
    """Request schema for tool configuration."""
    providers: Optional[Dict[str, Any]] = Field(default=None, description="Provider configuration")
    settings: Optional[Dict[str, Any]] = Field(default=None, description="Additional settings")
    scope: str = Field(default="user", description="Config scope: system, agent, or user")
    agent_id: Optional[str] = Field(default=None, description="Agent ID for agent-scoped config")


class ToolConfigResponse(BaseModel):
    """Response schema for tool configuration."""
    tool_id: str
    tool_name: str
    scope: str = "user"
    providers: Dict[str, Any] = Field(default_factory=dict)
    settings: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None


@router.get("/{tool_id}/config", response_model=ToolConfigResponse)
async def get_tool_config(
    tool_id: uuid.UUID,
    scope: str = Query("user", description="Config scope to retrieve: system, agent, or user"),
    agent_id: Optional[str] = Query(None, description="Agent ID for agent-scoped config"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolConfigResponse:
    """
    Get configuration for a specific tool.
    
    Config hierarchy (checked in order if not found at requested scope):
    1. User-level (scope=user)
    2. Agent-level (scope=agent, requires agent_id)
    3. System-level (scope=system)
    """
    from app.models.domain import ToolConfig
    from app.services.builtin_tools import get_builtin_tool_by_id
    from sqlalchemy import and_
    
    # Get tool name
    builtin_tool = get_builtin_tool_by_id(tool_id)
    if builtin_tool:
        tool_name = builtin_tool.name
    else:
        tool = await session.get(ToolDefinition, tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found")
        tool_name = tool.name
    
    config = None
    found_scope = scope
    
    # Try requested scope first, then fall back through hierarchy
    scopes_to_try = []
    if scope == "user":
        scopes_to_try = ["user", "agent", "system"] if agent_id else ["user", "system"]
    elif scope == "agent":
        scopes_to_try = ["agent", "system"]
    else:
        scopes_to_try = ["system"]
    
    for try_scope in scopes_to_try:
        if try_scope == "user":
            stmt = select(ToolConfig).where(
                and_(
                    ToolConfig.tool_id == tool_id,
                    ToolConfig.scope == "user",
                    ToolConfig.user_id == principal.sub
                )
            )
        elif try_scope == "agent" and agent_id:
            try:
                agent_uuid = uuid.UUID(agent_id)
                stmt = select(ToolConfig).where(
                    and_(
                        ToolConfig.tool_id == tool_id,
                        ToolConfig.scope == "agent",
                        ToolConfig.agent_id == agent_uuid
                    )
                )
            except ValueError:
                continue
        elif try_scope == "system":
            stmt = select(ToolConfig).where(
                and_(
                    ToolConfig.tool_id == tool_id,
                    ToolConfig.scope == "system"
                )
            )
        else:
            continue
        
        result = await session.execute(stmt)
        config = result.scalar_one_or_none()
        if config:
            found_scope = try_scope
            break
    
    if not config:
        return ToolConfigResponse(
            tool_id=str(tool_id),
            tool_name=tool_name,
            scope=scope,
            providers={},
            settings={},
        )
    
    return ToolConfigResponse(
        tool_id=str(tool_id),
        tool_name=tool_name,
        scope=found_scope,
        providers=config.config.get("providers", {}),
        settings=config.config.get("settings", {}),
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )


@router.put("/{tool_id}/config", response_model=ToolConfigResponse)
async def update_tool_config(
    tool_id: uuid.UUID,
    payload: ToolConfigRequest,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolConfigResponse:
    """
    Update configuration for a specific tool.
    
    Scope determines where the config is stored:
    - user: Per-user config (default)
    - agent: Per-agent config (requires agent_id)
    - system: System-wide default config
    """
    from app.models.domain import ToolConfig
    from app.services.builtin_tools import get_builtin_tool_by_id
    from sqlalchemy import and_
    
    scope = payload.scope or "user"
    agent_uuid = None
    
    # Validate scope and agent_id
    if scope == "agent":
        if not payload.agent_id:
            raise HTTPException(status_code=400, detail="agent_id required for agent-scoped config")
        try:
            agent_uuid = uuid.UUID(payload.agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid agent_id format")
    
    # Get tool name
    builtin_tool = get_builtin_tool_by_id(tool_id)
    if builtin_tool:
        tool_name = builtin_tool.name
    else:
        tool = await session.get(ToolDefinition, tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail="Tool not found")
        tool_name = tool.name
    
    # Find or create config at the specified scope
    if scope == "user":
        stmt = select(ToolConfig).where(
            and_(
                ToolConfig.tool_id == tool_id,
                ToolConfig.scope == "user",
                ToolConfig.user_id == principal.sub
            )
        )
    elif scope == "agent":
        stmt = select(ToolConfig).where(
            and_(
                ToolConfig.tool_id == tool_id,
                ToolConfig.scope == "agent",
                ToolConfig.agent_id == agent_uuid
            )
        )
    else:  # system
        stmt = select(ToolConfig).where(
            and_(
                ToolConfig.tool_id == tool_id,
                ToolConfig.scope == "system"
            )
        )
    
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        config = ToolConfig(
            tool_id=tool_id,
            tool_name=tool_name,
            scope=scope,
            user_id=principal.sub if scope == "user" else None,
            agent_id=agent_uuid if scope == "agent" else None,
            config={},
        )
        session.add(config)
    
    # Update config
    config.config = {
        "providers": payload.providers or {},
        "settings": payload.settings or {},
    }
    
    await session.commit()
    await session.refresh(config)
    
    logger.info(
        "tool_config_updated",
        tool_id=str(tool_id),
        tool_name=tool_name,
        scope=scope,
        user_id=principal.sub,
    )
    
    return ToolConfigResponse(
        tool_id=str(tool_id),
        tool_name=tool_name,
        scope=scope,
        providers=config.config.get("providers", {}),
        settings=config.config.get("settings", {}),
        updated_at=config.updated_at.isoformat() if config.updated_at else None,
    )


@router.delete("/tools/{tool_id}/config")
async def delete_tool_config(
    tool_id: uuid.UUID,
    scope: str = Query(default="user", description="Configuration scope to delete: user, agent, or system"),
    agent_id: Optional[str] = Query(default=None, description="Agent ID for agent-scoped config"),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    """
    Delete tool configuration at a specific scope.
    
    This allows users to clear their personal or agent-level overrides
    and fall back to the inherited configuration from a higher scope.
    
    - Personal (user) scope: Clears user's personal config, falls back to agent or system
    - Agent scope: Clears agent-specific config, falls back to system
    - System scope: Cannot be deleted (returns error)
    """
    
    # Prevent deleting system config
    if scope == "system":
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete system configuration. It serves as the default for all users."
        )
    
    agent_uuid = None
    if scope == "agent":
        if not agent_id:
            raise HTTPException(status_code=400, detail="agent_id required for agent-scoped config")
        try:
            agent_uuid = uuid.UUID(agent_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid agent_id format")
    
    # Find the config to delete
    if scope == "user":
        stmt = select(ToolConfig).where(
            and_(
                ToolConfig.tool_id == tool_id,
                ToolConfig.scope == "user",
                ToolConfig.user_id == principal.sub
            )
        )
    else:  # agent
        stmt = select(ToolConfig).where(
            and_(
                ToolConfig.tool_id == tool_id,
                ToolConfig.scope == "agent",
                ToolConfig.agent_id == agent_uuid
            )
        )
    
    result = await session.execute(stmt)
    config = result.scalar_one_or_none()
    
    if not config:
        raise HTTPException(status_code=404, detail="No configuration found at this scope")
    
    # Delete the config
    await session.delete(config)
    await session.commit()
    
    logger.info(
        "tool_config_deleted",
        tool_id=str(tool_id),
        scope=scope,
        user_id=principal.sub,
    )
    
    return {"success": True, "message": f"Configuration at {scope} scope deleted successfully"}
