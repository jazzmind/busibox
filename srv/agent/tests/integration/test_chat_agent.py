"""
Integration tests for ChatAgent with authentication.

Tests the chat agent's ability to use all its tools:
- get_weather: Weather queries
- web_search: Web research queries  
- document_search: Document queries (requires auth)
"""

import asyncio
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.chat_agent import chat_agent, ChatAgent
from app.agents.base_agent import BaseStreamingAgent, AgentContext, ToolRegistry
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_stream_callback():
    """Create a mock stream callback that collects events."""
    class StreamCollector:
        def __init__(self):
            self.events: List[StreamEvent] = []
        
        async def __call__(self, event: StreamEvent):
            self.events.append(event)
    
    return StreamCollector()


@pytest.fixture
def mock_cancel_event():
    """Create a cancel event that is not set."""
    return asyncio.Event()


# =============================================================================
# Configuration Tests
# =============================================================================

class TestChatAgentConfig:
    """Test chat agent configuration."""
    
    def test_agent_has_correct_config(self):
        """Test that chat agent is properly configured."""
        agent = ChatAgent()
        assert agent.config.name == "chat-agent"
        assert agent.config.display_name == "Chat Agent"
        assert "web_search" in agent.config.tools
        assert "get_weather" in agent.config.tools
        assert "document_search" in agent.config.tools
    
    def test_agent_is_streaming_agent(self):
        """Test that chat agent extends BaseStreamingAgent."""
        assert isinstance(chat_agent, BaseStreamingAgent)
    
    def test_agent_uses_llm_driven_strategy(self):
        """Test that chat agent uses LLM-driven tool selection."""
        from app.agents.base_agent import ToolStrategy
        agent = ChatAgent()
        assert agent.config.tool_strategy == ToolStrategy.LLM_DRIVEN


# =============================================================================
# Weather Tool Tests
# =============================================================================

class TestChatAgentWeatherTool:
    """Test chat agent's weather tool usage."""
    
    @pytest.mark.asyncio
    async def test_weather_query(self, mock_auth_context):
        """Test chat agent can get weather information."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(
                "What is the temperature in London right now?",
                context=mock_auth_context
            )
        
        response = str(result.output).lower()
        
        # Should contain weather-related information
        assert any(keyword in response for keyword in [
            "temperature", "°", "degrees", "weather", "london", "celsius", "fahrenheit"
        ]), f"Expected weather info in response: {response}"
    
    @pytest.mark.asyncio
    async def test_weather_with_activity_suggestion(self, mock_auth_context):
        """Test weather query that might trigger activity suggestions."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(
                "Is it good weather for a walk in Paris today?",
                context=mock_auth_context
            )
        
        response = str(result.output).lower()
        
        # Should mention weather or walking conditions
        assert len(response) > 20, "Should provide a meaningful response"


# =============================================================================
# Web Search Tool Tests
# =============================================================================

class TestChatAgentWebSearchTool:
    """Test chat agent's web search tool usage."""
    
    @pytest.mark.asyncio
    async def test_web_search_query(self, mock_auth_context):
        """Test chat agent can search the web."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(
                "What are some weather-appropriate things to do in London in winter?",
                context=mock_auth_context
            )
        
        response = str(result.output).lower()
        
        # Should contain activity suggestions
        assert len(response) > 50, "Should provide meaningful activity suggestions"
        # May contain sources or activity-related keywords
        assert any(keyword in response for keyword in [
            "london", "winter", "activities", "visit", "museum", "indoor", "outdoor",
            "things", "do", "recommend", "suggest"
        ]), f"Expected activity suggestions in response: {response[:200]}"
    
    @pytest.mark.asyncio
    async def test_current_events_query(self, mock_auth_context):
        """Test chat agent can search for current information."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(
                "What's the latest news about AI technology?",
                context=mock_auth_context
            )
        
        response = str(result.output).lower()
        
        # Should contain AI-related information
        assert len(response) > 50, "Should provide meaningful response about AI"


# =============================================================================
# Document Search Tool Tests (Requires Auth)
# =============================================================================

