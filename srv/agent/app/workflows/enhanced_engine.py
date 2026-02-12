"""
Enhanced workflow execution engine with full support for:
- Condition steps with branching
- Human-in-loop approval steps
- Parallel step execution
- Loop/iteration steps
- Usage tracking and guardrails
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import (
    WorkflowDefinition,
    WorkflowExecution,
    StepExecution,
    AgentDefinition,
)
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token
from app.workflows.engine import (
    WorkflowExecutionError,
    GuardrailsExceededError,
    UsageLimits,
    _resolve_value,
    _resolve_args,
    _evaluate_condition,
    _estimate_cost,
)

logger = logging.getLogger(__name__)


def _extract_urls_from_result(agent_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract URLs from an agent result.
    
    Handles various result formats from web search agents:
    - Direct URL list
    - Results with 'results' array containing objects with 'url' field
    - Text content with URLs parsed out
    
    Args:
        agent_result: The agent result dict
        
    Returns:
        Dict with 'urls' list and 'url_count' int
    """
    import re
    
    urls = []
    
    # Check for direct urls field
    if isinstance(agent_result, dict):
        if "urls" in agent_result:
            urls = agent_result["urls"]
        elif "results" in agent_result:
            # Extract URLs from results array
            for item in agent_result["results"]:
                if isinstance(item, dict) and "url" in item:
                    urls.append(item["url"])
                elif isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
        elif "result" in agent_result or "content" in agent_result:
            # Try to extract URLs from text result or content
            text = str(agent_result.get("content", agent_result.get("result", "")))
            
            # First try markdown link format: [text](url)
            markdown_url_pattern = r'\]\((https?://[^\s\)]+)\)'
            urls = re.findall(markdown_url_pattern, text)
            
            # Also extract bare URLs
            bare_url_pattern = r'(?<!\()https?://[^\s<>"\')\]]+[^\s<>"\')\].,;:!?]'
            urls.extend(re.findall(bare_url_pattern, text))
            
            logger.debug(f"Extracted {len(urls)} URLs from text content (len={len(text)})")
    elif isinstance(agent_result, list):
        for item in agent_result:
            if isinstance(item, str) and item.startswith("http"):
                urls.append(item)
            elif isinstance(item, dict) and "url" in item:
                urls.append(item["url"])
    
    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    
    return {
        "urls": unique_urls,
        "url_count": len(unique_urls),
    }


async def _check_urls_in_library(search_client: BusiboxClient, urls: List[str]) -> Dict[str, Any]:
    """
    Check which URLs already exist in the user's document library.
    
    Searches for each URL using keyword search mode. Documents are expected to have
    the URL in their title or content (set during ingest).
    
    Args:
        search_client: Authenticated BusiboxClient for search API
        urls: List of URLs to check
        
    Returns:
        Dict with existing_urls, new_urls, and counts
    """
    existing_urls = []
    new_urls = []
    
    for url in urls:
        try:
            # Extract domain and path for a more targeted search
            # Use keyword mode to search for the URL in document metadata/title
            search_query = url.replace("https://", "").replace("http://", "").replace("/", " ")[:100]
            
            result = await search_client.search(
                query=search_query,
                mode="keyword",
                top_k=1,
                rerank=False,
            )
            
            # Check if we found a match with the same URL
            if result.get("results") and len(result["results"]) > 0:
                # Check if any result has this URL in its metadata or title
                for doc in result["results"]:
                    doc_url = doc.get("metadata", {}).get("url", "")
                    doc_title = doc.get("title", "")
                    if url in doc_url or url in doc_title:
                        existing_urls.append(url)
                        break
                else:
                    new_urls.append(url)
            else:
                new_urls.append(url)
                
        except Exception as e:
            # If search fails for this URL, assume it's new
            logger.warning(f"Failed to check URL {url}: {e}")
            new_urls.append(url)
    
    logger.info(f"URL check complete: {len(existing_urls)} existing, {len(new_urls)} new out of {len(urls)} total")
    
    return {
        "existing_urls": existing_urls,
        "new_urls": new_urls,
        "total_checked": len(urls),
        "existing_count": len(existing_urls),
        "new_count": len(new_urls),
    }


