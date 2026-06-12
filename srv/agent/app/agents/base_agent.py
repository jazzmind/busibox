"""
Base Agent Framework.

Provides a flexible BaseStreamingAgent class that handles:
- Authentication and token exchange
- Tool execution with configurable strategies
- Streaming of thoughts, tool events, and content
- Configurable execution modes (run once, until done, max iterations)

Specific agents extend this class and only define their unique aspects:
- System prompts and instructions
- Tool configuration
- Pipeline steps (for predefined pipelines)
- Exit conditions
"""

import asyncio
import functools
import inspect
import json
import logging
import os
import re
import time
import warnings
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Type, Union

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import (
    AgentStreamEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPartDelta,
    UserPromptPart,
)
from pydantic_ai.run import AgentRunResultEvent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.core import BusiboxDeps
from app.agents.streaming_agent import StreamingAgent, StreamCallback
from app.clients.busibox import BusiboxClient
from app.config.settings import get_settings
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent, thought, tool_start, tool_result, content, error, complete, clarify_parallel, progress
from app.services.attachment_resolver import attachment_resolver
from app.services.token_service import get_or_exchange_token
from app.services.skills_service import get_skills_service
from app.services.mcp_client import (
    MCPServerConfig,
    get_mcp_client,
    build_mcp_tool_function,
    parse_mcp_server_configs,
)

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think>", re.DOTALL)

# ---------------------------------------------------------------------------
# Model capabilities cache – queried from LiteLLM /model/info once per model
# ---------------------------------------------------------------------------
_model_capabilities_cache: Dict[str, Dict[str, Any]] = {}
_model_capabilities_lock = asyncio.Lock()
_MODEL_CAPS_DEFAULT_MAX_OUTPUT = 16384  # generous fallback


async def _get_model_max_output_tokens(model_name: str) -> int:
    """Return the max output token budget for *model_name*.

    Queries LiteLLM ``/model/info`` on the first call per model name and
    caches the result for the process lifetime.  Falls back to
    ``_MODEL_CAPS_DEFAULT_MAX_OUTPUT`` if the proxy is unreachable or the
    model entry doesn't advertise output limits.
    """
    if model_name in _model_capabilities_cache:
        return _model_capabilities_cache[model_name].get(
            "max_output_tokens", _MODEL_CAPS_DEFAULT_MAX_OUTPUT
        )

    async with _model_capabilities_lock:
        # Double-check after acquiring lock
        if model_name in _model_capabilities_cache:
            return _model_capabilities_cache[model_name].get(
                "max_output_tokens", _MODEL_CAPS_DEFAULT_MAX_OUTPUT
            )

        caps: Dict[str, Any] = {}
        try:
            import httpx

            settings = get_settings()
            base_url = str(settings.litellm_base_url).rstrip("/")
            # Strip /v1 suffix – /model/info lives on the proxy root
            if base_url.endswith("/v1"):
                base_url = base_url[:-3]
            headers: Dict[str, str] = {"Content-Type": "application/json"}
            if settings.litellm_api_key:
                headers["Authorization"] = f"Bearer {settings.litellm_api_key}"

            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(f"{base_url}/model/info", headers=headers)
                if resp.status_code == 200:
                    for entry in resp.json().get("data", []):
                        entry_name = entry.get("model_name", "")
                        info = entry.get("model_info", {}) or {}
                        litellm_params = entry.get("litellm_params", {}) or {}
                        entry_caps: Dict[str, Any] = {}

                        # max_output_tokens: check model_info first, then
                        # litellm_params, then derive from max_model_len
                        max_out = (
                            info.get("max_output_tokens")
                            or info.get("max_tokens")
                            or litellm_params.get("max_tokens")
                        )
                        if max_out:
                            entry_caps["max_output_tokens"] = int(max_out)

                        ctx_window = info.get("max_model_len") or info.get("max_input_tokens")
                        if ctx_window:
                            entry_caps["context_window"] = int(ctx_window)
                            if "max_output_tokens" not in entry_caps:
                                # Heuristic: allow up to half the context window
                                # for output, capped at 32k
                                entry_caps["max_output_tokens"] = min(
                                    int(ctx_window) // 2, 32768
                                )

                        if not entry_caps.get("max_output_tokens"):
                            entry_caps["max_output_tokens"] = _MODEL_CAPS_DEFAULT_MAX_OUTPUT

                        _model_capabilities_cache[entry_name] = entry_caps

                    logger.info(
                        "Model capabilities cache populated",
                        extra={
                            "model_count": len(_model_capabilities_cache),
                            "models": list(_model_capabilities_cache.keys()),
                        },
                    )
                else:
                    logger.warning(
                        "LiteLLM /model/info returned non-200",
                        extra={"status": resp.status_code},
                    )
        except Exception as exc:
            logger.warning(
                "Failed to query LiteLLM /model/info for model capabilities",
                extra={"error": str(exc)},
            )

        if model_name not in _model_capabilities_cache:
            _model_capabilities_cache[model_name] = {
                "max_output_tokens": _MODEL_CAPS_DEFAULT_MAX_OUTPUT,
            }

        return _model_capabilities_cache[model_name].get(
            "max_output_tokens", _MODEL_CAPS_DEFAULT_MAX_OUTPUT
        )


def _ensure_openai_env():
    """
    Ensure OpenAI environment is configured for LiteLLM.
    
    Called lazily when agents are instantiated, not at module import time.
    This allows test conftest to load .env files first.
    
    Note: We always set OPENAI_BASE_URL and OPENAI_API_KEY to our LiteLLM
    configuration, overriding any existing OpenAI keys. This is because
    we route all LLM calls through LiteLLM proxy, not direct to OpenAI.
    """
    from busibox_common.llm import ensure_openai_env
    settings = get_settings()
    ensure_openai_env(
        base_url=str(settings.litellm_base_url),
        api_key=settings.litellm_api_key,
    )


# Maximum characters for a single tool result before truncation.
# ~2000 tokens at ~4 chars/token. Prevents context window overflow
# when tools return large datasets (e.g. query_data with many records).
MAX_TOOL_RESULT_CHARS = 8000


def _truncate_tool_result(result: Any) -> Any:
    """
    Truncate a tool result if its serialized form exceeds MAX_TOOL_RESULT_CHARS.
    
    For results with a 'records' list (e.g. QueryDataOutput), progressively
    removes records until the result fits. Adds a _truncated flag and guidance
    note so the LLM knows to use select/where/limit to narrow queries.
    
    Returns the original result if it's small enough, or a truncated dict.
    """
    try:
        if isinstance(result, BaseModel):
            serialized = result.model_dump_json()
            if len(serialized) <= MAX_TOOL_RESULT_CHARS:
                return result
            
            # Try to truncate records-based results
            if hasattr(result, 'records') and result.records:
                data = result.model_dump()
                total = data.get('total', len(data.get('records', [])))
                records = data.get('records', [])
                original_count = len(records)
                
                # Progressively remove records until under limit
                while len(json.dumps(data, default=str)) > MAX_TOOL_RESULT_CHARS and records:
                    records.pop()
                
                data['records'] = records
                data['_truncated'] = True
                data['_note'] = (
                    f"Results truncated to {len(records)} of {total} total records "
                    f"(originally fetched {original_count}). "
                    f"Use 'select' to fetch only needed fields, 'where' to filter, "
                    f"or reduce 'limit' to avoid truncation."
                )
                logger.info(
                    f"Tool result truncated: {original_count} -> {len(records)} records "
                    f"({len(serialized)} -> {len(json.dumps(data, default=str))} chars)"
                )
                return data
            
            # For non-records results, truncate the serialized string
            data = result.model_dump()
            serialized = json.dumps(data, default=str)
            if len(serialized) > MAX_TOOL_RESULT_CHARS:
                truncated_str = serialized[:MAX_TOOL_RESULT_CHARS]
                logger.info(f"Tool result string-truncated: {len(serialized)} -> {MAX_TOOL_RESULT_CHARS} chars")
                return {
                    '_truncated': True,
                    '_note': 'Result was too large and has been truncated.',
                    'data': truncated_str,
                }
        
        # Handle dict results
        elif isinstance(result, dict):
            serialized = json.dumps(result, default=str)
            if len(serialized) > MAX_TOOL_RESULT_CHARS:
                if 'records' in result and isinstance(result.get('records'), list):
                    total = result.get('total', len(result['records']))
                    records = list(result['records'])
                    original_count = len(records)
                    data = dict(result)
                    while len(json.dumps(data, default=str)) > MAX_TOOL_RESULT_CHARS and records:
                        records.pop()
                    data['records'] = records
                    data['_truncated'] = True
                    data['_note'] = (
                        f"Results truncated to {len(records)} of {total} total records. "
                        f"Use 'select', 'where', or smaller 'limit'."
                    )
                    return data
        
        return result
    except Exception as e:
        logger.warning(f"Failed to truncate tool result: {e}")
        return result


def _wrap_tool_with_truncation(tool_func: Callable) -> Callable:
    """
    Wrap a pydantic-ai tool function so its return value is truncated
    if it exceeds MAX_TOOL_RESULT_CHARS.
    
    Preserves the original function's signature, annotations, and metadata
    so pydantic-ai can introspect it correctly.
    """
    @functools.wraps(tool_func)
    async def wrapper(*args, **kwargs):
        result = await tool_func(*args, **kwargs)
        return _truncate_tool_result(result)
    
    # Preserve the full signature for pydantic-ai's introspection.
    # functools.wraps copies __name__, __doc__, __module__, __qualname__,
    # __dict__, and __wrapped__, but pydantic-ai also reads __signature__
    # and __annotations__ to discover tool parameters.
    wrapper.__signature__ = inspect.signature(tool_func)
    wrapper.__annotations__ = getattr(tool_func, '__annotations__', {})
    return wrapper


class ExecutionMode(str, Enum):
    """How the agent should handle iterations."""
    RUN_ONCE = "run_once"  # Execute pipeline once and synthesize
    RUN_UNTIL_DONE = "run_until_done"  # Loop until LLM signals completion
    RUN_MAX_ITERATIONS = "run_max_iterations"  # Loop up to max_iterations times


class ToolStrategy(str, Enum):
    """How tools should be executed."""
    SEQUENTIAL = "sequential"  # Execute tools one at a time in order
    PARALLEL = "parallel"  # Execute independent tools concurrently
    PREDEFINED_PIPELINE = "predefined_pipeline"  # Follow pipeline_steps() definition
    LLM_DRIVEN = "llm_driven"  # Let LLM decide which tools to call


# Mapping of tool names to their required OAuth scopes
TOOL_SCOPES: Dict[str, List[str]] = {
    "document_search": ["search.read"],
    "web_search": [],  # No auth needed
    "web_scraper": [],  # No auth needed
    "playwright_browser": [],  # No auth needed
    "data_document": ["data.write"],
    "list_data_documents": ["data.read"],
    "query_data": ["data.read"],
    "insert_records": ["data.write"],
    "update_records": ["data.write"],
    "delete_records": ["data.write"],
    "create_data_document": ["data.write"],
    "get_data_document": ["data.read"],
    "get_weather": [],  # No auth needed
    "rag_query": ["rag.read"],
    "create_task": ["task.write"],  # Create tasks
    "send_notification": [],  # No special auth needed (uses configured providers)
    "generate_image": ["data.write"],
    "transcribe_audio": ["data.read"],
    "text_to_speech": ["data.write"],
    "memory_search": [],
    "memory_save": [],
    "calendar_list_events": [],
    "calendar_create_event": [],
    "search_users": ["authz.read"],
    "aggregate_data": ["data.read"],
    "get_facets": ["data.read"],
    "graph_query": ["data.read"],
    "graph_explore": ["data.read"],
    "graph_relate": ["data.write"],
}

