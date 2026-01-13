"""
Agentic Dispatcher Service.

Orchestrates streaming agents in an autonomous loop, routing queries to
appropriate agents and deciding if additional work is needed based on results.

All thoughts and progress are streamed to the user in real-time.
"""

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, List, Optional, Any, Tuple

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.streaming_agent import StreamingAgent
from app.agents.web_search_agent_streaming import web_search_agent_streaming
from app.config.settings import get_settings
from app.core.logging import get_logger
from app.schemas.streaming import StreamEvent, thought, content, complete, error

logger = get_logger(__name__)
settings = get_settings()

# Configure OpenAI for routing decisions
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
os.environ["OPENAI_API_KEY"] = settings.litellm_api_key or "sk-1234"


# Registry of available streaming agents by type/name
STREAMING_AGENTS: Dict[str, StreamingAgent] = {
    "web_search": web_search_agent_streaming,
    "web_search_agent": web_search_agent_streaming,  # Alias
}

# Map agent names/types to streaming agent keys
AGENT_TYPE_MAPPING = {
    "web_search": "web_search",
    "web_search_agent": "web_search",
    "web-search": "web_search",
    "websearch": "web_search",
    "research": "web_search",
    "web search agent": "web_search",
    "web_search_agent_streaming": "web_search",
}

# Agent descriptions for routing
AGENT_DESCRIPTIONS = {
    "web_search": "Search the web for current information, news, and real-time data",
    "document": "Search internal documents and knowledge bases",
    "weather": "Get current weather information for a location",
    "chat": "General conversation and questions",
}

# UUID pattern for detecting agent IDs
UUID_PATTERN = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE)


