"""Attachment handling agent for file processing decisions."""
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.schemas.attachment import AttachmentDecision

settings = get_settings()

# Configure OpenAI client to use LiteLLM
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
litellm_api_key = settings.litellm_api_key or "sk-1234"
os.environ["OPENAI_API_KEY"] = litellm_api_key

# Create OpenAI-compatible model
model = OpenAIModel(
    model_name=settings.default_model,
    provider="openai",
)

# Create the attachment agent with structured output
# PydanticAI's output_type enables structured output - the model will return
# data that validates against the AttachmentDecision schema
attachment_agent: Agent[None, AttachmentDecision] = Agent(
    model=model,
    output_type=AttachmentDecision,
    tools=[],  # No tools needed - decision-making only
    system_prompt="""You are an attachment handling agent that decides how to process file attachments.

Analyze attachment information and decide:
1. How to handle the attachment (upload, inline, reject)
2. Where to store it (doc-library, temp, etc.)
3. What model hints to use for processing

Decision guidelines:

**No attachments**: action=none, target=none
**Images** (jpg, png, gif, webp): action=upload, target=doc-library, model_hint=multimodal
**Text/Documents** (pdf, docx, txt, md): action=upload, target=doc-library, model_hint=text
**Archives** (zip, tar, gz): action=preprocess, target=doc-library, model_hint=none
**Code files** (py, js, ts, java, etc.): action=upload, target=doc-library, model_hint=code
**Unsupported types**: action=reject, target=none, model_hint=none

Provide a brief note explaining your decision.""",
    retries=1,
)









