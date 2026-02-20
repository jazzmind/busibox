"""
Unit tests for BaseStreamingAgent framework.

Tests cover:
- AgentConfig validation and defaults
- ExecutionMode behavior
- ToolStrategy execution patterns
- Authentication and token exchange
- Tool execution with streaming events
- Synthesis and fallback handling
- Pipeline execution
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolRegistry,
    ToolStrategy,
    TOOL_SCOPES,
    create_agent_from_definition,
)
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_stream_callback():
    """Create a mock streaming callback that collects events."""
    events = []
    
    async def callback(event: StreamEvent):
        events.append(event)
    
    callback.events = events
    return callback


@pytest.fixture
def mock_cancel_event():
    """Create a mock cancellation event."""
    return asyncio.Event()


@pytest.fixture
def test_principal():
    """Create a test principal with token."""
    return Principal(
        sub="test-user-123",
        email="test@example.com",
        roles=["user"],
        scopes=["search.read", "data.write"],
        token="test-jwt-token-12345",
    )


@pytest.fixture
def test_agent_config():
    """Create a basic test AgentConfig."""
    return AgentConfig(
        name="test-agent",
        display_name="Test Agent",
        instructions="You are a test agent. Be helpful.",
        tools=["document_search"],
        execution_mode=ExecutionMode.RUN_ONCE,
        tool_strategy=ToolStrategy.PREDEFINED_PIPELINE,
    )


@pytest.fixture
def test_agent_config_no_auth():
    """Create an AgentConfig that doesn't require auth."""
    return AgentConfig(
        name="test-web-agent",
        display_name="Test Web Agent",
        instructions="You are a web search agent.",
        tools=["web_search"],
        execution_mode=ExecutionMode.RUN_ONCE,
        tool_strategy=ToolStrategy.PREDEFINED_PIPELINE,
    )


@pytest.fixture
def test_context(test_principal):
    """Create a test execution context."""
    return {
        "principal": test_principal,
        "session": AsyncMock(),
        "user_id": "test-user-123",
    }


# =============================================================================
# AgentConfig Tests
# =============================================================================


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""
    
    def test_default_values(self):
        """Test default values are set correctly."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test instructions",
            tools=["web_search"],
        )
        
        assert config.model == "agent"
        assert config.streaming is True
        assert config.execution_mode == ExecutionMode.RUN_ONCE
        assert config.tool_strategy == ToolStrategy.PREDEFINED_PIPELINE
        assert config.max_iterations == 5
        assert config.synthesis_prompt is None
    
    def test_required_fields(self):
        """Test that required fields are enforced."""
        # Should work with all required fields
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Instructions",
            tools=[],
        )
        assert config.name == "test"
    
    def test_get_required_scopes_single_tool(self):
        """Test getting scopes for a single tool."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["document_search"],
        )
        
        scopes = config.get_required_scopes()
        assert "search.read" in scopes
    
    def test_get_required_scopes_multiple_tools(self):
        """Test getting scopes for multiple tools."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["document_search", "data_document"],
        )
        
        scopes = config.get_required_scopes()
        assert "search.read" in scopes
        assert "data.write" in scopes
    
    def test_get_required_scopes_no_auth_tools(self):
        """Test scopes for tools that don't need auth."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["web_search", "web_scraper"],
        )
        
        scopes = config.get_required_scopes()
        assert scopes == []
    
    def test_requires_auth_true(self):
        """Test requires_auth returns True when tools need auth."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["document_search"],
        )
        
        assert config.requires_auth() is True
    
    def test_requires_auth_false(self):
        """Test requires_auth returns False when no auth needed."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["web_search"],
        )
        
        assert config.requires_auth() is False


# =============================================================================
# ExecutionMode Tests
# =============================================================================


class TestExecutionMode:
    """Tests for ExecutionMode enum."""
    
    def test_run_once_value(self):
        """Test RUN_ONCE enum value."""
        assert ExecutionMode.RUN_ONCE.value == "run_once"
    
    def test_run_until_done_value(self):
        """Test RUN_UNTIL_DONE enum value."""
        assert ExecutionMode.RUN_UNTIL_DONE.value == "run_until_done"
    
    def test_run_max_iterations_value(self):
        """Test RUN_MAX_ITERATIONS enum value."""
        assert ExecutionMode.RUN_MAX_ITERATIONS.value == "run_max_iterations"
    
    def test_enum_from_string(self):
        """Test creating enum from string value."""
        mode = ExecutionMode("run_once")
        assert mode == ExecutionMode.RUN_ONCE


