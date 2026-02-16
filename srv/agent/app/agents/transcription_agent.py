"""Transcription Agent.

Specialized assistant for transcribing spoken audio to text.
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


TRANSCRIPTION_SYSTEM_PROMPT = """You are an audio transcription assistant.

Use transcribe_audio to convert speech recordings into text.
Use document_search when the user asks to compare or cross-reference prior documents.

Guidelines:
- Ask for or confirm an audio file URL when missing
- Preserve important names, numbers, and action items
- Provide concise summaries after transcription when requested
- If language is specified by the user, pass that language hint"""


class TranscriptionAgent(BaseStreamingAgent):
    """Agent specialized in audio-to-text workflows."""

    def __init__(self):
        config = AgentConfig(
            name="transcription-agent",
            display_name="Transcription Agent",
            instructions=TRANSCRIPTION_SYSTEM_PROMPT,
            tools=["transcribe_audio", "document_search"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        return []


transcription_agent = TranscriptionAgent()

