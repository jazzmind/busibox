"""
Unit tests for create_agent_from_definition factory function.

Tests cover:
- Creating agents from database definitions
- Default values when workflows is None
- Parsing execution_mode and tool_strategy from strings
- Handling invalid enum values
- Creating pipeline agents when pipeline is defined
"""

from unittest.mock import MagicMock

import pytest

from app.agents.base_agent import (
    AgentConfig,
    BaseStreamingAgent,
    ExecutionMode,
    ToolStrategy,
    create_agent_from_definition,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def basic_definition():
    """Create a basic mock AgentDefinition."""
    definition = MagicMock()
    definition.name = "test-agent"
    definition.display_name = "Test Agent"
    definition.instructions = "You are a helpful test agent."
    definition.tools = {"names": ["web_search"]}
    definition.model = "agent"
    definition.workflows = None
    return definition


@pytest.fixture
def full_definition():
    """Create a fully configured mock AgentDefinition."""
    definition = MagicMock()
    definition.name = "full-agent"
    definition.display_name = "Full Agent"
    definition.instructions = "You are a full-featured agent."
    definition.tools = {"names": ["document_search", "web_search"]}
    definition.model = "gpt-4"
    definition.workflows = {
        "execution_mode": "run_max_iterations",
        "tool_strategy": "sequential",
        "max_iterations": 10,
    }
    return definition


# =============================================================================
# Basic Creation Tests
# =============================================================================


class TestCreateAgentBasic:
    """Tests for basic agent creation."""
    
    def test_creates_agent_from_basic_definition(self, basic_definition):
        """Test creating agent from a basic definition."""
        agent = create_agent_from_definition(basic_definition)
        
        assert isinstance(agent, BaseStreamingAgent)
        assert agent.config.name == "test-agent"
        assert agent.config.display_name == "Test Agent"
    
    def test_uses_definition_instructions(self, basic_definition):
        """Test that instructions are taken from definition."""
        basic_definition.instructions = "Custom instructions here."
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.instructions == "Custom instructions here."
    
    def test_uses_definition_tools(self, basic_definition):
        """Test that tools are taken from definition."""
        basic_definition.tools = {"names": ["web_search", "web_scraper"]}
        agent = create_agent_from_definition(basic_definition)
        
        assert "web_search" in agent.config.tools
        assert "web_scraper" in agent.config.tools
    
    def test_uses_definition_model(self, basic_definition):
        """Test that model is taken from definition."""
        basic_definition.model = "claude-3"
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.model == "claude-3"


# =============================================================================
# Default Values Tests
# =============================================================================


class TestDefaultValues:
    """Tests for default values when workflows is None."""
    
    def test_default_execution_mode_is_run_once(self, basic_definition):
        """Test default execution_mode is RUN_ONCE."""
        basic_definition.workflows = None
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_default_tool_strategy_is_llm_driven(self, basic_definition):
        """Test default tool_strategy is LLM_DRIVEN."""
        basic_definition.workflows = None
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_default_max_iterations_is_5(self, basic_definition):
        """Test default max_iterations is 5."""
        basic_definition.workflows = None
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.max_iterations == 5
    
    def test_default_streaming_is_true(self, basic_definition):
        """Test default streaming is True."""
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.streaming is True
    
    def test_handles_empty_workflows(self, basic_definition):
        """Test handling empty workflows dict."""
        basic_definition.workflows = {}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_handles_missing_display_name(self, basic_definition):
        """Test handling missing display_name."""
        basic_definition.display_name = None
        agent = create_agent_from_definition(basic_definition)
        
        # Should use name as fallback
        assert agent.config.display_name == basic_definition.name
    
    def test_handles_missing_instructions(self, basic_definition):
        """Test handling missing instructions."""
        basic_definition.instructions = None
        agent = create_agent_from_definition(basic_definition)
        
        # Should use default
        assert agent.config.instructions == "You are a helpful assistant."
    
    def test_handles_missing_model(self, basic_definition):
        """Test handling missing model."""
        basic_definition.model = None
        agent = create_agent_from_definition(basic_definition)
        
        # Should use default
        assert agent.config.model == "agent"
    
    def test_handles_empty_tools(self, basic_definition):
        """Test handling empty tools."""
        basic_definition.tools = None
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tools == []
    
    def test_handles_tools_without_names_key(self, basic_definition):
        """Test handling tools dict without 'names' key."""
        basic_definition.tools = {"something_else": ["value"]}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tools == []


# =============================================================================
# ExecutionMode Parsing Tests
# =============================================================================


class TestExecutionModeParsing:
    """Tests for parsing execution_mode from string."""
    
    def test_parses_run_once(self, basic_definition):
        """Test parsing 'run_once' string."""
        basic_definition.workflows = {"execution_mode": "run_once"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_parses_run_until_done(self, basic_definition):
        """Test parsing 'run_until_done' string."""
        basic_definition.workflows = {"execution_mode": "run_until_done"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_UNTIL_DONE
    
    def test_parses_run_max_iterations(self, basic_definition):
        """Test parsing 'run_max_iterations' string."""
        basic_definition.workflows = {"execution_mode": "run_max_iterations"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
    
    def test_invalid_execution_mode_defaults_to_run_once(self, basic_definition):
        """Test invalid execution_mode defaults to RUN_ONCE."""
        basic_definition.workflows = {"execution_mode": "invalid_mode"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_empty_execution_mode_defaults_to_run_once(self, basic_definition):
        """Test empty execution_mode defaults to RUN_ONCE."""
        basic_definition.workflows = {"execution_mode": ""}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_ONCE


# =============================================================================
# ToolStrategy Parsing Tests
# =============================================================================


class TestToolStrategyParsing:
    """Tests for parsing tool_strategy from string."""
    
    def test_parses_sequential(self, basic_definition):
        """Test parsing 'sequential' string."""
        basic_definition.workflows = {"tool_strategy": "sequential"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.SEQUENTIAL
    
    def test_parses_parallel(self, basic_definition):
        """Test parsing 'parallel' string."""
        basic_definition.workflows = {"tool_strategy": "parallel"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.PARALLEL
    
    def test_parses_predefined_pipeline(self, basic_definition):
        """Test parsing 'predefined_pipeline' string."""
        basic_definition.workflows = {"tool_strategy": "predefined_pipeline"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.PREDEFINED_PIPELINE
    
    def test_parses_llm_driven(self, basic_definition):
        """Test parsing 'llm_driven' string."""
        basic_definition.workflows = {"tool_strategy": "llm_driven"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_invalid_tool_strategy_defaults_to_llm_driven(self, basic_definition):
        """Test invalid tool_strategy defaults to LLM_DRIVEN."""
        basic_definition.workflows = {"tool_strategy": "invalid_strategy"}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN
    
    def test_empty_tool_strategy_defaults_to_llm_driven(self, basic_definition):
        """Test empty tool_strategy defaults to LLM_DRIVEN."""
        basic_definition.workflows = {"tool_strategy": ""}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN


# =============================================================================
# Max Iterations Tests
# =============================================================================


class TestMaxIterations:
    """Tests for max_iterations configuration."""
    
    def test_uses_custom_max_iterations(self, basic_definition):
        """Test using custom max_iterations."""
        basic_definition.workflows = {"max_iterations": 10}
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.max_iterations == 10
    
    def test_max_iterations_with_run_max_mode(self, basic_definition):
        """Test max_iterations with RUN_MAX_ITERATIONS mode."""
        basic_definition.workflows = {
            "execution_mode": "run_max_iterations",
            "max_iterations": 15
        }
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
        assert agent.config.max_iterations == 15


# =============================================================================
# Pipeline Creation Tests
# =============================================================================


class TestPipelineCreation:
    """Tests for creating agents with predefined pipelines."""
    
    def test_creates_pipeline_agent_when_pipeline_defined(self, basic_definition):
        """Test creating agent with predefined pipeline."""
        basic_definition.workflows = {
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}}
            ]
        }
        agent = create_agent_from_definition(basic_definition)
        
        from app.agents.base_agent import AgentContext
        context = AgentContext()
        
        steps = agent.pipeline_steps("test query", context)
        
        assert len(steps) == 1
        assert steps[0].tool == "web_search"
    
    def test_pipeline_substitutes_query_placeholder(self, basic_definition):
        """Test that {query} placeholder is substituted."""
        basic_definition.workflows = {
            "pipeline": [
                {"tool": "document_search", "args": {"query": "{query}", "limit": 5}}
            ]
        }
        agent = create_agent_from_definition(basic_definition)
        
        from app.agents.base_agent import AgentContext
        context = AgentContext()
        
        steps = agent.pipeline_steps("my search query", context)
        
        assert steps[0].args["query"] == "my search query"
        assert steps[0].args["limit"] == 5
    
    def test_pipeline_with_multiple_steps(self, basic_definition):
        """Test pipeline with multiple steps."""
        basic_definition.workflows = {
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}},
                {"tool": "web_scraper", "args": {"url": "https://example.com"}},
            ]
        }
        agent = create_agent_from_definition(basic_definition)
        
        from app.agents.base_agent import AgentContext
        context = AgentContext()
        
        steps = agent.pipeline_steps("test", context)
        
        assert len(steps) == 2
        assert steps[0].tool == "web_search"
        assert steps[1].tool == "web_scraper"
    
    def test_empty_pipeline_returns_base_agent(self, basic_definition):
        """Test empty pipeline list returns BaseStreamingAgent."""
        basic_definition.workflows = {"pipeline": []}
        agent = create_agent_from_definition(basic_definition)
        
        # Should still be a valid agent
        assert isinstance(agent, BaseStreamingAgent)
    
    def test_pipeline_step_without_args(self, basic_definition):
        """Test pipeline step without args."""
        basic_definition.workflows = {
            "pipeline": [
                {"tool": "web_search"}
            ]
        }
        agent = create_agent_from_definition(basic_definition)
        
        from app.agents.base_agent import AgentContext
        context = AgentContext()
        
        steps = agent.pipeline_steps("test", context)
        
        assert steps[0].args == {}


# =============================================================================
# Full Configuration Tests
# =============================================================================


class TestFullConfiguration:
    """Tests for fully configured agent definitions."""
    
    def test_full_configuration(self, full_definition):
        """Test agent with full configuration."""
        agent = create_agent_from_definition(full_definition)
        
        assert agent.config.name == "full-agent"
        assert agent.config.display_name == "Full Agent"
        assert agent.config.model == "gpt-4"
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
        assert agent.config.tool_strategy == ToolStrategy.SEQUENTIAL
        assert agent.config.max_iterations == 10
        assert "document_search" in agent.config.tools
        assert "web_search" in agent.config.tools
    
    def test_combined_workflows_and_pipeline(self, basic_definition):
        """Test workflows with both settings and pipeline."""
        basic_definition.workflows = {
            "execution_mode": "run_max_iterations",
            "tool_strategy": "predefined_pipeline",
            "max_iterations": 3,
            "pipeline": [
                {"tool": "web_search", "args": {"query": "{query}"}}
            ]
        }
        agent = create_agent_from_definition(basic_definition)
        
        assert agent.config.execution_mode == ExecutionMode.RUN_MAX_ITERATIONS
        assert agent.config.tool_strategy == ToolStrategy.PREDEFINED_PIPELINE
        assert agent.config.max_iterations == 3
        
        from app.agents.base_agent import AgentContext
        context = AgentContext()
        
        steps = agent.pipeline_steps("test", context)
        assert len(steps) == 1