class AgenticDispatcher:
    """
    Orchestrates streaming agents in an autonomous loop.
    
    The dispatcher:
    1. Analyzes the user's query
    2. Routes to appropriate agent(s)
    3. Streams agent thoughts/progress to user
    4. Decides if more work is needed
    5. Repeats until complete
    """
    
    def __init__(self):
        # Create routing model
        self.routing_model = OpenAIModel(
            model_name="fast",  # Use fast model for routing decisions
            provider="openai",
        )
        
        self.routing_agent = Agent(
            model=self.routing_model,
            system_prompt="""You are a query router. Analyze the user's query and determine which agent to use.

Available agents:
- web_search: For current events, news, real-time information, research topics
- document: For internal documents, knowledge base queries
- weather: For weather information
- chat: For general questions, conversation, explanations

Respond with ONLY the agent name (e.g., "web_search" or "chat").
Choose the most appropriate single agent for the query.""",
        )
        
        # Cache for agent definitions looked up from DB
        self._agent_cache: Dict[str, Dict[str, Any]] = {}
    
    async def _lookup_agent_by_id(
        self, 
        agent_id: str, 
        session: AsyncSession
    ) -> Optional[Dict[str, Any]]:
        """
        Look up an agent definition by UUID from the database or built-in agents.
        
        Returns dict with: id, name, display_name, agent_type, tools
        """
        # Check cache first
        if agent_id in self._agent_cache:
            return self._agent_cache[agent_id]
        
        # First, check built-in agents (they have deterministic UUIDs)
        try:
            from app.services.builtin_agents import get_builtin_agent_definitions
            
            builtin_agents = get_builtin_agent_definitions()
            for agent_def in builtin_agents:
                if str(agent_def.id) == agent_id:
                    agent_info = {
                        "id": str(agent_def.id),
                        "name": agent_def.name,
                        "display_name": agent_def.display_name or agent_def.name,
                        "agent_type": agent_def.agent_type or agent_def.name,
                        "tools": agent_def.tools or [],
                    }
                    self._agent_cache[agent_id] = agent_info
                    logger.info(f"Found built-in agent: {agent_info['display_name']}")
                    return agent_info
        except Exception as e:
            logger.warning(f"Failed to check built-in agents: {e}")
        
        # Then check database
        try:
            from app.models.domain import AgentDefinition
            
            result = await session.execute(
                select(AgentDefinition).where(AgentDefinition.id == agent_id)
            )
            agent_def = result.scalar_one_or_none()
            
            if agent_def:
                agent_info = {
                    "id": str(agent_def.id),
                    "name": agent_def.name,
                    "display_name": agent_def.display_name or agent_def.name,
                    "agent_type": agent_def.agent_type,
                    "tools": agent_def.tools or [],
                }
                self._agent_cache[agent_id] = agent_info
                return agent_info
        except Exception as e:
            logger.warning(f"Failed to lookup agent {agent_id} in database: {e}")
        
        return None
    
    def _resolve_streaming_agent(self, agent_info: Dict[str, Any]) -> Optional[str]:
        """
        Resolve an agent definition to a streaming agent key.
        
        Maps agent types/names to our streaming agent registry.
        """
        # Try agent_type first
        agent_type = agent_info.get("agent_type", "").lower()
        if agent_type in AGENT_TYPE_MAPPING:
            return AGENT_TYPE_MAPPING[agent_type]
        
        # Try name
        name = agent_info.get("name", "").lower().replace(" ", "_").replace("-", "_")
        if name in AGENT_TYPE_MAPPING:
            return AGENT_TYPE_MAPPING[name]
        
        # Check if name contains keywords
        name_lower = name.lower()
        if "web" in name_lower and "search" in name_lower:
            return "web_search"
        if "research" in name_lower:
            return "web_search"
        
        # Check tools - if it has web_search tool, use web_search agent
        tools = agent_info.get("tools", [])
        if any("web_search" in str(t).lower() for t in tools):
            return "web_search"
        
        return None
    
    async def run(
        self,
        query: str,
        user_id: str,
        session: AsyncSession,
        cancel: asyncio.Event,
        available_agents: Optional[List[str]] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """
        Main entry point for agentic dispatch.
        
        Yields StreamEvents as the dispatcher and agents work.
        
        Args:
            query: User's query
            user_id: User ID for logging
            session: Database session
            cancel: Cancellation event
            available_agents: List of agent IDs that can be used (can be UUIDs or names)
            conversation_history: Previous messages for context
        """
        start_time = datetime.now(timezone.utc)
        
        logger.info(
            "Agentic dispatcher started",
            extra={
                "user_id": user_id,
                "query_length": len(query),
                "available_agents": available_agents,
            }
        )
        
        try:
            # Step 1: Resolve agents and determine routing strategy
            resolved_agents: List[Tuple[str, str, Optional[str]]] = []  # (id, display_name, streaming_key)
            
            if available_agents:
                for agent_id in available_agents:
                    # Check if it's a UUID (specific agent from DB)
                    if UUID_PATTERN.match(agent_id):
                        agent_info = await self._lookup_agent_by_id(agent_id, session)
                        if agent_info:
                            streaming_key = self._resolve_streaming_agent(agent_info)
                            resolved_agents.append((
                                agent_id,
                                agent_info["display_name"],
                                streaming_key
                            ))
                            logger.info(f"Resolved agent {agent_id} -> {agent_info['display_name']} -> {streaming_key}")
                        else:
                            # Unknown UUID, skip
                            logger.warning(f"Could not resolve agent UUID: {agent_id}")
                    else:
                        # It's a known agent type/name
                        display_name = self._get_agent_display_name(agent_id)
                        streaming_key = agent_id if agent_id in STREAMING_AGENTS else AGENT_TYPE_MAPPING.get(agent_id)
                        resolved_agents.append((agent_id, display_name, streaming_key))
            
            # If no agents resolved, use defaults
            if not resolved_agents:
                resolved_agents = [
                    ("web_search", "Web Search Agent", "web_search"),
                    ("chat", "Chat Assistant", None),
                ]
            
            # Step 2: Determine which agent to use
            # If only one agent is available, use it directly (no routing needed)
            single_agent_mode = len(resolved_agents) == 1
            
            if single_agent_mode:
                selected_id, selected_display_name, selected_streaming_key = resolved_agents[0]
                
                yield thought(
                    source="dispatcher",
                    message=f"Using **{selected_display_name}** to help with your request..."
                )
            else:
                # Multiple agents - need to route
                yield thought(
                    source="dispatcher",
                    message=f"Analyzing your request..."
                )
                
                if cancel.is_set():
                    return
                
                # Route to best agent
                agent_names = [name for _, name, _ in resolved_agents]
                selected_idx = await self._route_query_to_index(query, agent_names)
                selected_id, selected_display_name, selected_streaming_key = resolved_agents[selected_idx]
                
                yield thought(
                    source="dispatcher",
                    message=f"I'll use the **{selected_display_name}** to help with this.",
                    data={"selected_agent": selected_id}
                )
            
            if cancel.is_set():
                return
            
            # Step 3: Execute the selected agent
            if selected_streaming_key and selected_streaming_key in STREAMING_AGENTS:
                agent = STREAMING_AGENTS[selected_streaming_key]
                
                # Create a queue to collect agent events
                events_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
                agent_complete = asyncio.Event()
                agent_result = {"output": ""}
                
                async def stream_callback(event: StreamEvent):
                    """Callback for agent to stream events."""
                    await events_queue.put(event)
                    # Capture content events for the result
                    if event.type == "content":
                        agent_result["output"] = event.message
                
                # Run agent in background task
                async def run_agent():
                    try:
                        result = await agent.run_with_streaming(
                            query=query,
                            stream=stream_callback,
                            cancel=cancel,
                            context={"conversation_history": conversation_history}
                        )
                        agent_result["output"] = result
                    except Exception as e:
                        await events_queue.put(error(
                            source=agent.name,
                            message=f"Agent error: {str(e)}"
                        ))
                    finally:
                        agent_complete.set()
                
                # Start agent task
                agent_task = asyncio.create_task(run_agent())
                
                # Yield events as they come in
                try:
                    while not agent_complete.is_set() or not events_queue.empty():
                        if cancel.is_set():
                            agent_task.cancel()
                            break
                        
                        try:
                            # Wait for event with timeout
                            event = await asyncio.wait_for(
                                events_queue.get(),
                                timeout=0.1
                            )
                            yield event
                        except asyncio.TimeoutError:
                            # No event ready, check if agent is done
                            continue
                    
                    # Drain any remaining events
                    while not events_queue.empty():
                        yield await events_queue.get()
                        
                except asyncio.CancelledError:
                    agent_task.cancel()
                    raise
                
                # Wait for agent to finish
                await agent_task
                
            else:
                # Fallback for non-streaming agents (chat, etc.)
                yield thought(
                    source="dispatcher",
                    message=f"Processing with general assistant..."
                )
                
                # Use a simple response for now
                fallback_response = await self._fallback_response(query)
                yield content(
                    source="dispatcher",
                    message=fallback_response
                )
            
            if cancel.is_set():
                return
            
            # Step 3: Check if more work is needed (future enhancement)
            # For now, we complete after one agent
            
            yield thought(
                source="dispatcher",
                message="Research complete."
            )
            
            # Final completion
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            yield complete(
                source="dispatcher",
                message="Done!",
                data={"elapsed_seconds": elapsed}
            )
            
            logger.info(
                "Agentic dispatcher completed",
                extra={
                    "user_id": user_id,
                    "elapsed_seconds": elapsed,
                    "selected_agent": selected_id,
                    "selected_agent_name": selected_display_name,
                }
            )
            
        except asyncio.CancelledError:
            logger.info("Agentic dispatcher cancelled", extra={"user_id": user_id})
            yield thought(
                source="dispatcher",
                message="Request cancelled."
            )
            raise
            
        except Exception as e:
            logger.error(
                f"Agentic dispatcher error: {e}",
                extra={"user_id": user_id, "error": str(e)},
                exc_info=True
            )
            yield error(
                source="dispatcher",
                message=f"An error occurred: {str(e)}"
            )
    
    async def _route_query_to_index(
        self,
        query: str,
        agent_names: List[str]
    ) -> int:
        """
        Route the query to the appropriate agent.
        
        Returns the index of the selected agent in the list.
        """
        if len(agent_names) == 1:
            return 0
        
        try:
            # Use routing agent to decide
            agents_list = "\n".join(f"- {i}: {name}" for i, name in enumerate(agent_names))
            result = await self.routing_agent.run(
                f"Query: {query}\n\nAvailable agents:\n{agents_list}\n\nRespond with ONLY the number of the best agent."
            )
            
            response = str(result.output).strip() if hasattr(result, 'output') else str(result).strip()
            
            # Try to extract a number
            for i in range(len(agent_names)):
                if str(i) in response:
                    return i
            
            # Check for name matches
            response_lower = response.lower()
            for i, name in enumerate(agent_names):
                if name.lower() in response_lower:
                    return i
            
            # Default: prefer web search for research-like queries
            query_lower = query.lower()
            if any(word in query_lower for word in ["search", "find", "research", "what", "how", "news", "current", "today", "happening"]):
                for i, name in enumerate(agent_names):
                    if "search" in name.lower() or "research" in name.lower():
                        return i
            
            # Default to first agent
            return 0
            
        except Exception as e:
            logger.warning(f"Routing failed, using first agent: {e}")
            return 0
    
    def _get_agent_display_name(self, agent_id: str) -> str:
        """Get human-readable name for an agent."""
        names = {
            "web_search": "Web Search Agent",
            "document": "Document Search Agent",
            "weather": "Weather Agent",
            "chat": "Chat Assistant",
        }
        return names.get(agent_id, agent_id.replace("_", " ").title())
    
    async def _fallback_response(self, query: str) -> str:
        """Generate a fallback response for non-streaming agents."""
        try:
            model = OpenAIModel(model_name=settings.default_model, provider="openai")
            agent = Agent(model=model)
            result = await agent.run(query)
            return str(result.output) if hasattr(result, 'output') else str(result)
        except Exception as e:
            return f"I encountered an error processing your request: {str(e)}"


# Singleton instance
agentic_dispatcher = AgenticDispatcher()


async def run_agentic_dispatcher(
    query: str,
    user_id: str,
    session: AsyncSession,
    cancel: asyncio.Event,
    available_agents: Optional[List[str]] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None,
) -> AsyncGenerator[StreamEvent, None]:
    """
    Convenience function to run the agentic dispatcher.
    
    Yields StreamEvents as the dispatcher and agents work.
    """
    async for event in agentic_dispatcher.run(
        query=query,
        user_id=user_id,
        session=session,
        cancel=cancel,
        available_agents=available_agents,
        conversation_history=conversation_history,
    ):
        yield event
