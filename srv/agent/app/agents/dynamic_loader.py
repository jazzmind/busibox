import os
import uuid
from typing import Dict, List, Optional

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps, ingest_tool, rag_tool, search_tool
from app.config.settings import get_settings
from app.models.domain import AgentDefinition
from app.schemas.definitions import AgentDefinitionCreate

settings = get_settings()

# Registry of permitted tool adapters
TOOL_REGISTRY = {
    "search": search_tool,
    "ingest": ingest_tool,
    "rag": rag_tool,
}


def _configure_litellm_env():
    """Configure OpenAI environment for LiteLLM using shared utilities."""
    from busibox_common.llm import ensure_openai_env
    ensure_openai_env(
        base_url=str(settings.litellm_base_url),
        api_key=settings.litellm_api_key,
    )


async def load_active_agents(session: AsyncSession) -> Dict[uuid.UUID, Agent[BusiboxDeps, object]]:
    """
    Hydrate active agent definitions from the database and register allowed tools.
    """
    # Configure OpenAI client to use LiteLLM
    _configure_litellm_env()
    
    stmt = select(AgentDefinition).where(AgentDefinition.is_active.is_(True))
    result = await session.execute(stmt)
    agents: Dict[uuid.UUID, Agent[BusiboxDeps, object]] = {}
    for definition in result.scalars().all():
        # Create OpenAI-compatible model for LiteLLM
        model = OpenAIModel(
            model_name=definition.model,
            provider="openai",
        )
        agent = Agent[BusiboxDeps, object](
            model=model,
            instructions=definition.instructions,
        )
        for tool_name in definition.tools.get("names", []):
            tool_fn = TOOL_REGISTRY.get(tool_name)
            if tool_fn:
                agent.tool(tool_fn)  # type: ignore[arg-type]
        agents[definition.id] = agent
    return agents


def validate_tool_references(tool_names: List[str]) -> None:
    """
    Validate that all tool names reference entries in the TOOL_REGISTRY.
    
    Args:
        tool_names: List of tool names to validate
        
    Raises:
        ValueError: If any tool name is not in the registry
    """
    invalid_tools = [name for name in tool_names if name not in TOOL_REGISTRY]
    if invalid_tools:
        available = ", ".join(sorted(TOOL_REGISTRY.keys()))
        raise ValueError(
            f"Invalid tool references: {', '.join(invalid_tools)}. "
            f"Available tools: {available}"
        )


async def register_agent(
    session: AsyncSession,
    payload: AgentDefinitionCreate,
    created_by: Optional[str] = None,
    is_builtin: bool = False
) -> tuple[uuid.UUID, Agent[BusiboxDeps, object]]:
    """
    Persist a new agent definition and return a hydrated Agent instance.
    
    Args:
        session: Database session
        payload: Agent definition data
        created_by: User ID who created the agent (for personal agents)
        is_builtin: Whether this is a built-in system agent (default: False)
    
    Raises:
        ValueError: If any tool references are invalid
    """
    # Validate tool references before persisting
    tool_names = payload.tools.get("names", [])
    validate_tool_references(tool_names)
    
    definition = AgentDefinition(
        name=payload.name,
        display_name=payload.display_name,
        description=payload.description,
        model=payload.model,
        instructions=payload.instructions,
        tools=payload.tools,
        workflows=payload.workflows,
        scopes=payload.scopes,
        is_active=payload.is_active,
        is_builtin=is_builtin,
        created_by=created_by,
    )
    session.add(definition)
    await session.commit()
    await session.refresh(definition)
    
    # Configure OpenAI client to use LiteLLM
    _configure_litellm_env()
    
    # Create OpenAI-compatible model for LiteLLM
    model = OpenAIModel(
        model_name=definition.model,
        provider="openai",
    )
    agent = Agent[BusiboxDeps, object](model=model, instructions=definition.instructions)
    for tool_name in tool_names:
        tool_fn = TOOL_REGISTRY[tool_name]  # Safe to use [] now after validation
        agent.tool(tool_fn)  # type: ignore[arg-type]
    return definition.id, agent
