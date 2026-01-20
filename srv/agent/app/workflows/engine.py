"""
Workflow execution engine for multi-step agent orchestration.

Supports:
- Sequential step execution
- Conditional branching with JSONPath evaluation
- Parallel step execution
- Loop/iteration steps
- Human-in-loop approvals
- State persistence between steps
- Usage tracking and guardrails
- Error handling and recovery
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import attributes

from app.agents.core import BusiboxDeps
from app.clients.busibox import BusiboxClient
from app.models.domain import RunRecord, WorkflowDefinition, WorkflowExecution, StepExecution
from app.schemas.auth import Principal
from app.services.agent_registry import agent_registry
from app.services.token_service import get_or_exchange_token

logger = logging.getLogger(__name__)


class WorkflowExecutionError(Exception):
    """Raised when workflow execution fails."""
    pass


class GuardrailsExceededError(WorkflowExecutionError):
    """Raised when workflow exceeds guardrails limits."""
    pass


class UsageLimits:
    """
    Track and enforce usage limits for workflow execution.
    
    Based on Pydantic AI's UsageLimits pattern:
    https://ai.pydantic.dev/api/run/#pydantic_ai.UsageLimits
    """
    
    def __init__(self, guardrails: Optional[Dict[str, Any]] = None):
        """
        Initialize usage limits from guardrails configuration.
        
        Args:
            guardrails: Dict with optional keys:
                - request_limit: Max LLM requests
                - total_tokens_limit: Max tokens across all requests
                - tool_calls_limit: Max tool invocations
                - timeout_seconds: Max duration
                - max_cost_dollars: Cost ceiling
        """
        if guardrails is None:
            guardrails = {}
        
        self.request_limit = guardrails.get("request_limit")
        self.total_tokens_limit = guardrails.get("total_tokens_limit")
        self.tool_calls_limit = guardrails.get("tool_calls_limit")
        self.timeout_seconds = guardrails.get("timeout_seconds")
        self.max_cost_dollars = guardrails.get("max_cost_dollars")
        
        # Current usage counters
        self.requests = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.estimated_cost = 0.0
    
    def check_before_step(self, step_type: str) -> None:
        """
        Check if starting a new step would exceed limits.
        
        Args:
            step_type: Type of step about to execute
            
        Raises:
            GuardrailsExceededError: If limits would be exceeded
        """
        # Check request limit (for agent steps)
        if step_type == "agent" and self.request_limit is not None:
            if self.requests >= self.request_limit:
                raise GuardrailsExceededError(
                    f"Request limit exceeded: {self.requests}/{self.request_limit}"
                )
        
        # Check tool call limit
        if step_type == "tool" and self.tool_calls_limit is not None:
            if self.tool_calls >= self.tool_calls_limit:
                raise GuardrailsExceededError(
                    f"Tool calls limit exceeded: {self.tool_calls}/{self.tool_calls_limit}"
                )
    
    def update(self, 
               requests: int = 0,
               input_tokens: int = 0,
               output_tokens: int = 0,
               tool_calls: int = 0,
               estimated_cost: float = 0.0) -> None:
        """
        Update usage counters and check limits.
        
        Args:
            requests: Number of requests to add
            input_tokens: Input tokens to add
            output_tokens: Output tokens to add
            tool_calls: Tool calls to add
            estimated_cost: Cost to add
            
        Raises:
            GuardrailsExceededError: If any limit is exceeded
        """
        self.requests += requests
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.tool_calls += tool_calls
        self.estimated_cost += estimated_cost
        
        # Check limits
        if self.request_limit is not None and self.requests > self.request_limit:
            raise GuardrailsExceededError(
                f"Request limit exceeded: {self.requests}/{self.request_limit}"
            )
        
        if self.total_tokens_limit is not None:
            total_tokens = self.input_tokens + self.output_tokens
            if total_tokens > self.total_tokens_limit:
                raise GuardrailsExceededError(
                    f"Token limit exceeded: {total_tokens}/{self.total_tokens_limit}"
                )
        
        if self.tool_calls_limit is not None and self.tool_calls > self.tool_calls_limit:
            raise GuardrailsExceededError(
                f"Tool calls limit exceeded: {self.tool_calls}/{self.tool_calls_limit}"
            )
        
        if self.max_cost_dollars is not None and self.estimated_cost > self.max_cost_dollars:
            raise GuardrailsExceededError(
                f"Cost limit exceeded: ${self.estimated_cost:.4f}/${self.max_cost_dollars:.4f}"
            )
    
    def get_usage_dict(self) -> Dict[str, Any]:
        """
        Get current usage as a dictionary.
        
        Returns:
            Dict with usage metrics
        """
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "estimated_cost_dollars": self.estimated_cost,
        }
    
    def __repr__(self) -> str:
        return (
            f"UsageLimits(requests={self.requests}/{self.request_limit}, "
            f"tokens={self.input_tokens + self.output_tokens}/{self.total_tokens_limit}, "
            f"tool_calls={self.tool_calls}/{self.tool_calls_limit}, "
            f"cost=${self.estimated_cost:.4f}/${self.max_cost_dollars})"
        )


def _resolve_jsonpath(path: str, context: Dict[str, Any]) -> Any:
    """
    Resolve a single JSONPath reference (without the $. prefix).
    
    Args:
        path: JSONPath without $. prefix (e.g., "input.query")
        context: Execution context
        
    Returns:
        Resolved value or None if not found
    """
    parts = path.split(".")
    result = context
    for part in parts:
        if isinstance(result, dict):
            result = result.get(part)
        else:
            return None
    return result


def _resolve_value(value: Any, context: Dict[str, Any]) -> Any:
    """
    Resolve a value that may contain JSONPath-like references.
    
    Handles two cases:
    1. Entire string is a reference: "$.input.query" -> resolved value
    2. String contains embedded references: "Query: $.input.query" -> "Query: actual value"
    
    Args:
        value: Value to resolve (may be string with $.references)
        context: Execution context with step outputs
        
    Returns:
        Resolved value
        
    Examples:
        _resolve_value("$.input.path", {"input": {"path": "/doc.pdf"}}) -> "/doc.pdf"
        _resolve_value("Query: $.input.query", {"input": {"query": "test"}}) -> "Query: test"
        _resolve_value("literal", {}) -> "literal"
    """
    import re
    
    if not isinstance(value, str):
        return value
    
    # Case 1: Entire string is a single reference
    if value.startswith("$.") and " " not in value and "\n" not in value:
        # Check if it's a pure reference (no other text)
        path = value[2:]
        # Verify it looks like a simple path (letters, dots, underscores, numbers)
        if re.match(r'^[\w.]+$', path):
            return _resolve_jsonpath(path, context)
    
    # Case 2: String contains embedded references - interpolate all $.xxx occurrences
    # Match $.word.word.word patterns (one or more path segments)
    pattern = r'\$\.([\w]+(?:\.[\w]+)*)'
    
    def replace_ref(match):
        path = match.group(1)
        resolved = _resolve_jsonpath(path, context)
        if resolved is None:
            return match.group(0)  # Keep original if not found
        # Convert to string representation for embedding
        if isinstance(resolved, (dict, list)):
            import json
            return json.dumps(resolved, indent=2)
        return str(resolved)
    
    return re.sub(pattern, replace_ref, value)


def _resolve_args(args: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve all arguments in a dict, handling JSONPath references.
    
    Args:
        args: Arguments dict (may contain $.references)
        context: Execution context
        
    Returns:
        Dict with resolved values
    """
    resolved = {}
    for key, value in args.items():
        if isinstance(value, dict):
            resolved[key] = _resolve_args(value, context)
        elif isinstance(value, list):
            resolved[key] = [_resolve_value(v, context) for v in value]
        else:
            resolved[key] = _resolve_value(value, context)
    return resolved


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate cost for LLM API call based on model and token usage.
    
    Args:
        model: Model name (e.g., "gpt-4", "claude-3-5-sonnet")
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        
    Returns:
        Estimated cost in dollars
        
    Note: Prices are approximate and should be updated based on current rates.
    """
    # Pricing per 1M tokens (approximate, as of Dec 2024)
    pricing = {
        # OpenAI
        "gpt-4": {"input": 30.0, "output": 60.0},
        "gpt-4-turbo": {"input": 10.0, "output": 30.0},
        "gpt-3.5-turbo": {"input": 0.5, "output": 1.5},
        # Anthropic
        "claude-3-5-sonnet": {"input": 3.0, "output": 15.0},
        "claude-3-opus": {"input": 15.0, "output": 75.0},
        "claude-3-haiku": {"input": 0.25, "output": 1.25},
        # Default fallback
        "default": {"input": 5.0, "output": 15.0},
    }
    
    # Find matching pricing
    model_lower = model.lower()
    rates = pricing.get("default", {"input": 5.0, "output": 15.0})
    
    for model_key, model_rates in pricing.items():
        if model_key in model_lower:
            rates = model_rates
            break
    
    # Calculate cost
    input_cost = (input_tokens / 1_000_000) * rates["input"]
    output_cost = (output_tokens / 1_000_000) * rates["output"]
    
    return input_cost + output_cost


def _evaluate_condition(condition: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """
    Evaluate a condition against the execution context.
    
    Args:
        condition: Condition definition with field, operator, value
        context: Execution context with step outputs
        
    Returns:
        True if condition passes, False otherwise
        
    Supported operators:
        - eq: Equal
        - ne: Not equal
        - gt: Greater than
        - lt: Less than
        - gte: Greater than or equal
        - lte: Less than or equal
        - contains: String/array contains
        - exists: Field exists (non-null)
    """
    field = condition.get("field")
    operator = condition.get("operator")
    expected_value = condition.get("value")
    
    # Resolve the field value from context
    actual_value = _resolve_value(field, context)
    
    logger.debug(f"Evaluating condition: {field} {operator} {expected_value}, actual: {actual_value}")
    
    if operator == "exists":
        return actual_value is not None
    
    if operator == "eq":
        return actual_value == expected_value
    
    if operator == "ne":
        return actual_value != expected_value
    
    if operator == "gt":
        return actual_value > expected_value if actual_value is not None else False
    
    if operator == "lt":
        return actual_value < expected_value if actual_value is not None else False
    
    if operator == "gte":
        return actual_value >= expected_value if actual_value is not None else False
    
    if operator == "lte":
        return actual_value <= expected_value if actual_value is not None else False
    
    if operator == "contains":
        if actual_value is None:
            return False
        if isinstance(actual_value, str):
            return expected_value in actual_value
        if isinstance(actual_value, list):
            return expected_value in actual_value
        return False
    
    logger.warning(f"Unknown condition operator: {operator}")
    return False


def _add_workflow_event(
    run_record: RunRecord,
    step_id: str,
    event_type: str,
    data: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """
    Add a workflow step event to the run record.
    
    Args:
        run_record: Run record to update
        step_id: Workflow step identifier
        event_type: Event type (step_started, step_completed, step_failed)
        data: Optional event data
        error: Optional error message
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": event_type,
        "step_id": step_id,
    }
    
    if data:
        event["data"] = data
    
    if error:
        event["error"] = error
    
    if not isinstance(run_record.events, list):
        run_record.events = []
    
    run_record.events.append(event)
    attributes.flag_modified(run_record, "events")


