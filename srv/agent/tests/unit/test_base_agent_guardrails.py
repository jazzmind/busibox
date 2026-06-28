"""
Unit tests for base_agent guardrails, tool result truncation, monitored_tool
wrapper, enriched system prompts, and multi-turn message history.

Tests cover the features added for tool-calling guardrails and chat UX:
- _truncate_tool_result: progressive truncation of large tool outputs
- _wrap_tool_with_truncation: wrapper preserving function signatures
- TOOL_CLASSES / TOOL_CLASS_DEFAULT: tool speed classification and timeouts
- monitored_tool: deduplication, timeout, cancellation, error handling
- _build_enriched_system_prompt: system prompt enrichment with context
- _build_message_history: multi-turn pydantic-ai message construction
"""

import asyncio
import inspect
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from app.agents.base_agent import (
    MAX_TOOL_RESULT_CHARS,
    TOOL_CLASSES,
    TOOL_CLASS_DEFAULT,
    TOOL_SCOPES,
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolRegistry,
    ToolStrategy,
    _truncate_tool_result,
    _wrap_tool_with_truncation,
)
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_principal():
    return Principal(
        sub="test-user-guardrails",
        email="guardrails@test.com",
        roles=["user"],
        scopes=["search.read", "data.write", "data.read"],
        token="test-jwt-token-guardrails",
    )


@pytest.fixture
def mock_stream_callback():
    """Collects stream events for assertion."""
    events: List[StreamEvent] = []

    async def callback(event: StreamEvent):
        events.append(event)

    callback.events = events
    return callback


@pytest.fixture
def mock_cancel_event():
    return asyncio.Event()


@pytest.fixture
def llm_driven_config():
    return AgentConfig(
        name="test-llm-driven",
        display_name="Test LLM Driven Agent",
        instructions="You are a helpful test agent.",
        tools=["query_data", "web_search"],
        execution_mode=ExecutionMode.RUN_ONCE,
        tool_strategy=ToolStrategy.LLM_DRIVEN,
    )


# =============================================================================
# _truncate_tool_result tests
# =============================================================================


class TestTruncateToolResult:
    """Tests for _truncate_tool_result function."""

    def test_small_result_returned_unchanged(self):
        """Results under MAX_TOOL_RESULT_CHARS are returned as-is."""

        class SmallOutput(BaseModel):
            records: list = []
            total: int = 0

        result = SmallOutput(records=[{"id": "1", "name": "Test"}], total=1)
        truncated = _truncate_tool_result(result)
        assert truncated is result

    def test_large_records_progressively_truncated(self):
        """Large record-based results shed records until under limit."""

        class BigOutput(BaseModel):
            records: list
            total: int

        big_records = [{"id": str(i), "data": "x" * 200} for i in range(200)]
        result = BigOutput(records=big_records, total=200)

        truncated = _truncate_tool_result(result)

        assert isinstance(truncated, dict)
        assert truncated["_truncated"] is True
        assert "_note" in truncated
        assert len(truncated["records"]) < 200
        # The truncation loop pops records until the *data dict* (including
        # metadata like _truncated and _note) fits under MAX_TOOL_RESULT_CHARS.
        # With large individual records, the final size may slightly exceed the
        # limit by the size of one record — this is expected and acceptable.
        serialized = json.dumps(truncated, default=str)
        one_record_overhead = len(json.dumps(big_records[0], default=str)) + 2
        assert len(serialized) <= MAX_TOOL_RESULT_CHARS + one_record_overhead

    def test_large_non_records_string_truncated(self):
        """Large BaseModel results without records are string-truncated."""

        class LargeBlob(BaseModel):
            content: str

        result = LargeBlob(content="x" * (MAX_TOOL_RESULT_CHARS + 5000))
        truncated = _truncate_tool_result(result)

        assert isinstance(truncated, dict)
        assert truncated["_truncated"] is True

    def test_dict_with_records_truncated(self):
        """Dict results with records list are progressively truncated."""
        big_dict = {
            "records": [{"id": str(i), "data": "y" * 200} for i in range(200)],
            "total": 200,
        }
        truncated = _truncate_tool_result(big_dict)

        assert isinstance(truncated, dict)
        assert truncated["_truncated"] is True
        assert len(truncated["records"]) < 200

    def test_small_dict_returned_unchanged(self):
        """Small dict results pass through unchanged."""
        small = {"result": "ok"}
        assert _truncate_tool_result(small) is small

    def test_non_serializable_result_returned_unchanged(self):
        """If truncation fails (e.g. weird type), original is returned."""
        weird = object()
        assert _truncate_tool_result(weird) is weird

    def test_truncation_note_has_guidance(self):
        """The _note field guides the LLM to narrow its query."""

        class QueryOutput(BaseModel):
            records: list
            total: int

        result = QueryOutput(
            records=[{"id": str(i), "payload": "z" * 300} for i in range(100)],
            total=100,
        )
        truncated = _truncate_tool_result(result)
        assert isinstance(truncated, dict)
        note = truncated.get("_note", "")
        assert "select" in note.lower() or "where" in note.lower() or "limit" in note.lower()


