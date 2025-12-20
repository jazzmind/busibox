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
        error: Optional[str] = None
    ):
        self.agent_id = agent_id
        self.run_id = run_id
        self.success = success
        self.output = output
        self.metadata = metadata or {}
        self.error = error
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_id": self.agent_id,
            "run_id": str(self.run_id),
            "success": self.success,
            "output": self.output,
            "metadata": self.metadata,
            "error": self.error
        }


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
    from app.schemas.auth import Principal
    
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
        
        # Use run_service for actual execution
        run_record = await create_run(
            session=session,
            principal=principal,
            agent_id=agent_uuid,
            payload={"prompt": query, "context": context or {}},
            scopes=["search:read", "ingest:write", "rag:read"],
            purpose="chat-agent-execution",
            agent_tier="simple"  # Chat agents use simple tier (30s timeout)
        )
        
        # Convert RunRecord to AgentExecutionResult
        success = run_record.status == "succeeded"
        
        # Extract output string
        if success:
            output_obj = run_record.output or {}
            if isinstance(output_obj, dict):
                # Try to get a clean output string
                if "result" in output_obj:
                    output = str(output_obj["result"])
                elif "data" in output_obj:
                    output = str(output_obj["data"])
                else:
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
            error=error
        )
        
    except Exception as e:
        logger.error(
            f"Agent execution failed for user {user_id}: {e}",
            extra={"user_id": user_id, "agent_id": agent_id, "error": str(e)},
            exc_info=True
        )
        
        # Return error result with a generated run_id
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
    # Build context from results
    context_parts = []
    
    # Add tool results
    for result in tool_results:
        if result.success:
            context_parts.append(f"**{result.tool_name.replace('_', ' ').title()} Results:**\n{result.output}")
        else:
            context_parts.append(f"**{result.tool_name.replace('_', ' ').title()}:** {result.error}")
    
    # Add agent results
    for result in agent_results:
        if result.success:
            context_parts.append(f"**Agent {result.agent_id} Output:**\n{result.output}")
        else:
            context_parts.append(f"**Agent {result.agent_id}:** {result.error}")
    
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
    Execute chat with streaming updates.
    
    Yields:
        Dict with event type and data
    """
    logger.info(
        f"Executing streaming chat for user {user_id}",
        extra={"user_id": user_id, "model": model}
    )
    
    # Execute tools
    if routing_decision.selected_tools:
        yield {
            "type": "tools_start",
            "data": {"tools": routing_decision.selected_tools}
        }
        
        tool_results = await execute_tools(routing_decision.selected_tools, query, user_id)
        
        for result in tool_results:
            yield {
                "type": "tool_result",
                "data": result.to_dict()
            }
    else:
        tool_results = []
    
    # Execute agents
    if routing_decision.selected_agents:
        yield {
            "type": "agents_start",
            "data": {"agents": routing_decision.selected_agents}
        }
        
        agent_results = await execute_agents(
            routing_decision.selected_agents,
            query,
            user_id,
            session,
            principal
        )
        
        for result in agent_results:
            yield {
                "type": "agent_result",
                "data": result.to_dict()
            }
    else:
        agent_results = []
    
    # Synthesize response
    yield {"type": "synthesis_start", "data": {}}
    
    content = await synthesize_response(
        query,
        tool_results,
        agent_results,
        model,
        conversation_history
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
        "data": {
            "tool_count": len(tool_results),
            "agent_count": len(agent_results)
        }
    }

