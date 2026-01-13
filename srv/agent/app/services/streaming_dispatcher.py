"""
Streaming dispatcher service for real-time interactive query routing and execution.

Provides progressive updates during:
- Query analysis and planning
- Tool and agent routing decisions
- Tool/agent execution with status updates
- Result synthesis
- Follow-up suggestions
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dispatcher import dispatcher_agent
from app.agents.web_search_agent import web_search_agent
from app.agents.document_agent import document_agent
from app.agents.weather_agent import weather_agent
from app.agents.chat_agent import chat_agent
from app.core.logging import get_logger
from app.models.dispatcher_log import DispatcherDecisionLog
from app.schemas.auth import Principal
from app.schemas.dispatcher import (
    DispatcherStreamEvent,
    RoutingDecision,
    StreamingDispatcherRequest,
)

logger = get_logger(__name__)


# Friendly names for tools and agents
TOOL_DISPLAY_NAMES = {
    "web_search": "Web Search",
    "document_search": "Document Search",
    "get_weather": "Weather",
    "ingest_document": "Document Ingestion",
    "web_scraper": "Web Page Reader",
}

AGENT_DISPLAY_NAMES = {
    "web_search_agent": "Web Research Agent",
    "document_agent": "Document Analysis Agent",
    "weather_agent": "Weather Agent",
    "chat_agent": "Chat Agent",
}


def _event(
    event_type: str,
    message: str,
    data: Optional[Dict[str, Any]] = None
) -> DispatcherStreamEvent:
    """Helper to create a stream event."""
    return DispatcherStreamEvent(
        type=event_type,
        message=message,
        data=data,
        timestamp=datetime.now(timezone.utc),
    )


def _get_tool_display_name(tool_name: str) -> str:
    """Get friendly display name for a tool."""
    return TOOL_DISPLAY_NAMES.get(tool_name, tool_name.replace("_", " ").title())


def _get_agent_display_name(agent_id: str) -> str:
    """Get friendly display name for an agent."""
    return AGENT_DISPLAY_NAMES.get(agent_id, agent_id.replace("_", " ").title())


async def route_and_execute_stream(
    request: StreamingDispatcherRequest,
    user_id: str,
    request_id: str,
    session: AsyncSession,
    principal: Optional[Principal] = None,
) -> AsyncGenerator[DispatcherStreamEvent, None]:
    """
    Route a query and execute tools/agents with streaming updates.
    
    This function provides real-time feedback about what the dispatcher is doing,
    making the AI feel more interactive and transparent.
    
    Args:
        request: Streaming dispatcher request
        user_id: Authenticated user ID
        request_id: Request correlation ID
        session: Database session
        principal: Optional principal for agent execution
        
    Yields:
        DispatcherStreamEvent objects for each step of execution
    """
    start_time = datetime.now(timezone.utc)
    
    # Phase 1: Planning
    yield _event(
        "planning",
        f"Analyzing your query: \"{request.query[:100]}{'...' if len(request.query) > 100 else ''}\"",
        {"query_length": len(request.query), "has_attachments": len(request.attachments) > 0}
    )
    
    await asyncio.sleep(0.1)  # Small delay for UX
    
    # Get enabled tools/agents
    if request.user_settings:
        enabled_tools = request.user_settings.enabled_tools if request.user_settings.enabled_tools else request.available_tools
        enabled_agents = request.user_settings.enabled_agents if request.user_settings.enabled_agents else request.available_agents
    else:
        enabled_tools = request.available_tools
        enabled_agents = request.available_agents
    
    # Check if any resources available
    if not enabled_tools and not enabled_agents:
        yield _event(
            "error",
            "No tools or agents are enabled. Please check your settings.",
            {"available_tools": request.available_tools, "available_agents": request.available_agents}
        )
        return
    
    # Phase 2: Routing Decision
    yield _event(
        "routing",
        "Determining the best approach to answer your question...",
        {"available_tools": enabled_tools, "available_agents": enabled_agents}
    )
    
    try:
        # Build prompt for dispatcher
        prompt = f"""Query: {request.query}

Available tools: {', '.join(enabled_tools) if enabled_tools else 'none'}
Available agents: {', '.join(enabled_agents) if enabled_agents else 'none'}
Attachments: {len(request.attachments)} file(s) attached

