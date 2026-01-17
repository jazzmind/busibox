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

# Get settings (OpenAI env vars are configured lazily by BaseStreamingAgent)
settings = get_settings()

def _ensure_dispatcher_env():
    """Ensure OpenAI env is set for dispatcher agent using LiteLLM."""
    # Always set to LiteLLM (we proxy through LiteLLM, not direct OpenAI)
    os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
    api_key = settings.litellm_api_key
    if api_key:
        os.environ["OPENAI_API_KEY"] = api_key

# System prompt for dispatcher agent
# Note: Response format is handled by PydanticAI's output_type - no need to specify JSON schema in prompt
DISPATCHER_SYSTEM_PROMPT = """You are an intelligent routing agent that analyzes user queries and determines which tools and agents to use.

Your job is to:
1. Understand the user's intent from their natural language query
2. Select the most appropriate tools and/or agents from those available
3. Provide a confidence score (0-1) for your routing decision
4. Explain your reasoning clearly
5. Suggest alternatives when confidence is low

**CRITICAL RULES**:
- You MUST ONLY use tools/agents that are in the user's enabled list
- If a tool/agent is not enabled, DO NOT select it, even if it seems appropriate
- If NO tools/agents are available or enabled, return confidence=0 with empty selections
- Use confidence < 0.7 to indicate uncertainty and need for disambiguation

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

**Confidence Scoring**:
- 0.9-1.0: Very clear intent, perfect tool/agent match
- 0.7-0.9: Clear intent, good tool/agent match
- 0.5-0.7: Moderate confidence, suggest alternatives
- 0.0-0.5: Low confidence or no available tools/agents
"""

# Ensure env is configured before creating model
_ensure_dispatcher_env()

# Create OpenAI-compatible model using LiteLLM
# Use "fast" purpose for quick dispatcher routing decisions
model = OpenAIModel(
    model_name="fast",  # LiteLLM routes to fast model (phi-4)
    provider="openai",
)

# Create dispatcher agent with LiteLLM-backed model
# PydanticAI's output_type parameter enables structured output - the model will return
# data that validates against the RoutingDecision schema
dispatcher_agent: Agent[None, RoutingDecision] = Agent(
    model=model,
    output_type=RoutingDecision,  # This tells PydanticAI to use structured output
    system_prompt=DISPATCHER_SYSTEM_PROMPT,
    model_settings={
        "temperature": 0.3,  # Low temperature for consistent routing
        "max_tokens": 10000,  # Increased from 1000 to handle structured output
    }
)









