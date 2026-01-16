"""
Integration tests for WebSearchAgent.

Tests the web search agent with real API calls (when available)
or mocked services for CI environments.
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.web_search_agent import WebSearchAgent
from app.agents.base_agent import AgentContext, PipelineStep, ExecutionMode, ToolStrategy
from app.schemas.streaming import StreamEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def web_search_agent():
    """Create a WebSearchAgent instance."""
    return WebSearchAgent()


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
def mock_search_result():
    """Create a mock web search result."""
    result = MagicMock()
    result.found = True
    result.result_count = 3
    result.results = [
        MagicMock(
            title="First Result",
            url="https://example.com/1",
            snippet="First snippet text",
            source="duckduckgo",
        ),
        MagicMock(
            title="Second Result",
            url="https://example.com/2",
            snippet="Second snippet text",
            source="duckduckgo",
        ),
        MagicMock(
            title="Third Result",
            url="https://example.com/3",
            snippet="Third snippet text",
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
    result.content = "This is the full scraped content from the web page."
    result.word_count = 10
    result.error = None
    result.model_dump = MagicMock(return_value={"success": True})
    return result


@pytest.fixture
def mock_failed_scrape_result():
    """Create a mock failed scrape result."""
    result = MagicMock()
    result.success = False
    result.url = "https://example.com/1"
    result.error = "Connection timeout"
    result.model_dump = MagicMock(return_value={"success": False})
    return result


# =============================================================================
# Agent Configuration Tests
# =============================================================================


class TestWebSearchAgentConfiguration:
    """Tests for WebSearchAgent configuration."""
    
    def test_agent_has_correct_name(self, web_search_agent):
        """Test agent has correct display name."""
        assert web_search_agent.name == "Web Search Agent"
        assert web_search_agent.config.display_name == "Web Search Agent"
    
    def test_agent_has_correct_tools(self, web_search_agent):
        """Test agent is configured with web_search and web_scraper tools."""
        assert "web_search" in web_search_agent.config.tools
        assert "web_scraper" in web_search_agent.config.tools
    
    def test_agent_execution_mode_is_run_once(self, web_search_agent):
        """Test agent uses RUN_ONCE execution mode."""
        assert web_search_agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_agent_tool_strategy_is_sequential(self, web_search_agent):
        """Test agent uses SEQUENTIAL strategy for dynamic pipeline."""
        assert web_search_agent.config.tool_strategy == ToolStrategy.SEQUENTIAL
    
    def test_agent_config_does_not_require_auth_scopes(self, web_search_agent):
        """Test agent config has no required scopes (auth is still required at runtime)."""
        # The agent config doesn't require specific scopes,
        # but all agents require authentication at runtime
        assert web_search_agent.config.requires_auth() is False
    
    def test_agent_required_scopes_empty(self, web_search_agent):
        """Test agent requires no scopes."""
        scopes = web_search_agent.config.get_required_scopes()
        assert scopes == []


# =============================================================================
# Pipeline Tests
# =============================================================================


class TestWebSearchAgentPipeline:
    """Tests for WebSearchAgent pipeline."""
    
    def test_pipeline_steps_returns_search_step(self, web_search_agent):
        """Test pipeline_steps returns web_search step."""
        context = AgentContext()
        steps = web_search_agent.pipeline_steps("test query", context)
        
        assert len(steps) == 1
        assert steps[0].tool == "web_search"
    
    def test_pipeline_step_includes_query(self, web_search_agent):
        """Test pipeline step includes the query in args."""
        context = AgentContext()
        steps = web_search_agent.pipeline_steps("my search query", context)
        
        assert steps[0].args["query"] == "my search query"
    
    def test_pipeline_step_has_max_results(self, web_search_agent):
        """Test pipeline step has max_results parameter."""
        context = AgentContext()
        steps = web_search_agent.pipeline_steps("test", context)
        
        assert steps[0].args["max_results"] == 5


# =============================================================================
# Dynamic Pipeline Tests
# =============================================================================


@pytest.mark.asyncio
class TestWebSearchAgentDynamicPipeline:
    """Tests for dynamic pipeline behavior."""
    
    async def test_process_tool_result_adds_scrape_steps(
        self,
        web_search_agent,
        mock_search_result,
    ):
        """Test that process_tool_result adds scrape steps after search."""
        context = AgentContext()
        step = PipelineStep(tool="web_search", args={"query": "test"})
        
        additional_steps = await web_search_agent.process_tool_result(
            step, mock_search_result, context
        )
        
        # Should add scrape steps for top 3 results
        assert len(additional_steps) == 3
        assert all(s.tool == "web_scraper" for s in additional_steps)
    
    async def test_scrape_steps_have_correct_urls(
        self,
        web_search_agent,
        mock_search_result,
    ):
        """Test that scrape steps have correct URLs from search results."""
        context = AgentContext()
        step = PipelineStep(tool="web_search", args={"query": "test"})
        
        additional_steps = await web_search_agent.process_tool_result(
            step, mock_search_result, context
        )
        
        urls = [s.args["url"] for s in additional_steps]
        assert "https://example.com/1" in urls
        assert "https://example.com/2" in urls
        assert "https://example.com/3" in urls
    
    async def test_process_scrape_result_updates_content(
        self,
        web_search_agent,
        mock_search_result,
        mock_scrape_result,
    ):
        """Test that process_tool_result updates scraped content."""
        context = AgentContext()
        
        # First, process search result to populate _scraped_content
        search_step = PipelineStep(tool="web_search", args={"query": "test"})
        await web_search_agent.process_tool_result(search_step, mock_search_result, context)
        
        # Then process scrape result
        scrape_step = PipelineStep(
            tool="web_scraper",
            args={"url": "https://example.com/1"}
        )
        await web_search_agent.process_tool_result(scrape_step, mock_scrape_result, context)
        
        # Check that content was updated
        entry = next(
            (e for e in web_search_agent._scraped_content if e["url"] == "https://example.com/1"),
            None
        )
        assert entry is not None
        assert entry["content"] == mock_scrape_result.content
        assert entry["snippet_only"] is False
    
    async def test_failed_scrape_keeps_snippet(
        self,
        web_search_agent,
        mock_search_result,
        mock_failed_scrape_result,
    ):
        """Test that failed scrape keeps original snippet."""
        context = AgentContext()
        
        # First, process search result
        search_step = PipelineStep(tool="web_search", args={"query": "test"})
        await web_search_agent.process_tool_result(search_step, mock_search_result, context)
        
        # Get original content
        original_entry = next(
            (e for e in web_search_agent._scraped_content if e["url"] == "https://example.com/1"),
            None
        )
        original_content = original_entry["content"] if original_entry else ""
        
        # Process failed scrape
        scrape_step = PipelineStep(
            tool="web_scraper",
            args={"url": "https://example.com/1"}
        )
        mock_failed_scrape_result.url = "https://example.com/1"
        await web_search_agent.process_tool_result(scrape_step, mock_failed_scrape_result, context)
        
        # Content should not be overwritten
        entry = next(
            (e for e in web_search_agent._scraped_content if e["url"] == "https://example.com/1"),
            None
        )
        assert entry["snippet_only"] is True


# =============================================================================
# Streaming Event Tests
# =============================================================================


@pytest.mark.asyncio
class TestWebSearchAgentStreaming:
    """Tests for streaming behavior of WebSearchAgent."""
    
    async def test_streams_thought_events(
        self,
        web_search_agent,
        mock_stream_callback,
        mock_cancel_event,
        mock_search_result,
        mock_scrape_result,
        mock_auth_context,
    ):
        """Test that agent streams thought events."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            with patch('app.agents.base_agent.ToolRegistry.get') as mock_get:
                def get_tool(name):
                    if name == "web_search":
                        return AsyncMock(return_value=mock_search_result)
                    return AsyncMock(return_value=mock_scrape_result)
                
                mock_get.side_effect = get_tool
                
                with patch.object(web_search_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Test synthesis response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await web_search_agent.run_with_streaming(
                        query="test query",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context=mock_auth_context,
                    )
        
        thought_events = [e for e in mock_stream_callback.events if e.type == "thought"]
        assert len(thought_events) > 0
    
    async def test_streams_tool_events_for_search_and_scrape(
        self,
        web_search_agent,
        mock_stream_callback,
        mock_cancel_event,
        mock_search_result,
        mock_scrape_result,
        mock_auth_context,
    ):
        """Test that agent streams tool events for both search and scrape."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            with patch('app.agents.base_agent.ToolRegistry.get') as mock_get:
                def get_tool(name):
                    if name == "web_search":
                        return AsyncMock(return_value=mock_search_result)
                    return AsyncMock(return_value=mock_scrape_result)
                
                mock_get.side_effect = get_tool
                
                with patch.object(web_search_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await web_search_agent.run_with_streaming(
                        query="test",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context=mock_auth_context,
                    )
        
        tool_start_events = [e for e in mock_stream_callback.events if e.type == "tool_start"]
        tool_result_events = [e for e in mock_stream_callback.events if e.type == "tool_result"]
        
        # Should have events for search + scrapes
        assert len(tool_start_events) >= 1
        assert len(tool_result_events) >= 1


# =============================================================================
# Authentication Required Tests
# =============================================================================


@pytest.mark.asyncio
class TestWebSearchAgentAuthRequired:
    """Tests confirming web search agent requires authentication."""
    
    async def test_fails_without_principal(
        self,
        web_search_agent,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that agent fails without principal."""
        result = await web_search_agent.run_with_streaming(
            query="test",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context={},  # No principal
        )
        
        # Should have auth error
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) > 0
        assert any("Authentication" in e.message for e in error_events)
    
    async def test_fails_with_empty_context(
        self,
        web_search_agent,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that agent fails with empty context."""
        result = await web_search_agent.run_with_streaming(
            query="test",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context=None,
        )
        
        # Should have auth error
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) > 0


# =============================================================================
# Synthesis Context Tests
# =============================================================================


class TestWebSearchAgentSynthesis:
    """Tests for synthesis context building."""
    
    def test_build_synthesis_context_with_scraped_content(self, web_search_agent):
        """Test _build_synthesis_context with scraped content."""
        web_search_agent._scraped_content = [
            {
                "url": "https://example.com/1",
                "title": "Test Page",
                "domain": "example.com",
                "content": "Test content here",
                "snippet_only": False,
            }
        ]
        context = AgentContext()
        
        synthesis_ctx = web_search_agent._build_synthesis_context("test query", context)
        
        assert "test query" in synthesis_ctx
        assert "Test Page" in synthesis_ctx
        assert "Test content" in synthesis_ctx
    
    def test_build_synthesis_context_marks_snippets(self, web_search_agent):
        """Test _build_synthesis_context marks snippet-only content."""
        web_search_agent._scraped_content = [
            {
                "url": "https://example.com/1",
                "title": "Test Page",
                "domain": "example.com",
                "content": "Just a snippet",
                "snippet_only": True,
            }
        ]
        context = AgentContext()
        
        synthesis_ctx = web_search_agent._build_synthesis_context("test", context)
        
        assert "snippet" in synthesis_ctx.lower()
    
    def test_build_fallback_response(self, web_search_agent):
        """Test _build_fallback_response generates readable output."""
        web_search_agent._scraped_content = [
            {
                "url": "https://example.com/1",
                "title": "Test Page",
                "domain": "example.com",
                "content": "Test content",
                "snippet_only": False,
            }
        ]
        context = AgentContext()
        
        fallback = web_search_agent._build_fallback_response("test query", context)
        
        assert "test query" in fallback
        assert "Test Page" in fallback
        assert "example.com" in fallback


# =============================================================================
# Cancellation Tests
# =============================================================================


@pytest.mark.asyncio
class TestWebSearchAgentCancellation:
    """Tests for cancellation handling."""
    
    async def test_respects_cancellation(
        self,
        web_search_agent,
        mock_stream_callback,
        mock_auth_context,
    ):
        """Test that agent respects cancellation event."""
        cancel = asyncio.Event()
        cancel.set()  # Already cancelled
        
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await web_search_agent.run_with_streaming(
                query="test",
                stream=mock_stream_callback,
                cancel=cancel,
                context=mock_auth_context,
            )
        
        # Should return empty string on cancellation
        assert result == ""
