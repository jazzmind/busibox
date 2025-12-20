import asyncio
import os
import uuid
from typing import Dict, Optional

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps
from app.agents.dynamic_loader import load_active_agents, register_agent, TOOL_REGISTRY
from app.schemas.definitions import AgentDefinitionCreate


class AgentRegistry:
    """
    In-memory registry of hydrated agents. Refreshable on demand.
    """

    def __init__(self) -> None:
        self._agents: Dict[uuid.UUID, Agent[BusiboxDeps, object]] = {}
        self._lock = asyncio.Lock()

    async def refresh(self, session: AsyncSession) -> None:
        async with self._lock:
            self._agents = await load_active_agents(session)

    async def add(
        self,
        session: AsyncSession,
        payload: AgentDefinitionCreate,
        created_by: str | None = None,
        is_builtin: bool = False
    ) -> uuid.UUID:
        async with self._lock:
            agent_id, agent = await register_agent(session, payload, created_by=created_by, is_builtin=is_builtin)
            self._agents[agent_id] = agent
            return agent_id

    def get(self, agent_id: uuid.UUID) -> Agent[BusiboxDeps, object]:
        """
        Get agent by ID. Checks in order:
        1. In-memory registry (database agents loaded at startup)
        2. Built-in code agents (always use latest from code)
        
        Raises:
            KeyError: If agent not found
        """
        # Check in-memory registry first
        if agent_id in self._agents:
            return self._agents[agent_id]
        
        # Check built-in code agents
        from app.services.builtin_agents import get_builtin_agent_by_id
        agent = get_builtin_agent_by_id(agent_id)
        if agent:
            return agent
        
        raise KeyError(f"agent {agent_id} not loaded")
    
    async def get_or_load(
        self, 
        agent_id: uuid.UUID, 
        session: Optional[AsyncSession] = None
    ) -> Agent[BusiboxDeps, object]:
        """
        Get agent by ID with on-demand loading from database if not in registry.
        
        Checks in order:
        1. In-memory registry (database agents loaded at startup)
        2. Built-in code agents (always use latest from code)
        3. Database (on-demand loading for personal/custom agents)
        
        Args:
            agent_id: Agent UUID
            session: Optional database session for on-demand loading
            
        Returns:
            Agent instance
            
        Raises:
            KeyError: If agent not found anywhere
            ValueError: If agent found but inactive
        """
        # Try standard get first (checks registry + built-ins)
        try:
            return self.get(agent_id)
        except KeyError:
            pass
        
        # If session provided, try loading from database
        if session:
            from app.models.domain import AgentDefinition
            from app.config.settings import get_settings
            
            stmt = select(AgentDefinition).where(AgentDefinition.id == agent_id)
            result = await session.execute(stmt)
            definition = result.scalar_one_or_none()
            
            if not definition:
                raise KeyError(f"agent {agent_id} not found in registry, built-ins, or database")
            
            if not definition.is_active:
                raise ValueError(f"agent {agent_id} is not active")
            
            # Configure OpenAI client to use LiteLLM
            settings = get_settings()
            os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
            litellm_api_key = os.getenv("LITELLM_API_KEY", "sk-1234")
            os.environ["OPENAI_API_KEY"] = litellm_api_key
            
            # Create agent instance
            model = OpenAIModel(
                model_name=definition.model,
                provider="openai",
            )
            agent = Agent[BusiboxDeps, object](
                model=model,
                instructions=definition.instructions,
            )
            
            # Register tools
            for tool_name in definition.tools.get("names", []):
                tool_fn = TOOL_REGISTRY.get(tool_name)
                if tool_fn:
                    agent.tool(tool_fn)
            
            # Cache in registry for future use
            async with self._lock:
                self._agents[agent_id] = agent
            
            return agent
        
        raise KeyError(f"agent {agent_id} not found and no session provided for on-demand loading")


agent_registry = AgentRegistry()