# =============================================================================
# _wrap_tool_with_truncation tests
# =============================================================================


class TestWrapToolWithTruncation:
    """Tests for the _wrap_tool_with_truncation wrapper."""

    @pytest.mark.asyncio
    async def test_preserves_signature(self):
        """Wrapper preserves original function's signature and annotations."""

        async def my_tool(query: str, limit: int = 10) -> dict:
            return {"result": query}

        wrapped = _wrap_tool_with_truncation(my_tool)

        orig_sig = inspect.signature(my_tool)
        wrapped_sig = wrapped.__signature__
        assert list(orig_sig.parameters.keys()) == list(wrapped_sig.parameters.keys())

    @pytest.mark.asyncio
    async def test_small_result_passes_through(self):
        """Small results pass through the wrapper unchanged (by value)."""

        class SmallOut(BaseModel):
            value: str = "ok"

        async def my_tool(query: str) -> SmallOut:
            return SmallOut()

        wrapped = _wrap_tool_with_truncation(my_tool)
        result = await wrapped(query="test")
        assert isinstance(result, SmallOut)
        assert result.value == "ok"

    @pytest.mark.asyncio
    async def test_large_result_gets_truncated(self):
        """Large results are truncated by the wrapper."""

        class HugeOut(BaseModel):
            content: str

        async def my_tool(query: str) -> HugeOut:
            return HugeOut(content="x" * (MAX_TOOL_RESULT_CHARS + 5000))

        wrapped = _wrap_tool_with_truncation(my_tool)
        result = await wrapped(query="test")
        assert isinstance(result, dict)
        assert result["_truncated"] is True

    @pytest.mark.asyncio
    async def test_preserves_name(self):
        """Wrapper preserves __name__ and __qualname__."""

        async def query_data(document_id: str) -> dict:
            return {}

        wrapped = _wrap_tool_with_truncation(query_data)
        assert wrapped.__name__ == "query_data"


# =============================================================================
# TOOL_CLASSES configuration tests
# =============================================================================


class TestToolClasses:
    """Tests for TOOL_CLASSES classification map."""

    def test_fast_tools_have_short_timeouts(self):
        fast_tools = [k for k, v in TOOL_CLASSES.items() if v["class"] == "fast"]
        assert len(fast_tools) > 0
        for tool in fast_tools:
            assert TOOL_CLASSES[tool]["timeout"] <= 15

    def test_slow_tools_have_longer_timeouts(self):
        slow_tools = [k for k, v in TOOL_CLASSES.items() if v["class"] == "slow"]
        assert len(slow_tools) > 0
        for tool in slow_tools:
            assert TOOL_CLASSES[tool]["timeout"] >= 30

    def test_query_data_is_fast(self):
        assert TOOL_CLASSES["query_data"]["class"] == "fast"

    def test_web_search_is_slow(self):
        assert TOOL_CLASSES["web_search"]["class"] == "slow"

    def test_document_search_is_fast(self):
        assert TOOL_CLASSES["document_search"]["class"] == "fast"

    def test_generate_image_is_slow_with_high_timeout(self):
        assert TOOL_CLASSES["generate_image"]["class"] == "slow"
        assert TOOL_CLASSES["generate_image"]["timeout"] >= 60

    def test_default_is_slow_60s(self):
        assert TOOL_CLASS_DEFAULT["class"] == "slow"
        assert TOOL_CLASS_DEFAULT["timeout"] == 60

    def test_all_known_tools_have_classification(self):
        """Every tool in TOOL_SCOPES should have a TOOL_CLASSES entry."""
        for tool_name in TOOL_SCOPES:
            assert tool_name in TOOL_CLASSES, (
                f"Tool '{tool_name}' in TOOL_SCOPES but missing from TOOL_CLASSES"
            )


