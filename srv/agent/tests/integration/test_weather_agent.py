"""Integration tests for weather agent with LiteLLM and external API calls."""
import pytest
from unittest.mock import MagicMock, patch

from app.agents.weather_agent import weather_agent, WeatherAgent
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


class TestWeatherAgentConfig:
    """Test weather agent configuration."""
    
    def test_agent_has_correct_config(self):
        """Test that weather agent is properly configured."""
        agent = WeatherAgent()
        assert agent.config.name == "weather-agent"
        assert agent.config.display_name == "Weather Agent"
        assert "get_weather" in agent.config.tools
    
    def test_agent_is_streaming_agent(self):
        """Test that weather agent extends BaseStreamingAgent."""
        from app.agents.base_agent import BaseStreamingAgent
        assert isinstance(weather_agent, BaseStreamingAgent)


class TestWeatherAgent:
    """Test the weather agent with LiteLLM integration."""
    
    @pytest.mark.asyncio
    async def test_agent_can_get_weather(self, mock_auth_context):
        """Test that agent can successfully use weather tool."""
        # Mock token exchange
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            # Run the agent with a weather query
            result = await weather_agent.run(
                "What's the weather in San Francisco?",
                context=mock_auth_context
            )
        
        # Verify we got a response
        assert result.output is not None
        response_text = str(result.output).lower()
        
        # Check that the response contains weather-related information
        assert any(
            keyword in response_text
            for keyword in ["temperature", "°", "degrees", "weather", "conditions", "san francisco"]
        )
    
    @pytest.mark.asyncio
    async def test_agent_handles_missing_location(self, mock_auth_context):
        """Test agent behavior with vague query."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await weather_agent.run("What's the weather like?", context=mock_auth_context)
        
        response_text = str(result.output).lower()
        
        # Agent should still provide some response (may use default location)
        assert len(response_text) > 0
    
    @pytest.mark.asyncio
    async def test_agent_multiple_locations(self, mock_auth_context):
        """Test that agent can handle multiple location queries."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result1 = await weather_agent.run("What's the weather in Paris?", context=mock_auth_context)
            result2 = await weather_agent.run("And what about Berlin?", context=mock_auth_context)
        
        response1 = str(result1.output).lower()
        response2 = str(result2.output).lower()
        
        # Both should contain weather information
        assert len(response1) > 0
        assert len(response2) > 0


class TestWeatherAgentLiteLLMIntegration:
    """Test LiteLLM integration specifically."""
    
    @pytest.mark.asyncio
    async def test_litellm_model_responds(self, mock_auth_context):
        """Test that LiteLLM model is accessible and responds."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await weather_agent.run("Weather in Tokyo", context=mock_auth_context)
        
        assert result.output is not None
        response_text = str(result.output).lower()
        assert len(response_text) > 0
    
    @pytest.mark.asyncio
    async def test_litellm_with_weather_query(self, mock_auth_context):
        """Test that LiteLLM works with weather queries."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await weather_agent.run(
                "What's the current temperature in Miami?",
                context=mock_auth_context
            )
        
        # If we get here, the agent worked
        assert result.output is not None


@pytest.mark.integration
class TestWeatherAgentEndToEnd:
    """End-to-end integration tests."""
    
    @pytest.mark.asyncio
    async def test_full_weather_query_flow(self, mock_auth_context):
        """Test complete flow: user query -> tool call -> external API -> response."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            result = await weather_agent.run(
                "I'm planning to visit Seattle tomorrow. What's the weather like there?",
                context=mock_auth_context
            )
        
        response_text = str(result.output).lower()
        
        # Verify all components worked:
        # - Tool was called and returned data (weather info present)
        assert any(
            keyword in response_text
            for keyword in ["temperature", "°", "degrees", "weather", "humidity", "wind", "seattle"]
        )
        
        # - Response is meaningful (not just raw data)
        assert len(response_text) > 20
    
    @pytest.mark.asyncio
    async def test_error_handling(self, mock_auth_context):
        """Test that agent handles errors gracefully."""
        with patch('app.agents.base_agent.get_or_exchange_token') as mock_exchange:
            mock_token = MagicMock()
            mock_token.access_token = "test-token"
            mock_exchange.return_value = mock_token
            
            # Try with an invalid location - agent may still provide a response
            result = await weather_agent.run(
                "What's the weather in XYZ123InvalidCity?",
                context=mock_auth_context
            )
        
        response_text = str(result.output).lower()
        
        # Agent should provide some response
        assert len(response_text) > 0









