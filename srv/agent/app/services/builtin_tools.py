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
        "description": "Search the web for current, up-to-date information. Supports multiple providers (DuckDuckGo, Perplexity, Tavily, Brave). Returns titles, URLs, and snippets from search results.",
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
                        "description": "Maximum number of results per provider (default: 5). Each enabled provider returns up to this many results.",
                        "default": 5
                    }
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Whether results were found"},
                    "result_count": {"type": "integer", "description": "Total number of results returned"},
                    "results": {"type": "array", "description": "List of search results"},
                    "query": {"type": "string", "description": "The search query used"},
                    "providers_used": {"type": "array", "description": "List of providers that returned results"},
                    "results_per_provider": {"type": "object", "description": "Number of results from each provider"},
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
    "data_tool": {
        "name": "data_document",
        "description": "Ingest and process a document file for analysis and search. Handles PDF, DOCX, TXT, MD, and other text formats.",
        "entrypoint": "app.tools.data_tool:data_document",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to data"
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
    # Data management tools for structured data documents
    "data_tool_create": {
        "name": "create_data_document",
        "description": "Create a new structured data document for storing records (like a database table or Notion database).",
        "entrypoint": "app.tools.data_tool:create_data_document",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the data document"},
                    "schema": {"type": "object", "description": "Optional schema definition with field types"},
                    "initial_records": {"type": "array", "description": "Optional initial records to insert"},
                    "visibility": {"type": "string", "description": "Visibility: 'personal' or 'shared'", "default": "personal"}
                },
                "required": ["name"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "document_id": {"type": "string"},
                    "name": {"type": "string"},
                    "record_count": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_query": {
        "name": "query_data",
        "description": "Query records from a data document with SQL-like filtering, sorting, and pagination.",
        "entrypoint": "app.tools.data_tool:query_data",
        "scopes": ["data:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "select": {"type": "array", "description": "Fields to return (default: all)"},
                    "where": {"type": "object", "description": "Filter conditions"},
                    "order_by": {"type": "array", "description": "Sort specification"},
                    "limit": {"type": "integer", "description": "Max records (default: 50)", "default": 50},
                    "offset": {"type": "integer", "description": "Pagination offset", "default": 0}
                },
                "required": ["document_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "records": {"type": "array"},
                    "total": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_insert": {
        "name": "insert_records",
        "description": "Insert records into a data document.",
        "entrypoint": "app.tools.data_tool:insert_records",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "records": {"type": "array", "description": "Records to insert"}
                },
                "required": ["document_id", "records"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "count": {"type": "integer"},
                    "record_ids": {"type": "array"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_update": {
        "name": "update_records",
        "description": "Update records in a data document matching a filter.",
        "entrypoint": "app.tools.data_tool:update_records",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "updates": {"type": "object", "description": "Field updates to apply"},
                    "where": {"type": "object", "description": "Filter for which records to update"}
                },
                "required": ["document_id", "updates"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "count": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_delete": {
        "name": "delete_records",
        "description": "Delete records from a data document by filter or IDs.",
        "entrypoint": "app.tools.data_tool:delete_records",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "where": {"type": "object", "description": "Filter for records to delete"},
                    "record_ids": {"type": "array", "description": "Specific record IDs to delete"}
                },
                "required": ["document_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "count": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_list": {
        "name": "list_data_documents",
        "description": "List available data documents accessible to the user.",
        "entrypoint": "app.tools.data_tool:list_data_documents",
        "scopes": ["data:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "visibility": {"type": "string", "description": "Filter: 'personal' or 'shared'"},
                    "limit": {"type": "integer", "description": "Max documents (default: 20)", "default": 20}
                },
                "required": []
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "documents": {"type": "array"},
                    "total": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "data_tool_get": {
        "name": "get_data_document",
        "description": "Get a data document with schema, metadata, and optionally all records.",
        "entrypoint": "app.tools.data_tool:get_data_document",
        "scopes": ["data:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "include_records": {"type": "boolean", "description": "Include all records", "default": True}
                },
                "required": ["document_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "document": {"type": "object"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    # Graph database tools for knowledge graph operations
    "graph_query_tool": {
        "name": "graph_query",
        "description": "Search the knowledge graph for entities (people, organizations, technologies, concepts) and their relationships. Use this to find connections between entities.",
        "entrypoint": "app.tools.graph_tool:graph_query",
        "scopes": ["graph:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query to find entities by name"},
                    "entity_type": {"type": "string", "description": "Optional filter: Person, Organization, Technology, Concept, Location, Project"},
                    "depth": {"type": "integer", "description": "Graph traversal depth (1-5, default 2)", "default": 2},
                    "limit": {"type": "integer", "description": "Maximum results (default 20)", "default": 20}
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Whether results were found"},
                    "node_count": {"type": "integer", "description": "Number of nodes returned"},
                    "edge_count": {"type": "integer", "description": "Number of edges returned"},
                    "context": {"type": "string", "description": "Formatted context for reasoning"},
                    "nodes": {"type": "array", "description": "Graph nodes"},
                    "edges": {"type": "array", "description": "Graph edges"},
                    "error": {"type": "string", "description": "Error message if query failed"}
                }
            }
        }
    },
    "graph_explore_tool": {
        "name": "graph_explore",
        "description": "Explore the neighborhood of a specific entity in the knowledge graph. Use this to discover what an entity is connected to.",
        "entrypoint": "app.tools.graph_tool:graph_explore",
        "scopes": ["graph:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string", "description": "ID of the node to explore"},
                    "depth": {"type": "integer", "description": "Traversal depth (1-5, default 2)", "default": 2},
                    "rel_types": {"type": "array", "description": "Optional relationship type filter"},
                    "limit": {"type": "integer", "description": "Maximum neighbors (default 30)", "default": 30}
                },
                "required": ["node_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean", "description": "Whether the node was found"},
                    "center_node": {"type": "object", "description": "The explored node"},
                    "neighbor_count": {"type": "integer", "description": "Number of connected nodes"},
                    "context": {"type": "string", "description": "Formatted context"},
                    "neighbors": {"type": "array", "description": "Connected nodes"},
                    "relationships": {"type": "array", "description": "Connecting relationships"},
                    "error": {"type": "string", "description": "Error message"}
                }
            }
        }
    },
    "graph_relate_tool": {
        "name": "graph_relate",
        "description": "Create a relationship between two entities in the knowledge graph. Use this to explicitly connect things you discover during conversation.",
        "entrypoint": "app.tools.graph_tool:graph_relate",
        "scopes": ["graph:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "from_id": {"type": "string", "description": "Source node ID"},
                    "relationship": {"type": "string", "description": "Relationship type (e.g., WORKS_ON, DEPENDS_ON, RELATED_TO)"},
                    "to_id": {"type": "string", "description": "Target node ID"}
                },
                "required": ["from_id", "relationship", "to_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether the relationship was created"},
                    "message": {"type": "string", "description": "Status message"},
                    "error": {"type": "string", "description": "Error message"}
                }
            }
        }
    },
    # Library trigger tools
    "library_trigger_tool": {
        "name": "create_library_trigger",
        "description": "Create a library trigger that automatically fires an agent when documents complete processing in a specific library. Use this to set up automated extraction pipelines.",
        "entrypoint": "app.tools.library_trigger_tool:create_library_trigger",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "library_id": {"type": "string", "description": "UUID of the library to watch"},
                    "name": {"type": "string", "description": "Human-readable trigger name"},
                    "agent_id": {"type": "string", "description": "UUID of the agent to execute"},
                    "prompt": {"type": "string", "description": "Instructions for the agent"},
                    "description": {"type": "string", "description": "Optional description"},
                    "schema_document_id": {"type": "string", "description": "Optional schema data document UUID"}
                },
                "required": ["library_id", "name", "agent_id", "prompt"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether creation succeeded"},
                    "trigger_id": {"type": "string", "description": "UUID of the created trigger"},
                    "name": {"type": "string", "description": "Trigger name"},
                    "library_id": {"type": "string", "description": "Library being watched"},
                    "message": {"type": "string", "description": "Status message"},
                    "error": {"type": "string", "description": "Error message if failed"}
                }
            }
        }
    },
    "image_tool": {
        "name": "generate_image",
        "description": "Generate an image from a text prompt and return a URL to the generated image.",
        "entrypoint": "app.tools.image_tool:generate_image",
        "scopes": ["media:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "Prompt describing the image to generate"},
                    "size": {"type": "string", "description": "Image size (e.g. 1024x1024)", "default": "1024x1024"},
                    "style": {"type": "string", "description": "Optional style guidance"}
                },
                "required": ["prompt"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "image_url": {"type": "string"},
                    "revised_prompt": {"type": "string"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "transcription_tool": {
        "name": "transcribe_audio",
        "description": "Transcribe an audio file from URL and return text output.",
        "entrypoint": "app.tools.transcription_tool:transcribe_audio",
        "scopes": ["media:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "file_url": {"type": "string", "description": "URL to the audio file"},
                    "language": {"type": "string", "description": "Optional language hint (e.g. en)"}
                },
                "required": ["file_url"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "text": {"type": "string"},
                    "language": {"type": "string"},
                    "duration": {"type": "number"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "tts_tool": {
        "name": "text_to_speech",
        "description": "Convert text to speech and return a URL to the generated audio file.",
        "entrypoint": "app.tools.tts_tool:text_to_speech",
        "scopes": ["media:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to synthesize into speech"},
                    "voice": {"type": "string", "description": "Voice preset", "default": "alloy"},
                    "speed": {"type": "number", "description": "Speech speed", "default": 1.0}
                },
                "required": ["text"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "audio_url": {"type": "string"},
                    "duration_seconds": {"type": "number"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    # Extraction schema tool
    "extraction_schema_tool": {
        "name": "create_extraction_schema",
        "description": "Create a data document with an extraction schema optimized for automated document processing. Includes graph node and relationship configuration for knowledge graph population.",
        "entrypoint": "app.tools.extraction_schema_tool:create_extraction_schema",
        "scopes": ["data:write"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the data document (e.g., 'Parsed Resumes')"},
                    "item_label": {"type": "string", "description": "Label for individual records (e.g., 'Resume', 'RFP')"},
                    "graph_node_label": {"type": "string", "description": "Neo4j node label (e.g., 'Resume', 'RFP')"},
                    "fields": {"type": "object", "description": "Schema field definitions with types and metadata"},
                    "graph_relationships": {"type": "array", "description": "Graph relationship definitions"},
                    "description": {"type": "string", "description": "Optional schema description"}
                },
                "required": ["name", "item_label", "fields"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether creation succeeded"},
                    "document_id": {"type": "string", "description": "UUID of the created data document"},
                    "name": {"type": "string", "description": "Document name"},
                    "field_count": {"type": "integer", "description": "Number of schema fields"},
                    "message": {"type": "string", "description": "Status message"},
                    "error": {"type": "string", "description": "Error message if failed"}
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
        "data_document": ("app.tools.data_tool", "data_document"),
        "web_scraper": ("app.tools.web_scraper_tool", "scrape_webpage"),
        # Data management tools
        "create_data_document": ("app.tools.data_tool", "create_data_document"),
        "query_data": ("app.tools.data_tool", "query_data"),
        "insert_records": ("app.tools.data_tool", "insert_records"),
        "update_records": ("app.tools.data_tool", "update_records"),
        "delete_records": ("app.tools.data_tool", "delete_records"),
        "list_data_documents": ("app.tools.data_tool", "list_data_documents"),
        "get_data_document": ("app.tools.data_tool", "get_data_document"),
        # Graph tools
        "graph_query": ("app.tools.graph_tool", "graph_query"),
        "graph_explore": ("app.tools.graph_tool", "graph_explore"),
        "graph_relate": ("app.tools.graph_tool", "graph_relate"),
        # Library trigger tools
        "create_library_trigger": ("app.tools.library_trigger_tool", "create_library_trigger"),
        # Extraction schema tool
        "create_extraction_schema": ("app.tools.extraction_schema_tool", "create_extraction_schema"),
        # Media tools
        "generate_image": ("app.tools.image_tool", "generate_image"),
        "transcribe_audio": ("app.tools.transcription_tool", "transcribe_audio"),
        "text_to_speech": ("app.tools.tts_tool", "text_to_speech"),
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


def get_tool_object(tool_name: str) -> Optional[Any]:
    """
    Get the pre-built PydanticAI Tool object for a built-in tool.
    
    Returns Tool objects (with takes_ctx properly configured) instead of
    raw functions. This avoids PydanticAI schema generation errors when
    registering tools with agent.tool().
    
    Args:
        tool_name: Tool name (e.g., "web_search", "get_weather")
        
    Returns:
        PydanticAI Tool object or None if not found
    """
    # Map tool names to their pre-built Tool object variable names
    tool_objects = {
        "web_search": ("app.tools.web_search_tool", "web_search_tool"),
        "document_search": ("app.tools.document_search_tool", "document_search_tool"),
        "get_weather": ("app.tools.weather_tool", "weather_tool"),
        "data_document": ("app.tools.ingestion_tool", "data_tool"),
        "web_scraper": ("app.tools.web_scraper_tool", "web_scraper_tool"),
        # Data management tools
        "create_data_document": ("app.tools.data_tool", "create_data_document_tool"),
        "query_data": ("app.tools.data_tool", "query_data_tool"),
        "insert_records": ("app.tools.data_tool", "insert_records_tool"),
        "update_records": ("app.tools.data_tool", "update_records_tool"),
        "delete_records": ("app.tools.data_tool", "delete_records_tool"),
        "list_data_documents": ("app.tools.data_tool", "list_data_documents_tool"),
        "get_data_document": ("app.tools.data_tool", "get_data_document_tool"),
        # Graph tools
        "graph_query": ("app.tools.graph_tool", "graph_query_tool"),
        "graph_explore": ("app.tools.graph_tool", "graph_explore_tool"),
        "graph_relate": ("app.tools.graph_tool", "graph_relate_tool"),
        # Library trigger tools
        "create_library_trigger": ("app.tools.library_trigger_tool", "create_library_trigger_tool"),
        # Extraction schema tool
        "create_extraction_schema": ("app.tools.extraction_schema_tool", "create_extraction_schema_tool"),
        # Media tools
        "generate_image": ("app.tools.image_tool", "image_tool"),
        "transcribe_audio": ("app.tools.transcription_tool", "transcription_tool"),
        "text_to_speech": ("app.tools.tts_tool", "tts_tool"),
    }
    
    if tool_name not in tool_objects:
        return None
    
    module_path, obj_name = tool_objects[tool_name]
    try:
        module = importlib.import_module(module_path)
        return getattr(module, obj_name, None)
    except Exception as e:
        print(f"Warning: Failed to load Tool object for {tool_name}: {e}")
        return None
