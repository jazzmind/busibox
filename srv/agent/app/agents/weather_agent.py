"""
Weather Agent.

A weather assistant that provides accurate weather information using tool calling.
Uses LLM-driven tool selection to intelligently extract location and format requests.

This agent extends BaseStreamingAgent with weather-specific configuration.
"""

import logging
from typing import List

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)

logger = logging.getLogger(__name__)


# Weather agent synthesis prompt
WEATHER_SYSTEM_PROMPT = """You are a helpful weather assistant that provides accurate weather information.

Your primary function is to consider how accurate weather details can be used to resolve the user's question. Use the get_weather tool to get the weather details and then use the weather details to resolve the user's question.

**Available Tool:**
- **get_weather(location: str)**: Get current weather for a city. Pass just the city name (e.g., "London", "New York", "Tokyo").

**Your Workflow:**
1. Extract the location from the user's query
2. Call get_weather with the city name
3. Resolve the question in a friendly, informative format, including specific weather details when appropriate.

- If the location isn't clear, make a reasonable guess or ask for clarification

**Example:**
User: "What's the weather like in Seattle?"
→ Call get_weather(location="Seattle")
→ Format the response in a friendly way"""


class WeatherAgent(BaseStreamingAgent):
    """
    A streaming weather agent that:
    1. Uses LLM to extract location from user query
    2. Calls weather tool with extracted location
    3. Presents information in a friendly format
    
    All steps stream their progress to the user in real-time.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="weather-agent",
            display_name="Weather Agent",
            instructions=WEATHER_SYSTEM_PROMPT,
            tools=["get_weather"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,  # Let LLM extract location and call tool
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        For LLM_DRIVEN strategy, this returns an empty list.
        The LLM will decide how to call the weather tool.
        """
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis from weather data.
        """
        weather_result = context.tool_results.get("get_weather")
        
        if not weather_result:
            return f"User Question: {query}\n\nNo weather data was retrieved."
        
        parts = [f"User Question: {query}\n\nWeather Data:\n"]
        
        if hasattr(weather_result, 'model_dump'):
            data = weather_result.model_dump()
            for key, value in data.items():
                parts.append(f"- {key}: {value}")
        else:
            parts.append(str(weather_result))
        
        parts.append("\nPlease present this weather information in a friendly, helpful format.")
        return "\n".join(parts)
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        """
        weather_result = context.tool_results.get("get_weather")
        
        if not weather_result:
            return "I couldn't retrieve weather information. Please try again with a specific city name."
        
        if hasattr(weather_result, 'temperature'):
            return f"Current weather: {weather_result.temperature}°C, {weather_result.conditions if hasattr(weather_result, 'conditions') else 'conditions unknown'}"
        
        return f"Weather data: {str(weather_result)[:500]}"


# Singleton instance
weather_agent = WeatherAgent()
