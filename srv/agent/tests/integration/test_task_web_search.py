"""
Integration tests to diagnose task execution path vs chat execution path.

This test file isolates the differences between:
1. Chat path: chat_executor.py calls web_search_agent.run(query) directly
2. Task path: scheduler.py -> run_service.create_run() calls agent.run(prompt, context={...})

The goal is to identify why tool calls work in chat but fail in task mode.
"""

import asyncio
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.agents.web_search_agent import WebSearchAgent, web_search_agent
from app.agents.base_agent import AgentContext, PipelineStep, ToolRegistry
from app.schemas.streaming import StreamEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def fresh_web_search_agent():
    """Create a fresh WebSearchAgent instance for each test."""
    return WebSearchAgent()


@pytest.fixture
def mock_stream_callback():
    """Create a mock streaming callback that collects events."""
    events: List[StreamEvent] = []
    
    async def callback(event: StreamEvent):
        events.append(event)
    
    callback.events = events
    return callback


@pytest.fixture
def mock_cancel_event():
    """Create a mock cancellation event."""
    return asyncio.Event()


@pytest.fixture
def mock_search_result():
    """Create a mock web search result."""
    result = MagicMock()
    result.found = True
    result.result_count = 3
    result.results = [
        MagicMock(
            title="First Result",
            url="https://example.com/1",
            snippet="First snippet text about latest news",
            source="duckduckgo",
        ),
        MagicMock(
            title="Second Result",
            url="https://example.com/2",
            snippet="Second snippet about current events",
            source="duckduckgo",
        ),
        MagicMock(
            title="Third Result",
            url="https://example.com/3",
            snippet="Third snippet with more information",
            source="duckduckgo",
        ),
    ]
    result.providers_used = ["duckduckgo"]
    result.error = None
    result.model_dump = MagicMock(return_value={"found": True, "result_count": 3})
    return result


@pytest.fixture
def mock_scrape_result():
    """Create a mock scrape result."""
    result = MagicMock()
    result.success = True
    result.url = "https://example.com/1"
    result.title = "Scraped Page Title"
    result.content = "This is the full scraped content from the web page with detailed information."
    result.word_count = 15
    result.error = None
    result.model_dump = MagicMock(return_value={"success": True, "content": result.content})
    return result


@pytest.fixture
def mock_principal():
    """Create a mock principal for task-style execution."""
    from app.schemas.auth import Principal
    return Principal(
        sub="test-user-123",
        scopes=["agent:read", "agent:write"],
        token="mock-token-for-testing",
    )


@pytest.fixture
def task_style_context(mock_principal):
    """
    Create a context dict that mimics how tasks invoke agents.
    
    This is the context passed from scheduler.py -> run_service.py -> agent.run()
    """
    return {
        "principal": mock_principal,
        "session": AsyncMock(),  # Mock session
        "user_id": "test-user-123",
        "agent_id": str(uuid.uuid4()),
    }


# =============================================================================
# Tool Registration Tests
# =============================================================================


class TestToolRegistration:
    """Verify that tools are properly registered and accessible."""
    
    def test_web_search_tool_is_registered(self):
        """Test that web_search tool is in the registry."""
        assert ToolRegistry.has("web_search"), "web_search tool should be registered"
        tool_func = ToolRegistry.get("web_search")
        assert tool_func is not None, "web_search tool function should not be None"
        assert callable(tool_func), "web_search tool should be callable"
    
    def test_web_scraper_tool_is_registered(self):
        """Test that web_scraper tool is in the registry."""
        assert ToolRegistry.has("web_scraper"), "web_scraper tool should be registered"
        tool_func = ToolRegistry.get("web_scraper")
        assert tool_func is not None, "web_scraper tool function should not be None"
        assert callable(tool_func), "web_scraper tool should be callable"
    
    def test_all_web_search_agent_tools_registered(self, fresh_web_search_agent):
        """Test that all tools required by WebSearchAgent are registered."""
        for tool_name in fresh_web_search_agent.config.tools:
            assert ToolRegistry.has(tool_name), f"Tool {tool_name} should be registered"
    
    def test_list_registered_tools(self):
        """List all registered tools for debugging."""
        # Access the internal _tools dict for debugging
        registered = list(ToolRegistry._tools.keys())
        print(f"Registered tools: {registered}")
        assert len(registered) > 0, "At least some tools should be registered"


