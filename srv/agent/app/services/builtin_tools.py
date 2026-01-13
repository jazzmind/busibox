"""
Discover and load built-in tools from the tools directory.

This module dynamically scans the app/tools/ directory for tool Python files
and exposes them as built-in tool definitions without requiring database entries.
"""
import importlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.schemas.definitions import ToolDefinitionRead


# Mapping of tool file names to their metadata
BUILTIN_TOOL_METADATA = {
    "web_search_tool": {
        "name": "web_search",
        "description": "Search the web for current, up-to-date information using DuckDuckGo. Returns titles, URLs, and snippets from search results.",
        "entrypoint": "app.tools.web_search_tool:search_web",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query string"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Whether results were found"},
                    "result_count": {"type": "integer", "description": "Number of results returned"},
                    "results": {"type": "array", "description": "List of search results"},
                    "query": {"type": "string", "description": "The search query used"},
                    "error": {"type": "string", "description": "Error message if search failed"}
                }
            }
        }
    },
    "document_search_tool": {
        "name": "document_search",
        "description": "Search through user documents to find relevant information using semantic, keyword, or hybrid search.",
        "entrypoint": "app.tools.document_search_tool:search_documents",
        "scopes": ["search:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant documents"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default 5, max 50)",
                        "default": 5
                    },
                    "mode": {
                        "type": "string",
                        "description": "Search mode: hybrid, semantic, or keyword",
                        "default": "hybrid"
                    },
                    "file_ids": {
                        "type": "array",
                        "description": "Optional list of file IDs to filter"
                    }
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Whether relevant documents were found"},
                    "result_count": {"type": "integer", "description": "Number of results returned"},
                    "context": {"type": "string", "description": "Formatted context from search results"},
                    "results": {"type": "array", "description": "List of search results with metadata"},
                    "error": {"type": "string", "description": "Error message if search failed"}
                }
            }
        }
    },
    "weather_tool": {
        "name": "get_weather",
        "description": "Get current weather information for a specific location using Open-Meteo API. Use city name only (e.g., 'New York' not 'New York, NY').",
        "entrypoint": "app.tools.weather_tool:get_weather",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City name to get weather for (e.g., 'New York', 'London', 'Tokyo')"
                    }
                },
                "required": ["location"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "temperature": {"type": "number", "description": "Current temperature in Celsius"},
                    "feels_like": {"type": "number", "description": "Apparent temperature in Celsius"},
                    "humidity": {"type": "number", "description": "Relative humidity percentage"},
                    "wind_speed": {"type": "number", "description": "Wind speed in km/h"},
                    "wind_gust": {"type": "number", "description": "Wind gust speed in km/h"},
                    "conditions": {"type": "string", "description": "Weather conditions description"},
                    "location": {"type": "string", "description": "Resolved location name"}
                }
            }
        }
    },
    "ingestion_tool": {
        "name": "ingest_document",
        "description": "Ingest and process a document file for analysis and search. Handles PDF, DOCX, TXT, MD, and other text formats.",
        "entrypoint": "app.tools.ingestion_tool:ingest_document",
        "scopes": ["ingest:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to ingest"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata dictionary"
                    },
                    "force_reprocess": {
                        "type": "boolean",
                        "description": "Force reprocessing even if duplicate",
                        "default": False
                    }
                },
                "required": ["file_path"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether ingestion was successful"},
                    "file_id": {"type": "string", "description": "Unique identifier for the ingested file"},
                    "filename": {"type": "string", "description": "Original filename"},
                    "status": {"type": "string", "description": "Processing status"},
                    "message": {"type": "string", "description": "Status message"},
                    "duplicate_detected": {"type": "boolean", "description": "Whether duplicate was detected"},
                    "error": {"type": "string", "description": "Error message if failed"}
                }
            }
        }
    },
    "web_scraper_tool": {
        "name": "web_scraper",
        "description": "Fetch and extract content from a web page URL. Retrieves HTML, removes markup, and returns clean text content.",
        "entrypoint": "app.tools.web_scraper_tool:scrape_webpage",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the web page to scrape"
                    },
                    "extract_links": {
                        "type": "boolean",
                        "description": "Whether to extract links from the page (default: false)",
                        "default": False
                    },
                    "max_content_length": {
                        "type": "integer",
                        "description": "Maximum characters to return (default: 10000)",
                        "default": 10000
                    }
                },
                "required": ["url"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether the page was successfully scraped"},
                    "url": {"type": "string", "description": "The URL that was scraped"},
                    "title": {"type": "string", "description": "Page title"},
                    "content": {"type": "string", "description": "Extracted text content"},
                    "word_count": {"type": "integer", "description": "Number of words in the content"},
                    "links": {"type": "array", "description": "Extracted links (if requested)"},
                    "error": {"type": "string", "description": "Error message if scraping failed"}
                }
            }
        }
    },
}


