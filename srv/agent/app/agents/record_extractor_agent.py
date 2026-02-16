"""
Record Extractor Agent.

Dedicated executor for structured extraction runs.
Unlike schema-builder, this agent is optimized to return compact, schema-aligned
JSON records for workflow/programmatic extraction calls.
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


RECORD_EXTRACTOR_SYSTEM_PROMPT = """You extract structured records from provided document text.

Rules:
- Return only data that is supported by document evidence.
- Follow the provided response schema exactly.
- Never add prose or markdown when structured output is requested.
- Prefer concise field values and avoid exhaustive lists.
- If information is missing, omit the field or return null.
"""


class RecordExtractorAgent(BaseStreamingAgent):
    """Deterministic extraction-oriented agent with no tool usage."""

    def __init__(self):
        config = AgentConfig(
            name="record-extractor",
            display_name="Record Extractor",
            instructions=RECORD_EXTRACTOR_SYSTEM_PROMPT,
            tools=[],
            model="agent",
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        return []


# Singleton instance
record_extractor_agent = RecordExtractorAgent()
