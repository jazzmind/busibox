"""Voice Agent.

Specialized assistant for generating speech audio from text.
"""

from typing import List

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)


VOICE_SYSTEM_PROMPT = """You are a text-to-speech assistant.

Use text_to_speech to generate spoken audio from user-provided text.

Guidelines:
- Confirm the target text before generating when ambiguous
- Keep wording natural and suitable for spoken delivery
- Return the generated audio URL clearly
- Offer to adjust pace or tone when users ask for revisions"""


class VoiceAgent(BaseStreamingAgent):
    """Agent specialized in text-to-speech workflows."""

    def __init__(self):
        config = AgentConfig(
            name="voice-agent",
            display_name="Voice Agent",
            instructions=VOICE_SYSTEM_PROMPT,
            tools=["text_to_speech"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        return []


voice_agent = VoiceAgent()

