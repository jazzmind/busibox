"""
Unit tests for chat executor service.

These tests focus on:
- Data class construction and validation (no mocks needed)
- Response synthesis logic (no mocks needed - tests formatting)
- Error handling paths

For full integration tests with real web search, document search, and LLM:
See: tests/integration/test_chat_flow.py
"""
import pytest
import uuid

from app.services.chat_executor import (
    ToolExecutionResult,
    AgentExecutionResult,
    ChatExecutionResult,
    synthesize_response,
    execute_tools,
)
from app.schemas.dispatcher import RoutingDecision


# =============================================================================
# Data Class Tests - Pure logic, no mocks needed
# =============================================================================

class TestToolExecutionResult:
    """Test ToolExecutionResult data class."""
    
    def test_creation_success(self):
        """Test successful result creation."""
        result = ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Search results",
            metadata={"query": "test"},
            error=None
        )
        
        assert result.tool_name == "web_search"
        assert result.success is True
        assert result.output == "Search results"
        assert result.metadata["query"] == "test"
        assert result.error is None
    
    def test_creation_failure(self):
        """Test failed result creation."""
        result = ToolExecutionResult(
            tool_name="web_search",
            success=False,
            output="",
            metadata={},
            error="API timeout"
        )
        
        assert result.tool_name == "web_search"
        assert result.success is False
        assert result.output == ""
        assert result.error == "API timeout"
    
    def test_to_dict(self):
        """Test to_dict serialization."""
        result = ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Search results",
            metadata={"query": "test"},
            error=None
        )
        
        result_dict = result.to_dict()
        assert result_dict["tool_name"] == "web_search"
        assert result_dict["success"] is True
        assert result_dict["output"] == "Search results"


class TestAgentExecutionResult:
    """Test AgentExecutionResult data class."""
    
    def test_creation(self):
        """Test result creation."""
        run_id = uuid.uuid4()
        result = AgentExecutionResult(
            agent_id="test-agent",
            run_id=run_id,
            success=True,
            output="Agent output",
            metadata={"test": "data"},
            error=None
        )
        
        assert result.agent_id == "test-agent"
        assert result.run_id == run_id
        assert result.success is True
        assert result.output == "Agent output"
    
    def test_to_dict(self):
        """Test to_dict serialization."""
        run_id = uuid.uuid4()
        result = AgentExecutionResult(
            agent_id="test-agent",
            run_id=run_id,
            success=True,
            output="Agent output",
            metadata={},
            error=None
        )
        
        result_dict = result.to_dict()
        assert result_dict["agent_id"] == "test-agent"
        assert result_dict["run_id"] == str(run_id)


class TestChatExecutionResult:
    """Test ChatExecutionResult data class."""
    
    def test_creation(self):
        """Test result creation with tool and agent results."""
        tool_result = ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Results",
            metadata={},
            error=None
        )
        
        run_id = uuid.uuid4()
        agent_result = AgentExecutionResult(
            agent_id="test-agent",
            run_id=run_id,
            success=True,
            output="Output",
            metadata={},
            error=None
        )
        
        routing_decision = RoutingDecision(
            selected_tools=["web_search"],
            selected_agents=["test-agent"],
            confidence=0.9,
            reasoning="Test routing",
            alternatives=[],
            requires_disambiguation=False
        )
        
        result = ChatExecutionResult(
            content="Final response",
            tool_results=[tool_result],
            agent_results=[agent_result],
            model_used="chat",
            routing_decision=routing_decision
        )
        
        assert result.content == "Final response"
        assert len(result.tool_results) == 1
        assert len(result.agent_results) == 1
        assert result.model_used == "chat"
    
    def test_get_tool_calls_json(self):
        """Test get_tool_calls_json method."""
        tool_result = ToolExecutionResult(
            tool_name="web_search",
            success=True,
            output="Results",
            metadata={},
            error=None
        )
        
        result = ChatExecutionResult(
            content="Response",
            tool_results=[tool_result],
            agent_results=[],
            model_used="chat",
            routing_decision=None
        )
        
        tool_calls = result.get_tool_calls_json()
        assert len(tool_calls) == 1
        assert tool_calls[0]["tool_name"] == "web_search"
    
    def test_get_run_ids(self):
        """Test get_run_ids method."""
        run_id = uuid.uuid4()
        agent_result = AgentExecutionResult(
            agent_id="test-agent",
            run_id=run_id,
            success=True,
            output="Output",
            metadata={},
            error=None
        )
        
        result = ChatExecutionResult(
            content="Response",
            tool_results=[],
            agent_results=[agent_result],
            model_used="chat",
            routing_decision=None
        )
        
        run_ids = result.get_run_ids()
        assert len(run_ids) == 1
        assert run_ids[0] == run_id