# =============================================================================
# ToolStrategy Tests
# =============================================================================


class TestToolStrategy:
    """Tests for ToolStrategy enum."""
    
    def test_sequential_value(self):
        """Test SEQUENTIAL enum value."""
        assert ToolStrategy.SEQUENTIAL.value == "sequential"
    
    def test_parallel_value(self):
        """Test PARALLEL enum value."""
        assert ToolStrategy.PARALLEL.value == "parallel"
    
    def test_predefined_pipeline_value(self):
        """Test PREDEFINED_PIPELINE enum value."""
        assert ToolStrategy.PREDEFINED_PIPELINE.value == "predefined_pipeline"
    
    def test_llm_driven_value(self):
        """Test LLM_DRIVEN enum value."""
        assert ToolStrategy.LLM_DRIVEN.value == "llm_driven"
    
    def test_enum_from_string(self):
        """Test creating enum from string value."""
        strategy = ToolStrategy("parallel")
        assert strategy == ToolStrategy.PARALLEL


# =============================================================================
# ToolRegistry Tests
# =============================================================================


class TestToolRegistry:
    """Tests for ToolRegistry."""
    
    def test_register_and_get_tool(self):
        """Test registering and retrieving a tool."""
        async def mock_tool(query: str):
            return {"result": query}
        
        ToolRegistry.register("test_tool", mock_tool)
        
        retrieved = ToolRegistry.get("test_tool")
        assert retrieved == mock_tool
    
    def test_get_nonexistent_tool(self):
        """Test getting a tool that doesn't exist."""
        result = ToolRegistry.get("nonexistent_tool_xyz")
        assert result is None
    
    def test_has_tool(self):
        """Test checking if a tool exists."""
        async def mock_tool():
            pass
        
        ToolRegistry.register("has_test_tool", mock_tool)
        
        assert ToolRegistry.has("has_test_tool") is True
        assert ToolRegistry.has("nonexistent") is False


# =============================================================================
# PipelineStep Tests
# =============================================================================


class TestPipelineStep:
    """Tests for PipelineStep dataclass."""
    
    def test_basic_step(self):
        """Test creating a basic pipeline step."""
        step = PipelineStep(tool="web_search")
        
        assert step.tool == "web_search"
        assert step.args == {}
        assert step.condition is None
    
    def test_step_with_args(self):
        """Test creating a step with arguments."""
        step = PipelineStep(
            tool="document_search",
            args={"query": "test", "limit": 10}
        )
        
        assert step.args["query"] == "test"
        assert step.args["limit"] == 10
    
    def test_step_with_condition(self):
        """Test creating a step with a condition."""
        def condition(results):
            return "web_search" in results
        
        step = PipelineStep(
            tool="web_scraper",
            args={"url": "https://example.com"},
            condition=condition
        )
        
        assert step.condition is not None
        assert step.condition({"web_search": True}) is True
        assert step.condition({}) is False


# =============================================================================
# AgentContext Tests
# =============================================================================


class TestAgentContext:
    """Tests for AgentContext dataclass."""
    
    def test_default_values(self):
        """Test default context values."""
        context = AgentContext()
        
        assert context.principal is None
        assert context.session is None
        assert context.deps is None
        assert context.tool_results == {}
        assert context.iteration == 0
    
    def test_with_principal(self, test_principal):
        """Test context with principal."""
        context = AgentContext(principal=test_principal)
        
        assert context.principal.sub == "test-user-123"
        assert context.principal.token == "test-jwt-token-12345"


# =============================================================================
# BaseStreamingAgent Tests
# =============================================================================


