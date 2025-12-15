"""Integration tests for chat agent with local LLM."""
import pytest
from pydantic_ai import Agent

from app.agents.chat_agent import chat_agent


class TestChatAgent:
    """Test chat agent with real LiteLLM."""

    @pytest.mark.asyncio
    async def test_chat_agent_basic_response(self):
        """Test chat agent can respond to basic query."""
        # Simple query that doesn't require tools
        result = await chat_agent.run("Hello! What can you help me with?")
        
        # Verify we got a response
        assert result is not None
        response_text = str(result)
        assert len(response_text) > 0
        print(f"\nChat agent response: {response_text}")

    @pytest.mark.asyncio
    async def test_chat_agent_with_context(self):
        """Test chat agent uses provided context."""
        # Provide context in the prompt
        query = """I have some document context for you:
        
Document: project_plan.pdf
Content: The project deadline is December 31st, 2024. Budget is $50,000.

Based on this context, when is the project deadline?"""
        
        result = await chat_agent.run(query)
        
        # Verify response mentions the deadline
        response = str(result).lower()
        assert "december" in response or "31" in response or "2024" in response
        print(f"\nChat agent with context: {response}")

    @pytest.mark.asyncio
    async def test_chat_agent_concise_response(self):
        """Test chat agent provides concise responses."""
        result = await chat_agent.run("What is 2 + 2?")
        
        # Verify response is concise (under 200 characters for simple math)
        response = str(result)
        assert len(response) < 200  # Generous limit
        assert "4" in response
        print(f"\nChat agent concise: {response}")





