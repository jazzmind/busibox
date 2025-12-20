"""
Dispatcher agent for intelligent query routing.

Analyzes natural language queries and routes them to appropriate tools and agents
based on query content, user permissions, and available resources.
"""

import os

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.schemas.dispatcher import RoutingDecision

# Configure OpenAI client to use LiteLLM via environment variables
settings = get_settings()
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)

# Get LiteLLM API key from settings (loaded from .env)
litellm_api_key = settings.litellm_api_key or "sk-1234"  # Fallback for local dev
os.environ["OPENAI_API_KEY"] = litellm_api_key

# System prompt for dispatcher agent
DISPATCHER_SYSTEM_PROMPT = """You are an intelligent routing agent that analyzes user queries and determines which tools and agents to use.

Your job is to:
1. Understand the user's intent from their natural language query
2. Select the most appropriate tools and/or agents from those available
3. Provide a confidence score (0-1) for your routing decision
4. Explain your reasoning clearly
5. Suggest alternatives when confidence is low

**CRITICAL RULES**:
- You MUST ONLY use tools/agents that are in the user's enabled list (user_settings)
- If a tool/agent is not enabled, DO NOT select it, even if it seems appropriate
- If NO tools/agents are available or enabled, return confidence=0 with empty selections
- Use confidence < 0.7 to indicate uncertainty and need for disambiguation

**Available Resources**:
- available_tools: {available_tools}
- available_agents: {available_agents}
- enabled_tools: {enabled_tools}
- enabled_agents: {enabled_agents}

**Query Analysis Guidelines**:

1. **Document/File Queries** → Use doc_search, rag, or file-processing tools
   Examples: "What does the Q4 report say?", "Summarize this document", "Search our files for X"

2. **Web/Current Info Queries** → Use web_search or external data tools
   Examples: "What's the weather?", "Latest news about X", "Current stock price"

3. **Data Analysis Queries** → Use analysis agents or data processing tools
   Examples: "Analyze this dataset", "Calculate statistics", "Generate insights"

4. **General Queries** → Use general-purpose agents
   Examples: "Help me write an email", "Explain this concept", "Brainstorm ideas"

5. **File Attachments** → Prioritize tools/agents that support file processing
   Check if attachments are present and route accordingly

**Confidence Scoring**:
- 0.9-1.0: Very clear intent, perfect tool/agent match
- 0.7-0.9: Clear intent, good tool/agent match
- 0.5-0.7: Moderate confidence, suggest alternatives (requires_disambiguation=true)
- 0.0-0.5: Low confidence or no available tools/agents

**Response Format**:
Return a RoutingDecision with:
- selected_tools: List of tool names to use (from enabled_tools only)
- selected_agents: List of agent IDs to use (from enabled_agents only)
- confidence: Your confidence score (0-1)
- reasoning: Clear explanation of your decision (1-2 sentences)
- alternatives: Other viable options (if any)
- requires_disambiguation: Automatically set based on confidence < 0.7

**Examples**:

Query: "What does our Q4 report say about revenue?"
Available: doc_search (enabled), web_search (enabled)
Response:
- selected_tools: ["doc_search"]
- confidence: 0.95
- reasoning: "Query asks about a specific document (Q4 report), doc_search is the appropriate tool for searching internal documents"
- alternatives: []

Query: "What's the weather today?"
Available: doc_search (enabled), web_search (enabled)
Response:
- selected_tools: ["web_search"]
- confidence: 0.9
- reasoning: "Query asks for current weather information, which requires external web data via web_search"
- alternatives: []

Query: "Help me analyze this data"
Available: doc_search (enabled), web_search (disabled)
Attachments: data.csv
Response:
- selected_tools: ["doc_search"]
- confidence: 0.6
- reasoning: "Query involves data analysis with file attachment, but no specialized data analysis tools are enabled. doc_search may help with document-based analysis but confidence is moderate"
- alternatives: ["Enable data analysis tools for better results"]

Query: "Search for information about AI"
Available: doc_search (disabled), web_search (disabled)
Response:
- selected_tools: []
- selected_agents: []
- confidence: 0.0
- reasoning: "No tools are enabled. Both doc_search and web_search are disabled in user settings"
- alternatives: ["Enable doc_search for internal documents", "Enable web_search for web information"]
"""

# Create OpenAI-compatible model using LiteLLM
# The model will automatically use the OPENAI_BASE_URL and OPENAI_API_KEY we set above
# Use "fast" purpose for quick dispatcher routing decisions
model = OpenAIModel(
    model_name="fast",  # LiteLLM routes to fast model (phi-4)
    provider="openai",
)

# Create dispatcher agent with LiteLLM-backed model
dispatcher_agent = Agent(
    model=model,
    result_type=RoutingDecision,
    system_prompt=DISPATCHER_SYSTEM_PROMPT,
    model_settings={
        "temperature": 0.3,  # Low temperature for consistent routing
        "max_tokens": 1000,
    }
)