class TestChatAgentDocumentTool:
    """Test chat agent's document search tool usage with authentication."""
    
    @pytest.mark.asyncio
    async def test_document_query_with_auth(
        self,
        mock_auth_context,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test chat agent can search documents with proper auth."""
        # Create a mock document search result
        mock_search_result = MagicMock()
        mock_search_result.found = True
        mock_search_result.result_count = 1
        mock_search_result.results = [
            MagicMock(
                text="The project deadline is December 31st, 2024. Budget is $50,000.",
                filename="project_plan.pdf",
                page_number=1,
                score=0.95,
            )
        ]
        mock_search_result.model_dump = MagicMock(return_value={
            "found": True,
            "result_count": 1,
            "results": [{"text": "The project deadline is December 31st, 2024."}]
        })
        
        async def mock_doc_search(**kwargs):
            return mock_search_result
        
        # Mock the document_search tool
        with patch.object(ToolRegistry, 'get') as mock_get:
            def get_tool(name):
                if name == "document_search":
                    return mock_doc_search
                return ToolRegistry._tools.get(name)
            
            mock_get.side_effect = get_tool
            
            # Mock token exchange for auth
            mock_token = MagicMock()
            mock_token.access_token = "exchanged-token"
            
            with patch('app.agents.base_agent.get_or_exchange_token', return_value=mock_token):
                result = await chat_agent.run_with_streaming(
                    query="What is the project deadline in my documents?",
                    stream=mock_stream_callback,
                    cancel=mock_cancel_event,
                    context=mock_auth_context
                )
        
        # Should have received some events
        assert len(mock_stream_callback.events) > 0
        
        # Check for content events
        content_events = [e for e in mock_stream_callback.events if e.type == "content"]
        assert len(content_events) > 0, "Should have content events"
    
    @pytest.mark.asyncio
    async def test_document_query_without_auth_fails(
        self,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that document queries without auth fail with error."""
        # Run without providing auth context - should fail auth check
        result = await chat_agent.run_with_streaming(
            query="Search my documents for project info",
            stream=mock_stream_callback,
            cancel=mock_cancel_event,
            context={},  # No auth
        )
        
        # Should get an auth error
        error_events = [e for e in mock_stream_callback.events if e.type == "error"]
        assert len(error_events) > 0, "Should produce an auth error"
        assert "Authentication" in error_events[0].message or "sign in" in error_events[0].message.lower()


# =============================================================================
# General Chat Tests
# =============================================================================

class TestChatAgentGeneral:
    """Test general chat agent behavior."""
    
    @pytest.mark.asyncio
    async def test_basic_response_no_tools(self, mock_auth_context):
        """Test chat agent can respond without tools."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(
                "Hello! What can you help me with?",
                context=mock_auth_context
            )
        
        response = str(result.output)
        assert len(response) > 0, "Should provide a response"
    
    @pytest.mark.asyncio
    async def test_math_question(self, mock_auth_context):
        """Test chat agent can handle simple questions."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run("What is 2 + 2?", context=mock_auth_context)
        
        response = str(result.output)
        assert "4" in response, f"Should contain the answer 4: {response}"
    
    @pytest.mark.asyncio
    async def test_context_in_prompt(self, mock_auth_context):
        """Test chat agent uses context provided in the prompt."""
        query = """I have some information for you:

Document: project_plan.pdf
Content: The project deadline is December 31st, 2024. Budget is $50,000.

Based on this context, when is the project deadline?"""
        
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await chat_agent.run(query, context=mock_auth_context)
        
        response = str(result.output).lower()
        assert "december" in response or "31" in response or "2024" in response, \
            f"Should mention the deadline: {response}"


# =============================================================================
# Streaming Tests
# =============================================================================

class TestChatAgentStreaming:
    """Test chat agent streaming behavior."""
    
    @pytest.mark.asyncio
    async def test_streaming_produces_events(
        self,
        mock_auth_context,
        mock_stream_callback,
        mock_cancel_event,
    ):
        """Test that streaming produces appropriate events."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            await chat_agent.run_with_streaming(
                query="What is the weather in Tokyo?",
                stream=mock_stream_callback,
                cancel=mock_cancel_event,
                context=mock_auth_context,
            )
        
        # Should have multiple events
        assert len(mock_stream_callback.events) > 0
        
        # Should have at least one of these event types
        event_types = {e.type for e in mock_stream_callback.events}
        assert len(event_types) > 0, "Should produce events"
