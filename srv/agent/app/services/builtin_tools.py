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
        "description": "Search through user documents to find relevant information using semantic, keyword, or hybrid search. Results are filtered by relevancy score.",
        "entrypoint": "app.tools.document_search_tool:search_documents",
        "scopes": ["search:read"],
        "version": 2,
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
                        "description": "Maximum number of results (default 10, max 50)",
                        "default": 10
                    },
                    "min_score": {
                        "type": "number",
                        "description": "Minimum relevancy score threshold (0-1, default 0.1). Results below this score are excluded.",
                        "default": 0.1
                    },
                    "mode": {
                        "type": "string",
                        "description": "Search mode: hybrid, semantic, or keyword",
                        "default": "hybrid"
                    },
                    "file_ids": {
                        "type": "array",
                        "description": "Optional list of file IDs to filter"
                    },
                    "expand_graph": {
                        "type": "boolean",
                        "description": "Expand graph relationships for richer context (adds latency, default false)",
                        "default": False
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
        "description": (
            "Fetch and extract content from a web page URL using a tiered stealth engine "
            "(curl_cffi chrome impersonation -> Playwright+stealth -> Camoufox). Auto-escalates "
            "on 403/Cloudflare/CAPTCHA/timeout. PDFs are auto-detected and a pypdf text preview "
            "is returned. Responses are cached in Redis."
        ),
        "entrypoint": "app.tools.web_scraper_tool:scrape_webpage",
        "scopes": [],
        "version": 3,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the web page to scrape"
                    },
                    "include_links": {
                        "type": "boolean",
                        "description": "Extract up to 50 links from the page",
                        "default": False
                    },
                    "max_content_length": {
                        "type": "integer",
                        "description": "Max characters of content to return",
                        "default": 10000
                    },
                    "use_browser": {
                        "type": "boolean",
                        "description": "Force Tier 2 (Playwright). Use after a BLOCKED_403 / JS_REQUIRED signal.",
                        "default": False
                    },
                    "use_camoufox": {
                        "type": "boolean",
                        "description": (
                            "Force Tier 3 (Camoufox anti-detect Firefox). Only use after "
                            "BLOCKED_CLOUDFLARE / CAPTCHA_REQUIRED. Requires ENABLE_CAMOUFOX=true on server."
                        ),
                        "default": False
                    },
                    "cache_ttl": {
                        "type": "integer",
                        "description": "Response cache TTL in seconds (0 to bypass cache)",
                        "default": 3600
                    },
                    "extract_mode": {
                        "type": "string",
                        "description": "Content extraction mode: 'auto' (default), 'article' (precision), 'full_page' (raw)",
                        "default": "auto"
                    },
                    "block_resources": {
                        "type": "boolean",
                        "description": "Block images/fonts/CSS/analytics in browser tiers (saves bandwidth)",
                        "default": True
                    },
                    "profile_name": {
                        "type": "string",
                        "description": "Override UA profile (chrome131_win, chrome131_mac, firefox121_win, safari17_mac, ...)"
                    }
                },
                "required": ["url"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether the page was successfully scraped"},
                    "url": {"type": "string", "description": "Final URL after redirects"},
                    "title": {"type": "string", "description": "Page title"},
                    "content": {"type": "string", "description": "Extracted text/markdown content"},
                    "word_count": {"type": "integer", "description": "Word count of returned content"},
                    "links": {"type": "array", "description": "Extracted links (if requested)"},
                    "method": {"type": "string", "description": "Tier used: curl_cffi | playwright | camoufox | cache"},
                    "extractor": {"type": "string", "description": "Extractor used: trafilatura | readability | regex | pdf"},
                    "error_code": {
                        "type": "string",
                        "description": (
                            "Structured error code: OK | BLOCKED_403 | BLOCKED_429 | BLOCKED_CLOUDFLARE | "
                            "CAPTCHA_REQUIRED | TIMEOUT | DNS_FAILED | TLS_FAILED | JS_REQUIRED | "
                            "UNSUPPORTED_CONTENT_TYPE | INVALID_URL | UNKNOWN"
                        )
                    },
                    "error": {"type": "string", "description": "Human-readable error message"},
                    "from_cache": {"type": "boolean", "description": "Whether result came from cache"},
                    "raw_html_len": {"type": "integer", "description": "Raw HTML length (0 for PDFs / cache hits)"}
                }
            }
        }
    },
    "playwright_tool": {
        "name": "playwright_browser",
        "description": (
            "Browse a web page with a stealth headless browser (JS + scripted interaction). "
            "Use when you need click/type/scroll actions or screenshots. For plain content, prefer "
            "`web_scraper` which auto-escalates tiers."
        ),
        "entrypoint": "app.tools.playwright_tool:browse_webpage",
        "scopes": [],
        "version": 2,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL of the web page to browse"},
                    "wait_for_selector": {
                        "type": "string",
                        "description": "CSS selector to wait for before extraction"
                    },
                    "actions": {
                        "type": "array",
                        "description": "List of actions: click(selector), type(selector,text), scroll, wait(ms)"
                    },
                    "include_links": {
                        "type": "boolean",
                        "description": "Extract up to 50 links",
                        "default": False
                    },
                    "screenshot": {
                        "type": "boolean",
                        "description": "Save a PNG screenshot",
                        "default": False
                    },
                    "max_content_length": {
                        "type": "integer",
                        "description": "Max characters of content to return",
                        "default": 15000
                    },
                    "block_resources": {
                        "type": "boolean",
                        "description": "Block images/fonts/CSS/analytics (saves bandwidth)",
                        "default": True
                    },
                    "extract_mode": {
                        "type": "string",
                        "description": "'auto' | 'article' | 'full_page'",
                        "default": "auto"
                    },
                    "profile_name": {
                        "type": "string",
                        "description": "Override UA profile"
                    }
                },
                "required": ["url"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean", "description": "Whether the page was successfully loaded"},
                    "url": {"type": "string", "description": "Final URL"},
                    "title": {"type": "string", "description": "Page title"},
                    "content": {"type": "string", "description": "Extracted text content"},
                    "links": {"type": "array", "description": "Extracted links (if requested)"},
                    "screenshot_path": {"type": "string", "description": "Screenshot path (if taken)"},
                    "error_code": {"type": "string", "description": "Structured error code"},
                    "error": {"type": "string", "description": "Error message if browsing failed"}
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
    "memory_tool_search": {
        "name": "memory_search",
        "description": "Search the user's saved memories and learned insights. Uses semantic retrieval with recency weighting.",
        "entrypoint": "app.tools.memory_tool:memory_search",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Memory search query"},
                    "limit": {"type": "integer", "description": "Maximum results (default 5)", "default": 5},
                    "score_threshold": {"type": "number", "description": "Cosine distance threshold (lower is stricter)", "default": 0.8},
                    "half_life_days": {"type": "number", "description": "Recency decay half-life in days", "default": 30.0}
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "found": {"type": "boolean"},
                    "result_count": {"type": "integer"},
                    "context": {"type": "string"},
                    "results": {"type": "array"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "memory_tool_save": {
        "name": "memory_save",
        "description": "Save an explicit long-term memory for the current user.",
        "entrypoint": "app.tools.memory_tool:memory_save",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content to save"},
                    "category": {"type": "string", "description": "Memory category (preference, fact, goal, context, other)", "default": "context"}
                },
                "required": ["content"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "memory_id": {"type": "string"},
                    "message": {"type": "string"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "calendar_tool_list": {
        "name": "calendar_list_events",
        "description": "List upcoming calendar events from the user's connected Google or Microsoft calendar.",
        "entrypoint": "app.tools.calendar_tool:calendar_list_events",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "Optional RFC3339 start window"},
                    "time_max": {"type": "string", "description": "Optional RFC3339 end window"},
                    "max_results": {"type": "integer", "description": "Maximum number of events", "default": 10}
                },
                "required": []
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "events": {"type": "array"},
                    "count": {"type": "integer"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "delegate_tool": {
        "name": "delegate_to_agent",
        "description": (
            "Invoke another specialist agent synchronously and return its output. "
            "Use for multi-agent orchestration: delegate sub-tasks to agents such as "
            "briefing-agent, scheduler-agent, or debrief-agent. "
            "One of agent_id or agent_name is required."
        ),
        "entrypoint": "app.tools.delegate_tool:delegate_to_agent",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "UUID of the target agent (preferred).",
                    },
                    "agent_name": {
                        "type": "string",
                        "description": "Name of the target agent (used when agent_id is not known).",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Task or question to send to the delegated agent.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Optional key/value context merged into the agent payload.",
                    },
                    "tier": {
                        "type": "string",
                        "enum": ["simple", "complex", "batch"],
                        "description": "Execution tier: simple (30 s), complex (5 min), batch (30 min).",
                        "default": "simple",
                    },
                },
                "required": ["prompt"],
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "run_id": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "agent_name": {"type": "string"},
                    "output": {"description": "Agent output (text or structured)"},
                    "status": {"type": "string"},
                    "error": {"type": "string"},
                },
            },
        },
    },
    "calendar_tool_create": {
        "name": "calendar_create_event",
        "description": "Create a calendar event (Google or Microsoft) with summary, start/end RFC3339 datetimes, and optional attendees.",
        "entrypoint": "app.tools.calendar_tool:calendar_create_event",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "start": {"type": "string", "description": "RFC3339 datetime"},
                    "end": {"type": "string", "description": "RFC3339 datetime"},
                    "description": {"type": "string"},
                    "timezone": {"type": "string", "default": "UTC"}
                },
                "required": ["summary", "start", "end"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "event": {"type": "object"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "email_tool_list_recent": {
        "name": "email_list_recent",
        "description": "List recent emails from the user's connected inbox (Gmail or Outlook).",
        "entrypoint": "app.tools.email_tool:email_list_recent",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "hours": {"type": "integer", "description": "How many hours back to look", "default": 24},
                    "max_results": {"type": "integer", "description": "Max emails to return (max 50)", "default": 20}
                },
                "required": []
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "messages": {"type": "array"},
                    "count": {"type": "integer"},
                    "provider": {"type": "string"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "email_tool_search": {
        "name": "email_search",
        "description": "Search the user's email inbox by keywords, sender, subject, or date range.",
        "entrypoint": "app.tools.email_tool:email_search",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Free-text search keywords"},
                    "max_results": {"type": "integer", "description": "Max emails to return (max 50)", "default": 20},
                    "from_email": {"type": "string", "description": "Filter by sender email"},
                    "subject": {"type": "string", "description": "Filter by subject keywords"},
                    "days_back": {"type": "integer", "description": "How many days back to search", "default": 30}
                },
                "required": ["query"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "messages": {"type": "array"},
                    "count": {"type": "integer"},
                    "provider": {"type": "string"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "email_tool_read": {
        "name": "email_read",
        "description": "Read the full content of a specific email message by its ID.",
        "entrypoint": "app.tools.email_tool:email_read",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "message_id": {"type": "string", "description": "Email message ID from email_list_recent or email_search"}
                },
                "required": ["message_id"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "messages": {"type": "array"},
                    "subject": {"type": "string"},
                    "count": {"type": "integer"},
                    "provider": {"type": "string"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "calendar_tool_availability": {
        "name": "calendar_get_availability",
        "description": "Get the user's free/busy calendar availability for a time range. Returns a list of busy slots.",
        "entrypoint": "app.tools.calendar_tool:calendar_get_availability",
        "scopes": [],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "time_min": {"type": "string", "description": "RFC3339 start of range, e.g. 2026-02-18T09:00:00Z"},
                    "time_max": {"type": "string", "description": "RFC3339 end of range, e.g. 2026-02-18T17:00:00Z"}
                },
                "required": ["time_min", "time_max"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "slots": {"type": "array"},
                    "provider": {"type": "string"},
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
    # Aggregation and facets tools
    "aggregate_data_tool": {
        "name": "aggregate_data",
        "description": "Run aggregation queries (count, sum, avg, min, max) on a data document, optionally grouped by fields. Use for analytics, summaries, and dashboards.",
        "entrypoint": "app.tools.data_tool:aggregate_data",
        "scopes": ["data:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "aggregate": {"type": "object", "description": "Aggregation functions, e.g., {\"count\": \"*\", \"avg\": \"salary\"}"},
                    "group_by": {"type": "array", "description": "Fields to group by, e.g., [\"department\", \"status\"]"},
                    "where": {"type": "object", "description": "Optional filter conditions (same syntax as query_data)"}
                },
                "required": ["document_id", "aggregate"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "results": {"type": "array", "description": "Aggregation results with group values and computed metrics"},
                    "total_groups": {"type": "integer", "description": "Number of groups returned"},
                    "error": {"type": "string"}
                }
            }
        }
    },
    "get_facets_tool": {
        "name": "get_facets",
        "description": "Get distinct values and counts for specified fields in a data document. Useful for discovering valid filter values and building category breakdowns.",
        "entrypoint": "app.tools.data_tool:get_facets",
        "scopes": ["data:read"],
        "version": 1,
        "schema": {
            "input": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string", "description": "UUID of the data document"},
                    "fields": {"type": "array", "description": "Field names to get facets for, e.g., [\"department\", \"status\"]"},
                    "where": {"type": "object", "description": "Optional filter to scope facet computation"}
                },
                "required": ["document_id", "fields"]
            },
            "output": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "facets": {"type": "object", "description": "Map of field name to [{value, count}] entries"},
                    "total_records": {"type": "integer"},
                    "error": {"type": "string"}
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
        "playwright_browser": ("app.tools.playwright_tool", "browse_webpage"),
        # Data management tools
        "create_data_document": ("app.tools.data_tool", "create_data_document"),
        "query_data": ("app.tools.data_tool", "query_data"),
        "insert_records": ("app.tools.data_tool", "insert_records"),
        "update_records": ("app.tools.data_tool", "update_records"),
        "delete_records": ("app.tools.data_tool", "delete_records"),
        "list_data_documents": ("app.tools.data_tool", "list_data_documents"),
        "get_data_document": ("app.tools.data_tool", "get_data_document"),
        "aggregate_data": ("app.tools.data_tool", "aggregate_data"),
        "get_facets": ("app.tools.data_tool", "get_facets"),
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
        "memory_search": ("app.tools.memory_tool", "memory_search"),
        "memory_save": ("app.tools.memory_tool", "memory_save"),
        "calendar_list_events": ("app.tools.calendar_tool", "calendar_list_events"),
        "calendar_create_event": ("app.tools.calendar_tool", "calendar_create_event"),
        "calendar_get_availability": ("app.tools.calendar_tool", "calendar_get_availability"),
        "email_list_recent": ("app.tools.email_tool", "email_list_recent"),
        "email_search": ("app.tools.email_tool", "email_search"),
        "email_read": ("app.tools.email_tool", "email_read"),
        "delegate_to_agent": ("app.tools.delegate_tool", "delegate_to_agent"),
        # Workforce tools
        "workforce_search_employees": ("app.tools.workforce_tool", "workforce_search_employees"),
        "workforce_get_stats": ("app.tools.workforce_tool", "workforce_get_stats"),
        "workforce_get_employee": ("app.tools.workforce_tool", "workforce_get_employee"),
        "workforce_search_checkins": ("app.tools.workforce_tool", "workforce_search_checkins"),
        "workforce_get_facets": ("app.tools.workforce_tool", "workforce_get_facets"),
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
        "playwright_browser": ("app.tools.playwright_tool", "playwright_browser_tool"),
        # Data management tools
        "create_data_document": ("app.tools.data_tool", "create_data_document_tool"),
        "query_data": ("app.tools.data_tool", "query_data_tool"),
        "insert_records": ("app.tools.data_tool", "insert_records_tool"),
        "update_records": ("app.tools.data_tool", "update_records_tool"),
        "delete_records": ("app.tools.data_tool", "delete_records_tool"),
        "list_data_documents": ("app.tools.data_tool", "list_data_documents_tool"),
        "get_data_document": ("app.tools.data_tool", "get_data_document_tool"),
        "aggregate_data": ("app.tools.data_tool", "aggregate_data_tool"),
        "get_facets": ("app.tools.data_tool", "get_facets_tool"),
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
        "memory_search": ("app.tools.memory_tool", "memory_search_tool"),
        "memory_save": ("app.tools.memory_tool", "memory_save_tool"),
        "calendar_list_events": ("app.tools.calendar_tool", "calendar_list_events_tool"),
        "calendar_create_event": ("app.tools.calendar_tool", "calendar_create_event_tool"),
        "calendar_get_availability": ("app.tools.calendar_tool", "calendar_get_availability_tool"),
        "email_list_recent": ("app.tools.email_tool", "email_list_recent_tool"),
        "email_search": ("app.tools.email_tool", "email_search_tool"),
        "email_read": ("app.tools.email_tool", "email_read_tool"),
        "delegate_to_agent": ("app.tools.delegate_tool", "delegate_to_agent_tool"),
        # Workforce tools
        "workforce_search_employees": ("app.tools.workforce_tool", "workforce_search_employees_tool"),
        "workforce_get_stats": ("app.tools.workforce_tool", "workforce_get_stats_tool"),
        "workforce_get_employee": ("app.tools.workforce_tool", "workforce_get_employee_tool"),
        "workforce_search_checkins": ("app.tools.workforce_tool", "workforce_search_checkins_tool"),
        "workforce_get_facets": ("app.tools.workforce_tool", "workforce_get_facets_tool"),
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
