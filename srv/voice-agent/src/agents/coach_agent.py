"""
Coach Agent.

Provides real-time text-based coaching and suggestions
to the human user during their conversation.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


@dataclass
class CoachSuggestion:
    """A coaching suggestion."""

    text: str
    category: str  # "info", "question", "warning", "action"
    confidence: float
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CoachContext:
    """Context for the coaching session."""

    call_purpose: Optional[str] = None
    key_topics: List[str] = field(default_factory=list)
    user_goals: List[str] = field(default_factory=list)
    important_info: Dict[str, str] = field(default_factory=dict)


class CoachAgent:
    """
    AI coaching agent for assisting humans during calls.
    
    Provides:
    - Suggested questions to ask
    - Information reminders
    - Warnings about common issues
    - Action suggestions based on conversation
    """

    def __init__(
        self,
        context: Optional[CoachContext] = None,
    ):
        settings = get_settings()
        
        self._context = context or CoachContext()
        
        self._litellm_url = settings.litellm_base_url
        self._litellm_key = settings.litellm_api_key
        self._model = settings.default_model
        
        self._transcript_history: List[Dict] = []
        self._suggestions_given: List[CoachSuggestion] = []
        self._is_active = False

    def start(self) -> None:
        """Start the coaching session."""
        self._is_active = True
        logger.info("Coach agent started")

    def stop(self) -> None:
        """Stop the coaching session."""
        self._is_active = False
        logger.info("Coach agent stopped")

    async def process_transcript(
        self,
        speaker: str,
        text: str,
    ) -> Optional[CoachSuggestion]:
        """
        Process transcript update and optionally generate a suggestion.
        
        Args:
            speaker: Who spoke ("user", "remote", "ai")
            text: What was said
            
        Returns:
            Coaching suggestion if appropriate
        """
        if not self._is_active:
            return None
        
        # Add to history
        self._transcript_history.append({
            "speaker": speaker,
            "text": text,
            "timestamp": datetime.utcnow().isoformat(),
        })
        
        # Only generate suggestions periodically or when triggered
        # To avoid overwhelming the user
        if len(self._transcript_history) % 3 != 0:  # Every 3 turns
            return None
        
        suggestion = await self._generate_suggestion()
        if suggestion:
            self._suggestions_given.append(suggestion)
        
        return suggestion

    async def get_suggestion(
        self,
        user_question: Optional[str] = None,
    ) -> Optional[CoachSuggestion]:
        """
        Get a coaching suggestion on demand.
        
        Args:
            user_question: Optional specific question from user
            
        Returns:
            Coaching suggestion
        """
        if not self._is_active:
            return None
        
        return await self._generate_suggestion(user_question)

    async def _generate_suggestion(
        self,
        user_question: Optional[str] = None,
    ) -> Optional[CoachSuggestion]:
        """Generate a coaching suggestion using LLM."""
        # Build context
        recent_transcript = self._transcript_history[-10:]
        transcript_text = "\n".join(
            f"[{t['speaker'].upper()}]: {t['text']}"
            for t in recent_transcript
        )
        
        system_prompt = """You are a helpful call coaching assistant. 
The user is on a phone call and you provide real-time suggestions via text (NOT voice).
Keep suggestions brief and actionable. Focus on:
1. Important questions they should ask
2. Key information they might need
3. Things to watch out for or clarify
4. Next steps or actions to take

Respond with a single, brief suggestion (1-2 sentences max)."""

        if self._context.call_purpose:
            system_prompt += f"\n\nCall purpose: {self._context.call_purpose}"
        
        if self._context.user_goals:
            system_prompt += f"\n\nUser goals: {', '.join(self._context.user_goals)}"
        
        if self._context.important_info:
            info_lines = [f"- {k}: {v}" for k, v in self._context.important_info.items()]
            system_prompt += f"\n\nImportant information:\n" + "\n".join(info_lines)

        user_prompt = f"""Recent conversation:
{transcript_text}

"""
        if user_question:
            user_prompt += f"User asks: {user_question}\n\n"
        
        user_prompt += "What's a helpful suggestion for the user right now? If nothing important to suggest, respond with just 'none'."

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{self._litellm_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._litellm_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.5,
                        "max_tokens": 100,
                    },
                )
                
                if response.status_code != 200:
                    logger.error("LLM request failed", status=response.status_code)
                    return None
                
                data = response.json()
                content = data["choices"][0]["message"]["content"].strip()
                
                # Check if no suggestion
                if content.lower() in ["none", "no suggestion", ""]:
                    return None
                
                # Categorize the suggestion
                category = self._categorize_suggestion(content)
                
                return CoachSuggestion(
                    text=content,
                    category=category,
                    confidence=0.8,
                )
                
        except Exception as e:
            logger.error("Failed to generate coaching suggestion", error=str(e))
            return None

    def _categorize_suggestion(self, text: str) -> str:
        """Categorize a suggestion based on content."""
        lower = text.lower()
        
        if any(word in lower for word in ["ask", "question", "inquire", "clarify"]):
            return "question"
        elif any(word in lower for word in ["warning", "careful", "watch out", "note that"]):
            return "warning"
        elif any(word in lower for word in ["remember", "don't forget", "important"]):
            return "info"
        elif any(word in lower for word in ["should", "could", "try", "consider"]):
            return "action"
        
        return "info"

    def update_context(
        self,
        call_purpose: Optional[str] = None,
        key_topics: Optional[List[str]] = None,
        user_goals: Optional[List[str]] = None,
        important_info: Optional[Dict[str, str]] = None,
    ) -> None:
        """Update coaching context."""
        if call_purpose is not None:
            self._context.call_purpose = call_purpose
        if key_topics is not None:
            self._context.key_topics = key_topics
        if user_goals is not None:
            self._context.user_goals = user_goals
        if important_info is not None:
            self._context.important_info.update(important_info)

    def get_suggestions_history(self) -> List[CoachSuggestion]:
        """Get all suggestions given in this session."""
        return self._suggestions_given.copy()

    def reset(self) -> None:
        """Reset the coaching session."""
        self._transcript_history = []
        self._suggestions_given = []
        self._is_active = False
