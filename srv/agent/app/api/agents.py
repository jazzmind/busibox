import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
from app.core.logging import get_logger
from app.db.session import get_session
from app.models.domain import AgentDefinition, EvalDefinition, ToolDefinition, WorkflowDefinition
from app.schemas.auth import Principal
from app.schemas.definitions import (
    AgentDefinitionCreate,
    AgentDefinitionRead,
    EvalDefinitionCreate,
    EvalDefinitionRead,
    ToolDefinitionCreate,
    ToolDefinitionRead,
    WorkflowDefinitionCreate,
    WorkflowDefinitionRead,
)
from app.services.agent_registry import agent_registry

logger = get_logger(__name__)

router = APIRouter(prefix="/agents", tags=["agents"])


class WeatherRequest(BaseModel):
    """Request to get weather via weather agent."""
    query: str


class WeatherResponse(BaseModel):
    """Response from weather agent."""
    response: str


@router.get("", response_model=List[AgentDefinitionRead])
async def list_agents(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[AgentDefinitionRead]:
    """
    List agents with personal filtering.
    
    Returns:
    - All built-in agents (dynamically loaded from app/agents/)
    - All built-in agents from database (is_builtin=True)
    - Personal agents created by the authenticated user (created_by=principal.sub)
    
    Other users' personal agents are not visible.
    """
    from app.services.builtin_agents import get_builtin_agent_definitions
    
    # Get dynamically loaded built-in agents from Python files
    builtin_agents = get_builtin_agent_definitions()
    
    # Get personal agents and database built-in agents
    stmt = select(AgentDefinition).where(
        AgentDefinition.is_active.is_(True),
        or_(
            AgentDefinition.is_builtin.is_(True),
            AgentDefinition.created_by == principal.sub
        )
    )
    result = await session.execute(stmt)
    db_agents = [AgentDefinitionRead.model_validate(a) for a in result.scalars().all()]
    
    # Combine built-in agents from code with database agents
    # Use a dict to deduplicate by name (code-based agents take precedence)
    agents_dict = {agent.name: agent for agent in db_agents}
    for builtin in builtin_agents:
        agents_dict[builtin.name] = builtin
    
    agents = list(agents_dict.values())
    
    logger.info(
        "agents_listed",
        user_id=principal.sub,
        total_count=len(agents),
        builtin_count=sum(1 for a in agents if a.is_builtin),
        personal_count=sum(1 for a in agents if not a.is_builtin),
        code_builtin_count=len(builtin_agents),
    )
    
    return agents


@router.get("/models")
async def list_available_models(
    # principal: Principal = Depends(get_principal),  # Temporarily disabled for testing
) -> dict:
    """
    List available models from LiteLLM.
    Models are configured in model_registry.yml with purposes like 'chat', 'research', 'agent'.
    """
    import httpx
    from app.config.settings import get_settings
    
    settings = get_settings()
    
    try:
        headers = {}
        if settings.litellm_api_key:
            headers["Authorization"] = f"Bearer {settings.litellm_api_key}"
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.litellm_base_url}/models",
                headers=headers
            )
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "error": str(e),
            "note": "Models are configured in model_registry.yml",
            "common_purposes": ["chat", "research", "agent", "tool_calling", "frontier"]
        }


