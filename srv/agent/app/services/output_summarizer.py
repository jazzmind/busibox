"""
Output Summarizer Service

Provides LLM-powered summarization of task outputs for notifications.
Falls back to truncation if LLM summarization fails.
"""

import logging
from typing import Optional

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIModel

from busibox_common.llm import ensure_openai_env
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# System prompt for summarization
SUMMARIZATION_PROMPT = """You are a concise summarizer for automated task outputs.

Your job is to create a brief, informative summary of the task output that:
1. Highlights the key findings or results
2. Mentions any important numbers, dates, or specific information
3. Notes any errors or warnings if present
4. Stays under the character limit

Format guidelines:
- Use plain text, no markdown formatting
- Be direct and informative
- Focus on what the user needs to know
- If the output describes multiple items, summarize the count and key points

Do NOT include:
- Greetings or sign-offs
- Meta-commentary about the task
- Unnecessary filler words
"""


async def summarize_task_output(
    output: str,
    task_name: str,
    max_length: int = 500,
    model_name: str = "fast",
) -> str:
    """
    Use LLM to summarize task output for notifications.
    
    Falls back to intelligent truncation if LLM call fails.
    
    Args:
        output: The full task output to summarize
        task_name: Name of the task (for context)
        max_length: Maximum length of the summary in characters
        model_name: LLM model to use (default: "fast" for speed)
        
    Returns:
        Summarized output string, guaranteed to be under max_length
    """
    if not output:
        return ""
    
    # If output is already short enough, return as-is
    if len(output) <= max_length:
        return output.strip()
    
    try:
        settings = get_settings()
        
        # Ensure OpenAI environment is configured
        ensure_openai_env(
            base_url=str(settings.litellm_base_url),
            api_key=settings.litellm_api_key,
        )
        
        # Create a simple agent for summarization
        model = OpenAIModel(model_name=model_name, provider="openai")
        agent = Agent(
            model=model,
            system_prompt=SUMMARIZATION_PROMPT,
            model_settings={"max_tokens": 300},  # Keep responses concise
        )
        
        # Build the prompt
        user_prompt = f"""Task: {task_name}

Output to summarize ({len(output)} characters, summarize to under {max_length} characters):

{output[:4000]}"""  # Limit input to avoid token limits
        
        # Run the summarization
        result = await agent.run(user_prompt)
        summary = result.data.strip() if result.data else ""
        
        # Ensure we don't exceed max_length
        if len(summary) > max_length:
            summary = summary[:max_length - 3] + "..."
        
        logger.debug(
            f"Summarized task output",
            extra={
                "task_name": task_name,
                "original_length": len(output),
                "summary_length": len(summary),
            }
        )
        
        return summary
        
    except Exception as e:
        logger.warning(
            f"LLM summarization failed, falling back to truncation: {e}",
            extra={"task_name": task_name, "error": str(e)}
        )
        return _intelligent_truncate(output, max_length)


def _intelligent_truncate(text: str, max_length: int) -> str:
    """
    Intelligently truncate text to max_length.
    
    Tries to:
    1. Break at sentence boundaries
    2. Break at paragraph boundaries
    3. Break at word boundaries
    
    Args:
        text: Text to truncate
        max_length: Maximum length
        
    Returns:
        Truncated text with ellipsis if truncated
    """
    if len(text) <= max_length:
        return text.strip()
    
    # Reserve space for ellipsis
    target_length = max_length - 3
    
    # Try to find a good breaking point
    truncated = text[:target_length]
    
    # Try to break at paragraph boundary
    last_para = truncated.rfind('\n\n')
    if last_para > target_length * 0.5:  # At least 50% of target
        return truncated[:last_para].strip() + "..."
    
    # Try to break at sentence boundary
    for punct in ['. ', '! ', '? ']:
        last_sentence = truncated.rfind(punct)
        if last_sentence > target_length * 0.5:
            return truncated[:last_sentence + 1].strip() + "..."
    
    # Try to break at word boundary
    last_space = truncated.rfind(' ')
    if last_space > target_length * 0.7:
        return truncated[:last_space].strip() + "..."
    
    # Just truncate
    return truncated.strip() + "..."


async def summarize_for_notification(
    output: str,
    task_name: str,
    channel: str = "email",
) -> str:
    """
    Summarize task output specifically for notification channels.
    
    Different channels may have different length requirements:
    - email: 1000 characters
    - teams: 500 characters
    - slack: 500 characters
    - webhook: 2000 characters
    
    Args:
        output: The full task output
        task_name: Name of the task
        channel: Notification channel type
        
    Returns:
        Summarized output appropriate for the channel
    """
    # Channel-specific length limits
    channel_limits = {
        "email": 1000,
        "teams": 500,
        "slack": 500,
        "webhook": 2000,
    }
    
    max_length = channel_limits.get(channel, 500)
    
    return await summarize_task_output(
        output=output,
        task_name=task_name,
        max_length=max_length,
    )
