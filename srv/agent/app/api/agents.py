import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import get_principal
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
    stmt = select(AgentDefinition).where(AgentDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    return [AgentDefinitionRead.model_validate(a) for a in result.scalars().all()]


@router.post("/definitions", response_model=AgentDefinitionRead, status_code=status.HTTP_201_CREATED)
async def create_agent_definition(
    payload: AgentDefinitionCreate,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> AgentDefinitionRead:
    agent_id = await agent_registry.add(session, payload)
    definition = await session.get(AgentDefinition, agent_id)
    if not definition:
        raise HTTPException(status_code=500, detail="failed to fetch saved definition")
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
    
    Raises:
        HTTPException: 400 if workflow validation fails
    """
    try:
        # Validate workflow steps
        from app.workflows.engine import validate_workflow_steps
        validate_workflow_steps(payload.steps)
        
        record = WorkflowDefinition(**payload.model_dump())
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
    record = EvalDefinition(**payload.model_dump())
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return EvalDefinitionRead.model_validate(record)


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
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.litellm_base_url}/models")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        return {
            "error": str(e),
            "note": "Models are configured in model_registry.yml",
            "common_purposes": ["chat", "research", "agent", "tool_calling", "frontier"]
        }


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
