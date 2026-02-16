"""Image Agent.

Specialized assistant for generating images from text prompts.

Uses a predefined pipeline strategy (not LLM-driven) because:
1. The generate_image tool uses a dedicated image model (e.g. gpt-image-1, FLUX)
   via the LiteLLM /images/generations endpoint - not through chat completions.
2. We don't need the chat LLM to decide *whether* to call the tool - if the user
   is talking to the Image Agent, they want an image generated.
3. The synthesis step formats the result as a markdown image so the UI renders it.
"""

import asyncio
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
from app.agents.streaming_agent import StreamCallback
from app.schemas.streaming import content

logger = logging.getLogger(__name__)


IMAGE_SYSTEM_PROMPT = """You are an image generation assistant.

When the user asks you to create or generate an image, use the generate_image tool.

CRITICAL: When the tool returns successfully with an image_url, you MUST respond with
a markdown image tag so the UI can display it. Use this exact format:

![Generated Image](THE_IMAGE_URL)

Optionally add a brief description below the image.

If the tool fails, explain what went wrong and suggest how to fix the prompt."""


IMAGE_SYNTHESIS_PROMPT = """You are an image generation assistant presenting results.

You will receive tool results from an image generation. Your job is to present them
to the user in a clear way.

CRITICAL RULES:
- If the tool returned an image_url, you MUST include it as a markdown image:
  ![Generated Image](THE_IMAGE_URL)
- If there is a revised_prompt, mention what was changed.
- If the tool failed, explain the error and suggest improvements.
- Keep your text brief - the image is the star."""


class ImageAgent(BaseStreamingAgent):
    """Agent specialized in text-to-image workflows.
    
    Uses PREDEFINED_PIPELINE strategy to directly call generate_image
    without needing the chat LLM to decide on tool usage.
    """

    def __init__(self):
        config = AgentConfig(
            name="image-agent",
            display_name="Image Agent",
            instructions=IMAGE_SYSTEM_PROMPT,
            synthesis_prompt=IMAGE_SYNTHESIS_PROMPT,
            tools=["generate_image"],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.PREDEFINED_PIPELINE,
        )
        super().__init__(config)

    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """Always run the generate_image tool with the user's query as the prompt."""
        return [
            PipelineStep(
                tool="generate_image",
                args={"prompt": query},
            )
        ]

    async def _synthesize(
        self,
        query: str,
        stream: StreamCallback,
        cancel: asyncio.Event,
        context: AgentContext,
    ) -> str:
        """Custom synthesis that directly renders the image if the tool succeeded.
        
        If the image tool returned a URL, emit it as markdown image content
        directly - no need for an LLM synthesis call. If the tool failed,
        fall back to LLM synthesis for a helpful error message.
        """
        if cancel.is_set():
            return ""

        # Check for image result
        image_result = context.tool_results.get("generate_image")
        if image_result:
            # Handle both Pydantic model and dict results
            if hasattr(image_result, 'success'):
                success = image_result.success
                image_url = image_result.image_url
                revised_prompt = image_result.revised_prompt
                error_msg = image_result.error
            elif isinstance(image_result, dict):
                success = image_result.get('success', False)
                image_url = image_result.get('image_url')
                revised_prompt = image_result.get('revised_prompt')
                error_msg = image_result.get('error')
            else:
                success = False
                image_url = None
                revised_prompt = None
                error_msg = "Unexpected result format"

            if success and image_url:
                # Build response with markdown image
                parts = [f"![Generated Image]({image_url})"]
                if revised_prompt and revised_prompt != query:
                    parts.append(f"\n*Prompt refined to: {revised_prompt}*")
                
                response = "\n".join(parts)
                await stream(content(
                    source=self.name,
                    message=response,
                ))
                logger.info(f"Image agent returned image URL: {image_url[:80]}...")
                return response

            elif not success and error_msg:
                response = f"Image generation failed: {error_msg}\n\nTry adjusting your prompt and trying again."
                await stream(content(
                    source=self.name,
                    message=response,
                ))
                return response

        # Fall back to base synthesis for unexpected cases
        return await super()._synthesize(query, stream, cancel, context)


image_agent = ImageAgent()