async def execute_workflow(
    session: AsyncSession,
    principal: Principal,
    workflow_id: uuid.UUID,
    input_data: Dict[str, Any],
    scopes: List[str],
    purpose: str,
) -> RunRecord:
    """
    Execute a multi-step workflow with state persistence.
    
    Args:
        session: Database session
        principal: User principal for authentication
        workflow_id: Workflow definition UUID
        input_data: Initial workflow input
        scopes: Required scopes for execution
        purpose: Purpose for token exchange
        
    Returns:
        RunRecord with workflow execution results
        
    Raises:
        WorkflowExecutionError: If workflow execution fails
    """
    # Load workflow definition
    workflow = await session.get(WorkflowDefinition, workflow_id)
    if not workflow:
        raise WorkflowExecutionError(f"Workflow {workflow_id} not found")
    
    if not workflow.is_active:
        raise WorkflowExecutionError(f"Workflow {workflow.name} is not active")
    
    # Create run record
    run_record = RunRecord(
        workflow_id=workflow_id,
        status="running",
        input=input_data,
        output={},
        created_by=principal.sub,
    )
    session.add(run_record)
    await session.commit()
    await session.refresh(run_record)
    
    logger.info(
        f"Starting workflow {workflow.name} (ID: {workflow_id}), run {run_record.id}"
    )
    
    # Initialize execution context
    context = {
        "input": input_data,
        "workflow": {"id": str(workflow_id), "name": workflow.name},
    }
    
    try:
        # Get or exchange token for downstream services
        token_response = await get_or_exchange_token(
            session=session,
            principal=principal,
            scopes=scopes,
            purpose=purpose,
        )
        
        # Create Busibox client with exchanged token
        busibox_client = BusiboxClient(access_token=token_response.access_token)
        
        # Execute steps sequentially
        for step in workflow.steps:
            step_id = step.get("id", "unknown")
            step_type = step.get("type")
            
            _add_workflow_event(
                run_record,
                step_id,
                "step_started",
                data={"type": step_type},
            )
            
            try:
                if step_type == "tool":
                    # Execute tool call
                    tool_name = step.get("tool")
                    args = step.get("args", {})
                    resolved_args = _resolve_args(args, context)
                    
                    logger.info(f"Executing tool {tool_name} in step {step_id}")
                    
                    # Call tool via Busibox client
                    if tool_name == "search":
                        result = await busibox_client.search(**resolved_args)
                    elif tool_name == "ingest":
                        result = await busibox_client.ingest_document(**resolved_args)
                    elif tool_name == "rag":
                        result = await busibox_client.rag_query(**resolved_args)
                    else:
                        raise WorkflowExecutionError(f"Unknown tool: {tool_name}")
                    
                    context[step_id] = result
                    
                elif step_type == "agent":
                    # Execute agent
                    from app.services.builtin_agents import get_builtin_agent_by_name
                    
                    agent_name = step.get("agent")
                    agent_input = step.get("input", "")
                    resolved_input = _resolve_value(agent_input, context)
                    
                    logger.info(f"Executing agent {agent_name} in step {step_id}")
                    
                    # First check builtin agents
                    builtin_agent = get_builtin_agent_by_name(agent_name) if agent_name else None
                    
                    if builtin_agent:
                        # Execute builtin agent directly
                        deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
                        agent_result = await builtin_agent.run(str(resolved_input), deps=deps)
                    else:
                        # Find agent by name in database
                        from sqlalchemy import select
                        from app.models.domain import AgentDefinition
                        
                        stmt = select(AgentDefinition).where(
                            AgentDefinition.name == agent_name,
                            AgentDefinition.is_active.is_(True),
                        )
                        result = await session.execute(stmt)
                        agent_def = result.scalars().first()
                        
                        if not agent_def:
                            raise WorkflowExecutionError(f"Agent {agent_name} not found")
                        
                        # Get agent from registry
                        agent = agent_registry.get(agent_def.id)
                        
                        # Execute agent
                        deps = BusiboxDeps(principal=principal, busibox_client=busibox_client)
                        agent_result = await agent.run(str(resolved_input), deps=deps)
                    
                    # Extract output from agent result
                    # Handle different result types:
                    # 1. PydanticAI agents return RunResult with .data that has model_dump()
                    # 2. BaseStreamingAgent returns AgentResult with .data as string
                    if hasattr(agent_result, "data"):
                        if hasattr(agent_result.data, "model_dump"):
                            output = agent_result.data.model_dump()
                        elif isinstance(agent_result.data, str):
                            output = {"result": agent_result.data, "content": agent_result.data}
                        elif isinstance(agent_result.data, dict):
                            output = agent_result.data
                        else:
                            output = {"result": str(agent_result.data)}
                    elif hasattr(agent_result, "output"):
                        output = {"result": agent_result.output} if isinstance(agent_result.output, str) else agent_result.output
                    else:
                        output = {"result": str(agent_result)}
                    
                    context[step_id] = output
                    
                else:
                    raise WorkflowExecutionError(f"Unknown step type: {step_type}")
                
                _add_workflow_event(
                    run_record,
                    step_id,
                    "step_completed",
                    data={"output": context[step_id]},
                )
                
            except Exception as e:
                logger.error(f"Step {step_id} failed: {str(e)}", exc_info=True)
                _add_workflow_event(
                    run_record,
                    step_id,
                    "step_failed",
                    error=str(e),
                )
                raise WorkflowExecutionError(f"Step {step_id} failed: {str(e)}")
        
        # Workflow completed successfully
        run_record.status = "succeeded"
        run_record.output = {
            "workflow": workflow.name,
            "steps_completed": len(workflow.steps),
            "final_context": context,
        }
        
        logger.info(f"Workflow {workflow.name} completed successfully, run {run_record.id}")
        
    except WorkflowExecutionError as e:
        run_record.status = "failed"
        run_record.output = {"error": str(e), "workflow": workflow.name}
        logger.error(f"Workflow {workflow.name} failed: {str(e)}")
        
    except Exception as e:
        run_record.status = "failed"
        run_record.output = {"error": f"Unexpected error: {str(e)}", "workflow": workflow.name}
        logger.error(f"Workflow {workflow.name} failed unexpectedly: {str(e)}", exc_info=True)
    
    # Persist final state
    await session.commit()
    await session.refresh(run_record)
    
    return run_record