def _extract_urls_from_search_results(search_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract URLs from web_search tool output (structured WebSearchOutput format).
    
    Args:
        search_results: The search results dict from web_search tool
        
    Returns:
        Dict with 'urls' list, 'url_count' int, and metadata
    """
    urls = []
    
    # Handle both dict and Pydantic model formats
    if isinstance(search_results, dict):
        results = search_results.get("results", [])
        query = search_results.get("query", "")
        optimized_queries = search_results.get("optimized_queries", [])
        providers = search_results.get("providers_used", [])
        results_per_provider = search_results.get("results_per_provider", {})
        result_count = search_results.get("result_count", 0)
    elif hasattr(search_results, "results"):
        results = search_results.results
        query = getattr(search_results, "query", "")
        optimized_queries = getattr(search_results, "optimized_queries", [])
        providers = getattr(search_results, "providers_used", [])
        results_per_provider = getattr(search_results, "results_per_provider", {})
        result_count = getattr(search_results, "result_count", 0)
    else:
        results = []
        query = ""
        optimized_queries = []
        providers = []
        results_per_provider = {}
        result_count = 0
    
    # Extract URLs from results
    for result in results:
        if isinstance(result, dict):
            url = result.get("url")
        elif hasattr(result, "url"):
            url = result.url
        else:
            continue
        
        if url:
            urls.append(url)
    
    # Deduplicate while preserving order
    seen = set()
    unique_urls = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            unique_urls.append(url)
    
    logger.debug(f"Extracted {len(unique_urls)} URLs from search results")
    
    return {
        "urls": unique_urls,
        "url_count": len(unique_urls),
        "original_query": query,
        "optimized_queries": optimized_queries,
        "providers_used": providers,
        "results_per_provider": results_per_provider,
        "result_count": result_count or len(results),
    }


async def execute_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> Any:
    """
    Execute a single workflow step.
    
    Args:
        session: Database session
        execution: WorkflowExecution record
        step: Step definition
        context: Execution context
        usage_limits: Usage tracking and limits
        busibox_client: Busibox API client
        principal: User principal
        
    Returns:
        Step output
        
    Raises:
        WorkflowExecutionError: If step execution fails
    """
    step_id = step.get("id", "unknown")
    step_type = step.get("type")
    step_description = step.get("description", "")
    
    # Resolve input data for this step (for logging/display)
    step_input_data = {}
    if step_type == "tool":
        tool_name = step.get("tool")
        tool_args = step.get("tool_args", {})
        # Resolve args for display
        resolved_args = _resolve_args(tool_args, context)
        step_input_data = {
            "tool": tool_name,
            "args": resolved_args,
        }
    elif step_type == "agent":
        agent_name = step.get("agent")
        prompt_template = step.get("agent_prompt", "")
        resolved_prompt = _resolve_value(prompt_template, context)
        step_input_data = {
            "agent": agent_name,
            "prompt": resolved_prompt,
        }
    elif step_type == "condition":
        condition = step.get("condition", {})
        step_input_data = {
            "condition": condition,
        }
    
    # Create step execution record
    step_exec = StepExecution(
        execution_id=execution.id,
        step_id=step_id,
        status="running",
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        input_data=step_input_data,  # Save resolved input
    )
    session.add(step_exec)
    await session.commit()
    
    try:
        # Check guardrails before starting
        usage_limits.check_before_step(step_type)
        
        result = None
        
        if step_type == "tool":
            result = await _execute_tool_step(step, context, busibox_client, usage_limits, session, principal)
        
        elif step_type == "agent":
            result = await _execute_agent_step(
                session, step, context, busibox_client, principal, usage_limits
            )
        
        elif step_type == "condition":
            result = await _execute_condition_step(step, context)
        
        elif step_type == "human":
            result = await _execute_human_step(session, execution, step, context)
        
        elif step_type == "parallel":
            result = await _execute_parallel_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
        
        elif step_type == "loop":
            result = await _execute_loop_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
        
        else:
            raise WorkflowExecutionError(f"Unknown step type: {step_type}")
        
        # Update step execution
        step_exec.status = "completed"
        step_exec.output_data = result if isinstance(result, dict) else {"result": result}
        step_exec.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        step_exec.duration_seconds = (
            step_exec.completed_at - step_exec.started_at
        ).total_seconds()
        
        # Copy usage from limits
        usage = usage_limits.get_usage_dict()
        step_exec.usage_requests = usage["requests"]
        step_exec.usage_input_tokens = usage["input_tokens"]
        step_exec.usage_output_tokens = usage["output_tokens"]
        step_exec.usage_tool_calls = usage["tool_calls"]
        step_exec.estimated_cost_dollars = usage["estimated_cost_dollars"]
        
        await session.commit()
        
        logger.info(f"Step {step_id} completed successfully")
        return result
        
    except Exception as e:
        # Update step execution with error
        step_exec.status = "failed"
        step_exec.error = str(e)
        step_exec.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        step_exec.duration_seconds = (
            step_exec.completed_at - step_exec.started_at
        ).total_seconds()
        await session.commit()
        
        logger.error(f"Step {step_id} failed: {str(e)}", exc_info=True)
        raise


async def _execute_tool_step(
    step: Dict[str, Any],
    context: Dict[str, Any],
    busibox_client: BusiboxClient,
    usage_limits: UsageLimits,
    session: Optional[AsyncSession] = None,
    principal: Optional[Principal] = None,
) -> Any:
    """
    Execute a tool call step.
    
    Supports both:
    - Busibox client tools: search, data, rag (require authenticated client with proper audience)
    - Direct tools from ToolRegistry: web_search, web_scraper (no auth needed)
    """
    tool_name = step.get("tool")
    args = step.get("tool_args", {})
    resolved_args = _resolve_args(args, context)
    
    logger.info(f"Executing tool {tool_name} with args: {resolved_args}")
    
    # Call tool via Busibox client (authenticated tools)
    # These need tokens with specific audiences, so we exchange on-demand
    if tool_name == "search" or tool_name == "rag":
        # Need search-api audience
        if session and principal:
            token_response = await get_or_exchange_token(
                session=session,
                principal=principal,
                scopes=["search.read"],
                purpose="search",
            )
            search_client = BusiboxClient(access_token=token_response.access_token)
            if tool_name == "search":
                result = await search_client.search(**resolved_args)
            else:
                result = await search_client.rag_query(**resolved_args)
        else:
            # Fallback to provided client (may fail if wrong audience)
            if tool_name == "search":
                result = await busibox_client.search(**resolved_args)
            else:
                result = await busibox_client.rag_query(**resolved_args)
    elif tool_name == "data":
        # Need data-api audience
        if session and principal:
            token_response = await get_or_exchange_token(
                session=session,
                principal=principal,
                scopes=["data.write"],
                purpose="data",
            )
            data_client = BusiboxClient(access_token=token_response.access_token)
        else:
            data_client = busibox_client
        
        # Check if this is content-based ingestion (web research) or file-based
        if "content" in resolved_args:
            # Content-based ingestion (for scraped web content)
            result = await data_client.data_content(
                content=resolved_args.get("content", ""),
                title=resolved_args.get("title", "Untitled"),
                url=resolved_args.get("url"),
                folder=resolved_args.get("folder"),
                library_id=resolved_args.get("library_id"),
                metadata=resolved_args.get("metadata"),
            )
        else:
            # Legacy file-based ingestion
            result = await data_client.data_document(**resolved_args)
    
    # Direct tools from ToolRegistry (no auth needed)
    elif tool_name == "web_search":
        from app.tools.web_search_tool import search_web
        result = await search_web(**resolved_args)
    elif tool_name == "web_scraper":
        from app.tools.web_scraper_tool import scrape_webpage
        result = await scrape_webpage(**resolved_args)
    elif tool_name == "get_weather":
        from app.tools.weather_tool import get_weather
        result = await get_weather(**resolved_args)
    
    # Workflow utility tools
    elif tool_name == "extract_urls_from_agent_result":
        # Extract URLs from an agent result (typically from web search agent)
        result = _extract_urls_from_result(resolved_args.get("agent_result", {}))
    
    elif tool_name == "extract_urls_from_search_results":
        # Extract URLs from web_search tool results (structured format)
        result = _extract_urls_from_search_results(resolved_args.get("search_results", {}))
    
    elif tool_name == "check_urls_in_library":
        # Check which URLs already exist in user's document library
        # Search for each URL as a keyword query
        urls = resolved_args.get("urls", [])
        if session and principal:
            token_response = await get_or_exchange_token(
                session=session,
                principal=principal,
                scopes=["search.read"],
                purpose="search",
            )
            search_client = BusiboxClient(access_token=token_response.access_token)
            result = await _check_urls_in_library(search_client, urls)
        else:
            result = {"existing_urls": [], "new_urls": urls, "total_checked": len(urls)}
    
    elif tool_name == "filter_new_urls":
        # Filter out URLs that already exist in the library
        all_urls = resolved_args.get("urls", [])
        existing_urls = resolved_args.get("existing_urls", [])
        new_urls = [url for url in all_urls if url not in existing_urls]
        result = {
            "new_urls": new_urls,
            "filtered_count": len(all_urls) - len(new_urls),
            "remaining_count": len(new_urls),
        }
    
    else:
        raise WorkflowExecutionError(f"Unknown tool: {tool_name}")
    
    # Update usage
    usage_limits.update(tool_calls=1)
    
    # Convert Pydantic models to dict for context storage
    if hasattr(result, 'model_dump'):
        return result.model_dump()
    return result


async def _execute_agent_step(
    session: AsyncSession,
    step: Dict[str, Any],
    context: Dict[str, Any],
    busibox_client: BusiboxClient,
    principal: Principal,
    usage_limits: UsageLimits,
) -> Any:
    """Execute an agent step."""
    from app.services.builtin_agents import get_builtin_agent_by_name, get_builtin_agent_by_id
    import uuid as uuid_module
    
    agent_id = step.get("agent_id")
    agent_name = step.get("agent")  # Backward compatibility
    agent_prompt = step.get("agent_prompt", "")
    resolved_prompt = _resolve_value(agent_prompt, context)
    
    logger.info(f"Executing agent {agent_id or agent_name}")
    
    # First check builtin agents
    builtin_agent = None
    if agent_name:
        builtin_agent = get_builtin_agent_by_name(agent_name)
    elif agent_id:
        try:
            agent_uuid = uuid_module.UUID(str(agent_id)) if not isinstance(agent_id, uuid_module.UUID) else agent_id
            builtin_agent = get_builtin_agent_by_id(agent_uuid)
        except (ValueError, TypeError):
            pass
    
    # Build context dict for agent execution
    # BaseStreamingAgent needs principal and session in context for authentication
    agent_context = {
        "principal": principal,
        "session": session,
        "user_id": principal.sub if principal else None,
    }
    
    if builtin_agent:
        # Execute builtin agent directly
        deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
        agent_result = await builtin_agent.run(str(resolved_prompt), deps=deps, context=agent_context)
    else:
        # Find agent by ID or name in database
        if agent_id:
            agent_def = await session.get(AgentDefinition, agent_id)
        else:
            stmt = select(AgentDefinition).where(
                AgentDefinition.name == agent_name,
                AgentDefinition.is_active.is_(True),
            )
            result = await session.execute(stmt)
            agent_def = result.scalars().first()
        
        if not agent_def:
            raise WorkflowExecutionError(f"Agent {agent_id or agent_name} not found")
        
        # Get agent from registry
        agent = agent_registry.get(agent_def.id)
        
        # Execute agent
        deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
        agent_result = await agent.run(str(resolved_prompt), deps=deps, context=agent_context)
    
    # Extract output from agent result
    # Handle different result types:
    # 1. PydanticAI agents return RunResult with .data that has model_dump()
    # 2. BaseStreamingAgent returns AgentResult with .data as string or .output
    if hasattr(agent_result, "data"):
        if hasattr(agent_result.data, "model_dump"):
            # PydanticAI result with Pydantic model
            output = agent_result.data.model_dump()
        elif isinstance(agent_result.data, str):
            # BaseStreamingAgent result - data is a string
            output = {"result": agent_result.data, "content": agent_result.data}
        elif isinstance(agent_result.data, dict):
            # Already a dict
            output = agent_result.data
        else:
            output = {"result": str(agent_result.data)}
    elif hasattr(agent_result, "output"):
        # Alternative output attribute
        output = {"result": agent_result.output} if isinstance(agent_result.output, str) else agent_result.output
    else:
        output = {"result": str(agent_result)}
    
    # Estimate usage (simplified - in reality would come from agent result)
    # For builtin agents, use a default model name; for db agents, use the stored model
    model_name = "agent"  # Default for builtin agents
    if not builtin_agent and agent_def:
        model_name = agent_def.model or "agent"
    
    usage_limits.update(
        requests=1,
        input_tokens=len(str(resolved_prompt)) // 4,  # Rough estimate
        output_tokens=len(str(output)) // 4,
        estimated_cost=_estimate_cost(model_name, 1000, 500),
    )
    
    return output


async def _execute_condition_step(
    step: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """Execute a condition step and determine next step."""
    condition = step.get("condition", {})
    
    # Evaluate condition
    passes = _evaluate_condition(condition, context)
    
    # Determine next step based on condition result
    next_step = condition.get("then_step") if passes else condition.get("else_step")
    
    logger.info(f"Condition evaluated to {passes}, next step: {next_step}")
    
    return {
        "condition_result": passes,
        "next_step": next_step,
    }


async def _execute_human_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute a human-in-loop step.
    
    This pauses workflow execution and waits for human approval.
    The workflow will be resumed via API call.
    """
    human_config = step.get("human_config", {})
    notification = _resolve_value(human_config.get("notification", ""), context)
    
    # Update execution status to awaiting_human
    execution.status = "awaiting_human"
    execution.current_step_id = step.get("id")
    execution.awaiting_approval_data = {
        "notification": notification,
        "options": human_config.get("options", []),
        "timeout_minutes": human_config.get("timeout_minutes"),
        "on_timeout": human_config.get("on_timeout", "continue"),
    }
    await session.commit()
    
    logger.info(f"Workflow paused for human approval: {notification}")
    
    # Return special marker that workflow is paused
    return {
        "status": "awaiting_human",
        "notification": notification,
    }


async def _execute_parallel_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> List[Any]:
    """Execute multiple steps in parallel."""
    parallel_steps = step.get("parallel_steps", [])
    
    logger.info(f"Executing {len(parallel_steps)} steps in parallel")
    
    # Execute all steps concurrently
    tasks = [
        execute_step(
            session, execution, sub_step, context, usage_limits, busibox_client, principal
        )
        for sub_step in parallel_steps
    ]
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Check for failures
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Parallel step {i} failed: {str(result)}")
            raise WorkflowExecutionError(f"Parallel step {i} failed: {str(result)}")
    
    return list(results)


async def _execute_loop_step(
    session: AsyncSession,
    execution: WorkflowExecution,
    step: Dict[str, Any],
    context: Dict[str, Any],
    usage_limits: UsageLimits,
    busibox_client: BusiboxClient,
    principal: Principal,
) -> List[Any]:
    """Execute a loop step, iterating over items."""
    loop_config = step.get("loop_config", {})
    items_path = loop_config.get("items_path")
    item_variable = loop_config.get("item_variable", "item")
    loop_steps = loop_config.get("steps", [])
    
    # Resolve items array - treat None/missing as empty list
    items = _resolve_value(items_path, context)
    if items is None:
        logger.info(f"Loop items_path '{items_path}' resolved to None, treating as empty list")
        items = []
    if not isinstance(items, list):
        raise WorkflowExecutionError(f"Loop items_path must resolve to an array, got {type(items)}")
    
    logger.info(f"Executing loop over {len(items)} items")
    
    results = []
    for i, item in enumerate(items):
        # Create loop context with current item
        loop_context = {
            **context,
            item_variable: item,
            "loop_index": i,
            "loop_count": len(items),
        }
        
        # Execute all loop steps for this item
        for loop_step in loop_steps:
            step_result = await execute_step(
                session, execution, loop_step, loop_context, usage_limits, busibox_client, principal
            )
            loop_context[loop_step.get("id")] = step_result
        
        results.append(loop_context)
    
    return results


async def create_workflow_execution(
    session: AsyncSession,
    principal: Principal,
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    override_guardrails: Optional[Dict[str, Any]] = None,
) -> WorkflowExecution:
    """
    Create a workflow execution record without running it.
    
    This allows the API to return immediately while the workflow runs in the background.
    
    Args:
        session: Database session
        principal: User principal
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        override_guardrails: Optional guardrails to override workflow defaults
        
    Returns:
        WorkflowExecution record with status "pending"
        
    Raises:
        WorkflowExecutionError: If workflow not found or inactive
    """
    from app.services.builtin_workflows import get_builtin_workflow_by_id, is_builtin_workflow
    
    # Load workflow definition - check database first, then built-in workflows
    workflow = await session.get(WorkflowDefinition, workflow_id)
    
    if not workflow and is_builtin_workflow(workflow_id):
        # Built-in workflow not yet persisted — create a database record for execution tracking
        builtin = get_builtin_workflow_by_id(workflow_id)
        if builtin:
            workflow = WorkflowDefinition(
                id=workflow_id,
                name=builtin.name,
                description=builtin.description,
                steps=builtin.steps,
                trigger=builtin.trigger,
                guardrails=builtin.guardrails,
                is_active=True,
                created_by=None,
                version=builtin.version,
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            logger.info(f"Persisted built-in workflow {builtin.name} (ID: {workflow_id}) for execution tracking")
    
    if not workflow:
        raise WorkflowExecutionError(f"Workflow {workflow_id} not found")
    
    if not workflow.is_active:
        raise WorkflowExecutionError(f"Workflow {workflow.name} is not active")
    
    # Merge guardrails
    guardrails = override_guardrails or workflow.guardrails
    
    # Create execution record with pending status
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="pending",
        trigger_source="manual",
        input_data=input_data,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        created_by=principal.sub,
    )
    session.add(execution)
    await session.commit()
    await session.refresh(execution)
    
    logger.info(f"Created workflow execution {execution.id} for workflow {workflow.name} (ID: {workflow_id})")
    
    return execution


async def run_workflow_execution(
    execution_id: uuid.UUID,
    principal: Principal,
    scopes: List[str],
    purpose: str,
) -> WorkflowExecution:
    """
    Run a previously created workflow execution.
    
    This function is designed to be called as a background task.
    It creates its own database session since it runs independently.
    
    Args:
        execution_id: The execution record UUID
        principal: User principal
        scopes: Required scopes
        purpose: Purpose for token exchange
        
    Returns:
        Updated WorkflowExecution record
    """
    from app.db.session import get_session_context
    
    async with get_session_context() as session:
        try:
            # Load execution and workflow
            execution = await session.get(WorkflowExecution, execution_id)
            if not execution:
                logger.error(f"Execution {execution_id} not found")
                return None
            
            workflow = await session.get(WorkflowDefinition, execution.workflow_id)
            if not workflow:
                # Check if it's a built-in workflow and persist it
                from app.services.builtin_workflows import get_builtin_workflow_by_id, is_builtin_workflow
                if is_builtin_workflow(execution.workflow_id):
                    builtin = get_builtin_workflow_by_id(execution.workflow_id)
                    if builtin:
                        workflow = WorkflowDefinition(
                            id=execution.workflow_id,
                            name=builtin.name,
                            description=builtin.description,
                            steps=builtin.steps,
                            trigger=builtin.trigger,
                            guardrails=builtin.guardrails,
                            is_active=True,
                            created_by=None,
                            version=builtin.version,
                        )
                        session.add(workflow)
                        await session.commit()
                        await session.refresh(workflow)
                        logger.info(f"Persisted built-in workflow {builtin.name} for background execution")
                
                if not workflow:
                    logger.error(f"Workflow {execution.workflow_id} not found")
                    execution.status = "failed"
                    execution.error = "Workflow not found"
                    await session.commit()
                    return execution
            
            # Mark as running
            execution.status = "running"
            await session.commit()
            
            logger.info(f"Starting workflow {workflow.name} (ID: {workflow.id}), execution {execution.id}")
            
            # Initialize guardrails
            guardrails = workflow.guardrails
            usage_limits = UsageLimits(guardrails)
            
            # Initialize execution context
            context = {
                "input": execution.input_data,
                "workflow": {"id": str(workflow.id), "name": workflow.name},
            }
            
            # Get or exchange token
            token_response = await get_or_exchange_token(
                session=session,
                principal=principal,
                scopes=scopes,
                purpose=purpose,
            )
            
            # Create Busibox client with exchanged token
            busibox_client = BusiboxClient(access_token=token_response.access_token)
            
            # Execute steps with branching support
            current_step_idx = 0
            steps_by_id = {step["id"]: step for step in workflow.steps}
            
            while current_step_idx < len(workflow.steps):
                step = workflow.steps[current_step_idx]
                step_id = step.get("id")
                
                # Check if execution was stopped by user (e.g. via stop API)
                # Reload status from DB to pick up external changes
                await session.refresh(execution)
                if execution.status in ("stopped", "cancelled"):
                    logger.info(
                        f"Workflow execution {execution.id} was stopped by user at step {step_id}"
                    )
                    # completed_at and duration already set by the stop endpoint
                    if not execution.completed_at:
                        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                        if execution.started_at:
                            execution.duration_seconds = (
                                execution.completed_at - execution.started_at
                            ).total_seconds()
                    await session.commit()
                    await session.refresh(execution)
                    return execution
                
                # Update current step
                execution.current_step_id = step_id
                await session.commit()
                
                # Execute step
                result = await execute_step(
                    session, execution, step, context, usage_limits, busibox_client, principal
                )
                
                # Store result in context
                context[step_id] = result
                execution.step_outputs[step_id] = result
                
                # Check if workflow is paused for human approval
                if isinstance(result, dict) and result.get("status") == "awaiting_human":
                    logger.info(f"Workflow paused at step {step_id} for human approval")
                    await session.commit()
                    return execution
                
                # Handle branching for condition steps
                if step.get("type") == "condition" and isinstance(result, dict):
                    next_step_id = result.get("next_step")
                    if next_step_id and next_step_id in steps_by_id:
                        # Jump to specified step
                        current_step_idx = workflow.steps.index(steps_by_id[next_step_id])
                        continue
                
                # Move to next step
                current_step_idx += 1
            
            # Workflow completed successfully
            execution.status = "completed"
            execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()
            
            # Update usage metrics
            usage = usage_limits.get_usage_dict()
            execution.usage_requests = usage["requests"]
            execution.usage_input_tokens = usage["input_tokens"]
            execution.usage_output_tokens = usage["output_tokens"]
            execution.usage_tool_calls = usage["tool_calls"]
            execution.estimated_cost_dollars = usage["estimated_cost_dollars"]
            
            logger.info(f"Workflow {workflow.name} completed successfully, execution {execution.id}")
            
        except GuardrailsExceededError as e:
            execution.status = "failed"
            execution.error = f"Guardrails exceeded: {str(e)}"
            execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()
            logger.error(f"Workflow failed: guardrails exceeded - {str(e)}")
            
        except WorkflowExecutionError as e:
            execution.status = "failed"
            execution.error = str(e)
            execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()
            logger.error(f"Workflow failed: {str(e)}")
            
        except Exception as e:
            execution.status = "failed"
            execution.error = f"Unexpected error: {str(e)}"
            execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            execution.duration_seconds = (
                execution.completed_at - execution.started_at
            ).total_seconds()
            logger.error(f"Workflow failed unexpectedly: {str(e)}", exc_info=True)
        
        # Persist final state
        await session.commit()
        await session.refresh(execution)
        return execution


async def execute_enhanced_workflow(
    session: AsyncSession,
    principal: Principal,
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    scopes: List[str],
    purpose: str,
    override_guardrails: Optional[Dict[str, Any]] = None,
) -> WorkflowExecution:
    """
    Execute a workflow with full support for all step types.
    
    This is the legacy synchronous execution - runs the workflow and waits for completion.
    For async execution, use create_workflow_execution + run_workflow_execution.
    
    Args:
        session: Database session
        principal: User principal
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        scopes: Required scopes
        purpose: Purpose for token exchange
        override_guardrails: Optional guardrails to override workflow defaults
        
    Returns:
        WorkflowExecution record
        
    Raises:
        WorkflowExecutionError: If workflow execution fails
    """
    from app.services.builtin_workflows import get_builtin_workflow_by_id, is_builtin_workflow
    
    # Load workflow definition - check database first, then built-in workflows
    workflow = await session.get(WorkflowDefinition, workflow_id)
    
    if not workflow and is_builtin_workflow(workflow_id):
        # Built-in workflow not yet persisted — create a database record for execution tracking
        builtin = get_builtin_workflow_by_id(workflow_id)
        if builtin:
            workflow = WorkflowDefinition(
                id=workflow_id,
                name=builtin.name,
                description=builtin.description,
                steps=builtin.steps,
                trigger=builtin.trigger,
                guardrails=builtin.guardrails,
                is_active=True,
                created_by=None,
                version=builtin.version,
            )
            session.add(workflow)
            await session.commit()
            await session.refresh(workflow)
            logger.info(f"Persisted built-in workflow {builtin.name} (ID: {workflow_id}) for execution tracking")
    
    if not workflow:
        raise WorkflowExecutionError(f"Workflow {workflow_id} not found")
    
    if not workflow.is_active:
        raise WorkflowExecutionError(f"Workflow {workflow.name} is not active")
    
    # Create execution record
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="running",
        trigger_source="manual",
        input_data=input_data,
        started_at=datetime.now(timezone.utc).replace(tzinfo=None),
        created_by=principal.sub,
    )
    session.add(execution)
    await session.commit()
    await session.refresh(execution)
    
    logger.info(f"Starting workflow {workflow.name} (ID: {workflow_id}), execution {execution.id}")
    
    # Initialize guardrails
    guardrails = override_guardrails or workflow.guardrails
    usage_limits = UsageLimits(guardrails)
    
    # Initialize execution context
    context = {
        "input": input_data,
        "workflow": {"id": str(workflow_id), "name": workflow.name},
    }
    
    try:
        # Get or exchange token
        token_response = await get_or_exchange_token(
            session=session,
            principal=principal,
            scopes=scopes,
            purpose=purpose,
        )
        
        # Create Busibox client with exchanged token
        busibox_client = BusiboxClient(access_token=token_response.access_token)
        
        # Execute steps with branching support
        current_step_idx = 0
        steps_by_id = {step["id"]: step for step in workflow.steps}
        
        while current_step_idx < len(workflow.steps):
            step = workflow.steps[current_step_idx]
            step_id = step.get("id")
            
            # Update current step
            execution.current_step_id = step_id
            await session.commit()
            
            # Execute step
            result = await execute_step(
                session, execution, step, context, usage_limits, busibox_client, principal
            )
            
            # Store result in context
            context[step_id] = result
            execution.step_outputs[step_id] = result
            
            # Check if workflow is paused for human approval
            if isinstance(result, dict) and result.get("status") == "awaiting_human":
                logger.info(f"Workflow paused at step {step_id} for human approval")
                await session.commit()
                return execution
            
            # Handle branching for condition steps
            if step.get("type") == "condition" and isinstance(result, dict):
                next_step_id = result.get("next_step")
                if next_step_id and next_step_id in steps_by_id:
                    # Jump to specified step
                    current_step_idx = workflow.steps.index(steps_by_id[next_step_id])
                    continue
            
            # Move to next step
            current_step_idx += 1
        
        # Workflow completed successfully
        execution.status = "completed"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        
        # Update usage metrics
        usage = usage_limits.get_usage_dict()
        execution.usage_requests = usage["requests"]
        execution.usage_input_tokens = usage["input_tokens"]
        execution.usage_output_tokens = usage["output_tokens"]
        execution.usage_tool_calls = usage["tool_calls"]
        execution.estimated_cost_dollars = usage["estimated_cost_dollars"]
        
        logger.info(f"Workflow {workflow.name} completed successfully, execution {execution.id}")
        
    except GuardrailsExceededError as e:
        execution.status = "failed"
        execution.error = f"Guardrails exceeded: {str(e)}"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed: guardrails exceeded - {str(e)}")
        
    except WorkflowExecutionError as e:
        execution.status = "failed"
        execution.error = str(e)
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed: {str(e)}")
        
    except Exception as e:
        execution.status = "failed"
        execution.error = f"Unexpected error: {str(e)}"
        execution.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        execution.duration_seconds = (
            execution.completed_at - execution.started_at
        ).total_seconds()
        logger.error(f"Workflow {workflow.name} failed unexpectedly: {str(e)}", exc_info=True)
    
    # Persist final state
    await session.commit()
    await session.refresh(execution)
    
    return execution
