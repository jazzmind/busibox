"""
Test Agent.

A minimal agent for validation tests. Uses the smallest available model (Qwen3-0.6B)
and has no tools enabled. Designed for quick, deterministic testing of the LLM chain.

Used by the deploy service's validate_llm_chain endpoint to verify the full pipeline:
Direct LLM -> LiteLLM -> Agent API
"""

import logging
from typing import List

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)

logger = logging.getLogger(__name__)


# Test agent system prompt - focused on direct, deterministic responses
# /no_think disables Qwen3's reasoning mode for cleaner, faster responses
TEST_SYSTEM_PROMPT = """/no_think
You are a test assistant used for validating the LLM pipeline.

**Important Instructions:**
1. Answer questions directly and concisely
2. For math questions, respond with ONLY the number (e.g., "4" not "The answer is 4")
3. Do not use tools - answer from your knowledge only
4. Keep responses short (under 100 tokens)

This agent is used for automated testing. Responses should be predictable and verifiable."""


class TestAgent(BaseStreamingAgent):
    """
    A minimal test agent that:
    1. Uses the smallest/fastest model available
    2. Has no tools enabled
    3. Provides direct, deterministic responses
    
    Used for LLM chain validation testing.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions=TEST_SYSTEM_PROMPT,
            tools=[],  # No tools - direct LLM responses only
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.SEQUENTIAL,  # Doesn't matter since no tools
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        No pipeline steps for test agent - just direct LLM response.
        """
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Minimal context for test agent - just the query.
        """
        return f"User query: {query}\n\nProvide a direct, concise answer."
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Simple fallback for test failures.
        """
        return "I'm a test agent. Please try a simple question like 'What is 2+2?'"


# Singleton instance
test_agent = TestAgent()
