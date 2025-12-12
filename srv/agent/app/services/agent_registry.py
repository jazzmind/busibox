import asyncio
import uuid
from typing import Dict

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps
from app.agents.dynamic_loader import load_active_agents, register_agent
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
        if agent_id not in self._agents:
            raise KeyError(f"agent {agent_id} not loaded")
        return self._agents[agent_id]


agent_registry = AgentRegistry()
