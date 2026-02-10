"""
Test Agent.

A minimal agent for validation tests. Uses the smallest available model (Qwen3-0.6B)
and has no tools enabled. Designed for quick, deterministic testing of the LLM chain.

Used by the deploy service's validate_llm_chain endpoint to verify the full pipeline:
Direct LLM -> LiteLLM -> Agent API
"""

import logging
import os
from typing import List

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.config.settings import get_settings

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
    1. Uses the smallest/fastest model available ("test" model = Qwen3-0.6B)
    2. Has no tools enabled
    3. Provides direct, deterministic responses
    
    Used for LLM chain validation testing.
    
    Uses LLM_DRIVEN strategy which directly calls the LLM (synthesis_agent) without
    needing any tools. This ensures we get a direct LLM response.
    """
    
    def __init__(self):
        settings = get_settings()
        # Use an overridable model alias so validation works with restricted LiteLLM keys.
        # Priority: explicit TEST_AGENT_MODEL -> app default model -> "agent".
        test_agent_model = os.getenv("TEST_AGENT_MODEL") or settings.default_model or "agent"

        config = AgentConfig(
            name="test-agent",
            display_name="Test Agent",
            instructions=TEST_SYSTEM_PROMPT,
            tools=[],  # No tools - direct LLM responses only
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,  # LLM_DRIVEN allows direct LLM responses
        )
        super().__init__(config)
        
        # Override synthesis model with a configurable alias rather than hardcoding "test".
        self.synthesis_model = OpenAIChatModel(
            model_name=test_agent_model,
            provider="openai",
        )
        
        # Recreate synthesis agent with test model
        self.synthesis_agent = Agent(
            model=self.synthesis_model,
            system_prompt=TEST_SYSTEM_PROMPT,
            model_settings={"max_tokens": 100},  # Short responses for test
        )
    
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
