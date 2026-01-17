"""Template generator agent for creating summary templates from documents."""
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.schemas.template import GeneratedTemplate
from app.tools.ingestion_tool import ingestion_tool

settings = get_settings()

# Configure OpenAI client to use LiteLLM
os.environ["OPENAI_BASE_URL"] = str(settings.litellm_base_url)
litellm_api_key = settings.litellm_api_key or "sk-1234"
os.environ["OPENAI_API_KEY"] = litellm_api_key

# Create OpenAI-compatible model (use fast model)
model = OpenAIModel(
    model_name="fast",
    provider="openai",
)

# Create the template generator agent with structured output
# PydanticAI's output_type enables structured output - the model will return
# data that validates against the GeneratedTemplate schema
template_generator_agent: Agent[None, GeneratedTemplate] = Agent(
    model=model,
    output_type=GeneratedTemplate,
    tools=[ingestion_tool],
    system_prompt="""You are an expert template generation specialist that analyzes summary documents and creates summary templates.

## Your Task
Examine existing summary documents and generate templates that can capture similar information from future documents.

## Process
1. Use the ingest_document tool to process the document and extract its structure
2. Identify all major sections and subsections
3. Analyze the type and format of information in each section
4. Generate a template with 5-15 sections covering all major information categories

## Template Design Guidelines
- **Section names**: Clear and descriptive
- **Descriptions**: Explain what each section should contain
- **Prompts**: Specific, actionable instructions for AI extraction (start with action words like Extract, Identify, List)
- **Data types**: Choose appropriate types (text, number, array, object) based on content
- **Required flag**: Mark sections as required if they contain critical business information

## Quality Standards
- Cover all important information categories from the source document
- Use clear, unambiguous section names
- Create detailed prompts that guide accurate extraction
- Focus on information that supports decision-making

Remember: Templates must be comprehensive, accurate, and designed to extract actionable business intelligence.""",
    retries=2,
)









