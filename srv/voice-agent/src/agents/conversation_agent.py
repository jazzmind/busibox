"""
Conversation Agent.

Handles real-time voice conversations with the remote party
using local LLM for responses and TTS for speech synthesis.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class ConversationState(str, Enum):
    """Conversation states."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    PAUSED = "paused"


@dataclass
class ConversationTurn:
    """Represents a single turn in the conversation."""

    speaker: str  # "ai" or "remote"
    text: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    audio_duration_ms: Optional[int] = None


@dataclass
class ConversationContext:
    """Context for the conversation."""

    user_name: Optional[str] = None
    target_name: Optional[str] = None
    goal: Optional[str] = None
    key_information: Dict[str, str] = field(default_factory=dict)
    special_instructions: Optional[str] = None


class ConversationAgent:
    """
    AI agent for voice conversations.
    
    Handles:
    - Turn-based conversation flow
    - Context management
    - Response generation via LLM
    - Conversation history
    """

    def __init__(
        self,
        context: Optional[ConversationContext] = None,
        on_response_ready: Optional[Callable[[str], None]] = None,
    ):
        settings = get_settings()
        
        self._context = context or ConversationContext()
        self._on_response_ready = on_response_ready
        
        self._litellm_url = settings.litellm_base_url
        self._litellm_key = settings.litellm_api_key
        self._model = settings.default_model
        
        self._state = ConversationState.IDLE
        self._history: List[ConversationTurn] = []
        self._is_active = False
        
        # System prompt template
        self._system_prompt = self._build_system_prompt()

    @property
    def state(self) -> ConversationState:
        """Get current conversation state."""
        return self._state

    @property
    def history(self) -> List[ConversationTurn]:
        """Get conversation history."""
        return self._history.copy()

    def _build_system_prompt(self) -> str:
        """Build the system prompt for the conversation."""
        parts = [
            "You are an AI assistant having a phone conversation.",
            "You are polite, professional, and helpful.",
            "Keep your responses concise and natural-sounding for voice.",
            "Avoid using bullet points, lists, or formatting that doesn't work in speech.",
        ]
        
        if self._context.target_name:
            parts.append(f"You are speaking with {self._context.target_name}.")
        
        if self._context.goal:
            parts.append(f"Your goal is: {self._context.goal}")
        
        if self._context.key_information:
            info_lines = [f"- {k}: {v}" for k, v in self._context.key_information.items()]
            parts.append("Key information to use if asked:")
            parts.extend(info_lines)
        
        if self._context.special_instructions:
            parts.append(f"Special instructions: {self._context.special_instructions}")
        
        parts.append("Respond naturally as if speaking on the phone.")
        
        return "\n".join(parts)

    def start(self) -> None:
        """Start the conversation."""
        self._is_active = True
        self._state = ConversationState.LISTENING
        logger.info("Conversation agent started")

    def pause(self) -> None:
        """Pause the conversation (for human takeover)."""
        self._is_active = False
        self._state = ConversationState.PAUSED
        logger.info("Conversation agent paused")

    def resume(self) -> None:
        """Resume the conversation."""
        self._is_active = True
        self._state = ConversationState.LISTENING
        logger.info("Conversation agent resumed")

    def stop(self) -> None:
        """Stop the conversation."""
        self._is_active = False
        self._state = ConversationState.IDLE
        logger.info("Conversation agent stopped")

    async def process_input(
        self,
        transcript: str,
        speaker: str = "remote",
    ) -> Optional[str]:
        """
        Process input from the remote party and generate a response.
        
        Args:
            transcript: Transcribed speech from remote party
            speaker: Speaker identifier
            
        Returns:
            AI response text, or None if not responding
        """
        if not self._is_active:
            return None
        
        # Add to history
        self._history.append(ConversationTurn(
            speaker=speaker,
            text=transcript,
        ))
        
        self._state = ConversationState.THINKING
        
        # Generate response
        response = await self._generate_response(transcript)
        
        if response:
            # Add AI response to history
            self._history.append(ConversationTurn(
                speaker="ai",
                text=response,
            ))
            
            self._state = ConversationState.SPEAKING
            
            # Notify callback
            if self._on_response_ready:
                self._on_response_ready(response)
            
            return response
        
        self._state = ConversationState.LISTENING
        return None

    async def _generate_response(self, input_text: str) -> Optional[str]:
        """Generate a response using the LLM."""
        # Build messages from history
        messages = [{"role": "system", "content": self._system_prompt}]
        
        for turn in self._history[-10:]:  # Last 10 turns for context
            role = "assistant" if turn.speaker == "ai" else "user"
            messages.append({"role": role, "content": turn.text})
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._litellm_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._litellm_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 200,  # Keep responses short for voice
                    },
                )
                
                if response.status_code != 200:
                    logger.error("LLM request failed", status=response.status_code)
                    return None
                
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                # Clean up response for voice
                content = self._clean_for_voice(content)
                
                return content
                
        except Exception as e:
            logger.error("Failed to generate response", error=str(e))
            return None

    def _clean_for_voice(self, text: str) -> str:
        """Clean up text for voice synthesis."""
        import re
        
        # Remove markdown formatting
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # Bold
        text = re.sub(r'\*(.+?)\*', r'\1', text)  # Italic
        text = re.sub(r'`(.+?)`', r'\1', text)  # Code
        
        # Remove bullet points and numbers at start of lines
        text = re.sub(r'^\s*[-•*]\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\s*\d+\.\s*', '', text, flags=re.MULTILINE)
        
        # Remove extra whitespace
        text = ' '.join(text.split())
        
        return text.strip()

    def get_initial_greeting(self) -> str:
        """Get an initial greeting for starting the conversation."""
        if self._context.goal:
            return f"Hello, I'm calling about {self._context.goal}. How can you help me today?"
        return "Hello, how can I help you today?"

    def update_context(
        self,
        user_name: Optional[str] = None,
        target_name: Optional[str] = None,
        goal: Optional[str] = None,
        key_information: Optional[Dict[str, str]] = None,
        special_instructions: Optional[str] = None,
    ) -> None:
        """Update conversation context."""
        if user_name is not None:
            self._context.user_name = user_name
        if target_name is not None:
            self._context.target_name = target_name
        if goal is not None:
            self._context.goal = goal
        if key_information is not None:
            self._context.key_information.update(key_information)
        if special_instructions is not None:
            self._context.special_instructions = special_instructions
        
        # Rebuild system prompt
        self._system_prompt = self._build_system_prompt()

    def add_turn(self, speaker: str, text: str) -> None:
        """Manually add a turn to history (e.g., for user speech)."""
        self._history.append(ConversationTurn(
            speaker=speaker,
            text=text,
        ))

    def reset(self) -> None:
        """Reset the conversation."""
        self._state = ConversationState.IDLE
        self._history = []
        self._is_active = False