def get_builtin_tool_definitions() -> List[ToolDefinitionRead]:
    """
    Get tool definitions for all built-in tools.
    
    Returns:
        List of ToolDefinitionRead objects for built-in tools
    """
    definitions = []
    now = datetime.now(timezone.utc)
    
    for module_name, metadata in BUILTIN_TOOL_METADATA.items():
        # Generate a deterministic UUID based on the tool name
        tool_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.tool.{metadata['name']}")
        
        definition = ToolDefinitionRead(
            id=tool_uuid,
            name=metadata["name"],
            description=metadata["description"],
            schema=metadata["schema"],
            entrypoint=metadata["entrypoint"],
            scopes=metadata["scopes"],
            is_active=True,
            is_builtin=True,
            created_by=None,
            version=metadata["version"],
            created_at=now,
            updated_at=now,
        )
        definitions.append(definition)
    
    return definitions


def get_builtin_tool_by_name(name: str) -> Optional[ToolDefinitionRead]:
    """
    Get a built-in tool definition by name.
    
    Args:
        name: Tool name (e.g., "web_search", "document_search")
        
    Returns:
        ToolDefinitionRead or None if not found
    """
    for module_name, metadata in BUILTIN_TOOL_METADATA.items():
        if metadata["name"] == name:
            tool_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.tool.{metadata['name']}")
            now = datetime.now(timezone.utc)
            
            return ToolDefinitionRead(
                id=tool_uuid,
                name=metadata["name"],
                description=metadata["description"],
                schema=metadata["schema"],
                entrypoint=metadata["entrypoint"],
                scopes=metadata["scopes"],
                is_active=True,
                is_builtin=True,
                created_by=None,
                version=metadata["version"],
                created_at=now,
                updated_at=now,
            )
    return None


def get_builtin_tool_by_id(tool_id: uuid.UUID) -> Optional[ToolDefinitionRead]:
    """
    Get a built-in tool definition by UUID.
    
    Args:
        tool_id: Tool UUID
        
    Returns:
        ToolDefinitionRead or None if not found
    """
    for module_name, metadata in BUILTIN_TOOL_METADATA.items():
        expected_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.tool.{metadata['name']}")
        if expected_uuid == tool_id:
            return get_builtin_tool_by_name(metadata["name"])
    return None


def get_tool_executor(tool_name: str) -> Optional[Callable]:
    """
    Get the executor function for a built-in tool.
    
    Args:
        tool_name: Tool name (e.g., "web_search", "get_weather")
        
    Returns:
        Async callable function or None if not found
    """
    executors = {
        "web_search": ("app.tools.web_search_tool", "search_web"),
        "document_search": ("app.tools.document_search_tool", "search_documents"),
        "get_weather": ("app.tools.weather_tool", "get_weather"),
        "ingest_document": ("app.tools.ingestion_tool", "ingest_document"),
        "web_scraper": ("app.tools.web_scraper_tool", "scrape_webpage"),
    }
    
    if tool_name not in executors:
        return None
    
    module_path, func_name = executors[tool_name]
    try:
        module = importlib.import_module(module_path)
        return getattr(module, func_name, None)
    except Exception as e:
        print(f"Warning: Failed to load tool executor for {tool_name}: {e}")
        return None