class TestBaseStreamingAgent:
    """Tests for BaseStreamingAgent class."""
    
    def test_initialization(self, test_agent_config):
        """Test agent initialization."""
        agent = BaseStreamingAgent(test_agent_config)
        
        assert agent.name == "Test Agent"
        assert agent.config == test_agent_config
        assert agent.synthesis_agent is not None
    
    def test_default_pipeline_steps_empty(self, test_agent_config):
        """Test that default pipeline_steps returns empty list."""
        agent = BaseStreamingAgent(test_agent_config)
        context = AgentContext()
        
        steps = agent.pipeline_steps("test query", context)
        assert steps == []
    
    @pytest.mark.asyncio
    async def test_default_process_tool_result_empty(self, test_agent_config):
        """Test that default process_tool_result returns empty list."""
        agent = BaseStreamingAgent(test_agent_config)
        context = AgentContext()
        step = PipelineStep(tool="web_search")
        
        # Run async method
        result = await agent.process_tool_result(step, {"result": "test"}, context)
        
        assert result == []
    
    @pytest.mark.asyncio
    async def test_should_continue_run_once(self, test_agent_config):
        """Test should_continue returns False for RUN_ONCE."""
        test_agent_config.execution_mode = ExecutionMode.RUN_ONCE
        agent = BaseStreamingAgent(test_agent_config)
        context = AgentContext()
        
        result = await agent.should_continue(context)
        assert result is False
    
    @pytest.mark.asyncio
    async def test_should_continue_max_iterations_within_limit(self, test_agent_config):
        """Test should_continue returns True when under max_iterations."""
        test_agent_config.execution_mode = ExecutionMode.RUN_MAX_ITERATIONS
        test_agent_config.max_iterations = 5
        agent = BaseStreamingAgent(test_agent_config)
        context = AgentContext(iteration=2)
        
        result = await agent.should_continue(context)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_should_continue_max_iterations_at_limit(self, test_agent_config):
        """Test should_continue returns False when at max_iterations."""
        test_agent_config.execution_mode = ExecutionMode.RUN_MAX_ITERATIONS
        test_agent_config.max_iterations = 5
        agent = BaseStreamingAgent(test_agent_config)
        context = AgentContext(iteration=5)
        
        result = await agent.should_continue(context)
        assert result is False


# =============================================================================
# Authentication Tests
# =============================================================================


class TestBaseStreamingAgentAuth:
    """Tests for authentication in BaseStreamingAgent."""
    
    @pytest.mark.asyncio
    async def test_setup_context_missing_principal(
        self, test_agent_config, mock_stream_callback
    ):
        """Test _setup_context fails without principal."""
        agent = BaseStreamingAgent(test_agent_config)
        
        result = await agent._setup_context({}, mock_stream_callback)
        
        assert result is None
        # Should have streamed an error
        assert len(mock_stream_callback.events) == 1
        assert mock_stream_callback.events[0].type == "error"
        assert "Authentication" in mock_stream_callback.events[0].message
    
    @pytest.mark.asyncio
    async def test_setup_context_missing_token(
        self, test_agent_config, mock_stream_callback
    ):
        """Test _setup_context fails when principal has no token."""
        agent = BaseStreamingAgent(test_agent_config)
        principal = Principal(
            sub="test",
            email="test@test.com",
            roles=[],
            scopes=[],
            token=None,  # No token
        )
        
        result = await agent._setup_context({"principal": principal}, mock_stream_callback)
        
        assert result is None
        assert mock_stream_callback.events[0].type == "error"
    
    @pytest.mark.asyncio
    async def test_setup_context_missing_session(
        self, test_agent_config, test_principal, mock_stream_callback
    ):
        """Test _setup_context fails without database session."""
        agent = BaseStreamingAgent(test_agent_config)
        
        result = await agent._setup_context(
            {"principal": test_principal},  # No session
            mock_stream_callback
        )
        
        assert result is None
        assert mock_stream_callback.events[0].type == "error"
        assert "session" in mock_stream_callback.events[0].message.lower()
    
    @pytest.mark.asyncio
    async def test_setup_context_auth_always_required(
        self, test_agent_config_no_auth, mock_stream_callback
    ):
        """Test _setup_context requires auth even for agents without auth-requiring tools."""
        # Auth is always required regardless of tool configuration
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        
        result = await agent._setup_context({}, mock_stream_callback)
        
        # Should fail without auth
        assert result is None
        assert mock_stream_callback.events[0].type == "error"
        assert "Authentication" in mock_stream_callback.events[0].message


# =============================================================================
# Tool Execution Tests
# =============================================================================


