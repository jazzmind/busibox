"""
Chat execution service for running tools and agents.

Handles:
- Tool execution (web search, document search)
- Agent execution with run records
- Result aggregation
- Streaming support
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from pydantic_ai import Agent
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.web_search_agent import web_search_agent
from app.agents.document_agent import document_agent
from app.models.domain import RunRecord
from app.schemas.auth import Principal
from app.schemas.dispatcher import RoutingDecision

logger = logging.getLogger(__name__)


class ToolExecutionResult:
    """Result from tool execution."""
    
    def __init__(
        self,
        tool_name: str,
        success: bool,
        output: str,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ):
        self.tool_name = tool_name
        self.success = success
        self.output = output
        self.metadata = metadata or {}
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "tool_name": self.tool_name,
            "success": self.success,
            "output": self.output,
            "metadata": self.metadata,
            "error": self.error
        }


class AgentExecutionResult:
    """Result from agent execution."""
    
    def __init__(
        self,
        agent_id: str,
        run_id: uuid.UUID,
        success: bool,
        output: str,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        agent_name: Optional[str] = None
    ):
        self.agent_id = agent_id
        self.run_id = run_id
        self.success = success
        self.output = output
        self.metadata = metadata or {}
        self.error = error
        self.agent_name = agent_name
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "agent_id": self.agent_id,
            "run_id": str(self.run_id),
            "success": self.success,
            "output": self.output,
            "metadata": self.metadata,
            "error": self.error
        }
        if self.agent_name:
            result["agent_name"] = self.agent_name
        return result


class ChatExecutionResult:
    """Combined result from chat execution."""
    
    def __init__(
        self,
        content: str,
        tool_results: List[ToolExecutionResult],
        agent_results: List[AgentExecutionResult],
        model_used: str,
        routing_decision: RoutingDecision
    ):
        self.content = content
        self.tool_results = tool_results
        self.agent_results = agent_results
        self.model_used = model_used
        self.routing_decision = routing_decision
    
    def get_tool_calls_json(self) -> List[Dict[str, Any]]:
        """Get tool calls in JSON format for storage."""
        return [result.to_dict() for result in self.tool_results]
    
    def get_run_ids(self) -> List[uuid.UUID]:
        """Get list of run IDs from agent executions."""
        return [result.run_id for result in self.agent_results]


async def execute_web_search(query: str, user_id: str) -> ToolExecutionResult:
    """
    Execute web search tool.
    
    Args:
        query: Search query
        user_id: User ID for logging
        
    Returns:
        ToolExecutionResult with search results
    """
    try:
        logger.info(
            f"Executing web search for user {user_id}",
            extra={"user_id": user_id, "query": query[:100]}
        )
        
        # Run the web search agent
        result = await web_search_agent.run(query)
        
        # Extract output
        output = result.data if hasattr(result, 'data') else str(result)
        
        logger.info(
            f"Web search completed for user {user_id}",
            extra={"user_id": user_id, "output_length": len(output)}
        )
        
        return ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output=output,
            metadata={
                "query": query,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        
    except Exception as e:
        logger.error(
            f"Web search failed for user {user_id}: {e}",
            extra={"user_id": user_id, "error": str(e)},
            exc_info=True
        )
        
        return ToolExecutionResult(
            tool_name="web_search",
            success=False,
            output="",
            error=f"Web search failed: {str(e)}"
        )


async def execute_document_search(query: str, user_id: str) -> ToolExecutionResult:
    """
    Execute document search tool.
    
    Args:
        query: Search query
        user_id: User ID for logging
        
    Returns:
        ToolExecutionResult with search results
    """
    try:
        logger.info(
            f"Executing document search for user {user_id}",
            extra={"user_id": user_id, "query": query[:100]}
        )
        
        # Run the document agent
        result = await document_agent.run(query)
        
        # Extract output
        output = result.data if hasattr(result, 'data') else str(result)
        
        logger.info(
            f"Document search completed for user {user_id}",
            extra={"user_id": user_id, "output_length": len(output)}
        )
        
        return ToolExecutionResult(
            tool_name="doc_search",
            success=True,
            output=output,
            metadata={
                "query": query,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
        
    except Exception as e:
        logger.error(
            f"Document search failed for user {user_id}: {e}",
            extra={"user_id": user_id, "error": str(e)},
            exc_info=True
        )
        
        return ToolExecutionResult(
            tool_name="doc_search",
            success=False,
            output="",
            error=f"Document search failed: {str(e)}"
        )


async def execute_tools(
    selected_tools: List[str],
    query: str,
    user_id: str
) -> List[ToolExecutionResult]:
    """
    Execute selected tools in parallel.
    
    Args:
        selected_tools: List of tool names to execute
        query: User query
        user_id: User ID for logging
        
    Returns:
        List of ToolExecutionResult
    """
    if not selected_tools:
        return []
    
    logger.info(
        f"Executing {len(selected_tools)} tools for user {user_id}",
        extra={"user_id": user_id, "tools": selected_tools}
    )
    
    # Create tasks for parallel execution
    tasks = []
    for tool_name in selected_tools:
        if tool_name == "web_search":
            tasks.append(execute_web_search(query, user_id))
        elif tool_name == "doc_search":
            tasks.append(execute_document_search(query, user_id))
        else:
            logger.warning(
                f"Unknown tool: {tool_name}",
                extra={"user_id": user_id, "tool_name": tool_name}
            )
    
    # Execute all tools in parallel
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to error results
        tool_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tool_name = selected_tools[i] if i < len(selected_tools) else "unknown"
                tool_results.append(
                    ToolExecutionResult(
                        tool_name=tool_name,
                        success=False,
                        output="",
                        error=f"Tool execution failed: {str(result)}"
                    )
                )
            else:
                tool_results.append(result)
        
        return tool_results
    
    return []


async def execute_agent(
    agent_id: str,
    query: str,
    user_id: str,
    session: AsyncSession,
    principal: Optional[Principal] = None,
    context: Optional[Dict[str, Any]] = None
) -> AgentExecutionResult:
    """
    Execute an agent via run_service and return result.
    
    This is a thin wrapper around run_service.create_run() that converts
    the RunRecord to an AgentExecutionResult for chat execution.
    
    Args:
        agent_id: Agent ID to execute
        query: User query
        user_id: User ID for logging
        session: Database session
        principal: Optional authenticated principal for token exchange
        context: Optional context for agent
        
    Returns:
        AgentExecutionResult with agent output
    """
    from app.services.run_service import create_run
    
    try:
        logger.info(
            f"Executing agent {agent_id} for user {user_id}",
            extra={"user_id": user_id, "agent_id": agent_id}
        )
        
        # Create principal if not provided
        if not principal:
            principal = Principal(sub=user_id, scopes=[], client_id="agent-api")
        
        # Convert agent_id to UUID
        agent_uuid = uuid.UUID(agent_id) if isinstance(agent_id, str) else agent_id
        
        # Check if agent uses tools to determine if we need token exchange
        # Get agent definition to check for tools and get agent name
        from app.models.domain import AgentDefinition
        from sqlalchemy import select
        from app.services.builtin_agents import get_builtin_agent_definitions
        
        needs_token_exchange = True  # Default to needing it
        agent_name = None  # Track agent name for response
        
        # Check built-in agents first
        builtin_defs = get_builtin_agent_definitions()
        for builtin_def in builtin_defs:
            if builtin_def.id == agent_uuid:
                # Check if this built-in has tools
                tool_names = builtin_def.tools.get("names", []) if builtin_def.tools else []
                needs_token_exchange = len(tool_names) > 0
                agent_name = builtin_def.display_name or builtin_def.name
                break
        else:
            # Not a built-in, check database
            stmt = select(AgentDefinition).where(AgentDefinition.id == agent_uuid)
            result = await session.execute(stmt)
            db_def = result.scalar_one_or_none()
            if db_def:
                tool_names = db_def.tools.get("names", []) if db_def.tools else []
                needs_token_exchange = len(tool_names) > 0
                agent_name = db_def.display_name or db_def.name
        
        # Set scopes based on whether agent uses tools
        if needs_token_exchange:
            scopes = ["search:read", "ingest:write", "rag:read"]
        else:
            scopes = []  # No tools = no downstream services = no token exchange needed
        
        # Use run_service for actual execution
        run_record = await create_run(
            session=session,
            principal=principal,
            agent_id=agent_uuid,
            payload={"prompt": query, "context": context or {}},
            scopes=scopes,
            purpose="chat-agent-execution",
            agent_tier="simple"  # Chat agents use simple tier (30s timeout)
        )
        
        # Convert RunRecord to AgentExecutionResult
        success = run_record.status == "succeeded"
        
        # Extract output string
        if success:
            output_obj = run_record.output or {}
            logger.info(
                f"Extracting output from run_record",
                extra={
                    "run_id": str(run_record.id),
                    "output_type": type(output_obj).__name__,
                    "output_keys": list(output_obj.keys()) if isinstance(output_obj, dict) else None,
                    "output_preview": str(output_obj)[:200]
                }
            )
            
            if isinstance(output_obj, dict):
                # Try to get a clean output string
                if "result" in output_obj:
                    output = str(output_obj["result"])
                elif "data" in output_obj:
                    # Handle nested data
                    data = output_obj["data"]
                    if isinstance(data, str):
                        output = data
                    else:
                        output = str(data)
                else:
                    # No recognized key, stringify the whole thing
                    output = str(output_obj)
            else:
                output = str(output_obj)
        else:
            output = ""
        
        # Extract error if failed
        error = None
        if not success:
            error_obj = run_record.output or {}
            if isinstance(error_obj, dict) and "error" in error_obj:
                error = error_obj["error"]
            else:
                error = f"Agent execution {run_record.status}"
        
        logger.info(
            f"Agent execution completed for user {user_id}",
            extra={
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": str(run_record.id),
                "status": run_record.status
            }
        )
        
        return AgentExecutionResult(
            agent_id=agent_id,
            run_id=run_record.id,
            success=success,
            output=output,
            metadata={
                "status": run_record.status,
                "events": run_record.events or [],
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            error=error,
            agent_name=agent_name
        )
        
    except Exception as e:
        logger.error(
            f"Agent execution failed for user {user_id}: {e}",
            extra={"user_id": user_id, "agent_id": agent_id, "error": str(e)},
            exc_info=True
        )
        
        # Return error result with a generated run_id (agent_name may not be available on error)
        return AgentExecutionResult(
            agent_id=agent_id,
            run_id=uuid.uuid4(),
            success=False,
            output="",
            error=f"Agent execution failed: {str(e)}"
        )


async def execute_agents(
    selected_agents: List[str],
    query: str,
    user_id: str,
    session: AsyncSession,
    principal: Optional[Principal] = None,
    context: Optional[Dict[str, Any]] = None
) -> List[AgentExecutionResult]:
    """
    Execute selected agents sequentially.
    
    Args:
        selected_agents: List of agent IDs to execute
        query: User query
        user_id: User ID for logging
        session: Database session
        principal: Optional authenticated principal for token exchange
        context: Optional context for agents
        
    Returns:
        List of AgentExecutionResult
    """
    if not selected_agents:
        return []
    
    logger.info(
        f"Executing {len(selected_agents)} agents for user {user_id}",
        extra={"user_id": user_id, "agents": selected_agents}
    )
    
    # Execute agents sequentially (could be parallel in future)
    results = []
    for agent_id in selected_agents:
        result = await execute_agent(agent_id, query, user_id, session, principal, context)
        results.append(result)
    
    return results


async def synthesize_response(
    query: str,
    tool_results: List[ToolExecutionResult],
    agent_results: List[AgentExecutionResult],
    model: str,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Synthesize final response from tool and agent results.
    
    Args:
        query: Original user query
        tool_results: Results from tool execution
        agent_results: Results from agent execution
        model: Model to use for synthesis
        conversation_history: Optional conversation history for context
        
    Returns:
        Synthesized response string
    """
    # If we have a single successful agent result with no tools, return it directly
    # (agent already generated a complete response)
    if len(agent_results) == 1 and not tool_results and agent_results[0].success:
        return agent_results[0].output
    
    # Build context from results for multi-step responses
    context_parts = []
    
    # Add tool results
    for result in tool_results:
        if result.success:
            context_parts.append(f"**{result.tool_name.replace('_', ' ').title()} Results:**\n{result.output}")
        else:
            context_parts.append(f"**{result.tool_name.replace('_', ' ').title()}:** {result.error}")
    
    # Add agent results
    for result in agent_results:
        agent_label = result.agent_name if result.agent_name else f"Agent {result.agent_id}"
        if result.success:
            context_parts.append(f"**{agent_label}:**\n{result.output}")
        else:
            context_parts.append(f"**{agent_label}:** {result.error}")
    
    if not context_parts:
        return "I wasn't able to gather information to answer your question. Please try rephrasing or enabling additional tools."
    
    # For now, return formatted results
    # TODO: Use LLM to synthesize a natural response
    response = f"Based on your query: \"{query}\"\n\n"
    response += "\n\n".join(context_parts)
    response += "\n\n---\n*Response synthesized from tool and agent results*"
    
    return response


