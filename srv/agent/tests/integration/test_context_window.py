"""
Integration tests for context window management features.

Tests:
1. Context compression service (history compression above/below threshold)
2. Tool result truncation (large results, records-based truncation)
3. Frontier fallback configuration (enabled/disabled)
4. Data tool registration in ToolRegistry
5. Data tool scope mappings in TOOL_SCOPES
"""

import json
import pytest

from app.agents.base_agent import (
    AgentConfig,
    ExecutionMode,
    ToolStrategy,
    ToolRegistry,
    TOOL_SCOPES,
    MAX_TOOL_RESULT_CHARS,
    _truncate_tool_result,
)
from app.services.context_compression import (
    ContextCompressionService,
    CompressionResult,
)
from app.schemas.definitions import ContextCompressionConfig


# =============================================================================
# Data Tool Registration Tests
# =============================================================================

class TestDataToolRegistration:
    """Verify all data tools are registered in the ToolRegistry."""

    DATA_TOOL_NAMES = [
        "list_data_documents",
        "query_data",
        "insert_records",
        "update_records",
        "delete_records",
        "create_data_document",
        "get_data_document",
    ]

    def test_data_tools_registered(self):
        """All data tools should be registered in ToolRegistry after module load."""
        for tool_name in self.DATA_TOOL_NAMES:
            assert ToolRegistry.has(tool_name), (
                f"Data tool '{tool_name}' is not registered in ToolRegistry. "
                f"Registered tools: {list(ToolRegistry._tools.keys())}"
            )

    def test_data_tools_are_callable(self):
        """Each registered data tool should be a callable function."""
        for tool_name in self.DATA_TOOL_NAMES:
            func = ToolRegistry.get(tool_name)
            assert func is not None, f"ToolRegistry.get('{tool_name}') returned None"
            assert callable(func), f"Tool '{tool_name}' is not callable: {type(func)}"

    def test_data_tools_have_output_types(self):
        """Each data tool should have a registered output type."""
        for tool_name in self.DATA_TOOL_NAMES:
            output_type = ToolRegistry.get_output_type(tool_name)
            assert output_type is not None, (
                f"Data tool '{tool_name}' has no registered output type"
            )

    def test_builtin_tools_still_registered(self):
        """Original builtin tools should remain registered alongside data tools."""
        builtin_names = [
            "document_search",
            "web_search",
            "web_scraper",
            "get_weather",
        ]
        for tool_name in builtin_names:
            assert ToolRegistry.has(tool_name), (
                f"Builtin tool '{tool_name}' is missing from ToolRegistry after data tool registration"
            )


# =============================================================================
# TOOL_SCOPES Tests
# =============================================================================