class TestToolExecution:
    """Tests for tool execution in BaseStreamingAgent."""
    
    @pytest.mark.asyncio
    async def test_execute_step_streams_tool_start(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step streams tool_start event."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        # Mock the tool
        mock_result = MagicMock()
        mock_result.found = True
        mock_result.result_count = 5
        mock_result.results = []
        mock_result.model_dump = MagicMock(return_value={"found": True})
        
        with patch.object(ToolRegistry, 'get', return_value=AsyncMock(return_value=mock_result)):
            step = PipelineStep(tool="web_search", args={"query": "test"})
            await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        # Should have tool_start event
        tool_start_events = [e for e in mock_stream_callback.events if e.type == "tool_start"]
        assert len(tool_start_events) == 1
    
    @pytest.mark.asyncio
    async def test_execute_step_streams_tool_result(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step streams tool_result event on success."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        # Mock the tool
        mock_result = MagicMock()
        mock_result.found = True
        mock_result.result_count = 5
        mock_result.results = []
        mock_result.model_dump = MagicMock(return_value={"found": True})
        
        with patch.object(ToolRegistry, 'get', return_value=AsyncMock(return_value=mock_result)):
            step = PipelineStep(tool="web_search", args={"query": "test"})
            await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        # Should have tool_result event
        tool_result_events = [e for e in mock_stream_callback.events if e.type == "tool_result"]
        assert len(tool_result_events) == 1
    
    @pytest.mark.asyncio
    async def test_execute_step_streams_error_on_failure(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step streams error event on failure."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        # Mock tool that raises exception
        async def failing_tool(**kwargs):
            raise ValueError("Tool failed!")
        
        with patch.object(ToolRegistry, 'get', return_value=failing_tool):
            step = PipelineStep(tool="web_search", args={"query": "test"})
            result = await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        # Should return None on error
        assert result is None
        
        # Should have error event
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) == 1
        assert "Tool error" in error_events[0].message
    
    @pytest.mark.asyncio
    async def test_execute_step_handles_tool_not_found(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step handles tool not found."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        with patch.object(ToolRegistry, 'get', return_value=None):
            step = PipelineStep(tool="nonexistent_tool", args={})
            result = await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        assert result is None
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) == 1
        assert "not found" in error_events[0].message.lower()
    
    @pytest.mark.asyncio
    async def test_execute_step_stores_result_in_context(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step stores result in context."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        mock_result = MagicMock()
        mock_result.found = True
        mock_result.result_count = 3
        mock_result.model_dump = MagicMock(return_value={"found": True})
        
        with patch.object(ToolRegistry, 'get', return_value=AsyncMock(return_value=mock_result)):
            step = PipelineStep(tool="web_search", args={"query": "test"})
            await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        assert "web_search" in context.tool_results
        assert context.tool_results["web_search"] == mock_result
    
    @pytest.mark.asyncio
    async def test_execute_step_respects_cancellation(
        self, test_agent_config_no_auth, mock_stream_callback
    ):
        """Test _execute_step respects cancellation event."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        cancel = asyncio.Event()
        cancel.set()  # Set cancellation
        
        step = PipelineStep(tool="web_search", args={"query": "test"})
        result = await agent._execute_step(step, mock_stream_callback, cancel, context)
        
        assert result is None
        # Should not have any events
        assert len(mock_stream_callback.events) == 0
    
    @pytest.mark.asyncio
    async def test_execute_step_skips_when_condition_false(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Test _execute_step skips when condition returns False."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext()
        
        # Condition that always returns False
        step = PipelineStep(
            tool="web_search",
            args={"query": "test"},
            condition=lambda results: False
        )
        
        result = await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)
        
        assert result is None
        # Should not have any events
        assert len(mock_stream_callback.events) == 0

    @pytest.mark.asyncio
    async def test_execute_step_passes_ctx_when_tool_requires_it_without_scopes(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event
    ):
        """Tools declaring `ctx` should receive it even when TOOL_SCOPES has no entry."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        context = AgentContext(deps=MagicMock())

        async def ctx_tool(ctx, query):
            assert hasattr(ctx, "deps")
            assert query == "test"
            result = MagicMock()
            result.found = True
            result.result_count = 1
            result.model_dump = MagicMock(return_value={"found": True})
            return result

        with patch.object(ToolRegistry, 'get', return_value=ctx_tool):
            step = PipelineStep(tool="custom_ctx_tool", args={"query": "test"})
            result = await agent._execute_step(step, mock_stream_callback, mock_cancel_event, context)

        assert result is not None


# =============================================================================
# Pipeline Execution Tests
# =============================================================================


class TestPipelineExecution:
    """Tests for pipeline execution."""
    
    @pytest.mark.asyncio
    async def test_execute_pipeline_sequential(
        self, mock_stream_callback, mock_cancel_event
    ):
        """Test sequential pipeline execution."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["web_search"],
            tool_strategy=ToolStrategy.SEQUENTIAL,
        )
        
        class TestAgent(BaseStreamingAgent):
            def pipeline_steps(self, query, context):
                return [
                    PipelineStep(tool="web_search", args={"query": query}),
                ]
        
        agent = TestAgent(config)
        context = AgentContext()
        
        mock_result = MagicMock()
        mock_result.found = True
        mock_result.result_count = 3
        mock_result.model_dump = MagicMock(return_value={})
        
        with patch.object(ToolRegistry, 'get', return_value=AsyncMock(return_value=mock_result)):
            await agent._execute_pipeline("test query", mock_stream_callback, mock_cancel_event, context)
        
        # Should have executed the tool
        assert "web_search" in context.tool_results
    
    @pytest.mark.asyncio
    async def test_execute_pipeline_with_dynamic_steps(
        self, mock_stream_callback, mock_cancel_event
    ):
        """Test pipeline with dynamically added steps."""
        config = AgentConfig(
            name="test",
            display_name="Test",
            instructions="Test",
            tools=["web_search", "web_scraper"],
            tool_strategy=ToolStrategy.SEQUENTIAL,
            # Use RUN_MAX_ITERATIONS to allow processing dynamic steps
            execution_mode=ExecutionMode.RUN_MAX_ITERATIONS,
            max_iterations=5,
        )
        
        class TestAgent(BaseStreamingAgent):
            def pipeline_steps(self, query, context):
                return [PipelineStep(tool="web_search", args={"query": query})]
            
            async def process_tool_result(self, step, result, context):
                if step.tool == "web_search":
                    return [PipelineStep(tool="web_scraper", args={"url": "https://example.com"})]
                return []
        
        agent = TestAgent(config)
        context = AgentContext()
        
        mock_search_result = MagicMock()
        mock_search_result.found = True
        mock_search_result.result_count = 1
        mock_search_result.model_dump = MagicMock(return_value={})
        
        mock_scrape_result = MagicMock()
        mock_scrape_result.success = True
        mock_scrape_result.model_dump = MagicMock(return_value={})
        
        # Create mock functions that return the results directly (not coroutines returning mocks)
        async def mock_web_search(**kwargs):
            return mock_search_result
        
        async def mock_web_scraper(**kwargs):
            return mock_scrape_result
        
        def get_mock_tool(name):
            if name == "web_search":
                return mock_web_search
            elif name == "web_scraper":
                return mock_web_scraper
            return None
        
        with patch.object(ToolRegistry, 'get', side_effect=get_mock_tool):
            await agent._execute_pipeline("test", mock_stream_callback, mock_cancel_event, context)
        
        # Should have executed both tools
        assert "web_search" in context.tool_results
        assert "web_scraper" in context.tool_results


# =============================================================================
# Synthesis Tests
# =============================================================================


class TestSynthesis:
    """Tests for synthesis in BaseStreamingAgent."""
    
    def test_build_synthesis_context_with_document_results(self, test_agent_config):
        """Test _build_synthesis_context with document search results."""
        agent = BaseStreamingAgent(test_agent_config)
        
        mock_result = MagicMock()
        mock_result.context = "Document content here"
        
        context = AgentContext(tool_results={"document_search": mock_result})
        
        synthesis_ctx = agent._build_synthesis_context("test query", context)
        
        assert "test query" in synthesis_ctx
        assert "Document content here" in synthesis_ctx
    
    def test_build_fallback_response(self, test_agent_config):
        """Test _build_fallback_response generates readable output."""
        agent = BaseStreamingAgent(test_agent_config)
        
        mock_result = MagicMock()
        mock_result.results = [
            MagicMock(text="Result 1 text"),
            MagicMock(text="Result 2 text"),
        ]
        
        context = AgentContext(tool_results={"document_search": mock_result})
        
        fallback = agent._build_fallback_response("test query", context)
        
        assert "test query" in fallback
        assert "Result 1" in fallback


# =============================================================================
# create_agent_from_definition Tests
# =============================================================================


class TestCreateAgentFromDefinition:
    """Tests for create_agent_from_definition factory function."""
    
    def test_creates_agent_with_defaults(self):
        """Test creating agent with default values."""
        definition = MagicMock()
        definition.name = "test-agent"
        definition.display_name = "Test Agent"
        definition.instructions = "Test instructions"
        definition.tools = {"names": ["web_search"]}
        definition.model = "agent"
        definition.workflows = None
        
        agent = create_agent_from_definition(definition)
        
        assert agent.config.name == "test-agent"
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_creates_agent_with_custom_execution_mode(self):
        """Test creating agent with custom execution mode."""
        definition = MagicMock()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = {"execution_mode": "run_max_iterations", "max_iterations": 10}
        
        agent = create_agent_from_definition(definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
        assert agent.config.max_iterations == 10
    
    def test_creates_agent_with_custom_tool_strategy(self):
        """Test creating agent with custom tool strategy."""
        definition = MagicMock()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = {"tool_strategy": "parallel"}
        
        agent = create_agent_from_definition(definition)
        
        assert agent.config.tool_strategy == ToolStrategy.PARALLEL
    
    def test_handles_invalid_execution_mode(self):
        """Test handling invalid execution mode."""
        definition = MagicMock()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = {"execution_mode": "invalid_mode"}
        
        agent = create_agent_from_definition(definition)
        
        # Should default to RUN_ONCE
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_handles_invalid_tool_strategy(self):
        """Test handling invalid tool strategy."""
        definition = MagicMock()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = {"tool_strategy": "invalid_strategy"}
        
        agent = create_agent_from_definition(definition)
        
        # Should default to LLM_DRIVEN
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_creates_pipeline_agent_when_pipeline_defined(self):
        """Test creating agent with predefined pipeline."""
        definition = MagicMock()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": ["web_search"]}
        definition.model = "agent"
        definition.workflows = {
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}}
            ]
        }
        
        agent = create_agent_from_definition(definition)
        context = AgentContext()
        
        # Should have pipeline_steps method that returns steps
        steps = agent.pipeline_steps("test query", context)
        
        assert len(steps) == 1
        assert steps[0].tool == "web_search"
        assert steps[0].args["query"] == "test query"


# =============================================================================
# Streaming Events Tests
# =============================================================================


class TestStreamingEvents:
    """Tests for streaming event generation."""
    
    @pytest.mark.asyncio
    async def test_run_with_streaming_yields_thought_events(
        self, test_agent_config_no_auth, mock_stream_callback, mock_cancel_event, test_context
    ):
        """Test that run_with_streaming yields thought events."""
        class TestAgent(BaseStreamingAgent):
            def pipeline_steps(self, query, context):
                return [PipelineStep(tool="web_search", args={"query": query})]
        
        agent = TestAgent(test_agent_config_no_auth)
        
        mock_result = MagicMock()
        mock_result.found = True
        mock_result.result_count = 3
        mock_result.results = []
        mock_result.model_dump = MagicMock(return_value={})
        
        # Mock token exchange to succeed (no scopes needed for web_search)
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            # Mock synthesis
            with patch.object(ToolRegistry, 'get', return_value=AsyncMock(return_value=mock_result)):
                with patch.object(agent.synthesis_agent, 'run_stream') as mock_run:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Test response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_run.return_value = mock_stream_result
                    
                    await agent.run_with_streaming("test", mock_stream_callback, mock_cancel_event, test_context)
        
        # Should have thought events
        thought_events = [e for e in mock_stream_callback.events if e.type == "thought"]
        assert len(thought_events) > 0
    
    @pytest.mark.asyncio
    async def test_run_with_streaming_respects_cancellation(
        self, test_agent_config_no_auth, mock_stream_callback, test_context
    ):
        """Test that run_with_streaming respects cancellation even with valid auth."""
        agent = BaseStreamingAgent(test_agent_config_no_auth)
        
        cancel = asyncio.Event()
        cancel.set()  # Already cancelled
        
        # Mock token exchange (though it won't be reached due to early cancellation check)
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await agent.run_with_streaming("test", mock_stream_callback, cancel, test_context)
        
        assert result == ""