async def execute_chat(
    query: str,
    routing_decision: RoutingDecision,
    model: str,
    user_id: str,
    session: AsyncSession,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> ChatExecutionResult:
    """
    Execute chat with tools and agents based on routing decision.
    
    Args:
        query: User query
        routing_decision: Routing decision from dispatcher
        model: Model to use
        user_id: User ID
        session: Database session
        conversation_history: Optional conversation history
        
    Returns:
        ChatExecutionResult with complete execution results
    """
    logger.info(
        f"Executing chat for user {user_id}",
        extra={
            "user_id": user_id,
            "model": model,
            "tools": routing_decision.selected_tools,
            "agents": routing_decision.selected_agents
        }
    )
    
    # Execute tools and agents in parallel
    tool_task = execute_tools(routing_decision.selected_tools, query, user_id)
    agent_task = execute_agents(routing_decision.selected_agents, query, user_id, session)
    
    tool_results, agent_results = await asyncio.gather(tool_task, agent_task)
    
    # Synthesize response
    content = await synthesize_response(
        query,
        tool_results,
        agent_results,
        model,
        conversation_history
    )
    
    logger.info(
        f"Chat execution completed for user {user_id}",
        extra={
            "user_id": user_id,
            "tool_count": len(tool_results),
            "agent_count": len(agent_results),
            "content_length": len(content)
        }
    )
    
    return ChatExecutionResult(
        content=content,
        tool_results=tool_results,
        agent_results=agent_results,
        model_used=model,
        routing_decision=routing_decision
    )


# Friendly display names for tools
TOOL_DISPLAY_NAMES = {
    "web_search": "Web Search",
    "document_search": "Document Search",
    "get_weather": "Weather",
    "ingest_document": "Document Ingestion",
    "web_scraper": "Web Page Reader",
}

# Friendly display names for agents
AGENT_DISPLAY_NAMES = {
    "web_search_agent": "Web Research Agent",
    "document_agent": "Document Analysis Agent",
    "weather_agent": "Weather Agent",
    "chat_agent": "Chat Agent",
}


def _get_tool_display_name(tool_name: str) -> str:
    """Get friendly display name for a tool."""
    return TOOL_DISPLAY_NAMES.get(tool_name, tool_name.replace("_", " ").title())


def _get_agent_display_name(agent_id: str) -> str:
    """Get friendly display name for an agent."""
    return AGENT_DISPLAY_NAMES.get(agent_id, agent_id.replace("_", " ").title())


def _summarize_tool_result(tool_name: str, result: ToolExecutionResult) -> str:
    """Generate a brief summary of a tool result."""
    if not result.success:
        return f"encountered an issue: {result.error or 'Unknown error'}"
    
    output = result.output
    if tool_name == "web_search":
        # Try to count results from output
        if "result" in output.lower():
            return "found relevant web results"
        return "completed web search"
    
    elif tool_name == "document_search":
        if "found" in output.lower() or "result" in output.lower():
            return "found relevant documents"
        return "completed document search"
    
    elif tool_name == "get_weather":
        if "°" in output or "temperature" in output.lower():
            return "retrieved current weather"
        return "completed weather lookup"
    
    elif tool_name == "web_scraper":
        return "extracted page content"
    
    return "completed successfully"


async def execute_chat_stream(
    query: str,
    routing_decision: RoutingDecision,
    model: str,
    user_id: str,
    session: AsyncSession,
    principal: Optional[Principal] = None,
    conversation_history: Optional[List[Dict[str, str]]] = None
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Execute chat with streaming updates and descriptive status messages.
    
    Yields:
        Dict with event type, data, and human-readable message
    """
    logger.info(
        f"Executing streaming chat for user {user_id}",
        extra={"user_id": user_id, "model": model}
    )
    
    # Announce what we're about to do
    tool_names = [_get_tool_display_name(t) for t in routing_decision.selected_tools]
    agent_names = [_get_agent_display_name(a) for a in routing_decision.selected_agents]
    
    resources_used = []
    if tool_names:
        resources_used.append(f"tools ({', '.join(tool_names)})")
    if agent_names:
        resources_used.append(f"agents ({', '.join(agent_names)})")
    
    if resources_used:
        yield {
            "type": "planning",
            "message": f"I'll use {' and '.join(resources_used)} to help with this.",
            "data": {
                "tools": routing_decision.selected_tools,
                "agents": routing_decision.selected_agents,
                "reasoning": routing_decision.reasoning,
            }
        }
    
    # Execute tools with descriptive messages
    tool_results = []
    if routing_decision.selected_tools:
        for tool_name in routing_decision.selected_tools:
            display_name = _get_tool_display_name(tool_name)
            
            # Announce tool start
            yield {
                "type": "tool_start",
                "message": f"Running {display_name}...",
                "data": {"tool": tool_name, "display_name": display_name}
            }
        
        # Execute all tools
        tool_results = await execute_tools(routing_decision.selected_tools, query, user_id)
        
        # Report results
        for result in tool_results:
            display_name = _get_tool_display_name(result.tool_name)
            summary = _summarize_tool_result(result.tool_name, result)
            
            yield {
                "type": "tool_result",
                "message": f"{display_name} {summary}.",
                "data": result.to_dict()
            }
    
    # Execute agents with descriptive messages
    agent_results = []
    if routing_decision.selected_agents:
        for agent_id in routing_decision.selected_agents:
            display_name = _get_agent_display_name(agent_id)
            
            # Announce agent start
            yield {
                "type": "agent_start",
                "message": f"Consulting {display_name}...",
                "data": {"agent": agent_id, "display_name": display_name}
            }
        
        # Execute all agents
        agent_results = await execute_agents(
            routing_decision.selected_agents,
            query,
            user_id,
            session,
            principal
        )
        
        # Report results
        for result in agent_results:
            display_name = _get_agent_display_name(result.agent_id)
            
            if result.success:
                yield {
                    "type": "agent_result",
                    "message": f"{display_name} responded.",
                    "data": result.to_dict()
                }
            else:
                yield {
                    "type": "agent_result",
                    "message": f"{display_name} encountered an issue: {result.error or 'Unknown error'}",
                    "data": result.to_dict()
                }
    
    # Synthesize response
    yield {
        "type": "synthesis_start",
        "message": "Combining results to create your answer...",
        "data": {"tool_count": len(tool_results), "agent_count": len(agent_results)}
    }
    
    content = await synthesize_response(
        query,
        tool_results,
        agent_results,
        model,
        conversation_history
    )
    
    logger.info(
        f"Synthesized content for streaming",
        extra={
            "user_id": user_id,
            "content_length": len(content),
            "content_preview": content[:200]
        }
    )
    
    # Stream content in chunks
    words = content.split()
    for i, word in enumerate(words):
        chunk = word + (" " if i < len(words) - 1 else "")
        yield {
            "type": "content_chunk",
            "data": {"chunk": chunk}
        }
        await asyncio.sleep(0.02)  # Small delay for streaming effect
    
    yield {
        "type": "execution_complete",
        "message": "Done!",
        "data": {
            "tool_count": len(tool_results),
            "agent_count": len(agent_results)
        }
    }

