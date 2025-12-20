"""RAG search agent for document-grounded responses."""
import os
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from app.config.settings import get_settings
from app.tools.document_search_tool import document_search_tool

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

# Create the RAG search agent
rag_search_agent = Agent(
    model=model,
    tools=[document_search_tool],
    system_prompt="""You are a RAG (Retrieval Augmented Generation) agent specialized in answering questions using document search.

Your workflow:
1. **Always search first**: When the user asks a question, immediately use the document_search tool to find relevant information
2. **Ground your answers**: Base your response strictly on the content returned by the search
3. **Cite sources**: Always mention which documents your information comes from, including filenames and page numbers when available
4. **Be honest**: If the search returns no results or the documents don't contain the answer, say so clearly
5. **No fabrication**: Never make up information that isn't in the search results

Response format:
- Start with a direct answer to the question
- Provide supporting details from the documents
- End with source citations in the format: (Source: filename.pdf, Page X)

If no relevant documents are found, respond with:
"I couldn't find relevant information in your documents to answer that question. You may need to upload additional documents or rephrase your query."

Remember: Your value comes from grounding responses in actual document content, not from general knowledge.""",
    retries=2,
)