TOOL_CLASSES: Dict[str, Dict[str, Any]] = {
    "document_search": {"class": "fast", "timeout": 15},
    "query_data": {"class": "fast", "timeout": 15},
    "aggregate_data": {"class": "fast", "timeout": 15},
    "get_data_document": {"class": "fast", "timeout": 10},
    "list_data_documents": {"class": "fast", "timeout": 10},
    "get_facets": {"class": "fast", "timeout": 10},
    "data_document": {"class": "fast", "timeout": 15},
    "insert_records": {"class": "fast", "timeout": 15},
    "update_records": {"class": "fast", "timeout": 15},
    "delete_records": {"class": "fast", "timeout": 15},
    "create_data_document": {"class": "fast", "timeout": 15},
    "graph_query": {"class": "fast", "timeout": 15},
    "graph_explore": {"class": "fast", "timeout": 15},
    "graph_relate": {"class": "fast", "timeout": 15},
    "memory_search": {"class": "fast", "timeout": 10},
    "memory_save": {"class": "fast", "timeout": 10},
    "search_users": {"class": "fast", "timeout": 10},
    "calendar_list_events": {"class": "fast", "timeout": 15},
    "calendar_create_event": {"class": "fast", "timeout": 15},
    "get_weather": {"class": "fast", "timeout": 15},
    "rag_query": {"class": "fast", "timeout": 15},
    "web_search": {"class": "slow", "timeout": 30},
    "web_scraper": {"class": "slow", "timeout": 180},
    "playwright_browser": {"class": "slow", "timeout": 120},
    "generate_image": {"class": "slow", "timeout": 120},
    "transcribe_audio": {"class": "slow", "timeout": 60},
    "text_to_speech": {"class": "slow", "timeout": 180},
    "send_notification": {"class": "slow", "timeout": 30},
    "create_task": {"class": "slow", "timeout": 30},
}

TOOL_CLASS_DEFAULT: Dict[str, Any] = {"class": "slow", "timeout": 60}


@dataclass
class PipelineStep:
    """A single step in a predefined pipeline."""
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    condition: Optional[Callable[[Any], bool]] = None  # Optional condition to run step


@dataclass
class AgentConfig:
    """Configuration for a streaming agent."""
    name: str  # Internal identifier
    display_name: str  # Human-readable name
    instructions: str  # System prompt for synthesis
    tools: List[str]  # Tool names from registry
    model: str = "agent"  # LiteLLM model name
    streaming: bool = True  # Whether to stream responses
    execution_mode: ExecutionMode = ExecutionMode.RUN_ONCE
    tool_strategy: ToolStrategy = ToolStrategy.PREDEFINED_PIPELINE
    max_iterations: int = 5
    synthesis_prompt: Optional[str] = None  # Override default synthesis prompt
    max_tokens: Optional[int] = None  # Max tokens for synthesis (None = model default, no limit)
    allow_frontier_fallback: bool = False  # Allow LiteLLM to fall back to frontier model on context overflow
    
    # Structured output: when set, PydanticAI enforces response_format=json_schema
    # so the LLM is forced to return valid JSON matching this Pydantic model.
    # The agent's run() result will contain a serialised JSON string of this type.
    output_type: Optional[Type[BaseModel]] = None
    
    # MCP server configs for external tool loading
    mcp_servers: List[MCPServerConfig] = field(default_factory=list)

    # Context compression settings
    enable_history_compression: bool = True  # Whether to compress long conversation history
    compression_threshold_chars: int = 8000  # Character threshold to trigger compression
    recent_messages_to_keep: int = 5  # Number of recent message pairs to keep in full
    
    def get_required_scopes(self) -> List[str]:
        """Get all OAuth scopes required by this agent's tools."""
        scopes = []
        for tool_name in self.tools:
            scopes.extend(TOOL_SCOPES.get(tool_name, []))
        return list(set(scopes))  # Deduplicate
    
    def requires_auth(self) -> bool:
        """Check if any tools require authentication."""
        return len(self.get_required_scopes()) > 0


class ToolRegistry:
    """Registry for tool functions that can be called by agents."""
    
    _tools: Dict[str, Callable] = {}
    _tool_outputs: Dict[str, Type[BaseModel]] = {}
    
    @classmethod
    def register(cls, name: str, func: Callable, output_type: Optional[Type[BaseModel]] = None):
        """Register a tool function."""
        cls._tools[name] = func
        if output_type:
            cls._tool_outputs[name] = output_type
    
    @classmethod
    def get(cls, name: str) -> Optional[Callable]:
        """Get a tool function by name."""
        return cls._tools.get(name)
    
    @classmethod
    def get_output_type(cls, name: str) -> Optional[Type[BaseModel]]:
        """Get the output type for a tool."""
        return cls._tool_outputs.get(name)
    
    @classmethod
    def has(cls, name: str) -> bool:
        """Check if a tool is registered."""
        return name in cls._tools


# Register built-in tools
def _register_builtin_tools():
    """Register all built-in tools with the registry."""
    from app.tools.document_search_tool import search_documents, DocumentSearchOutput
    from app.tools.web_search_tool import search_web, WebSearchOutput
    from app.tools.web_scraper_tool import scrape_webpage, WebScraperOutput
    from app.tools.weather_tool import get_weather, WeatherOutput
    from app.tools.image_tool import generate_image, ImageOutput
    from app.tools.transcription_tool import transcribe_audio, TranscriptionOutput
    from app.tools.tts_tool import text_to_speech, TTSOutput
    from app.tools.memory_tool import memory_search, memory_save, MemorySearchOutput, MemorySaveOutput
    from app.tools.calendar_tool import (
        calendar_list_events,
        calendar_create_event,
        CalendarListOutput,
        CalendarCreateOutput,
    )
    
    ToolRegistry.register("document_search", search_documents, DocumentSearchOutput)
    ToolRegistry.register("web_search", search_web, WebSearchOutput)
    ToolRegistry.register("web_scraper", scrape_webpage, WebScraperOutput)
    
    try:
        from app.tools.playwright_tool import browse_webpage as playwright_browse, PlaywrightBrowserOutput
        ToolRegistry.register("playwright_browser", playwright_browse, PlaywrightBrowserOutput)
    except ImportError as e:
        logger.warning(f"Could not register playwright_browser tool: {e}")
    
    ToolRegistry.register("get_weather", get_weather, WeatherOutput)
    ToolRegistry.register("generate_image", generate_image, ImageOutput)
    ToolRegistry.register("transcribe_audio", transcribe_audio, TranscriptionOutput)
    ToolRegistry.register("text_to_speech", text_to_speech, TTSOutput)
    ToolRegistry.register("memory_search", memory_search, MemorySearchOutput)
    ToolRegistry.register("memory_save", memory_save, MemorySaveOutput)
    ToolRegistry.register("calendar_list_events", calendar_list_events, CalendarListOutput)
    ToolRegistry.register("calendar_create_event", calendar_create_event, CalendarCreateOutput)
    
    # Register task and notification tools
    try:
        from app.tools.task_tool import create_task, TaskCreationOutput
        from app.tools.notification_tool import send_notification, NotificationOutput
        
        ToolRegistry.register("create_task", create_task, TaskCreationOutput)
        ToolRegistry.register("send_notification", send_notification, NotificationOutput)
    except ImportError as e:
        logger.warning(f"Could not register task/notification tools: {e}")
    
    # Register user search tool
    try:
        from app.tools.user_search_tool import search_users, UserSearchOutput
        ToolRegistry.register("search_users", search_users, UserSearchOutput)
    except ImportError as e:
        logger.warning(f"Could not register user search tool: {e}")

    # Register data management tools (list, query, insert, update, delete, aggregate, facets)
    try:
        from app.tools.data_tool import (
            list_data_documents, ListDataDocumentsOutput,
            query_data, QueryDataOutput,
            aggregate_data, AggregateDataOutput,
            get_facets, GetFacetsOutput,
            insert_records, InsertRecordsOutput,
            update_records, UpdateRecordsOutput,
            delete_records, DeleteRecordsOutput,
            create_data_document, CreateDataDocumentOutput,
            get_data_document, GetDocumentOutput,
        )
        
        ToolRegistry.register("list_data_documents", list_data_documents, ListDataDocumentsOutput)
        ToolRegistry.register("query_data", query_data, QueryDataOutput)
        ToolRegistry.register("aggregate_data", aggregate_data, AggregateDataOutput)
        ToolRegistry.register("get_facets", get_facets, GetFacetsOutput)
        ToolRegistry.register("insert_records", insert_records, InsertRecordsOutput)
        ToolRegistry.register("update_records", update_records, UpdateRecordsOutput)
        ToolRegistry.register("delete_records", delete_records, DeleteRecordsOutput)
        ToolRegistry.register("create_data_document", create_data_document, CreateDataDocumentOutput)
        ToolRegistry.register("get_data_document", get_data_document, GetDocumentOutput)
    except ImportError as e:
        logger.warning(f"Could not register data tools: {e}")

    # Register graph tools (knowledge graph queries)
    try:
        from app.tools.graph_tool import (
            graph_query, GraphQueryOutput,
            graph_explore, GraphExploreOutput,
            graph_relate, GraphRelateOutput,
        )
        
        ToolRegistry.register("graph_query", graph_query, GraphQueryOutput)
        ToolRegistry.register("graph_explore", graph_explore, GraphExploreOutput)
        ToolRegistry.register("graph_relate", graph_relate, GraphRelateOutput)
    except ImportError as e:
        logger.warning(f"Could not register graph tools: {e}")


# Initialize tool registry on module load
try:
    _register_builtin_tools()
    # Log registered tools for debugging
    registered = list(ToolRegistry._tools.keys())
    logger.info(f"Tool registry initialized with {len(registered)} tools: {registered}")
except ImportError as e:
    logger.warning(f"Could not register all builtin tools: {e}")


@dataclass
class AgentContext:
    """Runtime context for agent execution."""
    principal: Optional[Principal] = None
    session: Optional[AsyncSession] = None
    deps: Optional[BusiboxDeps] = None
    tool_results: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    # Compressed history (populated after compression)
    compressed_history_summary: Optional[str] = None
    recent_messages: List[Dict[str, Any]] = field(default_factory=list)
    # Relevant insights from user's past conversations (agent memories)
    relevant_insights: List[Dict[str, Any]] = field(default_factory=list)
    # Missing profile fields (computed by dispatcher)
    missing_profile_fields: List[str] = field(default_factory=list)
    # Follow-up profile questions that should be asked naturally when relevant
    pending_questions: List[Dict[str, Any]] = field(default_factory=list)
    # Whether the platform-level insights system is enabled
    insights_enabled: bool = True
    # Application context metadata (e.g. projectId, appName) from the chat request
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Raw attachment metadata from chat request (unresolved)
    attachment_metadata: List[Dict[str, Any]] = field(default_factory=list)
    # Resolved attachment content to inject into prompts
    resolved_attachments: List[Dict[str, Any]] = field(default_factory=list)
    # Optional runtime JSON Schema used for deterministic structured output.
    # This is typically provided by programmatic workflow-style invocations.
    response_schema: Optional[Dict[str, Any]] = None
    # The current user query (set during run_with_streaming)
    current_query: Optional[str] = None
    # Optional per-run token budget override.
    max_tokens: Optional[int] = None
    # Base64 images for multimodal structured-output calls (from /runs/invoke).
    # Each item: {"media_type": "image/jpeg", "data": "<base64>"}
    images: List[Dict[str, str]] = field(default_factory=list)
    # Deduplication cache for tool calls: maps (tool_name, args_json) -> result
    _tool_call_dedup: Dict[str, Any] = field(default_factory=dict)