@router.get("/{agent_id}", response_model=AgentDefinitionRead)
async def get_agent(
    agent_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    """
    Get individual agent by ID with ownership check.
    
    Returns:
    - Agent if it's built-in (visible to all)
    - Agent if it's personal and owned by authenticated user
    - 404 if agent doesn't exist or user doesn't have access
    """
    agent = await session.get(AgentDefinition, agent_id)
    
    # Return 404 if agent doesn't exist or is inactive
    if not agent or not agent.is_active:
        logger.warning(
            "agent_access_denied",
            agent_id=str(agent_id),
            user_id=principal.sub,
            reason="not_found_or_inactive"
        )
        raise HTTPException(status_code=404, detail="Agent not found")
    
    # Check authorization: built-in agents visible to all, personal agents only to creator
    if not agent.is_builtin and agent.created_by != principal.sub:
        logger.warning(
            "agent_access_denied",
            agent_id=str(agent_id),
            user_id=principal.sub,
            owner=agent.created_by,
            reason="unauthorized_personal_agent"
        )
        raise HTTPException(status_code=404, detail="Agent not found")
    
    logger.info(
        "agent_accessed",
        agent_id=str(agent_id),
        agent_name=agent.name,
        user_id=principal.sub,
        is_builtin=agent.is_builtin
    )
    
    return AgentDefinitionRead.model_validate(agent)


@router.post("/definitions", response_model=AgentDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_agent_definition(
    payload: AgentDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    """
    Create a new agent definition.
    
    Personal agents (is_builtin=False) are created with created_by set to authenticated user.
    Only system can create built-in agents (is_builtin=True).
    """
    agent_id = await agent_registry.add(session, payload, created_by=principal.sub, is_builtin=False)
    definition = await session.get(AgentDefinition, agent_id)
    if not definition:
        raise HTTPException(status_code=500, detail="failed to fetch saved definition")
    
    logger.info(
        "agent_created",
        agent_id=str(agent_id),
        agent_name=definition.name,
        user_id=principal.sub,
        is_builtin=False,
        model=definition.model
    )
    
    return AgentDefinitionRead.model_validate(definition)


@router.get("/tools", response_model=List[ToolDefinitionRead])
async def list_tools(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[ToolDefinitionRead]:
    stmt = select(ToolDefinition).where(ToolDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    return [ToolDefinitionRead.model_validate(t) for t in result.scalars().all()]


@router.post("/tools", response_model=ToolDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ToolDefinitionRead:
    definition = ToolDefinition(**payload.model_dump())
    session.add(definition)
    await session.commit()
    await session.refresh(definition)
    return ToolDefinitionRead.model_validate(definition)


@router.get("/workflows", response_model=List[WorkflowDefinitionRead])
async def list_workflows(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[WorkflowDefinitionRead]:
    stmt = select(WorkflowDefinition).where(WorkflowDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    return [WorkflowDefinitionRead.model_validate(w) for w in result.scalars().all()]


@router.post("/workflows", response_model=WorkflowDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkflowDefinitionRead:
    """
    Create a new workflow definition with step validation.
    
    Workflows are created with created_by set to authenticated user.
    
    Raises:
        HTTPException: 400 if workflow validation fails
    """
    try:
        # Validate workflow steps
        from app.workflows.engine import validate_workflow_steps
        validate_workflow_steps(payload.steps)
        
        record = WorkflowDefinition(
            **payload.model_dump(),
            created_by=principal.sub
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return WorkflowDefinitionRead.model_validate(record)
        
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid workflow definition: {str(e)}"
        )


@router.get("/evals", response_model=List[EvalDefinitionRead])
async def list_evals(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> List[EvalDefinitionRead]:
    stmt = select(EvalDefinition).where(EvalDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    return [EvalDefinitionRead.model_validate(e) for e in result.scalars().all()]


@router.post("/evals", response_model=EvalDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_eval(
    payload: EvalDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> EvalDefinitionRead:
    """
    Create a new evaluator definition.
    
    Evaluators are created with created_by set to authenticated user.
    """
    record = EvalDefinition(
        **payload.model_dump(),
        created_by=principal.sub
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return EvalDefinitionRead.model_validate(record)


@router.post("/weather/query", response_model=WeatherResponse)
async def query_weather_agent(
    payload: WeatherRequest,
    # principal: Principal = Depends(get_principal),  # Temporarily disabled for testing
) -> WeatherResponse:
    """
    Query the weather agent directly (for testing).
    This endpoint demonstrates LiteLLM integration and external API tool calling.
    """
    from app.agents.weather_agent import weather_agent
    
    result = await weather_agent.run(payload.query)
    # Pydantic AI result has .output attribute, not .data
    response_text = str(result.output) if hasattr(result, 'output') else str(result)
    return WeatherResponse(response=response_text)