Analyze this query and select the appropriate tools and/or agents."""

        result = await dispatcher_agent.run(prompt)
        routing_decision: RoutingDecision = result.output
        
        # Announce the routing decision
        selected_items = []
        if routing_decision.selected_tools:
            tool_names = [_get_tool_display_name(t) for t in routing_decision.selected_tools]
            selected_items.append(f"tools: {', '.join(tool_names)}")
        if routing_decision.selected_agents:
            agent_names = [_get_agent_display_name(a) for a in routing_decision.selected_agents]
            selected_items.append(f"agents: {', '.join(agent_names)}")
        
        decision_message = f"I'll use {' and '.join(selected_items)} to help with this."
        if routing_decision.reasoning:
            decision_message += f" {routing_decision.reasoning}"
        
        yield _event(
            "routing",
            decision_message,
            {
                "selected_tools": routing_decision.selected_tools,
                "selected_agents": routing_decision.selected_agents,
                "confidence": routing_decision.confidence,
                "reasoning": routing_decision.reasoning,
            }
        )
        
    except Exception as e:
        logger.error(
            "streaming_dispatcher_routing_error",
            user_id=user_id,
            request_id=request_id,
            error=str(e)
        )
        yield _event(
            "error",
            f"Failed to determine routing: {str(e)}",
            {"error_type": type(e).__name__}
        )
        return
    
    # Log decision to database
    try:
        decision_log = DispatcherDecisionLog(
            query_text=request.query[:1000],
            selected_tools=routing_decision.selected_tools,
            selected_agents=routing_decision.selected_agents,
            confidence=routing_decision.confidence,
            reasoning=routing_decision.reasoning,
            alternatives=routing_decision.alternatives,
            user_id=user_id,
            request_id=request_id,
            timestamp=datetime.now(timezone.utc)
        )
        session.add(decision_log)
        await session.commit()
    except Exception as e:
        logger.warning(
            "streaming_dispatcher_log_error",
            user_id=user_id,
            request_id=request_id,
            error=str(e)
        )
    
    # Phase 3: Execute Tools
    tool_results = []
    if routing_decision.selected_tools:
        for tool_name in routing_decision.selected_tools:
            display_name = _get_tool_display_name(tool_name)
            
            yield _event(
                "tool_start",
                f"Running {display_name}...",
                {"tool": tool_name, "display_name": display_name}
            )
            
            try:
                tool_result = await _execute_tool(tool_name, request.query, user_id)
                tool_results.append(tool_result)
                
                # Summarize the result
                if tool_result.get("success"):
                    summary = _summarize_tool_result(tool_name, tool_result)
                    yield _event(
                        "tool_result",
                        f"{display_name} completed: {summary}",
                        {"tool": tool_name, "success": True, "result": tool_result}
                    )
                else:
                    yield _event(
                        "tool_result",
                        f"{display_name} encountered an issue: {tool_result.get('error', 'Unknown error')}",
                        {"tool": tool_name, "success": False, "error": tool_result.get("error")}
                    )
                    
            except Exception as e:
                logger.error(
                    "streaming_dispatcher_tool_error",
                    user_id=user_id,
                    tool=tool_name,
                    error=str(e)
                )
                yield _event(
                    "tool_result",
                    f"{display_name} failed: {str(e)}",
                    {"tool": tool_name, "success": False, "error": str(e)}
                )
    
    # Phase 4: Execute Agents
    agent_results = []
    if routing_decision.selected_agents:
        for agent_id in routing_decision.selected_agents:
            display_name = _get_agent_display_name(agent_id)
            
            yield _event(
                "agent_start",
                f"Consulting {display_name}...",
                {"agent": agent_id, "display_name": display_name}
            )
            
            try:
                agent_result = await _execute_agent(
                    agent_id,
                    request.query,
                    user_id,
                    session,
                    tool_results,  # Pass tool results as context
                )
                agent_results.append(agent_result)
                
                if agent_result.get("success"):
                    # Truncate output for status message
                    output_preview = agent_result.get("output", "")[:200]
                    if len(agent_result.get("output", "")) > 200:
                        output_preview += "..."
                    
                    yield _event(
                        "agent_result",
                        f"{display_name} responded.",
                        {"agent": agent_id, "success": True, "preview": output_preview}
                    )
                else:
                    yield _event(
                        "agent_result",
                        f"{display_name} encountered an issue: {agent_result.get('error', 'Unknown error')}",
                        {"agent": agent_id, "success": False, "error": agent_result.get("error")}
                    )
                    
            except Exception as e:
                logger.error(
                    "streaming_dispatcher_agent_error",
                    user_id=user_id,
                    agent=agent_id,
                    error=str(e)
                )
                yield _event(
                    "agent_result",
                    f"{display_name} failed: {str(e)}",
                    {"agent": agent_id, "success": False, "error": str(e)}
                )
    
    # Phase 5: Synthesis
    yield _event(
        "synthesis",
        "Combining results to create your answer...",
        {"tool_count": len(tool_results), "agent_count": len(agent_results)}
    )
    
    try:
        final_response = await _synthesize_response(
            request.query,
            tool_results,
            agent_results,
            request.model or "gpt-4o-mini",
            request.conversation_history,
        )
        
        # Stream the final content in chunks for better UX
        words = final_response.split()
        content_buffer = []
        
        for i, word in enumerate(words):
            content_buffer.append(word)
            
            # Yield content in chunks of ~10 words
            if len(content_buffer) >= 10 or i == len(words) - 1:
                chunk = " ".join(content_buffer)
                yield _event(
                    "content",
                    chunk,
                    {"chunk_index": i // 10, "is_final": i == len(words) - 1}
                )
                content_buffer = []
                await asyncio.sleep(0.02)  # Small delay for streaming effect
        
    except Exception as e:
        logger.error(
            "streaming_dispatcher_synthesis_error",
            user_id=user_id,
            error=str(e)
        )
        yield _event(
            "error",
            f"Failed to synthesize response: {str(e)}",
            {"error_type": type(e).__name__}
        )
        return
    
    # Phase 6: Suggestions (non-blocking)
    suggestions = _generate_suggestions(request.query, routing_decision, tool_results, agent_results)
    if suggestions:
        yield _event(
            "suggestion",
            "You might also want to ask about: " + ", ".join(suggestions),
            {"suggestions": suggestions}
        )
    
    # Phase 7: Complete
    end_time = datetime.now(timezone.utc)
    duration = (end_time - start_time).total_seconds()
    
    yield _event(
        "complete",
        "Done!",
        {
            "duration_seconds": duration,
            "tool_count": len(tool_results),
            "agent_count": len(agent_results),
            "routing_confidence": routing_decision.confidence,
        }
    )
    
    logger.info(
        "streaming_dispatcher_complete",
        user_id=user_id,
        request_id=request_id,
        duration_seconds=duration,
        tool_count=len(tool_results),
        agent_count=len(agent_results),
    )


async def _execute_tool(tool_name: str, query: str, user_id: str) -> Dict[str, Any]:
    """Execute a tool and return the result."""
    from app.services.builtin_tools import get_tool_executor
    
    executor = get_tool_executor(tool_name)
    if not executor:
        return {"success": False, "error": f"Unknown tool: {tool_name}"}
    
    try:
        # Different tools have different signatures
        if tool_name == "web_search":
            result = await executor(query)
        elif tool_name == "document_search":
            result = await executor(query)
        elif tool_name == "get_weather":
            # Extract location from query (simple heuristic)
            location = query.replace("weather", "").replace("in", "").strip()
            if not location:
                location = "New York"
            result = await executor(location)
        elif tool_name == "web_scraper":
            # Extract URL from query if present
            import re
            url_match = re.search(r'https?://[^\s]+', query)
            if url_match:
                result = await executor(url_match.group(0))
            else:
                return {"success": False, "error": "No URL found in query for web scraper"}
        else:
            # Generic execution
            result = await executor(query)
        
        # Convert Pydantic models to dict
        if hasattr(result, 'model_dump'):
            return {"success": True, **result.model_dump()}
        elif hasattr(result, 'dict'):
            return {"success": True, **result.dict()}
        else:
            return {"success": True, "output": str(result)}
            
    except Exception as e:
        logger.error(f"Tool execution error for {tool_name}: {e}")
        return {"success": False, "error": str(e)}


async def _execute_agent(
    agent_id: str,
    query: str,
    user_id: str,
    session: AsyncSession,
    tool_context: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Execute an agent and return the result."""
    # Map agent IDs to agent instances
    agents = {
        "web_search_agent": web_search_agent,
        "document_agent": document_agent,
        "weather_agent": weather_agent,
        "chat_agent": chat_agent,
    }
    
    agent = agents.get(agent_id)
    if not agent:
        return {"success": False, "error": f"Unknown agent: {agent_id}"}
    
    try:
        # Build context from tool results
        context_parts = []
        for tool_result in tool_context:
            if tool_result.get("success"):
                tool_name = tool_result.get("tool_name", "tool")
                output = tool_result.get("output", str(tool_result))
                context_parts.append(f"[{tool_name} results]: {output}")
        
        # Augment query with context if available
        augmented_query = query
        if context_parts:
            augmented_query = f"{query}\n\nContext from previous tools:\n" + "\n".join(context_parts)
        
        result = await agent.run(augmented_query)
        
        # Extract output
        output = result.data if hasattr(result, 'data') else str(result)
        
        return {
            "success": True,
            "agent_id": agent_id,
            "output": output,
            "run_id": str(uuid.uuid4()),  # Generate a run ID
        }
        
    except Exception as e:
        logger.error(f"Agent execution error for {agent_id}: {e}")
        return {"success": False, "error": str(e)}


