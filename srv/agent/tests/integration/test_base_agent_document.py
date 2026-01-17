"""
Integration tests for DocumentAgent.

Tests the document search agent with real API calls (when available)
or mocked services for CI environments.
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.document_agent import DocumentAgent
from app.agents.base_agent import AgentContext, PipelineStep, ExecutionMode, ToolStrategy
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def document_agent():
    """Create a DocumentAgent instance."""
    return DocumentAgent()


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
        scopes=["search.read"],
        token="test-jwt-token-12345",
    )


@pytest.fixture
def mock_search_result():
    """Create a mock document search result."""
    result = MagicMock()
    result.found = True
    result.result_count = 2
    result.results = [
        MagicMock(
            filename="test-document.pdf",
            text="This is test content from the document.",
            score=0.95,
            page_number=1,
            chunk_index=0,
        ),
        MagicMock(
            filename="another-doc.pdf",
            text="More relevant content here.",
            score=0.85,
            page_number=3,
            chunk_index=2,
        ),
    ]
    result.context = "Formatted context for synthesis"
    result.error = None
    result.model_dump = MagicMock(return_value={"found": True, "result_count": 2})
    return result


@pytest.fixture
def mock_empty_search_result():
    """Create a mock empty search result."""
    result = MagicMock()
    result.found = False
    result.result_count = 0
    result.results = []
    result.context = ""
    result.error = None
    result.model_dump = MagicMock(return_value={"found": False, "result_count": 0})
    return result


# =============================================================================
# Agent Configuration Tests
# =============================================================================


class TestDocumentAgentConfiguration:
    """Tests for DocumentAgent configuration."""
    
    def test_agent_has_correct_name(self, document_agent):
        """Test agent has correct display name."""
        assert document_agent.name == "Document Assistant"
        assert document_agent.config.display_name == "Document Assistant"
    
    def test_agent_has_correct_tools(self, document_agent):
        """Test agent is configured with document_search tool."""
        assert "document_search" in document_agent.config.tools
    
    def test_agent_execution_mode_is_run_once(self, document_agent):
        """Test agent uses RUN_ONCE execution mode."""
        assert document_agent.config.execution_mode == ExecutionMode.RUN_ONCE
    
    def test_agent_tool_strategy_is_pipeline(self, document_agent):
        """Test agent uses PREDEFINED_PIPELINE strategy."""
        assert document_agent.config.tool_strategy == ToolStrategy.PREDEFINED_PIPELINE
    
    def test_agent_requires_auth(self, document_agent):
        """Test agent requires authentication."""
        assert document_agent.config.requires_auth() is True
    
    def test_agent_required_scopes(self, document_agent):
        """Test agent requires search.read scope."""
        scopes = document_agent.config.get_required_scopes()
        assert "search.read" in scopes


# =============================================================================
# Pipeline Tests
# =============================================================================


class TestDocumentAgentPipeline:
    """Tests for DocumentAgent pipeline."""
    
    def test_pipeline_steps_returns_search_step(self, document_agent):
        """Test pipeline_steps returns document_search step."""
        context = AgentContext()
        steps = document_agent.pipeline_steps("test query", context)
        
        assert len(steps) == 1
        assert steps[0].tool == "document_search"
    
    def test_pipeline_step_includes_query(self, document_agent):
        """Test pipeline step includes the query in args."""
        context = AgentContext()
        steps = document_agent.pipeline_steps("my search query", context)
        
        assert steps[0].args["query"] == "my search query"
    
    def test_pipeline_step_has_limit(self, document_agent):
        """Test pipeline step has limit parameter."""
        context = AgentContext()
        steps = document_agent.pipeline_steps("test", context)
        
        assert steps[0].args["limit"] == 5
    
    def test_pipeline_step_uses_hybrid_mode(self, document_agent):
        """Test pipeline step uses hybrid search mode."""
        context = AgentContext()
        steps = document_agent.pipeline_steps("test", context)
        
        assert steps[0].args["mode"] == "hybrid"


# =============================================================================
# Streaming Event Tests
# =============================================================================


@pytest.mark.asyncio
class TestDocumentAgentStreaming:
    """Tests for streaming behavior of DocumentAgent."""
    
    async def test_streams_thought_events(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
        mock_search_result,
    ):
        """Test that agent streams thought events."""
        mock_session = AsyncMock()
        
        # Mock token exchange
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token_response):
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_search_result)):
                with patch.object(document_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    # Mock streaming synthesis
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Test synthesis response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await document_agent.run_with_streaming(
                        query="test query",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context={
                            "principal": test_principal,
                            "session": mock_session,
                        }
                    )
        
        # Should have thought events
        thought_events = [e for e in mock_stream_callback.events if e.type == "thought"]
        assert len(thought_events) > 0
    
    async def test_streams_tool_events(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
        mock_search_result,
    ):
        """Test that agent streams tool_start and tool_result events."""
        mock_session = AsyncMock()
        
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token_response):
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_search_result)):
                with patch.object(document_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await document_agent.run_with_streaming(
                        query="test",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context={
                            "principal": test_principal,
                            "session": mock_session,
                        }
                    )
        
        tool_start_events = [e for e in mock_stream_callback.events if e.type == "tool_start"]
        tool_result_events = [e for e in mock_stream_callback.events if e.type == "tool_result"]
        
        assert len(tool_start_events) >= 1
        assert len(tool_result_events) >= 1
    
    async def test_streams_content_events(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
        mock_search_result,
    ):
        """Test that agent streams content events during synthesis."""
        mock_session = AsyncMock()
        
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token_response):
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_search_result)):
                with patch.object(document_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Chunk 1"
                        yield " Chunk 2"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await document_agent.run_with_streaming(
                        query="test",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context={
                            "principal": test_principal,
                            "session": mock_session,
                        }
                    )
        
        content_events = [e for e in mock_stream_callback.events if e.type == "content"]
        assert len(content_events) >= 1


# =============================================================================
# Authentication Tests
# =============================================================================


@pytest.mark.asyncio
class TestDocumentAgentAuthentication:
    """Tests for authentication in DocumentAgent."""
    
    async def test_requires_principal(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that agent requires principal for authentication."""
        result = await document_agent.run_with_streaming(
            query="test",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context={},  # No principal
        )
        
        # Should have error about authentication
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) == 1
        assert "Authentication" in error_events[0].message
    
    async def test_requires_token_in_principal(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that agent requires token in principal."""
        principal_without_token = Principal(
            sub="test",
            email="test@test.com",
            roles=[],
            scopes=[],
            token=None,
        )
        
        result = await document_agent.run_with_streaming(
            query="test",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context={"principal": principal_without_token},
        )
        
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) == 1
    
    async def test_requires_session_for_token_exchange(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
    ):
        """Test that agent requires session for token exchange."""
        result = await document_agent.run_with_streaming(
            query="test",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context={"principal": test_principal},  # No session
        )
        
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) == 1
        assert "session" in error_events[0].message.lower()
    
    async def test_performs_token_exchange(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
        mock_search_result,
    ):
        """Test that agent performs token exchange."""
        mock_session = AsyncMock()
        
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_exchange.return_value = mock_token_response
            
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_search_result)):
                with patch.object(document_agent.synthesis_agent, 'run_stream') as mock_synthesis:
                    mock_stream_result = MagicMock()
                    mock_stream_result.__aenter__ = AsyncMock(return_value=mock_stream_result)
                    mock_stream_result.__aexit__ = AsyncMock(return_value=None)
                    
                    async def mock_stream_text(delta=True):
                        yield "Response"
                    
                    mock_stream_result.stream_text = mock_stream_text
                    mock_synthesis.return_value = mock_stream_result
                    
                    await document_agent.run_with_streaming(
                        query="test",
                        stream=mock_stream_callback,
                        cancel=mock_cancel_event,
                        context={
                            "principal": test_principal,
                            "session": mock_session,
                        }
                    )
            
            # Verify token exchange was called with correct scopes
            mock_exchange.assert_called_once()
            call_kwargs = mock_exchange.call_args[1]
            assert "search.read" in call_kwargs.get("scopes", [])


# =============================================================================
# No Results Handling Tests
# =============================================================================


@pytest.mark.asyncio
class TestDocumentAgentNoResults:
    """Tests for handling no search results."""
    
    async def test_handles_no_results_gracefully(
        self,
        document_agent,
        mock_stream_callback,
        mock_cancel_event,
        test_principal,
        mock_empty_search_result,
    ):
        """Test that agent handles empty results gracefully."""
        mock_session = AsyncMock()
        
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token_response):
            with patch('app.agents.base_agent.ToolRegistry.get', return_value=AsyncMock(return_value=mock_empty_search_result)):
                # No need to mock synthesis since no results
                result = await document_agent.run_with_streaming(
                    query="nonexistent topic",
                    stream=mock_stream_callback,
                    cancel=mock_cancel_event,
                    context={
                        "principal": test_principal,
                        "session": mock_session,
                    }
                )
        
        # Should return a message about no results
        assert "couldn't find" in result.lower() or "no" in result.lower()


# =============================================================================
# Cancellation Tests
# =============================================================================


@pytest.mark.asyncio
class TestDocumentAgentCancellation:
    """Tests for cancellation handling."""
    
    async def test_respects_cancellation(
        self,
        document_agent,
        mock_stream_callback,
        test_principal,
    ):
        """Test that agent respects cancellation event."""
        cancel = asyncio.Event()
        cancel.set()  # Already cancelled
        
        mock_session = AsyncMock()
        
        # Mock token exchange to avoid actual auth
        mock_token_response = MagicMock()
        mock_token_response.access_token = "exchanged-token"
        
        with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token_response):
            result = await document_agent.run_with_streaming(
                query="test",
                stream=mock_stream_callback,
                cancel=cancel,
                context={
                    "principal": test_principal,
                    "session": mock_session,
                }
            )
        
        # Should return empty string on cancellation
        assert result == ""


# =============================================================================
# Synthesis Context Tests
# =============================================================================


class TestDocumentAgentSynthesis:
    """Tests for synthesis context building."""
    
    def test_build_synthesis_context_with_results(self, document_agent, mock_search_result):
        """Test _build_synthesis_context with search results."""
        context = AgentContext(tool_results={"document_search": mock_search_result})
        
        synthesis_ctx = document_agent._build_synthesis_context("test query", context)
        
        assert "test query" in synthesis_ctx
        assert "test-document.pdf" in synthesis_ctx
        assert "test content" in synthesis_ctx
    
    def test_build_synthesis_context_without_results(self, document_agent):
        """Test _build_synthesis_context without results."""
        context = AgentContext(tool_results={})
        
        synthesis_ctx = document_agent._build_synthesis_context("test query", context)
        
        assert "test query" in synthesis_ctx
        assert "No documents" in synthesis_ctx
    
    def test_build_fallback_response(self, document_agent, mock_search_result):
        """Test _build_fallback_response generates readable output."""
        context = AgentContext(tool_results={"document_search": mock_search_result})
        
        fallback = document_agent._build_fallback_response("test query", context)
        
        assert "test query" in fallback
        assert "test-document.pdf" in fallback
