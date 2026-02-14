"""
Discover and load built-in agents from the agents directory.

This module dynamically scans the app/agents/ directory for agent Python files
and exposes them as built-in agent definitions without requiring database entries.
"""
import importlib
import inspect
import os
import uuid
from pathlib import Path
from typing import Dict, List

from typing import Union
from pydantic_ai import Agent
from app.agents.base_agent import BaseStreamingAgent
from app.schemas.definitions import AgentDefinitionRead

# Type alias for agents (can be either PydanticAI Agent or our BaseStreamingAgent)
AgentInstance = Union[Agent, BaseStreamingAgent]


# Mapping of agent file names to their metadata
# The 'tools' field explicitly lists the tool names used by each agent
#
# NOTE: Agents in the 'in-progress' folder are not included here.
# They will be added back when they are ready for production use.
# In-progress agents:
#   - attachment_agent
#   - rag_search_agent
#   - rfp_agent
#   - summary_comparison_agent
#   - template_generator_agent
#   - template_improvement_agent
BUILTIN_AGENT_METADATA = {
    "test_agent": {
        "name": "test-agent",
        "display_name": "Test Agent",
        "description": "Minimal agent for LLM chain validation tests (uses configured model alias, no tools)",
        "model": "fast",
        "version": 1,
        "tools": [],
    },
    "chat_agent": {
        "name": "chat-agent",
        "display_name": "Chat Assistant",
        "description": "General purpose chat agent with access to web search, weather, document search, and ingestion tools",
        "model": "chat",
        "version": 1,
        "tools": ["web_search", "get_weather", "document_search", "data_document"],
    },
    "document_agent": {
        "name": "document-agent",
        "display_name": "Document Assistant",
        "description": "Intelligent document Q&A agent that searches and answers questions from your documents",
        "model": "agent",
        "version": 1,
        "tools": ["document_search"],
    },
    "web_search_agent": {
        "name": "web-search-agent",
        "display_name": "Web Search Agent",
        "description": "Finds up-to-date information from the internet using web search",
        "model": "agent",
        "version": 1,
        "tools": ["web_search", "web_scraper"],
    },
    "weather_agent": {
        "name": "weather-agent",
        "display_name": "Weather Agent",
        "description": "Provides weather information for any location using real-time data",
        "model": "agent",
        "version": 1,
        "tools": ["get_weather"],
    },
    "schema_builder_agent": {
        "name": "schema-builder",
        "display_name": "Schema Builder",
        "description": "Helps create extraction schemas and library triggers for automated document processing into structured data and knowledge graphs",
        "model": "agent",
        "version": 1,
        "tools": ["document_search", "create_data_document", "create_extraction_schema", "create_library_trigger", "list_data_documents", "query_data"],
    },
}


def discover_builtin_agents() -> Dict[str, AgentInstance]:
    """
    Discover and load all built-in agents from the agents directory.
    
    Returns:
        Dict mapping agent names to agent instances (PydanticAI Agent or BaseStreamingAgent)
    """
    agents_dir = Path(__file__).parent.parent / "agents"
    discovered_agents = {}
    
    # Scan for agent files
    for agent_file in agents_dir.glob("*_agent.py"):
        module_name = agent_file.stem  # e.g., "chat_agent"
        
        # Skip if not in metadata
        if module_name not in BUILTIN_AGENT_METADATA:
            continue
        
        try:
            # Import the module
            module = importlib.import_module(f"app.agents.{module_name}")
            
            # Find the agent instance (should be named like "chat_agent", "weather_agent", etc.)
            agent_var_name = module_name  # e.g., "chat_agent"
            if hasattr(module, agent_var_name):
                agent_instance = getattr(module, agent_var_name)
                # Accept both PydanticAI Agent and our BaseStreamingAgent
                if isinstance(agent_instance, (Agent, BaseStreamingAgent)):
                    metadata = BUILTIN_AGENT_METADATA[module_name]
                    discovered_agents[metadata["name"]] = agent_instance
        except Exception as e:
            print(f"Warning: Failed to load agent from {module_name}: {e}")
            continue
    
    return discovered_agents


def get_builtin_agent_definitions() -> List[AgentDefinitionRead]:
    """
    Get agent definitions for all built-in agents.
    
    Returns:
        List of AgentDefinitionRead objects for built-in agents
    """
    from datetime import datetime, timezone
    
    definitions = []
    
    for module_name, metadata in BUILTIN_AGENT_METADATA.items():
        # Generate a deterministic UUID based on the agent name
        agent_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.{metadata['name']}")
        
        # Extract instructions from the agent if possible
        instructions = f"Built-in {metadata['display_name']} agent"
        try:
            module = importlib.import_module(f"app.agents.{module_name}")
            agent_var_name = module_name
            if hasattr(module, agent_var_name):
                agent_instance = getattr(module, agent_var_name)
                if isinstance(agent_instance, Agent):
                    # PydanticAI stores system_prompt in _system_prompts list
                    if hasattr(agent_instance, '_system_prompts') and agent_instance._system_prompts:
                        # Get the first system prompt (there may be multiple)
                        first_prompt = agent_instance._system_prompts[0]
                        if isinstance(first_prompt, str):
                            instructions = first_prompt
                        elif callable(first_prompt):
                            try:
                                instructions = str(first_prompt()) or instructions
                            except:
                                pass
        except Exception as e:
            print(f"Warning: Failed to extract instructions from {module_name}: {e}")
        
        # Get tool names from explicit metadata (more reliable than introspection)
        tool_names = metadata.get("tools", [])
        
        # Use current timestamp
        now = datetime.now(timezone.utc)
        
        definition = AgentDefinitionRead(
            id=agent_uuid,
            name=metadata["name"],
            display_name=metadata["display_name"],
            description=metadata["description"],
            model=metadata["model"],
            instructions=instructions,
            tools={"names": tool_names},  # Use explicit tool list from metadata
            workflow=None,
            scopes=[],
            is_active=True,
            is_builtin=True,
            created_by=None,
            version=metadata["version"],
            created_at=now,
            updated_at=now,
        )
        definitions.append(definition)
    
    return definitions


def get_builtin_agent_by_name(name: str) -> AgentInstance | None:
    """
    Get a built-in agent instance by name.
    
    Args:
        name: Agent name (e.g., "chat", "web-search")
        
    Returns:
        Agent instance (PydanticAI Agent or BaseStreamingAgent) or None if not found
    """
    agents = discover_builtin_agents()
    return agents.get(name)


def get_builtin_agent_by_id(agent_id: uuid.UUID) -> AgentInstance | None:
    """
    Get a built-in agent instance by UUID.
    
    Args:
        agent_id: Agent UUID
        
    Returns:
        Agent instance (PydanticAI Agent or BaseStreamingAgent) or None if not found
    """
    # Find the agent name from the UUID
    for module_name, metadata in BUILTIN_AGENT_METADATA.items():
        expected_uuid = uuid.uuid5(uuid.NAMESPACE_DNS, f"busibox.builtin.{metadata['name']}")
        if expected_uuid == agent_id:
            return get_builtin_agent_by_name(metadata["name"])
    return None