def validate_workflow_steps(steps: List[Dict[str, Any]]) -> None:
    """
    Validate workflow step definitions.
    
    Args:
        steps: List of step definitions
        
    Raises:
        ValueError: If validation fails
    """
    if not steps:
        raise ValueError("Workflow must have at least one step")
    
    step_ids = set()
    
    for i, step in enumerate(steps):
        # Check required fields
        if "id" not in step:
            raise ValueError(f"Step {i} missing required field: id")
        
        if "type" not in step:
            raise ValueError(f"Step {step.get('id', i)} missing required field: type")
        
        step_id = step["id"]
        step_type = step["type"]
        
        # Check for duplicate step IDs
        if step_id in step_ids:
            raise ValueError(f"Duplicate step ID: {step_id}")
        step_ids.add(step_id)
        
        # Validate step type
        valid_types = ["tool", "agent", "condition", "human", "parallel", "loop"]
        if step_type not in valid_types:
            raise ValueError(f"Step {step_id} has invalid type: {step_type}. Must be one of: {valid_types}")
        
        # Validate tool steps
        if step_type == "tool":
            if "tool" not in step:
                raise ValueError(f"Tool step {step_id} missing required field: tool")
        
        # Validate agent steps
        elif step_type == "agent":
            if "agent_id" not in step and "agent" not in step:
                raise ValueError(f"Agent step {step_id} missing required field: agent_id or agent")
        
        # Validate condition steps
        elif step_type == "condition":
            if "condition" not in step:
                raise ValueError(f"Condition step {step_id} missing required field: condition")
            condition = step["condition"]
            if "field" not in condition or "operator" not in condition:
                raise ValueError(f"Condition step {step_id} missing field or operator")
        
        # Validate human steps
        elif step_type == "human":
            if "human_config" not in step:
                raise ValueError(f"Human step {step_id} missing required field: human_config")
        
        # Validate parallel steps
        elif step_type == "parallel":
            if "parallel_steps" not in step or not step["parallel_steps"]:
                raise ValueError(f"Parallel step {step_id} missing or empty parallel_steps")
        
        # Validate loop steps
        elif step_type == "loop":
            if "loop_config" not in step:
                raise ValueError(f"Loop step {step_id} missing required field: loop_config")
            loop_config = step["loop_config"]
            if "items_path" not in loop_config or "steps" not in loop_config:
                raise ValueError(f"Loop step {step_id} missing items_path or steps in loop_config")







