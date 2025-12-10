"""Weather agent that provides weather information using tool calling."""
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel, Provider

from app.config.settings import get_settings
from app.tools.weather_tool import weather_tool

settings = get_settings()

# Create AsyncOpenAI client pointing to LiteLLM
litellm_client = AsyncOpenAI(
    base_url=str(settings.litellm_base_url),
    api_key="litellm-placeholder",  # LiteLLM doesn't require a real key for local models
)

# Create a Provider wrapper for the client
litellm_provider = Provider(client=litellm_client)

# Create OpenAI-compatible model using custom provider
model = OpenAIModel(
    model_name=settings.default_model,
    provider=litellm_provider,
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
