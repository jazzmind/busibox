"""Weather agent that provides weather information using tool calling."""
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.tools.weather_tool import weather_tool

settings = get_settings()

# Configure OpenAI client to use LiteLLM via environment variables
# This is the standard way OpenAI clients discover custom endpoints
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)

# Get LiteLLM API key from environment (set by Ansible deployment)
litellm_api_key = settings.litellm_api_key or "sk-1234"  # Default for local dev
os.environ["OPENAI_API_KEY"] = litellm_api_key

# Create OpenAI-compatible model using standard provider
# The model will automatically use the OPENAI_BASE_URL and OPENAI_API_KEY we set above
model = OpenAIModel(
    model_name=settings.default_model,
    provider="openai",
)

# Create the weather agent with tool calling
weather_agent = Agent(
    model=model,
    tools=[weather_tool],
    system_prompt="""You are a helpful weather assistant that provides accurate weather information.

Your primary function is to help users get weather details for specific locations. When responding:
- Always ask for a location if none is provided
- If the location name isn't in English, please translate it
- If given a location with multiple parts (e.g. "New York, NY"), use the most relevant part (e.g. "New York")
- Include relevant details like humidity, wind conditions, and precipitation
- Keep responses concise but informative
- Use the get_weather tool to fetch current weather data

When you have weather data, present it in a clear, friendly format.""",
    retries=2,
)









