"""Weather tool for getting current weather information via Open-Meteo API."""
import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Tool


class WeatherInput(BaseModel):
    """Input schema for weather tool."""
    location: str = Field(description="City name to get weather for")


class WeatherOutput(BaseModel):
    """Output schema for weather tool."""
    temperature: float = Field(description="Current temperature in Celsius")
    feels_like: float = Field(description="Apparent temperature in Celsius")
    humidity: float = Field(description="Relative humidity percentage")
    wind_speed: float = Field(description="Wind speed in km/h")
    wind_gust: float = Field(description="Wind gust speed in km/h")
    conditions: str = Field(description="Weather conditions description")
    location: str = Field(description="Resolved location name")


class GeocodingResult(BaseModel):
    """Geocoding API result."""
    latitude: float
    longitude: float
    name: str


class GeocodingResponse(BaseModel):
    """Geocoding API response."""
    results: list[GeocodingResult] = []


class CurrentWeather(BaseModel):
    """Current weather data from API."""
    time: str
    temperature_2m: float
    apparent_temperature: float
    relative_humidity_2m: float
    wind_speed_10m: float
    wind_gusts_10m: float
    weather_code: int


class WeatherResponse(BaseModel):
    """Weather API response."""
    current: CurrentWeather


def get_weather_condition(code: int) -> str:
    """Convert weather code to human-readable condition."""
    conditions = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Foggy",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        56: "Light freezing drizzle",
        57: "Dense freezing drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        66: "Light freezing rain",
        67: "Heavy freezing rain",
        71: "Slight snow fall",
        73: "Moderate snow fall",
        75: "Heavy snow fall",
        77: "Snow grains",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        85: "Slight snow showers",
        86: "Heavy snow showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return conditions.get(code, "Unknown")


async def get_weather(location: str) -> WeatherOutput:
    """
    Fetch current weather for a location using Open-Meteo API.
    
    Args:
        location: City name to get weather for
        
    Returns:
        WeatherOutput with current weather data
        
    Raises:
        ValueError: If location is not found
        httpx.HTTPError: If API request fails
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First, geocode the location
        geocoding_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location}&count=1"
        geocoding_response = await client.get(geocoding_url)
        geocoding_response.raise_for_status()
        geocoding_data = GeocodingResponse.model_validate(geocoding_response.json())
        
        if not geocoding_data.results:
            raise ValueError(f"Location '{location}' not found")
        
        result = geocoding_data.results[0]
        
        # Fetch weather data
        weather_url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={result.latitude}"
            f"&longitude={result.longitude}"
            f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
            f"wind_speed_10m,wind_gusts_10m,weather_code"
        )
        
        weather_response = await client.get(weather_url)
        weather_response.raise_for_status()
        weather_data = WeatherResponse.model_validate(weather_response.json())
        
        return WeatherOutput(
            temperature=weather_data.current.temperature_2m,
            feels_like=weather_data.current.apparent_temperature,
            humidity=weather_data.current.relative_humidity_2m,
            wind_speed=weather_data.current.wind_speed_10m,
            wind_gust=weather_data.current.wind_gusts_10m,
            conditions=get_weather_condition(weather_data.current.weather_code),
            location=result.name,
        )


# Create the Pydantic AI tool
weather_tool = Tool(
    get_weather,
    takes_ctx=False,
    name="get_weather",
    description="Get current weather information for a specific location. Provide a city name.",
)






