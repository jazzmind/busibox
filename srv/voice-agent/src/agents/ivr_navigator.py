"""
IVR Navigator Agent.

Uses LLM to parse IVR menu prompts and decide which option to select.
Handles multi-level menus and tracks navigation path.
"""

import asyncio
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import httpx
import structlog

from config.settings import get_settings

logger = structlog.get_logger(__name__)


class IVRState(str, Enum):
    """IVR navigation states."""

    IDLE = "idle"
    LISTENING = "listening"
    ANALYZING = "analyzing"
    SELECTING = "selecting"
    WAITING = "waiting"  # Waiting for next menu
    COMPLETE = "complete"  # Reached destination (e.g., hold queue)
    FAILED = "failed"


@dataclass
class IVRMenuOption:
    """Represents a single IVR menu option."""

    key: str  # DTMF key (0-9, *, #)
    description: str
    is_target: bool = False  # Whether this leads to our goal
    is_repeat: bool = False  # Whether this repeats the menu
    is_back: bool = False  # Whether this goes back
    confidence: float = 0.0


@dataclass
class IVRMenu:
    """Represents a parsed IVR menu."""

    raw_text: str
    options: List[IVRMenuOption]
    greeting: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class NavigationGoal:
    """Defines the goal for IVR navigation."""

    description: str  # e.g., "Speak to a representative about tax filing"
    keywords: List[str] = field(default_factory=list)  # e.g., ["representative", "agent", "operator"]
    avoid_keywords: List[str] = field(default_factory=list)  # e.g., ["automated", "press 1"]
    prefer_human: bool = True


# Default navigation goal for reaching a human
DEFAULT_GOAL = NavigationGoal(
    description="Reach a live customer service representative",
    keywords=[
        "representative",
        "agent",
        "operator",
        "customer service",
        "speak to someone",
        "live person",
        "human",
        "associate",
    ],
    avoid_keywords=[
        "automated",
        "self-service",
        "leave a message",
        "voicemail",
        "callback",
    ],
    prefer_human=True,
)