# =============================================================================
# Chat Path Tests (Direct Invocation)
# =============================================================================


@pytest.mark.asyncio
class TestChatPathExecution:
    """
    Test the chat execution path: direct agent.run() without context.
    
    This mimics how chat_executor.py calls the agent:
    result = await web_search_agent.run(query)
    """
    
    async def test_chat_path_no_context(
        self,
        fresh_web_search_agent,
        mock_search_result,
        mock_scrape_result,
    ):
        """Test agent called without context (chat path)."""
        # Track tool calls
        tool_calls = []
        
        async def mock_web_search(**kwargs):
            tool_calls.append(("web_search", kwargs))
            return mock_search_result
        
        async def mock_web_scraper(**kwargs):
            tool_calls.append(("web_scraper", kwargs))
            return mock_scrape_result
        
        with patch.object(ToolRegistry, 'get') as mock_get:
            def get_tool(name):
                if name == "web_search":
                    return mock_web_search
                elif name == "web_scraper":
                    return mock_web_scraper
                return None
            
            mock_get.side_effect = get_tool
            
            # Also mock the synthesis agent to avoid LLM call
            with patch.object(fresh_web_search_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                mock_stream_result = MagicMock()
                mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                
                async def mock_stream_text(delta=True):
                    yield "Test synthesis response about the news"
                
                mock_stream_result.stream_text = mock_stream_text
                mock_synthesis.return_value = mock_stream_result
                
                # Call agent WITHOUT context (chat path)
                result = await fresh_web_search_agent.run("latest news")
        
        # Verify tools were called
        assert len(tool_calls) > 0, f"Expected tool calls but got none. Tool calls: {tool_calls}"
        
        # Verify web_search was called
        web_search_calls = [c for c in tool_calls if c[0] == "web_search"]
        assert len(web_search_calls) >= 1, "web_search should have been called"
        
        print(f"Chat path tool calls: {tool_calls}")


# =============================================================================
# Task Path Tests (With Context)
# =============================================================================


@pytest.mark.asyncio
class TestTaskPathExecution:
    """
    Test the task execution path: agent.run() with context dict.
    
    This mimics how run_service.py calls the agent:
    context = {
        "principal": principal,
        "session": session,
        "user_id": principal.sub,
        "agent_id": str(agent_id),
    }
    result = await agent.run(prompt, context=context)
    """
    
    async def test_task_path_with_context(
        self,
        fresh_web_search_agent,
        task_style_context,
        mock_search_result,
        mock_scrape_result,
    ):
        """Test agent called with task-style context."""
        # Track tool calls
        tool_calls = []
        
        async def mock_web_search(**kwargs):
            tool_calls.append(("web_search", kwargs))
            return mock_search_result
        
        async def mock_web_scraper(**kwargs):
            tool_calls.append(("web_scraper", kwargs))
            return mock_scrape_result
        
        with patch.object(ToolRegistry, 'get') as mock_get:
            def get_tool(name):
                if name == "web_search":
                    return mock_web_search
                elif name == "web_scraper":
                    return mock_web_scraper
                return None
            
            mock_get.side_effect = get_tool
            
            # Mock synthesis
            with patch.object(fresh_web_search_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                mock_stream_result = MagicMock()
                mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                
                async def mock_stream_text(delta=True):
                    yield "Test synthesis response"
                
                mock_stream_result.stream_text = mock_stream_text
                mock_synthesis.return_value = mock_stream_result
                
                # Call agent WITH context (task path)
                result = await fresh_web_search_agent.run(
                    "latest news",
                    context=task_style_context,
                )
        
        # Verify tools were called
        assert len(tool_calls) > 0, f"Expected tool calls but got none. Tool calls: {tool_calls}"
        
        # Verify web_search was called
        web_search_calls = [c for c in tool_calls if c[0] == "web_search"]
        assert len(web_search_calls) >= 1, "web_search should have been called in task path"
        
        print(f"Task path tool calls: {tool_calls}")
    
    async def test_task_path_context_setup(
        self,
        fresh_web_search_agent,
        task_style_context,
        mock_stream_callback,
    ):
        """Test that _setup_context properly processes task context."""
        # Directly test _setup_context
        agent_context = await fresh_web_search_agent._setup_context(
            task_style_context,
            mock_stream_callback,
        )
        
        # Context should be returned (not None) since web_search doesn't require auth scopes
        assert agent_context is not None, "Agent context should not be None"
        assert agent_context.principal is not None, "Principal should be set"
        assert agent_context.user_id == "test-user-123", "User ID should be set"
        
        print(f"Agent context setup: principal={agent_context.principal}, user_id={agent_context.user_id}")
    
    async def test_task_path_without_auth_scopes(
        self,
        fresh_web_search_agent,
        mock_stream_callback,
    ):
        """
        Test that WebSearchAgent doesn't require auth scopes.
        
        The fix in base_agent.py made auth conditional on whether the agent's
        tools require scopes. web_search and web_scraper have empty scopes.
        """
        # WebSearchAgent uses tools with no scopes
        scopes = fresh_web_search_agent.config.get_required_scopes()
        assert scopes == [], f"WebSearchAgent should require no scopes, got: {scopes}"
        
        # Context without principal should still work
        minimal_context = {
            "user_id": "test-user",
        }
        
        agent_context = await fresh_web_search_agent._setup_context(
            minimal_context,
            mock_stream_callback,
        )
        
        # Should succeed since no auth is required
        assert agent_context is not None, "Context setup should succeed without auth for WebSearchAgent"


# =============================================================================
# Pipeline Execution Tests
# =============================================================================


@pytest.mark.asyncio
class TestPipelineExecution:
    """Test that the pipeline executes correctly in both paths."""
    
    async def test_pipeline_steps_generated(self, fresh_web_search_agent):
        """Test that pipeline_steps() returns correct steps."""
        context = AgentContext()
        steps = fresh_web_search_agent.pipeline_steps("test query", context)
        
        assert len(steps) >= 1, "Should have at least one pipeline step"
        assert steps[0].tool == "web_search", "First step should be web_search"
        assert steps[0].args.get("query") == "test query", "Query should be passed to tool"
        
        print(f"Pipeline steps: {[(s.tool, s.args) for s in steps]}")
    
    async def test_process_tool_result_adds_scrape_steps(
        self,
        fresh_web_search_agent,
        mock_search_result,
    ):
        """Test that process_tool_result adds scrape steps after search."""
        context = AgentContext()
        step = PipelineStep(tool="web_search", args={"query": "test"})
        
        additional_steps = await fresh_web_search_agent.process_tool_result(
            step, mock_search_result, context
        )
        
        # Should add scrape steps for top 3 results
        assert len(additional_steps) == 3, f"Expected 3 scrape steps, got {len(additional_steps)}"
        assert all(s.tool == "web_scraper" for s in additional_steps), "All additional steps should be web_scraper"
        
        # Check URLs are correct
        urls = [s.args.get("url") for s in additional_steps]
        assert "https://example.com/1" in urls
        assert "https://example.com/2" in urls
        assert "https://example.com/3" in urls
        
        print(f"Additional scrape steps: {urls}")


# =============================================================================
# Comparison Tests
# =============================================================================


@pytest.mark.asyncio
class TestChatVsTaskComparison:
    """
    Direct comparison tests between chat and task paths.
    
    These tests run the same query through both paths and compare results.
    """
    
    async def test_both_paths_execute_tools(
        self,
        mock_search_result,
        mock_scrape_result,
    ):
        """Test that both chat and task paths execute tools."""
        chat_tool_calls = []
        task_tool_calls = []
        
        async def make_mock_tools(tool_calls_list):
            async def mock_web_search(**kwargs):
                tool_calls_list.append(("web_search", kwargs))
                return mock_search_result
            
            async def mock_web_scraper(**kwargs):
                tool_calls_list.append(("web_scraper", kwargs))
                return mock_scrape_result
            
            return mock_web_search, mock_web_scraper
        
        # Test chat path
        chat_agent = WebSearchAgent()
        chat_search, chat_scraper = await make_mock_tools(chat_tool_calls)
        
        with patch.object(ToolRegistry, 'get') as mock_get:
            mock_get.side_effect = lambda name: chat_search if name == "web_search" else chat_scraper if name == "web_scraper" else None
            
            with patch.object(chat_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                mock_stream_result = MagicMock()
                mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                mock_stream_result.stream_text = lambda delta=True: (x for x in ["Response"])
                async def gen():
                    yield "Response"
                mock_stream_result.stream_text = gen
                mock_synthesis.return_value = mock_stream_result
                
                await chat_agent.run("latest news")
        
        # Test task path
        task_agent = WebSearchAgent()
        task_search, task_scraper = await make_mock_tools(task_tool_calls)
        
        from app.schemas.auth import Principal
        task_context = {
            "principal": Principal(sub="test-user", scopes=[], token="test-token"),
            "session": AsyncMock(),
            "user_id": "test-user",
        }
        
        with patch.object(ToolRegistry, 'get') as mock_get:
            mock_get.side_effect = lambda name: task_search if name == "web_search" else task_scraper if name == "web_scraper" else None
            
            with patch.object(task_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                mock_stream_result = MagicMock()
                mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                async def gen():
                    yield "Response"
                mock_stream_result.stream_text = gen
                mock_synthesis.return_value = mock_stream_result
                
                await task_agent.run("latest news", context=task_context)
        
        # Compare results
        print(f"Chat path tool calls: {len(chat_tool_calls)}")
        print(f"Task path tool calls: {len(task_tool_calls)}")
        
        assert len(chat_tool_calls) > 0, "Chat path should have tool calls"
        assert len(task_tool_calls) > 0, "Task path should have tool calls"
        
        # Both paths should call web_search
        chat_search_calls = [c for c in chat_tool_calls if c[0] == "web_search"]
        task_search_calls = [c for c in task_tool_calls if c[0] == "web_search"]
        
        assert len(chat_search_calls) == len(task_search_calls), \
            f"Both paths should have same number of web_search calls: chat={len(chat_search_calls)}, task={len(task_search_calls)}"


# =============================================================================
# Diagnostic Tests
# =============================================================================


@pytest.mark.asyncio
class TestDiagnostics:
    """Diagnostic tests to help identify issues."""
    
    async def test_agent_config(self, fresh_web_search_agent):
        """Print agent configuration for debugging."""
        config = fresh_web_search_agent.config
        print(f"Agent name: {config.name}")
        print(f"Display name: {config.display_name}")
        print(f"Tools: {config.tools}")
        print(f"Execution mode: {config.execution_mode}")
        print(f"Tool strategy: {config.tool_strategy}")
        print(f"Requires auth: {config.requires_auth()}")
        print(f"Required scopes: {config.get_required_scopes()}")
        
        assert config.name == "web-search-agent"
        assert "web_search" in config.tools
        assert "web_scraper" in config.tools
    
    async def test_run_method_signature(self, fresh_web_search_agent):
        """Verify run method accepts context parameter."""
        import inspect
        sig = inspect.signature(fresh_web_search_agent.run)
        params = list(sig.parameters.keys())
        
        print(f"run() parameters: {params}")
        
        assert "query" in params, "run() should accept 'query' parameter"
        assert "context" in params, "run() should accept 'context' parameter"
    
    async def test_streaming_events_collected(
        self,
        fresh_web_search_agent,
        mock_stream_callback,
        mock_cancel_event,
        mock_search_result,
        mock_scrape_result,
    ):
        """Test that streaming events are properly collected."""
        with patch.object(ToolRegistry, 'get') as mock_get:
            def get_tool(name):
                if name == "web_search":
                    return AsyncMock(return_value=mock_search_result)
                elif name == "web_scraper":
                    return AsyncMock(return_value=mock_scrape_result)
                return None
            
            mock_get.side_effect = get_tool
            
            with patch.object(fresh_web_search_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                mock_stream_result = MagicMock()
                mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                async def gen():
                    yield "Response"
                mock_stream_result.stream_text = gen
                mock_synthesis.return_value = mock_stream_result
                
                await fresh_web_search_agent.run_with_streaming(
                    query="test query",
                    stream=mock_stream_callback,
                    cancel=mock_cancel_event,
                    context=None,
                )
        
        events = mock_stream_callback.events
        print(f"Collected {len(events)} streaming events:")
        for event in events:
            print(f"  - {event.type}: {event.message[:50] if event.message else 'N/A'}...")
        
        # Should have tool_start and tool_result events
        tool_start_events = [e for e in events if e.type == "tool_start"]
        tool_result_events = [e for e in events if e.type == "tool_result"]
        
        assert len(tool_start_events) > 0, "Should have tool_start events"
        assert len(tool_result_events) > 0, "Should have tool_result events"