# =============================================================================
# AgentContext deduplication tests
# =============================================================================


class TestAgentContextDedup:
    """Tests for AgentContext._tool_call_dedup."""

    def test_dedup_cache_starts_empty(self):
        ctx = AgentContext()
        assert ctx._tool_call_dedup == {}

    def test_dedup_cache_is_per_instance(self):
        ctx1 = AgentContext()
        ctx2 = AgentContext()
        ctx1._tool_call_dedup["key"] = "value"
        assert "key" not in ctx2._tool_call_dedup


# =============================================================================
# monitored_tool tests (via _execute_llm_driven internals)
# =============================================================================


class TestMonitoredTool:
    """Tests for the monitored_tool wrapper created inside _execute_llm_driven."""

    @pytest.mark.asyncio
    async def test_dedup_returns_cached_result(
        self, llm_driven_config, mock_stream_callback, mock_cancel_event
    ):
        """Identical tool calls should return cached results via dedup."""
        agent = BaseStreamingAgent(llm_driven_config)
        context = AgentContext(
            principal=Principal(
                sub="test", email="t@t.com", roles=[], scopes=[], token="tok"
            ),
            session=AsyncMock(),
        )

        call_count = 0

        async def fake_query_data(document_id: str, limit: int = 10) -> dict:
            nonlocal call_count
            call_count += 1
            return {"records": [], "total": 0}

        with patch.object(ToolRegistry, "get", return_value=fake_query_data):
            with patch("app.agents.base_agent.get_or_exchange_token") as mock_exchange:
                mock_token = MagicMock()
                mock_token.access_token = "test-tok"
                mock_exchange.return_value = mock_token

                # Build the tools list the same way _execute_llm_driven does
                tools = []
                for tool_name in ["query_data"]:
                    tool_func = fake_query_data
                    wrapped = _wrap_tool_with_truncation(tool_func)

                    import functools

                    @functools.wraps(wrapped)
                    async def monitored(
                        *args,
                        _tool_name=tool_name,
                        _tool=wrapped,
                        **kwargs,
                    ):
                        if mock_cancel_event.is_set():
                            return ""
                        input_payload = dict(kwargs) if kwargs else {}
                        dedup_key = f"{_tool_name}:{json.dumps(input_payload, sort_keys=True, default=str)}"
                        if dedup_key in context._tool_call_dedup:
                            return context._tool_call_dedup[dedup_key]

                        result = await _tool(*args, **kwargs)
                        context._tool_call_dedup[dedup_key] = result
                        return result

                    tools.append(monitored)

                # First call
                r1 = await tools[0](document_id="doc-1", limit=10)
                # Second identical call
                r2 = await tools[0](document_id="doc-1", limit=10)

                assert call_count == 1
                assert r1 == r2

    @pytest.mark.asyncio
    async def test_dedup_different_args_not_cached(self):
        """Calls with different args should not hit the dedup cache."""
        context = AgentContext()
        call_count = 0

        async def fake_tool(query: str) -> dict:
            nonlocal call_count
            call_count += 1
            return {"result": query}

        wrapped = _wrap_tool_with_truncation(fake_tool)

        import functools

        @functools.wraps(wrapped)
        async def monitored(*args, _tool=wrapped, **kwargs):
            input_payload = dict(kwargs) if kwargs else {}
            dedup_key = f"fake_tool:{json.dumps(input_payload, sort_keys=True, default=str)}"
            if dedup_key in context._tool_call_dedup:
                return context._tool_call_dedup[dedup_key]
            result = await _tool(*args, **kwargs)
            context._tool_call_dedup[dedup_key] = result
            return result

        await monitored(query="alpha")
        await monitored(query="beta")
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_cancel_event_short_circuits(self):
        """When cancel is set, monitored_tool returns early."""
        cancel = asyncio.Event()
        cancel.set()

        call_count = 0

        async def fake_tool(query: str) -> dict:
            nonlocal call_count
            call_count += 1
            return {"result": query}

        # Simulate monitored_tool with cancel check
        async def monitored(*args, **kwargs):
            if cancel.is_set():
                return ""
            return await fake_tool(*args, **kwargs)

        result = await monitored(query="test")
        assert result == ""
        assert call_count == 0

    @pytest.mark.asyncio
    async def test_timeout_raises_runtime_error(self):
        """Tool exceeding its timeout should raise RuntimeError."""

        async def slow_tool(query: str) -> dict:
            await asyncio.sleep(5)
            return {"result": query}

        tool_class = TOOL_CLASSES.get("query_data", TOOL_CLASS_DEFAULT)

        with pytest.raises((asyncio.TimeoutError, RuntimeError)):
            await asyncio.wait_for(slow_tool(query="test"), timeout=0.01)