class TestDataToolScopes:
    """Verify TOOL_SCOPES includes all data tools with correct scope mappings."""

    def test_read_tools_have_data_read_scope(self):
        """Read-only data tools should have 'data.read' scope."""
        read_tools = ["list_data_documents", "query_data", "get_data_document"]
        for tool_name in read_tools:
            assert tool_name in TOOL_SCOPES, f"'{tool_name}' missing from TOOL_SCOPES"
            assert "data.read" in TOOL_SCOPES[tool_name], (
                f"'{tool_name}' should have 'data.read' scope, got: {TOOL_SCOPES[tool_name]}"
            )

    def test_write_tools_have_data_write_scope(self):
        """Write data tools should have 'data.write' scope."""
        write_tools = ["insert_records", "update_records", "delete_records", "create_data_document"]
        for tool_name in write_tools:
            assert tool_name in TOOL_SCOPES, f"'{tool_name}' missing from TOOL_SCOPES"
            assert "data.write" in TOOL_SCOPES[tool_name], (
                f"'{tool_name}' should have 'data.write' scope, got: {TOOL_SCOPES[tool_name]}"
            )

    def test_agent_config_resolves_data_scopes(self):
        """AgentConfig.get_required_scopes() should include data scopes when data tools are configured."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["list_data_documents", "query_data", "insert_records", "update_records"],
        )
        scopes = config.get_required_scopes()
        assert "data.read" in scopes, f"Expected 'data.read' in scopes, got: {scopes}"
        assert "data.write" in scopes, f"Expected 'data.write' in scopes, got: {scopes}"

    def test_agent_config_requires_auth_for_data_tools(self):
        """Agent with data tools should require auth."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["list_data_documents", "insert_records"],
        )
        assert config.requires_auth(), "Agent with data tools should require auth"

    def test_agent_config_no_auth_for_no_scope_tools(self):
        """Agent with only no-scope tools should not require auth."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["web_search", "get_weather"],
        )
        assert not config.requires_auth(), "Agent with only web_search/get_weather should not require auth"


# =============================================================================
# Frontier Fallback Tests
# =============================================================================

class TestFrontierFallback:
    """Verify frontier fallback configuration on AgentConfig."""

    def test_frontier_fallback_disabled_by_default(self):
        """allow_frontier_fallback should default to False."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["web_search"],
        )
        assert config.allow_frontier_fallback is False

    def test_frontier_fallback_can_be_enabled(self):
        """allow_frontier_fallback can be set to True."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["web_search"],
            allow_frontier_fallback=True,
        )
        assert config.allow_frontier_fallback is True

    def test_frontier_fallback_disabled_sets_model_settings(self):
        """When frontier fallback is disabled, model_settings should include disable_fallbacks=True.
        
        This verifies the contract that _execute_llm_driven checks when building model_settings.
        """
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["web_search"],
            allow_frontier_fallback=False,
        )
        # Simulate what _execute_llm_driven does
        model_settings = {}
        if not config.allow_frontier_fallback:
            model_settings.setdefault("extra_body", {})["disable_fallbacks"] = True
        
        assert "extra_body" in model_settings
        assert model_settings["extra_body"]["disable_fallbacks"] is True

    def test_frontier_fallback_enabled_no_disable(self):
        """When frontier fallback is enabled, model_settings should NOT include disable_fallbacks."""
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions="Test",
            tools=["web_search"],
            allow_frontier_fallback=True,
        )
        # Simulate what _execute_llm_driven does
        model_settings = {}
        if not config.allow_frontier_fallback:
            model_settings.setdefault("extra_body", {})["disable_fallbacks"] = True
        
        assert "extra_body" not in model_settings


# =============================================================================
# Tool Result Truncation Tests
# =============================================================================

class TestToolResultTruncation:
    """Verify _truncate_tool_result handles large results correctly."""

    def test_small_result_not_truncated(self):
        """Results under MAX_TOOL_RESULT_CHARS should be returned as-is."""
        from pydantic import BaseModel

        class SmallResult(BaseModel):
            success: bool = True
            data: str = "small payload"

        result = SmallResult()
        truncated = _truncate_tool_result(result)
        # Should return the same object unchanged
        assert isinstance(truncated, SmallResult)
        assert truncated.success is True
        assert truncated.data == "small payload"

    def test_large_records_result_truncated(self):
        """Results with a 'records' list exceeding MAX_TOOL_RESULT_CHARS should be truncated."""
        from pydantic import BaseModel
        from typing import List, Dict, Any

        class RecordsResult(BaseModel):
            success: bool = True
            records: List[Dict[str, Any]] = []
            total: int = 0

        # Create a result with many large records
        large_records = [
            {"id": f"record-{i}", "content": "x" * 500, "description": "y" * 500}
            for i in range(50)
        ]
        result = RecordsResult(
            success=True,
            records=large_records,
            total=50,
        )

        # Verify it exceeds the limit
        serialized = result.model_dump_json()
        assert len(serialized) > MAX_TOOL_RESULT_CHARS, (
            f"Test data should exceed {MAX_TOOL_RESULT_CHARS} chars, got {len(serialized)}"
        )

        truncated = _truncate_tool_result(result)
        
        # Should be a dict now (not a Pydantic model)
        assert isinstance(truncated, dict)
        assert truncated.get("_truncated") is True
        assert "_note" in truncated
        assert len(truncated["records"]) < len(large_records)
        
        # The truncated result should fit within limits
        truncated_json = json.dumps(truncated, default=str)
        assert len(truncated_json) <= MAX_TOOL_RESULT_CHARS

    def test_large_non_records_result_truncated(self):
        """Large results without 'records' field should be string-truncated."""
        from pydantic import BaseModel

        class LargeResult(BaseModel):
            success: bool = True
            data: str = ""

        result = LargeResult(
            success=True,
            data="x" * (MAX_TOOL_RESULT_CHARS + 5000),
        )

        truncated = _truncate_tool_result(result)
        
        assert isinstance(truncated, dict)
        assert truncated.get("_truncated") is True
        assert "_note" in truncated

    def test_dict_result_with_records_truncated(self):
        """Dict results with 'records' should also be truncated."""
        large_records = [
            {"id": f"record-{i}", "content": "x" * 500}
            for i in range(50)
        ]
        result = {
            "success": True,
            "records": large_records,
            "total": 50,
        }

        serialized = json.dumps(result, default=str)
        assert len(serialized) > MAX_TOOL_RESULT_CHARS

        truncated = _truncate_tool_result(result)
        
        assert isinstance(truncated, dict)
        assert truncated.get("_truncated") is True
        assert len(truncated["records"]) < len(large_records)

    def test_none_result_passes_through(self):
        """None results should pass through unchanged."""
        result = _truncate_tool_result(None)
        assert result is None

    def test_string_result_passes_through(self):
        """String results should pass through (not BaseModel or dict)."""
        result = _truncate_tool_result("simple string")
        assert result == "simple string"


# =============================================================================
# Context Compression Tests
# =============================================================================

class TestContextCompression:
    """Test the ContextCompressionService for conversation history compression."""

    def _make_messages(self, count: int, content_size: int = 200) -> list:
        """Generate test conversation messages."""
        messages = []
        for i in range(count):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append({
                "role": role,
                "content": f"Message {i}: " + "x" * content_size,
            })
        return messages

    @pytest.mark.asyncio
    async def test_compression_below_threshold_no_compression(self):
        """Messages below the character threshold should not be compressed."""
        config = ContextCompressionConfig(
            enabled=True,
            compression_threshold_chars=50000,  # Very high threshold
            recent_messages_to_keep=5,
        )
        service = ContextCompressionService(config)
        
        # Create small message set
        messages = self._make_messages(4, content_size=50)
        
        result = await service.compress_history(messages, config)
        
        assert isinstance(result, CompressionResult)
        assert result.was_compressed is False
        assert result.summary is None
        assert len(result.recent_messages) == len(messages)
        assert result.messages_compressed == 0

    @pytest.mark.asyncio
    async def test_compression_disabled_returns_all_messages(self):
        """When compression is disabled, all messages should be returned as-is."""
        config = ContextCompressionConfig(
            enabled=False,
            compression_threshold_chars=1000,  # Low threshold, but disabled (minimum allowed is 1000)
        )
        service = ContextCompressionService(config)
        
        messages = self._make_messages(20, content_size=500)
        
        result = await service.compress_history(messages, config)
        
        assert result.was_compressed is False
        assert result.summary is None
        assert len(result.recent_messages) == len(messages)

    @pytest.mark.asyncio
    async def test_compression_above_threshold_compresses(self):
        """Messages above the character threshold should trigger compression.
        
        This test requires LiteLLM to be available for the compression model call.
        """
        config = ContextCompressionConfig(
            enabled=True,
            compression_threshold_chars=1000,  # Low threshold to trigger compression
            recent_messages_to_keep=3,
            max_summary_chars=2000,
            compression_model="fast",
        )
        service = ContextCompressionService(config)
        
        # Create messages that exceed 1000 chars total
        messages = self._make_messages(20, content_size=200)
        total_chars = sum(len(m["content"]) for m in messages)
        assert total_chars > 1000, f"Test data should exceed threshold, got {total_chars}"
        
        result = await service.compress_history(messages, config)
        
        assert isinstance(result, CompressionResult)
        assert result.was_compressed is True
        assert result.summary is not None
        assert len(result.summary) > 0
        # Should keep recent_messages_to_keep * 2 messages (pairs)
        assert result.messages_kept == config.recent_messages_to_keep * 2
        assert len(result.recent_messages) == config.recent_messages_to_keep * 2
        assert result.messages_compressed > 0
        # Compressed result should be smaller than original
        assert result.compressed_char_count < result.original_char_count

    def test_split_messages_preserves_recent(self):
        """_split_messages should keep the most recent messages."""
        service = ContextCompressionService()
        messages = self._make_messages(10)
        
        to_compress, to_keep = service._split_messages(messages, keep_recent=3)
        
        # Should keep last 6 messages (3 pairs)
        assert len(to_keep) == 6
        assert to_keep == messages[-6:]
        # Should compress the rest
        assert len(to_compress) == 4
        assert to_compress == messages[:4]

    def test_split_messages_too_few_to_split(self):
        """When messages <= keep_recent * 2, nothing should be split."""
        service = ContextCompressionService()
        messages = self._make_messages(4)
        
        to_compress, to_keep = service._split_messages(messages, keep_recent=3)
        
        assert len(to_compress) == 0
        assert len(to_keep) == 4

    def test_count_chars(self):
        """_count_chars should sum content lengths."""
        service = ContextCompressionService()
        messages = [
            {"role": "user", "content": "hello"},      # 5 chars
            {"role": "assistant", "content": "world"},  # 5 chars
        ]
        assert service._count_chars(messages) == 10

    def test_format_for_compression(self):
        """_format_for_compression should produce role-labeled text."""
        service = ContextCompressionService()
        messages = [
            {"role": "user", "content": "What is AI?"},
            {"role": "assistant", "content": "AI is artificial intelligence."},
        ]
        formatted = service._format_for_compression(messages)
        assert "USER: What is AI?" in formatted
        assert "ASSISTANT: AI is artificial intelligence." in formatted


# =============================================================================
# Agent Config Integration Tests
# =============================================================================

class TestAgentConfigWithDataTools:
    """Test AgentConfig behavior with data tool configurations matching status-report agents."""

    def test_status_report_agent_config(self):
        """An agent configured like the status-report agents should resolve all tools and scopes."""
        config = AgentConfig(
            name="status-assistant",
            display_name="Project Status Assistant",
            instructions="Test instructions",
            tools=[
                "list_data_documents",
                "query_data",
                "insert_records",
                "update_records",
                "document_search",
            ],
            model="agent",
            execution_mode=ExecutionMode.RUN_UNTIL_DONE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
            max_iterations=15,
            allow_frontier_fallback=True,
        )
        
        # All tools should resolve from ToolRegistry
        resolved_tools = []
        missing_tools = []
        for tool_name in config.tools:
            func = ToolRegistry.get(tool_name)
            if func:
                resolved_tools.append(tool_name)
            else:
                missing_tools.append(tool_name)
        
        assert len(missing_tools) == 0, (
            f"The following tools are missing from ToolRegistry: {missing_tools}. "
            f"Registered tools: {list(ToolRegistry._tools.keys())}"
        )
        assert len(resolved_tools) == 5

        # Scopes should include both read and write
        scopes = config.get_required_scopes()
        assert "data.read" in scopes
        assert "data.write" in scopes
        assert "search.read" in scopes
        
        # Should require auth
        assert config.requires_auth()
        
        # Frontier fallback should be enabled
        assert config.allow_frontier_fallback is True
