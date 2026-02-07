"""
Dynamic agent loader with tool registry support.

Loads agent definitions from database and dynamically attaches tools
based on tool names registered in BUILTIN_TOOL_METADATA.
"""
import uuid
from typing import Callable, Dict, List, Optional, Set

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps, data_tool, rag_tool, search_tool
from app.config.settings import get_settings
from app.models.domain import AgentDefinition
from app.schemas.definitions import AgentDefinitionCreate
from app.services.builtin_tools import BUILTIN_TOOL_METADATA, get_tool_executor

settings = get_settings()


def get_available_tool_names() -> Set[str]:
    """
    Get the set of all available tool names.
    
    Includes:
    - Built-in tools from BUILTIN_TOOL_METADATA
    - Legacy bundle tools (search, data, rag)
    
    Returns:
        Set of valid tool names
    """
    # Get tool names from BUILTIN_TOOL_METADATA
    builtin_names = {metadata["name"] for metadata in BUILTIN_TOOL_METADATA.values()}
    
    # Add legacy bundle tools for backward compatibility
    legacy_names = {"search", "data", "rag"}
    
    return builtin_names | legacy_names


# Legacy tool registry for backward compatibility
# These are the original bundle tools from core.py
LEGACY_TOOL_REGISTRY = {
    "search": search_tool,
    "data": data_tool,
    "rag": rag_tool,
}


def get_tool_function(tool_name: str) -> Optional[Callable]:
    """
    Get the tool function for a given tool name.
    
    Checks legacy tools first, then uses dynamic loading for builtin tools.
    
    Args:
        tool_name: Name of the tool
        
    Returns:
        Tool function or None if not found
    """
    # Check legacy tools first
    if tool_name in LEGACY_TOOL_REGISTRY:
        return LEGACY_TOOL_REGISTRY[tool_name]
    
    # Try to get from builtin tools (dynamic loading)
    return get_tool_executor(tool_name)


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
            tool_fn = get_tool_function(tool_name)
            if tool_fn:
                agent.tool(tool_fn)  # type: ignore[arg-type]
        agents[definition.id] = agent
    return agents


def validate_tool_references(tool_names: List[str]) -> None:
    """
    Validate that all tool names reference available tools.
    
    Args:
        tool_names: List of tool names to validate
        
    Raises:
        ValueError: If any tool name is not available
    """
    available_tools = get_available_tool_names()
    invalid_tools = [name for name in tool_names if name not in available_tools]
    if invalid_tools:
        available = ", ".join(sorted(available_tools))
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
        tool_fn = get_tool_function(tool_name)
        if tool_fn:
            agent.tool(tool_fn)  # type: ignore[arg-type]
    return definition.id, agent