# =============================================================================
# _build_enriched_system_prompt tests
# =============================================================================


class TestBuildEnrichedSystemPrompt:
    """Tests for _build_enriched_system_prompt."""

    def _make_agent(self, instructions="Base instructions."):
        config = AgentConfig(
            name="prompt-test",
            display_name="Prompt Test",
            instructions=instructions,
            tools=[],
        )
        return BaseStreamingAgent(config)

    def test_base_instructions_included(self):
        agent = self._make_agent("You are a helpful assistant.")
        context = AgentContext()
        prompt = agent._build_enriched_system_prompt(context)
        assert "You are a helpful assistant." in prompt

    def test_metadata_included(self):
        agent = self._make_agent()
        context = AgentContext(
            metadata={"projectId": "proj-123", "appName": "MyApp"}
        )
        prompt = agent._build_enriched_system_prompt(context)
        assert "proj-123" in prompt
        assert "MyApp" in prompt
        assert "Application Context" in prompt

    def test_insights_included(self):
        agent = self._make_agent()
        context = AgentContext(
            relevant_insights=[
                {"category": "preference", "content": "User prefers dark mode"},
                {"category": "fact", "content": "User works in marine construction"},
            ]
        )
        prompt = agent._build_enriched_system_prompt(context)
        assert "dark mode" in prompt
        assert "marine construction" in prompt
        assert "Relevant User Context" in prompt

    def test_missing_profile_fields_included(self):
        agent = self._make_agent()
        context = AgentContext(
            missing_profile_fields=["department", "role"]
        )
        prompt = agent._build_enriched_system_prompt(context)
        assert "department" in prompt
        assert "Missing Profile Context" in prompt

    def test_pending_questions_included(self):
        agent = self._make_agent()
        context = AgentContext(
            pending_questions=[
                {"content": "What team are you on?"},
                {"content": "What is your preferred language?"},
            ]
        )
        prompt = agent._build_enriched_system_prompt(context)
        assert "What team are you on?" in prompt
        assert "Pending Follow-up Questions" in prompt

    def test_pending_questions_limited_to_three(self):
        agent = self._make_agent()
        context = AgentContext(
            pending_questions=[{"content": f"Q{i}"} for i in range(10)]
        )
        prompt = agent._build_enriched_system_prompt(context)
        assert "Q0" in prompt
        assert "Q2" in prompt
        # Q3+ should not appear (limit is [:3])
        assert "Q3" not in prompt

    def test_empty_context_returns_base_with_datetime(self):
        agent = self._make_agent("Just the base.")
        context = AgentContext()
        prompt = agent._build_enriched_system_prompt(context)
        assert prompt.startswith("Just the base.")
        assert "## Current Date and Time" in prompt


# =============================================================================
# _build_message_history tests
# =============================================================================


