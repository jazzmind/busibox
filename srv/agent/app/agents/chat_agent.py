"""
Chat Agent.

A versatile chat agent with access to multiple tools for comprehensive assistance.
Uses LLM-driven tool selection to proactively help users with various tasks.

This agent extends BaseStreamingAgent with multi-tool access and LLM-driven
tool selection strategy.
"""

import logging
from typing import Any, List

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)

logger = logging.getLogger(__name__)


# Chat agent synthesis prompt
CHAT_SYSTEM_PROMPT = """You are a versatile chat agent with access to multiple tools for comprehensive assistance.

**Available Tools:**
- **web_search**: Search the internet for current information, news, and real-time data
- **get_weather**: Get current weather for any city
- **document_search**: Search through the user's uploaded documents

**Your Workflow:**

1. **Analyze the Query**: Determine which tools (if any) would help answer the question
   - Questions about current events, news, prices → use web_search
   - Questions about weather → use get_weather
   - Questions about user's documents → use document_search
   - General knowledge questions → respond directly

2. **Use Tools Proactively**: Don't wait for explicit requests
   - "What's happening with Tesla stock?" → search the web
   - "Is it going to rain in London?" → get weather
   - "What did my report say about Q3?" → search documents

3. **Synthesize Results**: Combine tool outputs into clear responses
   - Cite sources (URLs for web, filenames for documents)
   - Acknowledge when information is limited
   - Be concise but complete

4. **Handle Errors Gracefully**:
   - If a tool fails, explain and suggest alternatives
   - If no results found, acknowledge and offer to help differently

5. **Response Format**:
   - Start with the direct answer
   - Provide supporting details
   - End with sources when using tools

Be helpful, accurate, and proactive in using your tools to provide the best possible assistance."""


class ChatAgent(BaseStreamingAgent):
    """
    A versatile streaming chat agent that:
    1. Analyzes user queries to determine appropriate tools
    2. Uses LLM-driven tool selection for flexible assistance
    3. Synthesizes results from multiple sources
    
    All steps stream their progress to the user in real-time.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="chat-agent",
            display_name="Chat Agent",
            instructions=CHAT_SYSTEM_PROMPT,
            tools=["web_search", "get_weather", "document_search"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,  # Let LLM decide which tools to use
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        For LLM_DRIVEN strategy, this returns an empty list.
        The LLM will decide which tools to call.
        """
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis from all tool results.
        """
        parts = [f"User Question: {query}\n"]
        
        if not context.tool_results:
            parts.append("No tools were called - provide a general response.")
            return "\n".join(parts)
        
        parts.append("Tool Results:\n")
        
        for tool_name, result in context.tool_results.items():
            parts.append(f"\n--- {tool_name} ---")
            if hasattr(result, 'model_dump'):
                parts.append(str(result.model_dump()))
            else:
                parts.append(str(result))
        
        parts.append("\nPlease synthesize a helpful response based on these results.")
        return "\n".join(parts)
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        """
        if not context.tool_results:
            return "I'm here to help! What would you like to know?"
        
        parts = [f"Here's what I found:\n"]
        for tool_name, result in context.tool_results.items():
            parts.append(f"\n**{tool_name}**: {str(result)[:500]}")
        
        return "\n".join(parts)


# Singleton instance
chat_agent = ChatAgent()