class IVRNavigator:
    """
    LLM-powered IVR navigation agent.
    
    Parses IVR prompts and decides which options to select
    to reach a human agent.
    """

    def __init__(
        self,
        goal: Optional[NavigationGoal] = None,
        on_dtmf_request: Optional[Callable[[str], None]] = None,
    ):
        settings = get_settings()
        
        self.goal = goal or DEFAULT_GOAL
        self._on_dtmf_request = on_dtmf_request
        
        self._litellm_url = settings.litellm_base_url
        self._litellm_key = settings.litellm_api_key
        self._model = settings.default_model
        
        self._state = IVRState.IDLE
        self._navigation_path: List[str] = []
        self._menus_seen: List[IVRMenu] = []
        self._last_selection: Optional[str] = None
        
        # Track patterns to avoid loops
        self._seen_prompts: List[str] = []

    @property
    def state(self) -> IVRState:
        """Get current navigation state."""
        return self._state

    @property
    def navigation_path(self) -> List[str]:
        """Get the navigation path taken so far."""
        return self._navigation_path.copy()

    async def process_transcript(
        self,
        transcript: str,
    ) -> Optional[str]:
        """
        Process an IVR transcript and decide what to do.
        
        Args:
            transcript: Transcribed IVR audio
            
        Returns:
            DTMF key to press, or None if no action needed
        """
        self._state = IVRState.ANALYZING
        
        # Check if this looks like an IVR menu
        if not self._is_ivr_menu(transcript):
            logger.debug("Transcript doesn't appear to be IVR menu")
            return None
        
        # Check for loop detection
        if self._detect_loop(transcript):
            logger.warning("Detected IVR navigation loop")
            # Try to break out by pressing 0 (common for operator)
            return "0"
        
        self._seen_prompts.append(transcript)
        
        # Parse the menu using LLM
        menu = await self._parse_menu(transcript)
        if not menu or not menu.options:
            logger.warning("Could not parse IVR menu options")
            return None
        
        self._menus_seen.append(menu)
        
        # Select the best option
        selection = await self._select_option(menu)
        
        if selection:
            self._state = IVRState.SELECTING
            self._navigation_path.append(f"{selection.key}: {selection.description}")
            self._last_selection = selection.key
            
            logger.info(
                "IVR navigation selection",
                key=selection.key,
                description=selection.description,
                confidence=selection.confidence,
            )
            
            # Request DTMF if callback provided
            if self._on_dtmf_request:
                self._on_dtmf_request(selection.key)
            
            self._state = IVRState.WAITING
            return selection.key
        
        return None

    def _is_ivr_menu(self, transcript: str) -> bool:
        """Check if transcript appears to be an IVR menu."""
        lower = transcript.lower()
        
        # Look for common IVR patterns
        patterns = [
            r"press \d",
            r"dial \d",
            r"for .+,? press",
            r"option \d",
            r"say .+ or press",
            r"main menu",
            r"following options",
        ]
        
        for pattern in patterns:
            if re.search(pattern, lower):
                return True
        
        return False

    def _detect_loop(self, transcript: str) -> bool:
        """Detect if we're in a navigation loop."""
        if len(self._seen_prompts) < 3:
            return False
        
        # Simple similarity check
        lower = transcript.lower()
        similar_count = 0
        
        for seen in self._seen_prompts[-5:]:
            if self._text_similarity(lower, seen.lower()) > 0.8:
                similar_count += 1
        
        return similar_count >= 2

    def _text_similarity(self, text1: str, text2: str) -> float:
        """Simple text similarity using word overlap."""
        words1 = set(text1.split())
        words2 = set(text2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union)

    async def _parse_menu(self, transcript: str) -> Optional[IVRMenu]:
        """Parse IVR menu options using LLM."""
        prompt = f"""Parse the following IVR menu transcript and extract all options.
For each option, identify:
1. The key to press (number, * or #)
2. What the option does

IVR Transcript:
{transcript}

Respond in this JSON format:
{{
    "greeting": "optional greeting text",
    "options": [
        {{"key": "1", "description": "what pressing 1 does"}},
        {{"key": "2", "description": "what pressing 2 does"}}
    ]
}}

Only include options that are clearly stated. If no clear options, return empty options array."""

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
                        "messages": [
                            {"role": "system", "content": "You parse IVR phone menus and extract options as JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                    },
                )
                
                if response.status_code != 200:
                    logger.error("LLM request failed", status=response.status_code)
                    return None
                
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                
                import json
                parsed = json.loads(content)
                
                options = []
                for opt in parsed.get("options", []):
                    options.append(IVRMenuOption(
                        key=str(opt.get("key", "")),
                        description=opt.get("description", ""),
                    ))
                
                return IVRMenu(
                    raw_text=transcript,
                    options=options,
                    greeting=parsed.get("greeting"),
                )
                
        except Exception as e:
            logger.error("Failed to parse IVR menu", error=str(e))
            return None

    async def _select_option(self, menu: IVRMenu) -> Optional[IVRMenuOption]:
        """Select the best option to reach our goal."""
        if not menu.options:
            return None
        
        # First, try to match goal keywords
        scored_options = []
        
        for option in menu.options:
            score = 0.0
            desc_lower = option.description.lower()
            
            # Check for goal keywords
            for keyword in self.goal.keywords:
                if keyword.lower() in desc_lower:
                    score += 0.3
            
            # Penalize avoid keywords
            for keyword in self.goal.avoid_keywords:
                if keyword.lower() in desc_lower:
                    score -= 0.2
            
            # Common patterns for reaching humans
            human_patterns = [
                ("representative", 0.4),
                ("agent", 0.4),
                ("operator", 0.5),
                ("speak", 0.3),
                ("customer service", 0.3),
                ("other", 0.1),  # "For all other inquiries"
            ]
            
            for pattern, weight in human_patterns:
                if pattern in desc_lower:
                    score += weight
            
            # 0 or operator options often lead to humans
            if option.key == "0":
                score += 0.2
            
            option.confidence = min(1.0, max(0.0, score))
            scored_options.append((option, score))
        
        # Sort by score
        scored_options.sort(key=lambda x: x[1], reverse=True)
        
        # If top option has reasonable confidence, use it
        if scored_options and scored_options[0][1] > 0.2:
            return scored_options[0][0]
        
        # Otherwise, use LLM to decide
        return await self._llm_select_option(menu)

    async def _llm_select_option(self, menu: IVRMenu) -> Optional[IVRMenuOption]:
        """Use LLM to select the best option."""
        options_text = "\n".join(
            f"- Press {opt.key}: {opt.description}"
            for opt in menu.options
        )
        
        prompt = f"""You are navigating an IVR phone system.

Goal: {self.goal.description}

Current menu options:
{options_text}

Navigation path so far: {' -> '.join(self._navigation_path) if self._navigation_path else 'None'}

Which option should I press to get closer to the goal? Respond with ONLY the key to press (a number, * or #).
If none of the options seem relevant, respond with "0" to try to reach an operator."""

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
                        "messages": [
                            {"role": "system", "content": "You help navigate phone IVR systems. Respond with only the key to press."},
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 10,
                    },
                )
                
                if response.status_code != 200:
                    return None
                
                data = response.json()
                key = data["choices"][0]["message"]["content"].strip()
                
                # Find matching option
                for option in menu.options:
                    if option.key == key:
                        option.confidence = 0.7  # LLM selection
                        return option
                
                # If key not found but valid, create option
                if key in "0123456789*#":
                    return IVRMenuOption(
                        key=key,
                        description="LLM selected",
                        confidence=0.5,
                    )
                
        except Exception as e:
            logger.error("LLM option selection failed", error=str(e))
        
        return None

    def mark_human_reached(self) -> None:
        """Mark that we've reached a human agent."""
        self._state = IVRState.COMPLETE
        logger.info(
            "IVR navigation complete - human reached",
            path=self._navigation_path,
        )

    def reset(self) -> None:
        """Reset navigator for new session."""
        self._state = IVRState.IDLE
        self._navigation_path = []
        self._menus_seen = []
        self._last_selection = None
        self._seen_prompts = []
