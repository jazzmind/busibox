"""
Dispatcher service for intelligent query routing.

Provides:
- Query routing via dispatcher agent
- Decision logging for accuracy measurement
- Redis caching for repeated queries
- Performance monitoring
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dispatcher import dispatcher_agent
from app.core.logging import get_logger
from app.models.dispatcher_log import DispatcherDecisionLog
from app.schemas.dispatcher import DispatcherRequest, DispatcherResponse, RoutingDecision

logger = get_logger(__name__)


def _generate_cache_key(request: DispatcherRequest) -> str:
    """
    Generate cache key for dispatcher request.
    
    Cache key is based on:
    - Query text
    - Enabled tools (from user settings)
    - Enabled agents (from user settings)
    
    Args:
        request: Dispatcher request
        
    Returns:
        Cache key string (hex hash)
    """
    enabled_tools = request.user_settings.enabled_tools if request.user_settings else []
    enabled_agents = request.user_settings.enabled_agents if request.user_settings else []
    
    cache_data = {
        "query": request.query,
        "enabled_tools": sorted(enabled_tools),
        "enabled_agents": sorted(enabled_agents),
    }
    
    cache_str = json.dumps(cache_data, sort_keys=True)
    return hashlib.sha256(cache_str.encode()).hexdigest()


async def route_query(
    request: DispatcherRequest,
    user_id: str,
    request_id: str,
    session: AsyncSession,
    redis_client: Optional[Any] = None,  # TODO: Add Redis client type hint
) -> DispatcherResponse:
    """
    Route a user query to appropriate tools and agents.
    
    Args:
        request: Dispatcher request with query and available resources
        user_id: Authenticated user ID
        request_id: Request correlation ID for tracing
        session: Database session for decision logging
        redis_client: Optional Redis client for caching
        
    Returns:
        DispatcherResponse with routing decision
    """
    start_time = datetime.now(timezone.utc)
    
    # Check cache if Redis available
    cached_decision = None
    if redis_client:
        try:
            cache_key = f"dispatcher:route:{_generate_cache_key(request)}"
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                cached_decision = RoutingDecision.model_validate_json(cached_data)
                logger.info(
                    "dispatcher_cache_hit",
                    user_id=user_id,
                    request_id=request_id,
                    cache_key=cache_key
                )
        except Exception as e:
            logger.warning(
                "dispatcher_cache_error",
                user_id=user_id,
                request_id=request_id,
                error=str(e)
            )
    
    # If cached, use cached decision
    if cached_decision:
        routing_decision = cached_decision
    else:
        # Get enabled tools/agents from user settings
        # If user has settings but lists are empty, use all available (opt-out, not opt-in)
        if request.user_settings:
            enabled_tools = request.user_settings.enabled_tools if request.user_settings.enabled_tools else request.available_tools
            enabled_agents = request.user_settings.enabled_agents if request.user_settings.enabled_agents else request.available_agents
        else:
            # No settings yet - enable everything by default
            enabled_tools = request.available_tools
            enabled_agents = request.available_agents
        
        # Check if any tools/agents are available
        if not enabled_tools and not enabled_agents:
            # No tools/agents available - return confidence=0 with explanation
            routing_decision = RoutingDecision(
                selected_tools=[],
                selected_agents=[],
                confidence=0.0,
                reasoning="No tools or agents are enabled. Please check your settings or enable tools/agents to use.",
                alternatives=request.available_tools + request.available_agents,
                requires_disambiguation=True
            )
            
            logger.info(
                "dispatcher_no_resources",
                user_id=user_id,
                request_id=request_id,
                query=request.query[:100]
            )
        else:
            # Call dispatcher agent with LiteLLM
            try:
                # Format prompt with context
                prompt = f"""Query: {request.query}

Available tools: {', '.join(request.available_tools) if request.available_tools else 'none'}
Available agents: {', '.join(request.available_agents) if request.available_agents else 'none'}
Enabled tools: {', '.join(enabled_tools) if enabled_tools else 'none'}
Enabled agents: {', '.join(enabled_agents) if enabled_agents else 'none'}
Attachments: {len(request.attachments)} file(s) attached

Analyze this query and select the appropriate tools and/or agents."""

                result = await dispatcher_agent.run(prompt)
                
                # PydanticAI returns structured data in .data attribute for typed agents
                # For Agent[None, T], check .data first, then .output as fallback
                if hasattr(result, 'data') and isinstance(result.data, RoutingDecision):
                    routing_decision = result.data
                elif hasattr(result, 'output'):
                    # Try parsing output if it's a string
                    if isinstance(result.output, str):
                        import json
                        routing_decision = RoutingDecision.model_validate_json(result.output)
                    else:
                        routing_decision = result.output
                else:
                    raise ValueError(f"Unexpected result type from dispatcher: {type(result)}")
                
                logger.info(
                    "dispatcher_routing_success",
                    user_id=user_id,
                    request_id=request_id,
                    confidence=routing_decision.confidence,
                    selected_tools_count=len(routing_decision.selected_tools),
                    selected_agents_count=len(routing_decision.selected_agents)
                )
                
            except Exception as e:
                logger.error(
                    "dispatcher_routing_error",
                    user_id=user_id,
                    request_id=request_id,
                    error=str(e),
                    query=request.query[:100]
                )
                
                # Fallback: return low confidence with error explanation
                routing_decision = RoutingDecision(
                    selected_tools=[],
                    selected_agents=[],
                    confidence=0.0,
                    reasoning=f"Routing failed due to error: {str(e)}. Please try again or contact support.",
                    alternatives=enabled_tools + enabled_agents,
                    requires_disambiguation=True
                )
        
        # Cache the decision if Redis available
        if redis_client and routing_decision.confidence >= 0.7:
            try:
                cache_key = f"dispatcher:route:{_generate_cache_key(request)}"
                await redis_client.setex(
                    cache_key,
                    3600,  # 1 hour TTL
                    routing_decision.model_dump_json()
                )
            except Exception as e:
                logger.warning(
                    "dispatcher_cache_set_error",
                    user_id=user_id,
                    request_id=request_id,
                    error=str(e)
                )
    
    # Log decision to database for accuracy measurement
    try:
        decision_log = DispatcherDecisionLog(
            query_text=request.query[:1000],  # Truncate to 1000 chars
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
        logger.error(
            "dispatcher_log_error",
            user_id=user_id,
            request_id=request_id,
            error=str(e)
        )
        # Don't fail the request if logging fails
    
    # Calculate and log response time
    end_time = datetime.now(timezone.utc)
    response_time = (end_time - start_time).total_seconds()
    
    logger.info(
        "dispatcher_routing_complete",
        user_id=user_id,
        request_id=request_id,
        confidence=routing_decision.confidence,
        response_time_seconds=response_time,
        cached=cached_decision is not None,
        selected_tools_count=len(routing_decision.selected_tools),
        selected_agents_count=len(routing_decision.selected_agents),
        requires_disambiguation=routing_decision.requires_disambiguation
    )
    
    # Performance monitoring: Alert if response time exceeds target
    if response_time > 2.0:
        logger.warning(
            "dispatcher_slow_response",
            user_id=user_id,
            request_id=request_id,
            response_time_seconds=response_time,
            target_seconds=2.0,
            query_length=len(payload.query)
        )
    
    return DispatcherResponse(routing_decision=routing_decision)