async def _synthesize_response(
    query: str,
    tool_results: List[Dict[str, Any]],
    agent_results: List[Dict[str, Any]],
    model: str,
    conversation_history: Optional[List[Dict[str, str]]],
) -> str:
    """Synthesize a final response from tool and agent results."""
    # Build context from all results
    context_parts = []
    
    for result in tool_results:
        if result.get("success"):
            tool_name = result.get("tool_name", "tool")
            # Extract meaningful output
            if "results" in result:
                output = str(result["results"])[:1000]
            elif "output" in result:
                output = str(result["output"])[:1000]
            elif "content" in result:
                output = str(result["content"])[:1000]
            else:
                output = str(result)[:500]
            context_parts.append(f"[{tool_name}]: {output}")
    
    for result in agent_results:
        if result.get("success"):
            agent_id = result.get("agent_id", "agent")
            output = result.get("output", "")[:1000]
            context_parts.append(f"[{agent_id}]: {output}")
    
    # If we have agent results with good output, use that directly
    for result in agent_results:
        if result.get("success") and result.get("output"):
            return result["output"]
    
    # Otherwise, use chat agent to synthesize
    synthesis_prompt = f"""Based on the following information, provide a clear and helpful response to the user's query.

User Query: {query}

Available Information:
{chr(10).join(context_parts) if context_parts else "No additional information available."}

Provide a concise, well-organized response that directly addresses the user's question."""

    try:
        result = await chat_agent.run(synthesis_prompt)
        return result.data if hasattr(result, 'data') else str(result)
    except Exception as e:
        logger.error(f"Synthesis error: {e}")
        # Fallback: concatenate available outputs
        if context_parts:
            return "Here's what I found:\n\n" + "\n\n".join(context_parts)
        return "I apologize, but I encountered an error while processing your request."