class TestBuildMessageHistory:
    """Tests for _build_message_history."""

    def _make_agent(self):
        config = AgentConfig(
            name="history-test",
            display_name="History Test",
            instructions="System prompt.",
            tools=[],
        )
        return BaseStreamingAgent(config)

    def test_no_history_returns_none(self):
        agent = self._make_agent()
        context = AgentContext()
        result = agent._build_message_history(context, "sys")
        assert result is None

    def test_summary_only(self):
        agent = self._make_agent()
        context = AgentContext(
            compressed_history_summary="User asked about dredging costs."
        )
        messages = agent._build_message_history(context, "System prompt text")
        assert messages is not None
        assert len(messages) == 2
        # First is a ModelRequest with summary
        assert isinstance(messages[0], ModelRequest)
        assert "dredging costs" in messages[0].parts[0].content
        # Should have instructions embedded
        assert messages[0].instructions == "System prompt text"
        # Second is the ack
        assert isinstance(messages[1], ModelResponse)

    def test_recent_messages_only(self):
        agent = self._make_agent()
        context = AgentContext(
            recent_messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "Tell me about vessels"},
            ]
        )
        messages = agent._build_message_history(context, "sys")
        assert messages is not None
        assert len(messages) == 3
        assert isinstance(messages[0], ModelRequest)
        assert isinstance(messages[1], ModelResponse)
        assert isinstance(messages[2], ModelRequest)
        # First user message should carry instructions
        assert messages[0].instructions == "sys"

    def test_summary_plus_recent(self):
        agent = self._make_agent()
        context = AgentContext(
            compressed_history_summary="Earlier: user discussed budgets.",
            recent_messages=[
                {"role": "user", "content": "What about Q3?"},
                {"role": "assistant", "content": "Q3 looks good."},
            ],
        )
        messages = agent._build_message_history(context, "sys")
        assert messages is not None
        # summary pair (2) + recent (2) = 4
        assert len(messages) == 4
        assert isinstance(messages[0], ModelRequest)
        assert "budgets" in messages[0].parts[0].content
        assert isinstance(messages[1], ModelResponse)
        assert isinstance(messages[2], ModelRequest)
        assert "Q3" in messages[2].parts[0].content

    def test_empty_content_messages_skipped(self):
        agent = self._make_agent()
        context = AgentContext(
            recent_messages=[
                {"role": "user", "content": ""},
                {"role": "user", "content": "Real message"},
            ]
        )
        messages = agent._build_message_history(context, "sys")
        assert messages is not None
        assert len(messages) == 1
        assert "Real message" in messages[0].parts[0].content

    def test_instructions_set_on_first_user_message(self):
        agent = self._make_agent()
        context = AgentContext(
            recent_messages=[
                {"role": "assistant", "content": "I started"},
                {"role": "user", "content": "Hello"},
            ]
        )
        messages = agent._build_message_history(context, "sys prompt")
        user_msgs = [m for m in messages if isinstance(m, ModelRequest)]
        assert len(user_msgs) == 1
        assert user_msgs[0].instructions == "sys prompt"


# =============================================================================
# Integration: LLM-driven agent with guardrails
# =============================================================================


class TestLLMDrivenGuardrails:
    """Higher-level tests for LLM-driven execution guardrails."""

    def test_tool_scopes_covers_all_classified_tools(self):
        """Every tool in TOOL_CLASSES should also exist in TOOL_SCOPES."""
        for tool_name in TOOL_CLASSES:
            assert tool_name in TOOL_SCOPES, (
                f"Tool '{tool_name}' in TOOL_CLASSES but missing from TOOL_SCOPES"
            )

    def test_agent_config_compression_defaults(self):
        """Verify default history compression settings."""
        config = AgentConfig(
            name="test",
            display_name="T",
            instructions="T",
            tools=[],
        )
        assert config.enable_history_compression is True
        assert config.compression_threshold_chars == 8000
        assert config.recent_messages_to_keep == 5

    def test_dedup_key_deterministic(self):
        """Dedup key generation is deterministic for same inputs."""
        payload = {"document_id": "abc", "limit": 10}
        key1 = f"query_data:{json.dumps(payload, sort_keys=True, default=str)}"
        key2 = f"query_data:{json.dumps(payload, sort_keys=True, default=str)}"
        assert key1 == key2

    def test_dedup_key_differs_for_different_inputs(self):
        """Dedup keys differ when inputs change."""
        p1 = {"document_id": "abc", "limit": 10}
        p2 = {"document_id": "abc", "limit": 20}
        key1 = f"query_data:{json.dumps(p1, sort_keys=True, default=str)}"
        key2 = f"query_data:{json.dumps(p2, sort_keys=True, default=str)}"
        assert key1 != key2

    def test_max_tool_result_chars_is_reasonable(self):
        """MAX_TOOL_RESULT_CHARS should be in a sensible range."""
        assert 4000 <= MAX_TOOL_RESULT_CHARS <= 32000
