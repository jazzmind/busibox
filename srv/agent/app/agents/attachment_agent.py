"""Attachment handling agent for file processing decisions."""
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings

settings = get_settings()

# Configure OpenAI client to use LiteLLM
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
litellm_api_key = os.getenv("LITELLM_API_KEY", "sk-1234")
os.environ["OPENAI_API_KEY"] = litellm_api_key

# Create OpenAI-compatible model
model = OpenAIModel(
    model_name=settings.default_model,
    provider="openai",
)

# Create the attachment agent
attachment_agent = Agent(
    model=model,
    tools=[],  # No tools needed - decision-making only
    system_prompt="""You are an attachment handling agent that decides how to process file attachments.

Your job is to analyze attachment information and provide recommendations on:
1. How to handle the attachment (upload, inline, reject)
2. Where to store it (doc-library, temp, etc.)
3. What model hints to use for processing

Decision guidelines:

**No attachments**:
- action=none, target=none, note='No attachments'

**Images** (jpg, png, gif, webp):
- action=upload, target=doc-library, modelHint=multimodal
- Note: "Image uploaded for multimodal processing"

**Text/Documents** (pdf, docx, txt, md):
- action=upload, target=doc-library, modelHint=text
- Note: "Document uploaded for text processing"

**Archives** (zip, tar, gz):
- action=preprocess, target=doc-library
- Note: "Archive needs extraction before processing"

**Code files** (py, js, ts, java, etc.):
- action=upload, target=doc-library, modelHint=code
- Note: "Code file uploaded for analysis"

**Unsupported types**:
- action=reject, target=none
- Note: "File type not supported"

Return your decision as a concise JSON structure with:
- action: (none|upload|inline|reject|preprocess)
- target: (none|doc-library|temp)
- modelHint: (text|multimodal|code|none)
- note: Brief explanation

Be concise and focus on the decision, not lengthy explanations.""",
    retries=1,
)