class BaseStreamingAgent(StreamingAgent):
    """
    Base class for streaming agents with authentication, tool execution, and synthesis.
    
    Subclasses should override:
    - pipeline_steps() - For PREDEFINED_PIPELINE strategy
    - process_tool_result() - For dynamic pipeline modification
    - _build_synthesis_context() - For custom synthesis context building
    """
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self.name = config.display_name
        
        # Ensure OpenAI environment is configured (lazy init for test support)
        _ensure_openai_env()
        
        # Use the agent's configured model (from definition), falling back to settings
        settings = get_settings()
        model_name = config.model or settings.default_model
        
        # Create synthesis model using the agent's configured model
        self.synthesis_model = OpenAIChatModel(
            model_name=model_name,
            provider="openai",
        )
        logger.info(f"Agent '{config.display_name}' using model: {model_name}")
        
        # Build model settings - only include max_tokens if explicitly set
        # If max_tokens is None, don't pass it so the model uses its natural limit
        model_settings: Dict[str, Any] = {}
        if config.max_tokens is not None:
            model_settings["max_tokens"] = config.max_tokens
        self._inject_thinking_settings(model_settings)
        
        # Create synthesis agent
        self.synthesis_agent = Agent(
            model=self.synthesis_model,
            system_prompt=config.synthesis_prompt or config.instructions,
            model_settings=model_settings if model_settings else None,
        )
    
    _THINKING_DISABLED_MODELS = {"fast", "test", "chat"}
    _FRONTIER_MODEL_PREFIXES = {"frontier", "claude", "gpt", "o1", "o3", "gemini"}
    _THINKING_BUDGET_TOKENS: Dict[str, int] = {
        "default": 512,
        "chat": 512,
        "agent": 1024,
        "complex": 2048,
        "research": 4096,
    }
    _DEFAULT_THINKING_BUDGET = 512

    _FRONTIER_EFFORT_MAP: Dict[str, str] = {
        "default": "medium",
        "chat": "medium",
        "complex": "high",
        "research": "high",
        "frontier": "high",
        "frontier-fast": "medium",
    }
    _DEFAULT_FRONTIER_EFFORT = "medium"

    def _should_disable_thinking(self) -> bool:
        """Whether to suppress Qwen3-style <think> reasoning for this agent's model."""
        model_name = (self.config.model or "").lower()
        if model_name in self._THINKING_DISABLED_MODELS:
            return True
        if self.config.instructions and "/no_think" in self.config.instructions:
            return True
        return False

    def _is_frontier_model(self) -> bool:
        """Whether the model routes to a frontier API (Claude, OpenAI, etc.)."""
        model_name = (self.config.model or "").lower()
        return any(model_name.startswith(p) for p in self._FRONTIER_MODEL_PREFIXES)

    def _inject_thinking_settings(self, model_settings: Dict[str, Any]) -> None:
        """Mutate *model_settings* to control thinking across all backends.

        - **MLX**: ``extra_body.max_thinking_tokens`` for the custom MLX server's
          ``ThinkingBudgetProcessor`` logits processor.
        - **vLLM**: ``extra_body.thinking_token_budget`` only when the server has
          ``--reasoning-config`` set (requires explicit opt-in).
        - **Frontier** (Claude/OpenAI): ``reasoning_effort`` which LiteLLM maps
          to the provider's native effort/reasoning parameter.
        """
        model_name = (self.config.model or "").lower()

        if self._is_frontier_model():
            logger.info(
                "Thinking settings [frontier]: model=%s — no thinking limits applied",
                model_name,
            )
            return

        if self._should_disable_thinking():
            backend = get_settings().llm_backend.lower()
            if backend in ("mlx", "vllm"):
                model_settings.setdefault("extra_body", {}).setdefault(
                    "chat_template_kwargs", {}
                )["enable_thinking"] = False
            logger.info(
                "Thinking settings [disabled]: model=%s backend=%s", model_name, backend,
            )
            return

        backend = get_settings().llm_backend.lower()
        budget = self._THINKING_BUDGET_TOKENS.get(
            model_name, self._DEFAULT_THINKING_BUDGET
        )
        extra = model_settings.setdefault("extra_body", {})

        if backend == "mlx":
            extra["max_thinking_tokens"] = budget
            logger.info(
                "Thinking settings [mlx]: model=%s budget=%d tokens",
                model_name, budget,
            )
        elif backend == "vllm":
            # vLLM rejects thinking_token_budget unless the server was started
            # with --reasoning-config.  Skip it to avoid 503 errors.
            logger.info(
                "Thinking settings [vllm]: model=%s — skipping thinking_token_budget "
                "(server requires --reasoning-config)",
                model_name,
            )
        else:
            extra["max_thinking_tokens"] = budget
            logger.info(
                "Thinking settings [%s]: model=%s budget=%d tokens",
                backend or "unknown", model_name, budget,
            )

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        Define the pipeline steps to execute.
        
        Override in subclasses to define a predefined pipeline.
        Default implementation returns empty list (for LLM_DRIVEN strategy).
        
        Args:
            query: The user's query
            context: Current execution context
            
        Returns:
            List of PipelineStep objects to execute
        """
        return []
    
    async def process_tool_result(
        self, 
        step: PipelineStep, 
        result: Any, 
        context: AgentContext
    ) -> List[PipelineStep]:
        """
        Process a tool result and optionally add more pipeline steps.
        
        Override in subclasses to implement dynamic pipelines (e.g., web search
        that adds scrape steps based on search results).
        
        Args:
            step: The pipeline step that was executed
            result: The result from the tool
            context: Current execution context
            
        Returns:
            List of additional PipelineStep objects to add to the pipeline
        """
        return []
    
    async def should_continue(self, context: AgentContext) -> bool:
        """
        Check if execution should continue for another iteration.
        
        Override in subclasses for custom exit conditions.
        
        Args:
            context: Current execution context
            
        Returns:
            True if another iteration should run, False to stop
        """
        if self.config.execution_mode == ExecutionMode.RUN_ONCE:
            return False
        
        if self.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS:
            return context.iteration < self.config.max_iterations
        
        # RUN_UNTIL_DONE - subclass should override with LLM-based check
        return False
    
    async def run(
        self, 
        query: str, 
        deps: Any = None,
        context: Optional[dict] = None,
    ) -> Any:
        """
        Backward-compatible run method for legacy code.
        
        Runs the agent without streaming, collecting output directly.
        This allows BaseStreamingAgent to be used where PydanticAI agents
        were previously expected.
        
        Args:
            query: The user's query
            deps: Optional dependencies (ignored, for API compatibility)
            context: Optional context dict with principal, session, etc.
            
        Returns:
            A result-like object with .data attribute containing the output
        """
        # Debug: Log entry into run() method
        logger.info(
            f"{self.name}.run() called: query_len={len(query)}, "
            f"has_deps={deps is not None}, has_context={context is not None}"
        )
        if context:
            logger.info(
                f"{self.name}.run() context keys: {list(context.keys())}, "
                f"has_principal={'principal' in context}, "
                f"has_session={'session' in context}"
            )
        
        # Create a simple result collector
        collected_content = []
        collected_events = []
        
        async def collect_stream(event: StreamEvent):
            collected_events.append(event)
            if event.type == "content":
                collected_content.append(event.message)
        
        cancel = asyncio.Event()
        result = await self.run_with_streaming(
            query, collect_stream, cancel, context=context or {}
        )
        
        # Debug: Log collected events summary
        event_types = {}
        for e in collected_events:
            event_types[e.type] = event_types.get(e.type, 0) + 1
        logger.info(
            f"{self.name}.run() completed: collected_content_len={len(collected_content)}, "
            f"event_summary={event_types}, result_len={len(result) if result else 0}"
        )
        
        # Return a result-like object for backward compatibility
        class AgentResult:
            def __init__(self, data):
                self.data = data
                self.output = data
        
        return AgentResult("".join(collected_content) if collected_content else result)
    
    async def run_with_streaming(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        """
        Execute the agent with real-time streaming of progress.
        
        Args:
            query: The user's query
            stream: Callback to stream events to the user
            cancel: Event to signal cancellation
            context: Optional context dict (principal, session, etc.)
            
        Returns:
            Final output string
        """
        t0 = time.monotonic()
        logger.info(
            f"{self.name}.run_with_streaming started",
            extra={"query_preview": query[:80], "strategy": self.config.tool_strategy.value},
        )
        
        # Setup execution context (includes fetching relevant insights)
        t_ctx = time.monotonic()
        agent_context = await self._setup_context(context, stream, query)
        if agent_context is None:
            return "Authentication or session error. Please sign in and try again."
        agent_context.current_query = query
        logger.info(f"{self.name} context setup: {round((time.monotonic() - t_ctx) * 1000)}ms")

        t_att = time.monotonic()
        await self._resolve_attachments(query, stream, agent_context)
        att_ms = round((time.monotonic() - t_att) * 1000)
        if att_ms > 50:
            logger.info(f"{self.name} attachment resolution: {att_ms}ms")
        
        if cancel.is_set():
            return ""
        
        # Execute based on strategy
        try:
            t_exec = time.monotonic()
            if self.config.tool_strategy == ToolStrategy.LLM_DRIVEN:
                await self._execute_llm_driven(query, stream, cancel, agent_context)
            else:
                await self._execute_pipeline(query, stream, cancel, agent_context)
            logger.info(
                f"{self.name} execution phase complete",
                extra={
                    "elapsed_ms": round((time.monotonic() - t_exec) * 1000),
                    "strategy": self.config.tool_strategy.value,
                    "tool_results_keys": list(agent_context.tool_results.keys()),
                }
            )
        except Exception as e:
            logger.error(f"Agent execution error: {e}", exc_info=True)
            await stream(error(
                source=self.name,
                message=f"Error during execution: {str(e)}"
            ))
            return f"I encountered an error: {str(e)}"
        
        if cancel.is_set():
            return ""
        
        # For programmatic structured-output calls, skip synthesis entirely.
        # The structured output stored in tool_results IS the final result.
        if agent_context.response_schema is not None:
            raw = agent_context.tool_results.get("llm_response", "")
            if raw:
                await stream(content(source=self.name, message=raw))
                logger.info(
                    f"{self.name} structured output returned directly (no synthesis)",
                    extra={
                        "total_ms": round((time.monotonic() - t0) * 1000),
                        "result_length": len(raw),
                    },
                )
                return raw
            else:
                err_msg = "Structured output call produced no result"
                logger.error(f"{self.name}: {err_msg}")
                await stream(error(source=self.name, message=err_msg))
                return ""
        
        # Synthesize final response
        t_synth = time.monotonic()
        result = await self._synthesize(query, stream, cancel, agent_context)
        logger.info(
            f"{self.name} total request complete",
            extra={
                "total_ms": round((time.monotonic() - t0) * 1000),
                "synthesis_ms": round((time.monotonic() - t_synth) * 1000),
                "result_length": len(result) if result else 0,
            }
        )
        return result
    
    async def _setup_context(
        self, 
        context: Optional[dict], 
        stream: StreamCallback,
        query: Optional[str] = None,
    ) -> Optional[AgentContext]:
        """
        Setup execution context including authentication, dependencies, and insights.
        
        Args:
            context: Raw context dict from dispatcher, may include:
                - principal: Auth principal
                - session: DB session
                - user_id: User ID
                - conversation_history: List of past messages
                - relevant_insights: List of relevant user insights (fetched by dispatcher)
            stream: Stream callback for error reporting
            query: User's query (optional, kept for potential future use)
            
        Returns:
            AgentContext if successful, None if auth failed
        """
        agent_context = AgentContext()
        
        if context:
            agent_context.principal = context.get("principal")
            agent_context.session = context.get("session")
            agent_context.user_id = context.get("user_id")
            agent_context.agent_id = context.get("agent_id")
            agent_context.conversation_history = context.get("conversation_history", [])
            agent_context.missing_profile_fields = context.get("missing_profile_fields", []) or []
            agent_context.pending_questions = context.get("pending_questions", []) or []
            agent_context.insights_enabled = context.get("insights_enabled", True)
            agent_context.metadata = context.get("metadata") or {}
            agent_context.attachment_metadata = context.get("attachment_metadata", []) or []
            agent_context.response_schema = context.get("response_schema")
            agent_context.max_tokens = context.get("max_tokens")
            agent_context.images = context.get("images") or []

        # Check what scopes this agent's tools require
        scopes = self.config.get_required_scopes()
        requires_auth = len(scopes) > 0
        
        logger.info(
            f"{self.name} context setup: "
            f"principal={agent_context.principal is not None}, "
            f"has_token={agent_context.principal.token is not None if agent_context.principal else 'N/A'}, "
            f"session={agent_context.session is not None}, "
            f"user_id={agent_context.user_id}, "
            f"requires_auth={requires_auth}"
        )
        
        # Authentication is only required if the agent's tools need scopes
        if requires_auth:
            if not agent_context.principal or not agent_context.principal.token:
                logger.warning(
                    f"Missing authentication for {self.name}: "
                    f"principal={agent_context.principal is not None}, "
                    f"token={agent_context.principal.token is not None if agent_context.principal else 'no principal'}"
                )
                await stream(error(
                    source=self.name,
                    message="Authentication required. Please sign in."
                ))
                return None
            
            if not agent_context.session:
                logger.error(f"Missing database session for {self.name}")
                await stream(error(
                    source=self.name,
                    message="Internal error: missing database session."
                ))
                return None
        
        # Perform token exchange for tools that require scopes.
        # Different tools may target different downstream services (search-api,
        # data-api, etc.).  We group scopes by audience and exchange once per
        # audience so the BusiboxClient can send the right token to each service.
        if scopes and agent_context.principal and agent_context.session:
            try:
                from app.auth.tokens import _audience_for_purpose

                audience_scopes: Dict[str, List[str]] = {}
                for scope in scopes:
                    purpose = scope.split(".")[0]
                    aud = _audience_for_purpose(purpose, [scope])
                    audience_scopes.setdefault(aud, []).append(scope)

                tokens_by_audience: Dict[str, str] = {}
                first_token = None

                for aud, aud_scope_list in audience_scopes.items():
                    exchanged = await get_or_exchange_token(
                        session=agent_context.session,
                        principal=agent_context.principal,
                        scopes=aud_scope_list,
                        purpose=aud_scope_list[0].split(".")[0],
                    )
                    tokens_by_audience[aud] = exchanged.access_token
                    if first_token is None:
                        first_token = exchanged.access_token

                logger.info(
                    f"Token exchange successful for {self.name}",
                    extra={"audiences": list(tokens_by_audience.keys())},
                )

                busibox_client = BusiboxClient(
                    access_token=first_token,
                    tokens_by_audience=tokens_by_audience,
                )
                agent_context.deps = BusiboxDeps(
                    principal=agent_context.principal,
                    busibox_client=busibox_client,
                    metadata=agent_context.metadata,
                )
            except Exception as e:
                logger.error(f"Token exchange failed: {e}", exc_info=True)
                await stream(error(
                    source=self.name,
                    message=f"Authentication error: {str(e)}"
                ))
                return None
        
        # Compress conversation history if enabled and history is present
        if (self.config.enable_history_compression and
            agent_context.conversation_history):
            try:
                from app.services.context_compression import (
                    get_compression_service,
                )
                from app.schemas.definitions import ContextCompressionConfig

                compression_config = ContextCompressionConfig(
                    enabled=True,
                    compression_threshold_chars=self.config.compression_threshold_chars,
                    recent_messages_to_keep=self.config.recent_messages_to_keep,
                )

                raw_count = len(agent_context.conversation_history)
                raw_chars = sum(len(str(m.get("content", ""))) for m in agent_context.conversation_history)
                logger.info(
                    "%s context: %d messages, %d chars (threshold=%d)",
                    self.name, raw_count, raw_chars,
                    self.config.compression_threshold_chars,
                )

                t_compress = time.monotonic()
                compression_service = get_compression_service(compression_config)
                compression_result = await compression_service.compress_history(
                    agent_context.conversation_history
                )
                compress_ms = round((time.monotonic() - t_compress) * 1000)

                agent_context.compressed_history_summary = compression_result.summary
                agent_context.recent_messages = compression_result.recent_messages

                if compression_result.was_compressed:
                    extra = " (cache hit)" if compression_result.cache_hit else ""
                    logger.info(
                        "%s history compressed in %dms%s: %d->%d chars, %d compressed, %d kept",
                        self.name, compress_ms, extra,
                        compression_result.original_char_count,
                        compression_result.compressed_char_count,
                        compression_result.messages_compressed,
                        compression_result.messages_kept,
                    )
                else:
                    logger.info(
                        "%s history below threshold after filtering, no LLM call (%dms)",
                        self.name, compress_ms,
                    )

            except Exception as e:
                logger.warning(
                    "%s compression failed, using full history: %s",
                    self.name, e,
                )
                agent_context.recent_messages = agent_context.conversation_history
        else:
            agent_context.recent_messages = agent_context.conversation_history
        
        # Get relevant insights from context (passed by dispatcher)
        # The dispatcher fetches these based on the query before calling the agent
        if context:
            agent_context.relevant_insights = context.get("relevant_insights", [])
            if agent_context.relevant_insights:
                logger.info(
                    f"{self.name} received {len(agent_context.relevant_insights)} relevant insights from dispatcher"
                )

        # Discover and register tools from MCP servers (lazy, first-run only)
        if self.config.mcp_servers:
            await self._register_mcp_tools()
        
        return agent_context

    async def _register_mcp_tools(self) -> None:
        """
        Discover tools from configured MCP servers and register them
        in the ToolRegistry so LLM-driven execution can find them.

        Called once during context setup. Skips servers whose tools are
        already registered.
        """
        client = get_mcp_client()

        for server_config in self.config.mcp_servers:
            try:
                tool_defs = await client.discover_tools(server_config)
                registered_count = 0

                for tool_def in tool_defs:
                    if ToolRegistry.has(tool_def.qualified_name):
                        continue

                    proxy_fn = build_mcp_tool_function(server_config, tool_def)
                    ToolRegistry.register(tool_def.qualified_name, proxy_fn)
                    registered_count += 1

                    if tool_def.qualified_name not in self.config.tools:
                        self.config.tools.append(tool_def.qualified_name)

                if registered_count > 0:
                    logger.info(
                        "%s registered %d MCP tools from '%s'",
                        self.name, registered_count, server_config.name,
                    )
            except Exception as e:
                logger.warning(
                    "%s failed to load MCP tools from '%s': %s",
                    self.name, server_config.name, e,
                )

    async def _resolve_attachments(
        self,
        query: str,
        stream: StreamCallback,
        agent_context: AgentContext,
    ) -> None:
        """Resolve uploaded attachments into prompt-ready content."""
        if not agent_context.attachment_metadata:
            return

        context_token_estimate = 0
        if agent_context.compressed_history_summary:
            context_token_estimate += len(agent_context.compressed_history_summary) // 4
        if agent_context.recent_messages:
            context_token_estimate += sum(
                len(str(m.get("content", ""))) // 4 for m in agent_context.recent_messages
            )
        if agent_context.relevant_insights:
            context_token_estimate += sum(
                len(str(i.get("content", ""))) // 4 for i in agent_context.relevant_insights
            )

        try:
            agent_context.resolved_attachments = await attachment_resolver.resolve(
                query=query,
                attachment_metadata=agent_context.attachment_metadata,
                principal=agent_context.principal,
                user_id=agent_context.user_id,
                session=agent_context.session,
                stream=stream,
                context_token_estimate=context_token_estimate,
            )
        except Exception as exc:
            logger.warning("Attachment resolution failed entirely: %s", exc, exc_info=True)
            agent_context.resolved_attachments = []
            await stream(thought(
                source="attachments",
                message="Could not process attachments. Proceeding without them.",
            ))

    def _build_attachment_context_section(self, context: AgentContext) -> List[str]:
        """Render resolved attachment content for prompt injection."""
        if not context.resolved_attachments:
            return []

        parts: List[str] = []
        parts.append("## Attached Documents")
        parts.append("The user uploaded the following attachments for this request:")

        for idx, attachment in enumerate(context.resolved_attachments, start=1):
            filename = attachment.get("filename", f"attachment-{idx}")
            source_kind = attachment.get("source_kind", "document")
            parts.append(f"\n### Attachment {idx}: {filename} ({source_kind})")

            if source_kind == "image":
                image_url = attachment.get("image_url")
                if image_url:
                    parts.append(f"Image URL: {image_url}")
                else:
                    parts.append("Image attachment provided (no URL available).")
                continue

            if source_kind == "early_chunks":
                pages_proc = attachment.get("pages_processed")
                total_pages = attachment.get("total_pages")
                if pages_proc and total_pages:
                    parts.append(
                        f"**Note: This document is still being processed. "
                        f"The content below covers approximately {pages_proc} of "
                        f"{total_pages} pages. Your answer may be incomplete — "
                        f"the full document will be available shortly for follow-up questions.**"
                    )
                else:
                    parts.append(
                        "**Note: This document is still being processed. "
                        "Only partial content is available below. Your answer may be "
                        "incomplete — the full document will be available shortly for "
                        "follow-up questions.**"
                    )

            att_content = attachment.get("content", "")
            if isinstance(att_content, str) and att_content.strip():
                parts.append(att_content.strip())
            else:
                parts.append("No extracted text content available.")

        parts.append("")
        return parts
    
    async def _execute_pipeline(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> None:
        """
        Execute tools according to predefined pipeline.
        
        Args:
            query: User's query
            stream: Stream callback
            cancel: Cancellation event
            context: Execution context
        """
        # Get initial pipeline steps
        steps = self.pipeline_steps(query, context)
        
        pending_steps = list(steps)
        
        # Debug: Log tool registry state
        registered_tools = list(ToolRegistry._tools.keys())
        logger.info(
            f"{self.name} pipeline: {len(steps)} initial steps, "
            f"strategy={self.config.tool_strategy.value}, "
            f"registered_tools={registered_tools}"
        )
        if steps:
            logger.info(f"{self.name} pipeline steps: {[s.tool for s in steps]}")
            # Verify all required tools are registered
            for step in steps:
                if not ToolRegistry.has(step.tool):
                    logger.error(f"TOOL NOT REGISTERED: {step.tool} - available: {registered_tools}")
        
        while pending_steps:
            if cancel.is_set():
                return
            
            context.iteration += 1
            
            if self.config.tool_strategy == ToolStrategy.PARALLEL:
                # Execute all pending steps in parallel
                await self._execute_steps_parallel(pending_steps, stream, cancel, context)
                pending_steps = []
            else:
                # Execute one step at a time (SEQUENTIAL or PREDEFINED_PIPELINE)
                step = pending_steps.pop(0)
                result = await self._execute_step(step, stream, cancel, context)
                
                # Check for dynamic steps
                if result is not None:
                    additional_steps = await self.process_tool_result(step, result, context)
                    pending_steps.extend(additional_steps)
            
            # Check exit condition
            if not await self.should_continue(context):
                break
    
    async def _execute_steps_parallel(
        self,
        steps: List[PipelineStep],
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> None:
        """Execute multiple pipeline steps in parallel with concurrency cap."""
        from app.config.settings import get_settings
        _limit = get_settings().tool_parallel_limit
        sem = asyncio.Semaphore(_limit)

        async def _bounded(step: PipelineStep) -> Optional[Any]:
            async with sem:
                return await self._execute_step(step, stream, cancel, context)

        tasks = [_bounded(step) for step in steps]
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_step(
        self,
        step: PipelineStep,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> Optional[Any]:
        """
        Execute a single pipeline step.
        
        Args:
            step: Pipeline step to execute
            stream: Stream callback
            cancel: Cancellation event
            context: Execution context
            
        Returns:
            Tool result if successful, None on error
        """
        if cancel.is_set():
            return None
        
        # Check condition if present
        if step.condition and not step.condition(context.tool_results):
            logger.debug(f"Skipping step {step.tool} - condition not met")
            return None
        
        # Get tool function
        tool_func = ToolRegistry.get(step.tool)
        if not tool_func:
            logger.error(f"Tool not found: {step.tool}")
            await stream(error(
                source=self.name,
                message=f"Tool not found: {step.tool}"
            ))
            return None
        
        # Stream tool start
        await stream(tool_start(
            source=step.tool,
            message=f"Executing {step.tool}...",
            data={"args": step.args}
        ))
        
        try:
            # Filter planned/tool args to only what the tool function accepts.
            # This prevents planner-injected keys (e.g. user_id) from crashing tools.
            tool_sig = inspect.signature(tool_func)
            accepted_args = set(tool_sig.parameters.keys())
            # Context-aware tools receive ctx separately; never pass it from step args.
            accepted_args.discard("ctx")
            filtered_args = {k: v for k, v in step.args.items() if k in accepted_args}
            dropped_args = sorted(set(step.args.keys()) - set(filtered_args.keys()))
            if dropped_args:
                logger.info(
                    "Filtered unsupported tool args for %s: %s",
                    step.tool,
                    dropped_args,
                )

            # Validate that all required positional args are present.
            # The planner LLM can produce steps with missing args; catch
            # that here with a clear message instead of a Python TypeError.
            missing_required = []
            for param_name, param in tool_sig.parameters.items():
                if param_name == "ctx":
                    continue
                if (
                    param.default is inspect.Parameter.empty
                    and param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
                    and param_name not in filtered_args
                ):
                    missing_required.append(param_name)
            if missing_required:
                logger.warning(
                    "Skipping tool %s: planner did not provide required args %s (provided: %s)",
                    step.tool,
                    missing_required,
                    sorted(filtered_args.keys()),
                )
                await stream(error(
                    source=step.tool,
                    message=f"Skipped {step.tool}: missing required parameters {missing_required}",
                ))
                return None

            # Execute tool with timeout protection
            # Detect whether the tool function expects a RunContext `ctx`.
            tool_class = TOOL_CLASSES.get(step.tool, TOOL_CLASS_DEFAULT)
            tool_timeout_seconds = tool_class["timeout"]
            expects_ctx = "ctx" in tool_sig.parameters
            tool_scopes = TOOL_SCOPES.get(step.tool, [])
            logger.info(f"Executing tool {step.tool} with scopes={tool_scopes}, has_deps={context.deps is not None}")
            
            async def _run_tool():
                if expects_ctx and context.deps:
                    deps_for_tool = context.deps
                    if tool_scopes and context.session and context.principal:
                        try:
                            purpose = tool_scopes[0].split(".")[0] if tool_scopes else "search"
                            exchanged_token = await get_or_exchange_token(
                                session=context.session,
                                principal=context.principal,
                                scopes=tool_scopes,
                                purpose=purpose,
                            )
                            deps_for_tool = BusiboxDeps(
                                principal=context.principal,
                                busibox_client=BusiboxClient(access_token=exchanged_token.access_token),
                                metadata=context.deps.metadata if context.deps else {},
                            )
                        except Exception as exc:
                            logger.warning(
                                "Per-tool token exchange failed for %s, falling back to shared deps token: %s",
                                step.tool,
                                exc,
                            )
                    class MockRunContext:
                        def __init__(self, deps):
                            self.deps = deps
                    mock_ctx = MockRunContext(deps_for_tool)
                    return await tool_func(ctx=mock_ctx, **filtered_args)
                elif expects_ctx and not context.deps:
                    raise RuntimeError(f"Tool {step.tool} requires authenticated context")
                else:
                    return await tool_func(**filtered_args)

            try:
                result = await asyncio.wait_for(_run_tool(), timeout=tool_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error(f"Tool {step.tool} timed out after {tool_timeout_seconds}s")
                await stream(error(
                    source=step.tool,
                    message=f"Tool {step.tool} timed out after {tool_timeout_seconds}s"
                ))
                return None
            
            # Log result details for debugging
            result_count = getattr(result, 'result_count', None)
            if result_count is not None:
                logger.info(f"Tool {step.tool} completed with {result_count} results")
            else:
                logger.info(f"Tool {step.tool} completed, result type: {type(result).__name__}")
            
            # Store result
            context.tool_results[step.tool] = result
            
            # Stream tool result
            result_data = result.model_dump() if hasattr(result, 'model_dump') else str(result)
            await stream(tool_result(
                source=step.tool,
                message=self._format_tool_result_message(step.tool, result),
                data=result_data if isinstance(result_data, dict) else {"result": result_data}
            ))
            
            return result
            
        except Exception as e:
            logger.error(f"Tool execution error for {step.tool}: {e}", exc_info=True)
            await stream(error(
                source=step.tool,
                message=f"Tool error: {str(e)}"
            ))
            return None
    
    def _format_tool_result_message(self, tool_name: str, result: Any) -> str:
        """Format a human-readable message for tool results."""
        if hasattr(result, 'error') and getattr(result, 'error'):
            return f"Failed: {getattr(result, 'error')}"
        if hasattr(result, 'total') and isinstance(getattr(result, 'total'), int):
            return f"Found **{getattr(result, 'total')} documents**"
        if hasattr(result, 'result_count'):
            return f"Found **{result.result_count} results**"
        if hasattr(result, 'found') and not result.found:
            return "No results found"
        if hasattr(result, 'success') and result.success:
            return "Successfully completed"
        if hasattr(result, 'success') and not result.success:
            return f"Failed: {getattr(result, 'error', 'Unknown error')}"
        return "Completed"
    
    async def _execute_llm_driven(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> None:
        """
        Execute tools with LLM deciding which tools to call.
        
        For LLM_DRIVEN strategy, we use PydanticAI's native tool calling.
        Conversation history is passed via message_history for proper multi-turn context.
        """
        
        # For deterministic workflow/programmatic calls, disable tool usage and
        # force structured output via response_schema.
        force_structured_output = context.response_schema is not None

        # Get tool functions for this agent, wrapped with result truncation
        # to prevent large tool outputs from exceeding the LLM context window
        tools = []
        if not force_structured_output:
            for tool_name in self.config.tools:
                tool_func = ToolRegistry.get(tool_name)
                if tool_func:
                    wrapped_tool = _wrap_tool_with_truncation(tool_func)

                    @functools.wraps(wrapped_tool)
                    async def monitored_tool(*args, _tool_name=tool_name, _tool=wrapped_tool, **kwargs):
                        if cancel.is_set():
                            return ""
                        input_payload: Dict[str, Any]
                        if kwargs:
                            input_payload = dict(kwargs)
                        elif args:
                            input_payload = {"args": [str(a) for a in args]}
                        else:
                            input_payload = {}

                        # Deduplication: skip if identical call already made
                        dedup_key = f"{_tool_name}:{json.dumps(input_payload, sort_keys=True, default=str)}"
                        if dedup_key in context._tool_call_dedup:
                            logger.info(f"Tool {_tool_name} dedup hit, returning cached result")
                            return context._tool_call_dedup[dedup_key]

                        await stream(tool_start(
                            source=_tool_name,
                            message=f"Using {_tool_name}...",
                            data={
                                "tool_name": _tool_name,
                                "input": input_payload,
                            },
                        ))
                        tool_class = TOOL_CLASSES.get(_tool_name, TOOL_CLASS_DEFAULT)
                        timeout_s = tool_class["timeout"]
                        t_tool = time.monotonic()
                        try:
                            result = await asyncio.wait_for(
                                _tool(*args, **kwargs), timeout=timeout_s
                            )
                            tool_ms = round((time.monotonic() - t_tool) * 1000)
                            logger.info(
                                f"Tool {_tool_name} complete",
                                extra={"elapsed_ms": tool_ms},
                            )
                            context.tool_results[_tool_name] = result
                            context._tool_call_dedup[dedup_key] = result
                            result_data = (
                                result.model_dump()
                                if hasattr(result, "model_dump")
                                else {"result": str(result)}
                            )
                            if not isinstance(result_data, dict):
                                result_data = {"result": str(result_data)}
                            await stream(tool_result(
                                source=_tool_name,
                                message=self._format_tool_result_message(_tool_name, result),
                                data=result_data,
                            ))
                            return result
                        except asyncio.TimeoutError:
                            tool_ms = round((time.monotonic() - t_tool) * 1000)
                            logger.error(
                                f"Tool {_tool_name} timed out after {timeout_s}s ({tool_ms}ms elapsed)"
                            )
                            await stream(error(
                                source=_tool_name,
                                message=f"{_tool_name} timed out after {timeout_s}s",
                            ))
                            raise RuntimeError(f"{_tool_name} timed out after {timeout_s}s")
                        except Exception as e:
                            logger.error(
                                f"Tool {_tool_name} failed after {round((time.monotonic() - t_tool) * 1000)}ms: {e}",
                            )
                            await stream(error(
                                source=_tool_name,
                                message=f"{_tool_name} failed: {str(e)}",
                            ))
                            raise

                    monitored_tool.__name__ = tool_name
                    monitored_tool.__qualname__ = tool_name
                    monitored_tool.__signature__ = inspect.signature(wrapped_tool)
                    monitored_tool.__annotations__ = getattr(wrapped_tool, "__annotations__", {})
                    tools.append(monitored_tool)
        
        if not tools:
            # No tools configured - run as conversational agent without tool capabilities
            logger.info(f"No tools configured for {self.name}, running as conversational agent")

            # Build enriched system prompt and multi-turn message history
            enriched_system_prompt = self._build_enriched_system_prompt(context)
            msg_history = self._build_message_history(context, enriched_system_prompt)

            # Build plain-dict history for direct-client structured output paths
            plain_history: Optional[List[Dict[str, str]]] = None
            if context.recent_messages or context.compressed_history_summary:
                plain_history = []
                if context.compressed_history_summary:
                    plain_history.append({
                        "role": "user",
                        "content": f"Summary of earlier conversation:\n{context.compressed_history_summary}",
                    })
                    plain_history.append({
                        "role": "assistant",
                        "content": "Understood, I have the conversation context.",
                    })
                if context.recent_messages:
                    for m in context.recent_messages:
                        if m.get("content"):
                            plain_history.append({"role": m["role"], "content": m["content"]})

            # When a response_schema is provided, use PydanticAI NativeOutput
            # with a dynamically-built Pydantic model.  This sends
            # response_format to LiteLLM (enforced by vLLM+Outlines in prod)
            # AND validates the response with Pydantic (retry on mismatch).
            # Falls back to the direct OpenAI call if model conversion fails.
            if context.response_schema:
                t_struct = time.monotonic()
                try:
                    structured_output = await self._run_native_structured_output(
                        query=query,
                        context=context,
                        message_history=plain_history,
                        system_prompt_override=enriched_system_prompt,
                    )
                    logger.info(
                        f"{self.name} structured output call complete",
                        extra={
                            "elapsed_ms": round((time.monotonic() - t_struct) * 1000),
                            "output_length": len(structured_output) if structured_output else 0,
                        },
                    )
                    context.tool_results["llm_response"] = structured_output
                except Exception as e:
                    logger.error(
                        f"{self.name} NativeOutput structured output failed: {e}",
                        exc_info=True,
                    )
                    try:
                        logger.info(f"{self.name} attempting direct OpenAI structured output fallback")
                        structured_output = await self._call_structured_output(
                            prompt=query,
                            system_prompt=enriched_system_prompt,
                            response_schema=context.response_schema,
                            max_tokens=context.max_tokens or self.config.max_tokens,
                            message_history=plain_history,
                        )
                        context.tool_results["llm_response"] = structured_output
                    except Exception as fallback_err:
                        logger.error(
                            f"{self.name} all structured output paths failed: {fallback_err}",
                            exc_info=True,
                        )
                        await stream(error(
                            source=self.name,
                            message=f"Structured output failed: {str(fallback_err)}"
                        ))
                return

            # Build model settings - only include max_tokens if explicitly set
            model_settings: Dict[str, Any] = {}
            runtime_max_tokens = context.max_tokens if context.max_tokens is not None else self.config.max_tokens
            if runtime_max_tokens is not None:
                model_settings["max_tokens"] = runtime_max_tokens
            
            # Disable LiteLLM context_window_fallbacks unless agent opts in
            if not self.config.allow_frontier_fallback:
                model_settings.setdefault("extra_body", {})["disable_fallbacks"] = True

            self._inject_thinking_settings(model_settings)
            
            # Create agent without tools for pure conversation
            agent_kwargs: Dict[str, Any] = {
                "model": self.synthesis_model,
                "system_prompt": enriched_system_prompt,
                "model_settings": model_settings if model_settings else None,
            }
            if self.config.output_type is not None:
                agent_kwargs["output_type"] = self.config.output_type
            agent = Agent(**agent_kwargs)
            
            # Stream events in real-time so thinking/content arrive incrementally
            try:
                t_conv = time.monotonic()
                full_text = await self._stream_llm_events(
                    agent, query, context, stream, cancel,
                    model_settings=model_settings if model_settings else None,
                    message_history=msg_history,
                )
                logger.info(
                    f"{self.name} conversational LLM streaming complete",
                    extra={"elapsed_ms": round((time.monotonic() - t_conv) * 1000)},
                )
                context.tool_results["llm_response"] = full_text
                
            except Exception as e:
                logger.error(f"Conversational agent error after {round((time.monotonic() - t_conv) * 1000)}ms: {e}", exc_info=True)
                await stream(error(
                    source=self.name,
                    message=f"Error: {str(e)}"
                ))
            return
        
        # Build model settings - only include max_tokens if explicitly set
        model_settings = {}
        runtime_max_tokens = context.max_tokens if context.max_tokens is not None else self.config.max_tokens
        if runtime_max_tokens is not None:
            model_settings["max_tokens"] = runtime_max_tokens
        
        # Disable LiteLLM context_window_fallbacks unless agent opts in
        if not self.config.allow_frontier_fallback:
            model_settings.setdefault("extra_body", {})["disable_fallbacks"] = True

        self._inject_thinking_settings(model_settings)

        if context.response_schema:
            model_settings.setdefault("extra_body", {})["response_format"] = {
                "type": "json_schema",
                "json_schema": context.response_schema,
            }
        
        # Build enriched system prompt and multi-turn message history
        enriched_system_prompt = self._build_enriched_system_prompt(context)
        msg_history = self._build_message_history(context, enriched_system_prompt)

        # Create agent with tools
        agent_kwargs: Dict[str, Any] = {
            "model": self.synthesis_model,
            "tools": tools,
            "system_prompt": enriched_system_prompt,
            "model_settings": model_settings if model_settings else None,
        }
        if self.config.output_type is not None and not context.response_schema:
            agent_kwargs["output_type"] = self.config.output_type
        agent = Agent(**agent_kwargs)
        
        logger.info(
            f"{self.name} LLM-driven execution starting",
            extra={
                "insights_count": len(context.relevant_insights),
                "has_summary": context.compressed_history_summary is not None,
                "recent_messages_count": len(context.recent_messages),
                "history_messages": len(msg_history) if msg_history else 0,
                "tool_count": len(tools),
                "tool_names": [getattr(t, "__name__", str(t)) for t in tools],
            }
        )
        
        # Stream events in real-time: thinking blocks, tool calls, and
        # content arrive incrementally instead of waiting for full completion.
        # Track active LLM requests for load-aware fallback.
        from app.services.load_monitor import get_load_monitor
        _lm = get_load_monitor()
        _purpose = self.config.model or "agent"
        await _lm.acquire(_purpose)
        try:
            t_llm = time.monotonic()
            full_text = await self._stream_llm_events(
                agent, query, context, stream, cancel,
                model_settings=model_settings if model_settings else None,
                message_history=msg_history,
                usage_limits=UsageLimits(tool_calls_limit=30),
            )
            logger.info(
                f"{self.name} LLM streaming execution complete",
                extra={"elapsed_ms": round((time.monotonic() - t_llm) * 1000)},
            )
            context.tool_results["llm_response"] = full_text
            
        except Exception as e:
            logger.error(
                f"LLM-driven execution error: {e}",
                extra={"elapsed_ms": round((time.monotonic() - t_llm) * 1000)},
                exc_info=True,
            )
            await stream(error(
                source=self.name,
                message=f"Error: {str(e)}"
            ))
        finally:
            await _lm.release(_purpose)
    
    async def _stream_llm_events(
        self,
        agent: Agent,
        prompt: str,
        context: "AgentContext",
        stream: StreamCallback,
        cancel: asyncio.Event,
        *,
        model_settings: Optional[Dict[str, Any]] = None,
        message_history: Optional[List[ModelMessage]] = None,
        usage_limits: Optional[UsageLimits] = None,
    ) -> str:
        """Stream LLM execution events in real-time via run_stream_events().

        Handles thinking blocks (<think> tags), tool calls, and content deltas
        as they arrive from the model rather than waiting for full completion.

        When *message_history* is provided it is forwarded to pydantic-ai so
        the LLM receives proper multi-turn user/assistant messages instead of a
        flattened context string.

        When *usage_limits* is provided it caps the number of tool invocations
        pydantic-ai will allow before forcing a final text response.

        Returns the final text output (with think tags stripped).
        """
        full_text = ""
        has_output_type = self.config.output_type is not None

        # The Qwen3-family chat templates (Qwen3.5/Qwen3.6) inject <think>\n
        # into the prompt when enable_thinking=True, so the model starts
        # generating thinking content immediately without emitting an opening
        # <think> tag.
        # Frontier models (Claude, OpenAI) don't use <think> tags.
        # When tools are active (usage_limits set), the MLX server disables
        # thinking via enable_thinking=False, so the model won't emit thinking
        # content — start in content mode to avoid buffering the response.
        has_tools = usage_limits is not None
        starts_in_think = (
            not has_tools
            and not self._should_disable_thinking()
            and not self._is_frontier_model()
        )

        # State machine for think-tag parsing across streaming chunks.
        in_think = starts_in_think
        # Pending buffer holds un-dispatched text that might contain a
        # partial tag (e.g. "</thi" waiting for "nk>").
        pending = ""
        think_chars_streamed = 0

        OPEN_TAG = "<think>"
        CLOSE_TAG = "</think>"
        THINK_STREAM_INTERVAL = 80

        async def _process_chunk(chunk: str, final: bool = False):
            """Process a text chunk, emitting thought or content events."""
            nonlocal in_think, pending, full_text, think_chars_streamed

            pending += chunk

            while pending:
                if in_think:
                    close_idx = pending.find(CLOSE_TAG)
                    if close_idx != -1:
                        think_content = pending[:close_idx].strip()
                        if think_content:
                            await stream(thought(
                                source=self.name,
                                message=think_content,
                                data={"phase": "model_reasoning", "streaming": True},
                            ))
                        pending = pending[close_idx + len(CLOSE_TAG):]
                        in_think = False
                        think_chars_streamed = 0
                        continue
                    else:
                        # No close tag yet — check for partial tag at end
                        safe_len = len(pending)
                        if not final:
                            for k in range(1, len(CLOSE_TAG)):
                                if pending.endswith(CLOSE_TAG[:k]):
                                    safe_len = len(pending) - k
                                    break

                        streamable = pending[:safe_len]
                        if streamable and (
                            len(streamable) - think_chars_streamed >= THINK_STREAM_INTERVAL
                            or final
                        ):
                            await stream(thought(
                                source=self.name,
                                message=streamable,
                                data={
                                    "phase": "model_reasoning",
                                    "streaming": True,
                                    "partial": not final,
                                },
                            ))
                            think_chars_streamed = len(streamable)

                        if safe_len < len(pending):
                            pending = pending[safe_len:]
                        else:
                            pending = "" if final else pending
                        break
                else:
                    # Strip stray </think> tags that leak through when the
                    # budget processor forces end-of-thinking.
                    close_stray = pending.find(CLOSE_TAG)
                    if close_stray != -1:
                        before = pending[:close_stray]
                        if before:
                            full_text += before
                            if not has_output_type:
                                await stream(content(
                                    source=self.name,
                                    message=before,
                                    data={"streaming": True, "partial": True},
                                ))
                        pending = pending[close_stray + len(CLOSE_TAG):]
                        continue

                    open_idx = pending.find(OPEN_TAG)
                    if open_idx != -1:
                        before = pending[:open_idx]
                        if before:
                            full_text += before
                            if not has_output_type:
                                await stream(content(
                                    source=self.name,
                                    message=before,
                                    data={"streaming": True, "partial": True},
                                ))
                        pending = pending[open_idx + len(OPEN_TAG):]
                        in_think = True
                        think_chars_streamed = 0
                        continue
                    else:
                        # No tags — check for partial tag at end
                        safe_len = len(pending)
                        if not final:
                            for k in range(1, max(len(OPEN_TAG), len(CLOSE_TAG))):
                                if pending.endswith(OPEN_TAG[:k]) or pending.endswith(CLOSE_TAG[:k]):
                                    safe_len = len(pending) - k
                                    break

                        text = pending[:safe_len]
                        if text:
                            full_text += text
                            if not has_output_type:
                                await stream(content(
                                    source=self.name,
                                    message=text,
                                    data={"streaming": True, "partial": True},
                                ))

                        pending = pending[safe_len:]
                        break

        run_kwargs: Dict[str, Any] = {
            "deps": context.deps,
            "model_settings": model_settings,
            "message_history": message_history,
        }
        if usage_limits is not None:
            run_kwargs["usage_limits"] = usage_limits

        async for event in agent.run_stream_events(prompt, **run_kwargs):
            if cancel.is_set():
                break

            if isinstance(event, PartStartEvent):
                # Tool start/result events are emitted by monitored_tool() in
                # _execute_llm_driven. Emitting them again here creates duplicate
                # timeline entries and can desync frontend pending-tool state.
                pass

            elif isinstance(event, PartDeltaEvent):
                delta = event.delta
                if isinstance(delta, TextPartDelta):
                    chunk = delta.content_delta
                    if not chunk:
                        continue
                    await _process_chunk(chunk)

            elif isinstance(event, FunctionToolResultEvent):
                # PydanticAI event schema can vary by version; avoid hard
                # dependence on tool_call_part to prevent runtime crashes.
                tool_name = (
                    getattr(event, "tool_name", None)
                    or getattr(getattr(event, "tool_call_part", None), "tool_name", None)
                    or getattr(getattr(event, "tool_call", None), "tool_name", None)
                    or "tool"
                )
                result_str = str(event.result.content) if hasattr(event.result, "content") else str(event.result)
                # monitored_tool() already emits tool_result; only fall back here
                # if the tool wasn't captured in the wrapped callback path.
                if tool_name not in context.tool_results:
                    context.tool_results[tool_name] = result_str
                    await stream(tool_result(
                        source=tool_name,
                        message=result_str[:500],
                        data={"result": result_str[:2000]},
                    ))

            elif isinstance(event, AgentRunResultEvent):
                output = event.result.output
                if has_output_type:
                    if hasattr(output, "model_dump"):
                        full_text = json.dumps(output.model_dump())
                    elif isinstance(output, (dict, list)):
                        full_text = json.dumps(output)
                    else:
                        full_text = str(output) if output else ""

        # Flush any remaining buffered content (including partial tags)
        if pending:
            await _process_chunk("", final=True)

        # Clean up any leftover think tags (paired and bare) in the final text
        final = _THINK_RE.sub("", full_text)
        final = _THINK_TAG_RE.sub("", final).strip()
        return final

    async def _run_native_structured_output(
        self,
        query: str,
        context: "AgentContext",
        *,
        message_history: Optional[List[Dict[str, str]]] = None,
        system_prompt_override: Optional[str] = None,
    ) -> str:
        """
        Run structured output by sending our exact JSON Schema to the LLM
        via ``_call_structured_output``.

        We bypass PydanticAI's NativeOutput because the Pydantic round-trip
        (JSON Schema → Pydantic model → re-serialized JSON Schema) produces
        a degraded schema: ``anyOf``/``null`` unions replace simple types,
        ``$ref``/``$defs`` indirection confuses local models, ``maxLength``
        and ``maxItems`` constraints are dropped, and ``additionalProperties``
        properties like ``_provenance`` disappear.  The direct OpenAI path
        sends our carefully-crafted schema verbatim and includes its own
        jsonschema validation + retry loop.
        """
        response_schema = context.response_schema
        assert response_schema is not None

        return await self._call_structured_output(
            prompt=query,
            system_prompt=system_prompt_override or self.config.instructions,
            response_schema=response_schema,
            max_tokens=context.max_tokens or self.config.max_tokens,
            message_history=message_history,
            images=context.images or None,
        )

    @staticmethod
    def _extract_json_from_response(content: str) -> str:
        """Extract clean JSON from an LLM response that may contain thinking
        tokens, markdown fences, or preamble text."""
        import re

        # 1) Strip Qwen3-style <think>...</think> reasoning blocks
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

        # 2) Try direct parse first
        try:
            json.loads(cleaned)
            return cleaned
        except (json.JSONDecodeError, ValueError):
            pass

        # 3) Extract from ```json ... ``` fenced code blocks
        fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", cleaned, flags=re.IGNORECASE)
        for block in fenced:
            block = block.strip()
            try:
                json.loads(block)
                return block
            except (json.JSONDecodeError, ValueError):
                continue

        # 4) Find the first { ... } or [ ... ] blob
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start_idx = cleaned.find(start_char)
            if start_idx == -1:
                continue
            end_idx = cleaned.rfind(end_char)
            if end_idx > start_idx:
                candidate = cleaned[start_idx : end_idx + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except (json.JSONDecodeError, ValueError):
                    continue

        return cleaned

    @staticmethod
    def _build_user_content(prompt: str, images: Optional[List[Dict[str, str]]]) -> Any:
        """Plain string when no images; OpenAI content-part list otherwise."""
        if not images:
            return prompt
        parts: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img in images:
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{img['media_type']};base64,{img['data']}"},
            })
        return parts

    @staticmethod
    def _fixup_arrays(data: Any, schema: Dict[str, Any]) -> Any:
        """Deduplicate and truncate arrays that exceed maxItems.

        Local LLMs (MLX/vLLM) often ignore maxItems and return duplicate
        records.  Rather than wasting a retry call, fix it up in-place.
        """
        if not isinstance(data, dict) or not isinstance(schema, dict):
            return data

        props = schema.get("properties", {})
        for key, prop_schema in props.items():
            if key not in data or not isinstance(prop_schema, dict):
                continue
            if prop_schema.get("type") == "array" and isinstance(data[key], list):
                max_items = prop_schema.get("maxItems")
                arr = data[key]
                if len(arr) > 1:
                    seen: list = []
                    for item in arr:
                        if item not in seen:
                            seen.append(item)
                    if len(seen) < len(arr):
                        arr = seen
                        data[key] = arr
                if max_items is not None and len(arr) > max_items:
                    data[key] = arr[:max_items]
                items_schema = prop_schema.get("items", {})
                if isinstance(items_schema, dict) and items_schema.get("type") == "object":
                    for item in data[key]:
                        if isinstance(item, dict):
                            BaseStreamingAgent._fixup_arrays(item, items_schema)
            elif prop_schema.get("type") == "object" and isinstance(data[key], dict):
                BaseStreamingAgent._fixup_arrays(data[key], prop_schema)

        return data

    async def _call_structured_output(
        self,
        prompt: str,
        system_prompt: str,
        response_schema: Dict[str, Any],
        max_tokens: Optional[int] = None,
        message_history: Optional[List[Dict[str, str]]] = None,
        images: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """
        Call the LLM directly via the OpenAI client with response_format enforced.

        Bypasses PydanticAI so response_format reaches LiteLLM as a first-class
        parameter rather than being tunnelled through extra_body.

        When *message_history* is provided the conversation turns are inserted
        between the system prompt and the current user prompt so the LLM sees
        proper multi-turn context.

        Prepends ``/no_think`` to suppress Qwen3 reasoning blocks.
        Falls back to ``_extract_json_from_response`` if the raw content
        contains stray ``<think>`` tags or markdown fences.

        After receiving the response, validates it against the JSON Schema and
        retries once on validation failure.
        """
        import jsonschema as _jsonschema
        from openai import AsyncOpenAI

        settings = get_settings()
        client = AsyncOpenAI(
            base_url=str(settings.litellm_base_url),
            api_key=settings.litellm_api_key,
        )

        model_name = self.config.model or settings.default_model
        schema_name = response_schema.get("name", "unknown")
        json_schema = response_schema.get("schema", response_schema)

        if max_tokens is None:
            max_tokens = await _get_model_max_output_tokens(model_name)

        effective_prompt = "/no_think\n" + prompt
        user_content = self._build_user_content(effective_prompt, images)

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if message_history:
            messages.extend(message_history)
        messages.append({"role": "user", "content": user_content})

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": response_schema,
            },
        }
        _so_backend = get_settings().llm_backend.lower()
        if _so_backend in ("mlx", "vllm"):
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": False},
            }

        max_attempts = 2
        last_error: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            logger.info(
                f"{self.name} structured output call (attempt {attempt}/{max_attempts})",
                extra={
                    "model": model_name,
                    "schema_name": schema_name,
                    "prompt_length": len(effective_prompt),
                },
            )

            response = await client.chat.completions.create(**kwargs)
            raw_content = response.choices[0].message.content or ""
            content = self._extract_json_from_response(raw_content)

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                last_error = f"Response is not valid JSON: {e}"
                logger.warning(
                    f"{self.name} structured output attempt {attempt} returned invalid JSON",
                    extra={"error": last_error, "content_preview": raw_content[:500]},
                )
                if attempt < max_attempts:
                    retry_messages: List[Dict[str, Any]] = [
                        {"role": "system", "content": system_prompt},
                    ]
                    if message_history:
                        retry_messages.extend(message_history)
                    retry_messages.extend([
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": raw_content},
                        {"role": "user", "content": (
                            "/no_think\n"
                            f"Your response was not valid JSON. Error: {last_error}\n"
                            "Please try again and return ONLY valid JSON matching the required schema."
                        )},
                    ])
                    kwargs["messages"] = retry_messages
                    continue
                raise ValueError(f"Structured output failed after {max_attempts} attempts: {last_error}")

            parsed = self._fixup_arrays(parsed, json_schema)
            content = json.dumps(parsed)

            try:
                _jsonschema.validate(instance=parsed, schema=json_schema)
            except _jsonschema.ValidationError as e:
                last_error = f"JSON does not match schema: {e.message} (at path: {'/'.join(str(p) for p in e.absolute_path)})"
                logger.warning(
                    f"{self.name} structured output attempt {attempt} failed schema validation",
                    extra={"error": last_error, "schema_name": schema_name},
                )
                if attempt < max_attempts:
                    retry_messages = [
                        {"role": "system", "content": system_prompt},
                    ]
                    if message_history:
                        retry_messages.extend(message_history)
                    retry_messages.extend([
                        {"role": "user", "content": user_content},
                        {"role": "assistant", "content": content},
                        {"role": "user", "content": (
                            "/no_think\n"
                            f"Your response did not match the required schema. Validation error: {last_error}\n"
                            "Please try again and return JSON that strictly conforms to the schema."
                        )},
                    ])
                    kwargs["messages"] = retry_messages
                    continue
                raise ValueError(f"Structured output failed after {max_attempts} attempts: {last_error}")

            if attempt > 1:
                logger.info(f"{self.name} structured output succeeded on retry (attempt {attempt})")
            return content

        raise ValueError(f"Structured output failed after {max_attempts} attempts: {last_error}")

    def _build_enriched_system_prompt(self, context: AgentContext) -> str:
        """
        Build an enriched system prompt combining the agent's base instructions
        with per-request context (skills, metadata, insights, attachments, etc.).

        This is set as Agent(system_prompt=...) so LLM providers can cache it
        as a stable prefix across turns.
        """
        parts = [self.config.instructions]

        from datetime import datetime as _dt, timezone as _tz
        now = _dt.now(_tz.utc)
        parts.append("")
        parts.append(f"## Current Date and Time")
        parts.append(f"Today is {now.strftime('%A, %B %d, %Y, %H:%M UTC')}.")
        parts.append("When the user refers to a month or time period without specifying a year, assume the current year unless context clearly indicates otherwise.")

        try:
            skills_prompt = get_skills_service().render_skills_prompt(context.principal)
            if skills_prompt:
                parts.append("")
                parts.append(skills_prompt)
        except Exception as e:
            logger.debug(f"Failed to render skills prompt: {e}")

        if context.metadata:
            parts.append("")
            parts.append("## Application Context")
            parts.append("The following metadata was provided by the calling application.")
            parts.append("IMPORTANT: Any key ending in 'DocumentId' is a UUID you MUST pass as the document_id parameter to query_data, aggregate_data, get_facets, etc. Do NOT invent document IDs.")
            for key, value in context.metadata.items():
                if str(key).endswith("DocumentId"):
                    parts.append(f"- **{key}** (use as document_id): `{value}`")
                else:
                    parts.append(f"- **{key}**: {value}")

        if context.insights_enabled:
            if context.relevant_insights:
                parts.append("")
                parts.append("## Relevant User Context (from past conversations)")
                parts.append("These are relevant facts, preferences, and context learned from the user's past conversations:")
                for insight in context.relevant_insights:
                    category = insight.get("category", "context")
                    ins_content = insight.get("content", "")
                    parts.append(f"- [{category}] {ins_content}")

            if context.missing_profile_fields:
                parts.append("")
                parts.append("## Missing Profile Context")
                parts.append("These profile details are still missing and may improve future assistance:")
                for field_name in context.missing_profile_fields:
                    parts.append(f"- {field_name}")

            if context.pending_questions:
                parts.append("")
                parts.append("## Pending Follow-up Questions")
                parts.append(
                    "RULES: Only ask ONE of these if it is DIRECTLY relevant to what the user is currently discussing. "
                    "Do NOT ask a profile question if the user's request is unrelated to it. "
                    "Do NOT interrupt the user's task with a profile question. "
                    "If the user is asking about a specific topic, answer that first — "
                    "only append a profile question at the end if it naturally fits the conversation."
                )
                for item in context.pending_questions[:3]:
                    question = str(item.get("content", "")).strip()
                    if question:
                        parts.append(f"- {question}")

            if context.missing_profile_fields:
                _onboarding_triggers = {"learn about me", "set up my profile", "personalize my experience"}
                all_user_msgs = [
                    str(msg.get("content", "")).lower()
                    for msg in (context.conversation_history or context.recent_messages or [])
                    if msg.get("role") == "user"
                ]
                if context.current_query:
                    all_user_msgs.append(context.current_query.lower())
                if any(any(t in m for t in _onboarding_triggers) for m in all_user_msgs[:5]):
                    parts.append("")
                    parts.append("## Onboarding Mode")
                    parts.append(
                        "The user has asked you to learn about them. Start a friendly, conversational "
                        "profile-building session. Ask about the following fields ONE AT A TIME, using "
                        "quick-reply buttons where possible. After each answer, acknowledge it and ask "
                        "the next question. Keep it brief and friendly."
                    )
                    parts.append("Missing profile fields: " + ", ".join(context.missing_profile_fields))

        if any(t in self.config.tools for t in ("document_search", "web_search")):
            parts.append("")
            parts.append("## Search Result Relevance and Citations")
            parts.append(
                "When you receive results from document_search or web_search, critically evaluate "
                "whether they are actually relevant to the user's query. If the results are about "
                "a completely different topic, IGNORE them — do not summarize or reference irrelevant "
                "results. Only use results that genuinely answer or inform the user's question."
            )
        if "document_search" in self.config.tools:
            parts.append("")
            parts.append("## Mandatory Document Citations")
            parts.append(
                "EVERY sentence or claim that uses information from document_search results MUST "
                "include an inline citation immediately after the claim. Use EXACTLY this markdown "
                "format and nothing else:\n"
                "  [Source: filename, p.N](doc:file_id:page_number)\n"
                "Example: \"The policy requires annual reviews. [Source: policy.pdf, p.3](doc:abc-123:3)\"\n"
                "Rules:\n"
                "- Use the citation_url provided in the tool result for each source.\n"
                "- Do NOT omit citations. Do NOT use bare URLs or vague references.\n"
                "- If you cite multiple sources in one sentence, add each citation inline.\n"
                "- If no relevant documents were found, say so clearly without fabricating citations."
            )

        attachment_section = self._build_attachment_context_section(context)
        if attachment_section:
            parts.append("")
            parts.extend(attachment_section)

        return "\n".join(parts)

    def _build_message_history(
        self,
        context: AgentContext,
        enriched_system_prompt: str,
    ) -> Optional[List[ModelMessage]]:
        """
        Convert compressed_history_summary + recent_messages into a proper
        pydantic-ai message_history list.

        Returns None when there is no conversation history (first message).

        Structure:
        - If a compressed summary exists, it becomes a synthetic user message
          at position 0 so the LLM sees early context.
        - Recent messages map to ModelRequest (user) / ModelResponse (assistant).
        - The first ModelRequest carries instructions= so the system prompt is
          included even when pydantic-ai skips auto-generation for non-empty
          message_history.
        """
        has_summary = bool(context.compressed_history_summary)
        has_recent = bool(context.recent_messages)

        if not has_summary and not has_recent:
            return None

        messages: List[ModelMessage] = []
        instructions_set = False

        if has_summary:
            summary_text = f"Summary of earlier conversation:\n{context.compressed_history_summary}"
            messages.append(ModelRequest(
                parts=[UserPromptPart(content=summary_text)],
                instructions=enriched_system_prompt,
            ))
            instructions_set = True
            messages.append(ModelResponse(
                parts=[TextPart(content="Understood, I have the conversation context.")],
            ))

        if has_recent:
            for msg in context.recent_messages:
                role = msg.get("role", "")
                msg_content = msg.get("content", "")
                if not msg_content:
                    continue

                if role == "user":
                    req_kwargs: Dict[str, Any] = {
                        "parts": [UserPromptPart(content=msg_content)],
                    }
                    if not instructions_set:
                        req_kwargs["instructions"] = enriched_system_prompt
                        instructions_set = True
                    messages.append(ModelRequest(**req_kwargs))
                elif role == "assistant":
                    messages.append(ModelResponse(
                        parts=[TextPart(content=msg_content)],
                    ))

        if not messages:
            return None

        if not instructions_set and messages:
            first = messages[0]
            if isinstance(first, ModelRequest):
                first.instructions = enriched_system_prompt

        return messages

    def _build_llm_driven_prompt(self, query: str, context: AgentContext) -> str:
        """
        Build a prompt that includes conversation history and insights for LLM-driven execution.

        .. deprecated::
            Use ``_build_enriched_system_prompt`` + ``_build_message_history``
            instead, which produce proper multi-turn messages rather than a
            flattened string.  This method is kept for backward compatibility
            with subclass overrides.
        """
        warnings.warn(
            "_build_llm_driven_prompt is deprecated; "
            "use _build_enriched_system_prompt + _build_message_history instead",
            DeprecationWarning,
            stacklevel=2,
        )
        parts = []

        # Add role-gated SKILL.md skills if enabled.
        try:
            skills_prompt = get_skills_service().render_skills_prompt(context.principal)
            if skills_prompt:
                parts.append(skills_prompt)
                parts.append("")
        except Exception as e:
            logger.debug(f"Failed to render skills prompt: {e}")
        
        # Add application metadata context if present (e.g. projectId, appName)
        if context.metadata:
            parts.append("## Application Context")
            parts.append("The following metadata was provided by the calling application. Use these values when making tool calls:")
            for key, value in context.metadata.items():
                parts.append(f"- **{key}**: {value}")
            parts.append("")
        
        if context.insights_enabled:
            if context.relevant_insights:
                parts.append("## Relevant User Context (from past conversations)")
                parts.append("These are relevant facts, preferences, and context learned from the user's past conversations:")
                for insight in context.relevant_insights:
                    category = insight.get("category", "context")
                    content = insight.get("content", "")
                    parts.append(f"- [{category}] {content}")
                parts.append("")

            if context.missing_profile_fields:
                parts.append("## Missing Profile Context")
                parts.append("These profile details are still missing and may improve future assistance:")
                for field_name in context.missing_profile_fields:
                    parts.append(f"- {field_name}")
                parts.append("")

            if context.pending_questions:
                parts.append("## Pending Follow-up Questions")
                parts.append("Ask at most ONE of these naturally when relevant, then continue helping with the current request:")
                for item in context.pending_questions[:3]:
                    question = str(item.get("content", "")).strip()
                    if question:
                        parts.append(f"- {question}")
                parts.append("")
        
        # Add compressed history summary if present
        if context.compressed_history_summary:
            parts.append("## Previous Conversation Summary")
            parts.append(context.compressed_history_summary)
            parts.append("")
        
        # Add recent conversation history
        if context.recent_messages:
            parts.append("## Recent Conversation")
            for msg in context.recent_messages:
                role = msg.get("role", "unknown")
                msg_content = msg.get("content", "")
                if role == "user":
                    parts.append(f"User: {msg_content}")
                elif role == "assistant":
                    parts.append(f"Assistant: {msg_content}")
            parts.append("")

        # Add resolved attachment content (if available)
        parts.extend(self._build_attachment_context_section(context))
        
        # Add the current query
        parts.append("## Current Query")
        parts.append(query)
        
        has_context = (
            context.recent_messages
            or context.compressed_history_summary
            or (context.insights_enabled and context.relevant_insights)
            or context.metadata
        )
        if has_context:
            parts.append("")
            parts.append("Please respond to the current query using all available context above. Use the conversation history to understand follow-up references (like 'it', 'that', 'this topic'), and make informed decisions about which tools to use.")
        
        return "\n".join(parts)
    
    async def _synthesize(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> str:
        """
        Synthesize final response from tool results.
        
        Args:
            query: User's query
            stream: Stream callback
            cancel: Cancellation event
            context: Execution context with tool results
            
        Returns:
            Final output string
        """
        if cancel.is_set():
            return ""
        
        # Check if we have any results
        if not context.tool_results:
            await stream(content(
                source=self.name,
                message="I couldn't find any relevant information.",
            ))
            return "I couldn't find any relevant information."
        
        # For LLM_DRIVEN, the response was already streamed incrementally
        # via _stream_llm_events — just return the collected text.
        if "llm_response" in context.tool_results:
            return str(context.tool_results["llm_response"])
        
        # Build synthesis context
        SYNTHESIS_TIMEOUT_SECONDS = 90
        synthesis_context = self._build_synthesis_context(query, context)
        
        await stream(progress(
            source=self.name,
            message="Generating response...",
            data={"phase": "synthesis"},
        ))
        await stream(thought(
            source=self.name,
            message="Synthesizing answer from results..."
        ))
        
        try:
            async def _run_synthesis():
                full_output = ""
                async with self.synthesis_agent.run_stream(synthesis_context) as result:
                    async for chunk in result.stream_text(delta=True):
                        if cancel.is_set():
                            break
                        full_output += chunk
                        await stream(content(
                            source=self.name,
                            message=chunk,
                            data={"streaming": True, "partial": True}
                        ))
                return full_output

            full_output = await asyncio.wait_for(
                _run_synthesis(), timeout=SYNTHESIS_TIMEOUT_SECONDS
            )
            
            # Send completion marker
            await stream(content(
                source=self.name,
                message="",
                data={
                    "streaming": False,
                    "partial": False,
                    "complete": True,
                }
            ))
            
            return full_output.strip()

        except asyncio.TimeoutError:
            logger.error(f"Synthesis timed out after {SYNTHESIS_TIMEOUT_SECONDS}s")
            await stream(error(
                source=self.name,
                message=f"Response generation timed out after {SYNTHESIS_TIMEOUT_SECONDS}s"
            ))
            fallback = self._build_fallback_response(query, context)
            await stream(content(source=self.name, message=fallback))
            return fallback
            
        except Exception as e:
            logger.error(f"Synthesis error: {e}", exc_info=True)
            await stream(error(
                source=self.name,
                message=f"Error synthesizing answer: {str(e)}"
            ))
            
            # Return fallback
            fallback = self._build_fallback_response(query, context)
            await stream(content(source=self.name, message=fallback))
            return fallback
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context string for synthesis.
        
        Includes:
        1. Relevant insights (agent memories from past conversations)
        2. Compressed history summary (if compression was performed)
        3. Recent conversation messages (kept in full)
        4. Current user query
        5. Tool results from current execution
        
        Override in subclasses for custom context building.
        
        Args:
            query: User's query
            context: Execution context with tool results
            
        Returns:
            Context string for synthesis agent
        """
        parts = []

        # Add role-gated SKILL.md skills if enabled.
        try:
            skills_prompt = get_skills_service().render_skills_prompt(context.principal)
            if skills_prompt:
                parts.append(skills_prompt)
                parts.append("")
        except Exception as e:
            logger.debug(f"Failed to render skills prompt: {e}")
        
        if context.insights_enabled:
            if context.relevant_insights:
                parts.append("## Relevant User Context (from past conversations)")
                parts.append("These are relevant facts, preferences, and context learned from the user's past conversations:")
                for insight in context.relevant_insights:
                    category = insight.get("category", "context")
                    content = insight.get("content", "")
                    parts.append(f"- [{category}] {content}")
                parts.append("")

            if context.missing_profile_fields:
                parts.append("## Missing Profile Context")
                parts.append("These details are missing and would improve personalization:")
                for field_name in context.missing_profile_fields:
                    parts.append(f"- {field_name}")
                parts.append("")

            if context.pending_questions:
                parts.append("## Optional Profile Follow-ups (low priority)")
                parts.append("After fully answering the user's request, you may optionally append ONE of these questions. NEVER replace or skip the user's request to ask these instead:")
                for item in context.pending_questions[:3]:
                    question = str(item.get("content", "")).strip()
                    if question:
                        parts.append(f"- {question}")
                parts.append("")
        
        # 2. Add compressed history summary if present
        if context.compressed_history_summary:
            parts.append("## Previous Conversation Summary")
            parts.append(context.compressed_history_summary)
            parts.append("")
        
        # 3. Add recent conversation history
        if context.recent_messages:
            parts.append("## Recent Conversation")
            for msg in context.recent_messages:
                role = msg.get("role", "unknown")
                msg_content = msg.get("content", "")
                if role == "user":
                    parts.append(f"**User**: {msg_content}")
                elif role == "assistant":
                    parts.append(f"**Assistant**: {msg_content}")
                else:
                    parts.append(f"**{role}**: {msg_content}")
            parts.append("")

        # 4. Add resolved attachment content
        parts.extend(self._build_attachment_context_section(context))
        
        # 5. Add current query
        parts.append("## Current Query")
        parts.append(query)
        parts.append("")
        
        # 6. Add tool results
        if context.tool_results:
            parts.append("## Tool Results")
            for tool_name, result in context.tool_results.items():
                if hasattr(result, 'context'):
                    # Document search style result
                    parts.append(f"\n### {tool_name}\n{result.context}")
                elif hasattr(result, 'results') and isinstance(result.results, list):
                    # List of results
                    parts.append(f"\n### {tool_name} ({len(result.results)} items)")
                    for i, item in enumerate(result.results[:5], 1):
                        if hasattr(item, 'model_dump'):
                            parts.append(f"\n{i}. {item.model_dump()}")
                        else:
                            parts.append(f"\n{i}. {item}")
                elif hasattr(result, 'content'):
                    # Web scraper style result
                    parts.append(f"\n### {tool_name}\n{result.content[:2000]}")
                else:
                    # Generic result
                    parts.append(f"\n### {tool_name}\n{result}")
            parts.append("")
        
        parts.append("Please answer the user's question based on all available context. Be conversational and reference relevant context when appropriate. If any tool results above are not relevant to the user's query, ignore them completely.")
        return "\n".join(parts)
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response when synthesis fails.
        
        Override in subclasses for custom fallback handling.
        
        Args:
            query: User's query
            context: Execution context
            
        Returns:
            Fallback response string
        """
        parts = [f"Here's what I found about **{query}**:\n"]
        
        for tool_name, result in context.tool_results.items():
            if hasattr(result, 'results') and isinstance(result.results, list):
                parts.append(f"\n### {tool_name} Results:")
                for item in result.results[:3]:
                    if hasattr(item, 'text'):
                        parts.append(f"\n- {item.text[:200]}...")
                    elif hasattr(item, 'title'):
                        parts.append(f"\n- {item.title}")
        
        return "\n".join(parts)


def create_agent_from_definition(definition: Any) -> BaseStreamingAgent:
    """
    Create a streaming agent from a database AgentDefinition.
    
    Args:
        definition: AgentDefinition database model
        
    Returns:
        Configured BaseStreamingAgent instance
    """
    # Extract workflows config
    workflows = definition.workflows or {}
    
    # Parse execution mode
    execution_mode_str = workflows.get("execution_mode", "run_once")
    try:
        execution_mode = ExecutionMode(execution_mode_str)
    except ValueError:
        logger.warning(f"Invalid execution_mode '{execution_mode_str}', defaulting to RUN_ONCE")
        execution_mode = ExecutionMode.RUN_ONCE
    
    # Parse tool strategy
    tool_strategy_str = workflows.get("tool_strategy", "llm_driven")
    try:
        tool_strategy = ToolStrategy(tool_strategy_str)
    except ValueError:
        logger.warning(f"Invalid tool_strategy '{tool_strategy_str}', defaulting to LLM_DRIVEN")
        tool_strategy = ToolStrategy.LLM_DRIVEN
    
    # Get tool names
    tools_config = definition.tools or {}
    tool_names = tools_config.get("names", []) if isinstance(tools_config, dict) else []
    
    # Parse MCP server configs if present
    raw_mcp = getattr(definition, 'mcp_servers', None) or []
    mcp_configs = parse_mcp_server_configs(raw_mcp) if raw_mcp else []

    # Create config
    config = AgentConfig(
        name=definition.name,
        display_name=definition.display_name or definition.name,
        instructions=definition.instructions or "You are a helpful assistant.",
        tools=tool_names,
        model=definition.model or "agent",
        streaming=True,  # Default for DB agents
        execution_mode=execution_mode,
        tool_strategy=tool_strategy,
        max_iterations=workflows.get("max_iterations", 5),
        allow_frontier_fallback=getattr(definition, 'allow_frontier_fallback', False),
        mcp_servers=mcp_configs,
    )
    
    # Check for predefined pipeline
    pipeline_config = workflows.get("pipeline", [])
    
    if pipeline_config:
        # Create agent with predefined pipeline
        class DatabasePipelineAgent(BaseStreamingAgent):
            def __init__(self, config: AgentConfig, pipeline: List[Dict]):
                super().__init__(config)
                self._pipeline = pipeline
            
            def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
                steps = []
                for step_config in self._pipeline:
                    args = dict(step_config.get("args", {}))
                    # Substitute {query} placeholder
                    for key, value in args.items():
                        if value == "{query}":
                            args[key] = query
                    steps.append(PipelineStep(
                        tool=step_config.get("tool", ""),
                        args=args,
                    ))
                return steps
        
        return DatabasePipelineAgent(config, pipeline_config)
    
    return BaseStreamingAgent(config)
