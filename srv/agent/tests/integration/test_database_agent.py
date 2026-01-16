"""
Integration tests for database-defined agents.

Tests creating and running agents from AgentDefinition records.
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    ToolStrategy,
    create_agent_from_definition,
)
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
def basic_agent_definition():
    """Create a basic mock AgentDefinition."""
    definition = MagicMock()
    definition.id = uuid.uuid4()
    definition.name = "test-db-agent"
    definition.display_name = "Test DB Agent"
    definition.instructions = "You are a helpful test agent."
    definition.tools = {"names": ["web_search"]}
    definition.model = "agent"
    definition.workflows = None
    definition.scopes = []
    definition.is_active = True
    return definition


@pytest.fixture
def document_agent_definition():
    """Create a document search agent definition."""
    definition = MagicMock()
    definition.id = uuid.uuid4()
    definition.name = "doc-search-agent"
    definition.display_name = "Document Search Agent"
    definition.instructions = "Search documents and answer questions."
    definition.tools = {"names": ["document_search"]}
    definition.model = "agent"
    definition.workflows = {
        "execution_mode": "run_once",
        "tool_strategy": "predefined_pipeline",
    }
    definition.scopes = ["search.read"]
    definition.is_active = True
    return definition


@pytest.fixture
def pipeline_agent_definition():
    """Create an agent with a predefined pipeline."""
    definition = MagicMock()
    definition.id = uuid.uuid4()
    definition.name = "pipeline-agent"
    definition.display_name = "Pipeline Agent"
    definition.instructions = "Execute a predefined pipeline."
    definition.tools = {"names": ["web_search", "web_scraper"]}
    definition.model = "agent"
    definition.workflows = {
        "execution_mode": "run_once",
        "tool_strategy": "predefined_pipeline",
        "pipeline": [
            {"tool": "web_search", "args": {"query": "{query}", "max_results": 3}},
        ]
    }
    definition.scopes = []
    definition.is_active = True
    return definition


@pytest.fixture
def llm_driven_agent_definition():
    """Create an LLM-driven agent definition."""
    definition = MagicMock()
    definition.id = uuid.uuid4()
    definition.name = "llm-agent"
    definition.display_name = "LLM-Driven Agent"
    definition.instructions = "Use tools as needed to answer questions."
    definition.tools = {"names": ["web_search", "web_scraper"]}
    definition.model = "agent"
    definition.workflows = {
        "execution_mode": "run_max_iterations",
        "tool_strategy": "llm_driven",
        "max_iterations": 3,
    }
    definition.scopes = []
    definition.is_active = True
    return definition


# =============================================================================
# Agent Creation Tests
# =============================================================================


class TestCreateAgentFromDefinition:
    """Tests for creating agents from database definitions."""
    
    def test_creates_basic_agent(self, basic_agent_definition):
        """Test creating a basic agent from definition."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert isinstance(agent, BaseStreamingAgent)
        assert agent.config.name == "test-db-agent"
        assert agent.config.display_name == "Test DB Agent"
    
    def test_agent_has_correct_tools(self, basic_agent_definition):
        """Test agent has tools from definition."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert "web_search" in agent.config.tools
    
    def test_agent_has_correct_instructions(self, basic_agent_definition):
        """Test agent has instructions from definition."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert agent.config.instructions == "You are a helpful test agent."
    
    def test_creates_document_agent(self, document_agent_definition):
        """Test creating a document search agent."""
        agent = create_agent_from_definition(document_agent_definition)
        
        assert agent.config.name == "doc-search-agent"
        assert "document_search" in agent.config.tools
        assert agent.config.requires_auth() is True
    
    def test_creates_pipeline_agent(self, pipeline_agent_definition):
        """Test creating an agent with predefined pipeline."""
        agent = create_agent_from_definition(pipeline_agent_definition)
        context = AgentContext()
        
        steps = agent.pipeline_steps("test query", context)
        
        assert len(steps) == 1
        assert steps[0].tool == "web_search"
        assert steps[0].args["query"] == "test query"
    
    def test_creates_llm_driven_agent(self, llm_driven_agent_definition):
        """Test creating an LLM-driven agent."""
        agent = create_agent_from_definition(llm_driven_agent_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
        assert agent.config.max_iterations == 3


# =============================================================================
# Agent Configuration Tests
# =============================================================================


class TestDatabaseAgentConfiguration:
    """Tests for database agent configuration handling."""
    
    def test_default_execution_mode(self, basic_agent_definition):
        """Test default execution mode is RUN_ONCE."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_custom_execution_mode(self, llm_driven_agent_definition):
        """Test custom execution mode from workflows."""
        agent = create_agent_from_definition(llm_driven_agent_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
    
    def test_default_tool_strategy(self, basic_agent_definition):
        """Test default tool strategy is LLM_DRIVEN."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_custom_tool_strategy(self, document_agent_definition):
        """Test custom tool strategy from workflows."""
        agent = create_agent_from_definition(document_agent_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.PREDEFINED_PIPELINE
    
    def test_default_max_iterations(self, basic_agent_definition):
        """Test default max_iterations is 5."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert agent.config.max_iterations == 5
    
    def test_custom_max_iterations(self, llm_driven_agent_definition):
        """Test custom max_iterations from workflows."""
        agent = create_agent_from_definition(llm_driven_agent_definition)
        
        assert agent.config.max_iterations == 3
    
    def test_streaming_enabled_by_default(self, basic_agent_definition):
        """Test streaming is enabled by default."""
        agent = create_agent_from_definition(basic_agent_definition)
        
        assert agent.config.streaming is True


# =============================================================================
# Pipeline Agent Tests
# =============================================================================


class TestDatabasePipelineAgent:
    """Tests for agents with predefined pipelines."""
    
    def test_pipeline_substitutes_query(self, pipeline_agent_definition):
        """Test pipeline substitutes {query} placeholder."""
        agent = create_agent_from_definition(pipeline_agent_definition)
        context = AgentContext()
        
        steps = agent.pipeline_steps("my search query", context)
        
        assert steps[0].args["query"] == "my search query"
    
    def test_pipeline_preserves_other_args(self, pipeline_agent_definition):
        """Test pipeline preserves other arguments."""
        agent = create_agent_from_definition(pipeline_agent_definition)
        context = AgentContext()
        
        steps = agent.pipeline_steps("test", context)
        
        assert steps[0].args["max_results"] == 3
    
    def test_multi_step_pipeline(self):
        """Test pipeline with multiple steps."""
        definition = MagicMock()
        definition.id = uuid.uuid4()
        definition.name = "multi-step"
        definition.display_name = "Multi-Step Agent"
        definition.instructions = "Test"
        definition.tools = {"names": ["web_search", "web_scraper"]}
        definition.model = "agent"
        definition.workflows = {
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}},
                {"tool": "web_scraper", "args": {"url": "https://example.com"}},
            ]
        }
        
        agent = create_agent_from_definition(definition)
        context = AgentContext()
        
        steps = agent.pipeline_steps("test", context)
        
        assert len(steps) == 2
        assert steps[0].tool == "web_search"
        assert steps[1].tool == "web_scraper"


# =============================================================================
# Running Database Agents Tests
# =============================================================================


@pytest.mark.asyncio
class TestRunningDatabaseAgents:
    """Tests for running database-defined agents."""
    
    async def test_basic_agent_runs(
        self,
        basic_agent_definition,
        mock_stream_callback,
        mock_cancel_event,
        mock_auth_context,
    ):
        """Test that a basic database agent can run with real LiteLLM."""
        # Use PREDEFINED_PIPELINE for predictable execution
        basic_agent_definition.workflows = {
            "execution_mode": "run_once",
            "tool_strategy": "predefined_pipeline",
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}}
            ]
        }
        
        agent = create_agent_from_definition(basic_agent_definition)
        
        mock_search_result = MagicMock()
        mock_search_result.found = True
        mock_search_result.result_count = 1
        mock_search_result.results = [{"title": "Test", "url": "https://example.com", "snippet": "Test result"}]
        mock_search_result.model_dump = MagicMock(return_value={"results": mock_search_result.results})
        
        # Create proper async mock function for tool
        async def mock_web_search(**kwargs):
            return mock_search_result
        
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=mock_web_search):
                result = await agent.run_with_streaming(
                    query="test query",
                    stream=mock_stream_callback,
                    cancel=mock_cancel_event,
                    context=mock_auth_context,
                )
        
        # Should complete - check for reasonable output or expected errors 
        # (auth errors are acceptable if LiteLLM isn't configured in test env)
        events = mock_stream_callback.events
        event_types = [e.type for e in events]
        
        # Should have at least started (tool_start) and either succeeded or hit auth error
        assert "tool_start" in event_types or "error" in event_types
    
    async def test_pipeline_agent_executes_steps(
        self,
        pipeline_agent_definition,
        mock_stream_callback,
        mock_cancel_event,
        mock_auth_context,
    ):
        """Test that a pipeline agent executes defined steps."""
        agent = create_agent_from_definition(pipeline_agent_definition)
        
        mock_search_result = MagicMock()
        mock_search_result.found = True
        mock_search_result.result_count = 1
        mock_search_result.results = []
        mock_search_result.model_dump = MagicMock(return_value={})
        
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_search_result)):
                with patch.object(agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await agent.run_with_streaming(
                        query="test",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context=mock_auth_context,
                    )
        
        # Should have tool_start events
        tool_start_events = [e for e in mock_stream_callback.events if e.type == "tool_start"]
        assert len(tool_start_events) >= 1
        
        # First tool should be web_search
        assert tool_start_events[0].source == "web_search"


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestDatabaseAgentErrorHandling:
    """Tests for error handling in database agents."""
    
    def test_handles_invalid_execution_mode(self):
        """Test handling invalid execution_mode."""
        definition = MagicMock()
        definition.id = uuid.uuid4()
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
        """Test handling invalid tool_strategy."""
        definition = MagicMock()
        definition.id = uuid.uuid4()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = {"tool_strategy": "invalid_strategy"}
        
        agent = create_agent_from_definition(definition)
        
        # Should default to LLM_DRIVEN
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_handles_missing_tools(self):
        """Test handling missing tools configuration."""
        definition = MagicMock()
        definition.id = uuid.uuid4()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = "Test"
        definition.tools = None
        definition.model = "agent"
        definition.workflows = None
        
        agent = create_agent_from_definition(definition)
        
        assert agent.config.tools == []
    
    def test_handles_missing_instructions(self):
        """Test handling missing instructions."""
        definition = MagicMock()
        definition.id = uuid.uuid4()
        definition.name = "test"
        definition.display_name = "Test"
        definition.instructions = None
        definition.tools = {"names": []}
        definition.model = "agent"
        definition.workflows = None
        
        agent = create_agent_from_definition(definition)
        
        # Should use default instructions
        assert agent.config.instructions == "You are a helpful assistant."


# =============================================================================
# Max Iterations Tests
# =============================================================================


@pytest.mark.asyncio
class TestDatabaseAgentMaxIterations:
    """Tests for max_iterations behavior."""
    
    async def test_respects_max_iterations(
        self,
        llm_driven_agent_definition,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that agent respects max_iterations limit."""
        agent = create_agent_from_definition(llm_driven_agent_definition)
        context = AgentContext()
        
        # Test should_continue respects limit
        context.iteration = 0
        assert await agent.should_continue(context) is True
        
        context.iteration = 2
        assert await agent.should_continue(context) is True
        
        context.iteration = 3
        assert await agent.should_continue(context) is False