# =============================================================================
# Synthesize Response Tests - Tests formatting logic, uses real LLM
# =============================================================================

class TestSynthesizeResponse:
    """Test response synthesis logic."""
    
    @pytest.mark.asyncio
    async def test_with_tool_results(self):
        """Test synthesis with successful tool results."""
        tool_results = [
            ToolExecutionResult(
                tool_name="web_search",
                success=True,
                output="Web search found: AI news from major tech companies",
                metadata={},
                error=None
            ),
            ToolExecutionResult(
                tool_name="doc_search",
                success=True,
                output="Document says: AI is transforming industries",
                metadata={},
                error=None
            )
        ]
        
        response = await synthesize_response(
            query="Tell me about AI",
            tool_results=tool_results,
            agent_results=[],
            model="chat"
        )
        
        # Verify the response includes tool outputs
        assert "Web search found" in response
        assert "Document says" in response
        assert "Tell me about AI" in response
    
    @pytest.mark.asyncio
    async def test_with_agent_results(self):
        """Test synthesis with agent execution results."""
        agent_results = [
            AgentExecutionResult(
                agent_id="test-agent",
                run_id=uuid.uuid4(),
                success=True,
                output="Agent analysis: The data shows positive trends",
                metadata={},
                error=None
            )
        ]
        
        response = await synthesize_response(
            query="Analyze this data",
            tool_results=[],
            agent_results=agent_results,
            model="chat"
        )
        
        # Verify the response includes agent output
        assert "Agent analysis" in response
    
    @pytest.mark.asyncio
    async def test_with_tool_errors(self):
        """Test synthesis gracefully handles tool errors."""
        tool_results = [
            ToolExecutionResult(
                tool_name="web_search",
                success=False,
                output="",
                metadata={},
                error="Search API unavailable"
            )
        ]
        
        response = await synthesize_response(
            query="Search for something",
            tool_results=tool_results,
            agent_results=[],
            model="chat"
        )
        
        # Verify error is communicated
        assert "Search API unavailable" in response
    
    @pytest.mark.asyncio
    async def test_no_results(self):
        """Test synthesis with no results."""
        response = await synthesize_response(
            query="Test query",
            tool_results=[],
            agent_results=[],
            model="chat"
        )
        
        # Should indicate no results were gathered
        assert "wasn't able to gather information" in response.lower()


# =============================================================================
# Execute Tools Tests - Tests empty case and unknown tools
# =============================================================================

class TestExecuteTools:
    """Test tool execution logic."""
    
    @pytest.mark.asyncio
    async def test_empty_tool_list(self):
        """Test execute_tools with empty tool list."""
        results = await execute_tools([], "test query", "user-123")
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_unknown_tool(self):
        """Test execute_tools with unknown tool name."""
        results = await execute_tools(["unknown_tool"], "test query", "user-123")
        # Should skip unknown tools gracefully
        assert len(results) == 0
    
    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_web_search_real(self):
        """Test execute_tools with real web search.
        
        This test uses the real web_search_agent which calls DuckDuckGo.
        Marked as integration since it requires network access.
        """
        results = await execute_tools(["web_search"], "Python programming", "user-123")
        
        assert len(results) == 1
        assert results[0].tool_name == "web_search"
        # May succeed or fail depending on network/API availability
        if results[0].success:
            assert len(results[0].output) > 0
            print(f"Web search returned: {results[0].output[:200]}...")
        else:
            print(f"Web search failed (expected if network unavailable): {results[0].error}")