def _summarize_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
    """Generate a brief summary of a tool result."""
    if tool_name == "web_search":
        count = result.get("result_count", 0)
        if count > 0:
            return f"Found {count} relevant web results"
        return "No web results found"
    
    elif tool_name == "document_search":
        count = result.get("result_count", 0)
        if count > 0:
            return f"Found {count} relevant documents"
        return "No matching documents found"
    
    elif tool_name == "get_weather":
        if result.get("temperature") is not None:
            return f"{result.get('conditions', 'Weather')} at {result.get('temperature')}°C in {result.get('location', 'the area')}"
        return "Weather data retrieved"
    
    elif tool_name == "web_scraper":
        if result.get("success"):
            word_count = result.get("word_count", 0)
            title = result.get("title", "")[:50]
            return f"Read page: {title} ({word_count} words)"
        return "Page content extracted"
    
    else:
        return "Completed successfully"


def _generate_suggestions(
    query: str,
    routing_decision: RoutingDecision,
    tool_results: List[Dict[str, Any]],
    agent_results: List[Dict[str, Any]],
) -> List[str]:
    """Generate follow-up suggestions based on the query and results."""
    suggestions = []
    
    # Add alternatives from routing decision
    if routing_decision.alternatives:
        for alt in routing_decision.alternatives[:2]:
            display_name = _get_tool_display_name(alt) if alt in TOOL_DISPLAY_NAMES else _get_agent_display_name(alt)
            suggestions.append(f"try {display_name}")
    
    # Suggest related queries based on tools used
    if "web_search" in routing_decision.selected_tools:
        suggestions.append("dive deeper into specific results")
    
    if "document_search" in routing_decision.selected_tools:
        suggestions.append("search with different keywords")
    
    # Limit suggestions
    return suggestions[:3] if suggestions else []
