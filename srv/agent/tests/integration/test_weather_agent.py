"""Integration tests for weather agent with LiteLLM and external API calls."""
import pytest
from pydantic_ai import Agent

from app.agents.weather_agent import weather_agent
from app.tools.weather_tool import get_weather


class TestWeatherTool:
    """Test the weather tool directly."""
    
    @pytest.mark.asyncio
    async def test_get_weather_success(self):
        """Test that weather tool can fetch real weather data."""
        result = await get_weather("London")
        
        assert result.location == "London"
        assert isinstance(result.temperature, float)
        assert isinstance(result.feels_like, float)
        assert isinstance(result.humidity, float)
        assert isinstance(result.wind_speed, float)
        assert isinstance(result.wind_gust, float)
        assert isinstance(result.conditions, str)
        assert result.conditions != "Unknown"
    
    @pytest.mark.asyncio
    async def test_get_weather_invalid_location(self):
        """Test that weather tool raises error for invalid location."""
        with pytest.raises(ValueError, match="not found"):
            await get_weather("XYZ123InvalidCity")


class TestWeatherAgent:
    """Test the weather agent with LiteLLM integration."""
    
    @pytest.mark.asyncio
    async def test_agent_can_get_weather(self):
        """Test that agent can successfully call LiteLLM and use weather tool."""
        # Run the agent with a weather query
        result = await weather_agent.run("What's the weather in San Francisco?")
        
        # Verify we got a response
        assert result.data is not None
        response_text = str(result.data).lower()
        
        # Check that the response mentions San Francisco
        assert "san francisco" in response_text or "francisco" in response_text
        
        # Check that weather-related information is present
        # (temperature, conditions, etc.)
        assert any(
            keyword in response_text
            for keyword in ["temperature", "°", "degrees", "weather", "conditions"]
        )
    
    @pytest.mark.asyncio
    async def test_agent_tool_calling(self):
        """Test that agent actually uses the weather tool."""
        # Run the agent and capture the result
        result = await weather_agent.run("Get me the weather for Tokyo")
        
        # Check that the tool was called
        # Pydantic AI tracks tool calls in the result
        assert len(result.all_messages()) > 2  # User message + tool call + response
        
        # Verify tool was used by checking messages
        messages = result.all_messages()
        tool_calls = [msg for msg in messages if hasattr(msg, "parts")]
        assert len(tool_calls) > 0, "Agent should have made tool calls"
    
    @pytest.mark.asyncio
    async def test_agent_handles_missing_location(self):
        """Test that agent asks for location when not provided."""
        result = await weather_agent.run("What's the weather like?")
        
        response_text = str(result.data).lower()
        
        # Agent should ask for a location
        assert any(
            keyword in response_text
            for keyword in ["location", "where", "city", "place"]
        )
    
    @pytest.mark.asyncio
    async def test_agent_multiple_locations(self):
        """Test that agent can handle multiple location queries."""
        result1 = await weather_agent.run("What's the weather in Paris?")
        result2 = await weather_agent.run("And what about Berlin?")
        
        response1 = str(result1.data).lower()
        response2 = str(result2.data).lower()
        
        # Both should contain weather information
        assert "paris" in response1 or "temperature" in response1
        assert "berlin" in response2 or "temperature" in response2


class TestWeatherAgentLiteLLMIntegration:
    """Test LiteLLM integration specifically."""
    
    @pytest.mark.asyncio
    async def test_litellm_model_responds(self):
        """Test that LiteLLM model is accessible and responds."""
        # Simple query without tool calling
        result = await weather_agent.run("Say hello")
        
        assert result.data is not None
        response_text = str(result.data).lower()
        assert len(response_text) > 0
    
    @pytest.mark.asyncio
    async def test_litellm_supports_tool_calling(self):
        """Test that LiteLLM model supports function/tool calling."""
        # This will fail if the model doesn't support tool calling
        result = await weather_agent.run("What's the current temperature in Miami?")
        
        # If we get here, tool calling worked
        assert result.data is not None
        
        # Verify the agent actually called the tool
        messages = result.all_messages()
        assert len(messages) > 2, "Should have user message, tool call, and response"


@pytest.mark.integration
class TestWeatherAgentEndToEnd:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_full_weather_query_flow(self):
        """Test complete flow: user query -> LLM -> tool call -> external API -> response."""
        # This tests:
        # 1. Agent receives user query
        # 2. LiteLLM processes query and decides to call tool
        # 3. Weather tool calls Open-Meteo API
        # 4. Tool returns data to LLM
        # 5. LLM formats final response
        
        result = await weather_agent.run(
            "I'm planning to visit Seattle tomorrow. What's the weather like there?"
        )
        
        response_text = str(result.data).lower()
        
        # Verify all components worked:
        # - LLM understood the query (mentions Seattle)
        assert "seattle" in response_text
        
        # - Tool was called and returned data (weather info present)
        assert any(
            keyword in response_text
            for keyword in ["temperature", "°", "degrees", "weather", "humidity", "wind"]
        )
        
        # - LLM formatted a helpful response (not just raw data)
        assert len(response_text) > 50  # Should be a proper sentence, not just numbers
    
    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Test that agent handles errors gracefully."""
        # Try with an invalid location
        result = await weather_agent.run("What's the weather in XYZ123InvalidCity?")
        
        response_text = str(result.data).lower()
        
        # Agent should handle the error and provide a helpful message
        assert any(
            keyword in response_text
            for keyword in ["not found", "couldn't find", "unable", "sorry", "error"]
        )









