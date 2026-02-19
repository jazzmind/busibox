"""
Context Compression Service.

Implements conversation history compression for long threads.
Uses a hybrid approach:
1. Keep recent messages in full (last N turns)
2. Compress older messages into a summary
3. Cache summaries keyed by conversation content hash

Best practices from research:
- Sliding window: Keep most recent 5-10 turns verbatim
- Periodic summarization: Compress older turns when threshold exceeded
- Tag critical content: Preserve key entities, decisions, constraints
- Validate summaries: Ensure key information is retained
"""

import hashlib
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config.settings import get_settings
from app.schemas.definitions import ContextCompressionConfig

logger = logging.getLogger(__name__)

DEFAULT_COMPRESSION_CONFIG = ContextCompressionConfig()

COMPRESSION_SYSTEM_PROMPT = """You are a conversation summarizer. Compress a conversation history into a concise summary preserving all important information.

Your summary MUST retain:
1. Key facts and information discussed
2. User preferences and constraints mentioned
3. Decisions made and their reasoning
4. Important context that would affect future responses
5. Any specific instructions or requirements

Format as a structured list:
- **Context**: Brief description of what the conversation is about
- **Key Facts**: Important information established
- **User Preferences**: Any preferences or constraints mentioned
- **Decisions Made**: Key decisions or conclusions reached
- **Current State**: Where the conversation left off

Be concise but comprehensive. Do not lose important details."""

# Patterns for assistant content that should be stripped before compression.
# These match fast_ack placeholders, thinking status lines, etc.
_NOISE_PATTERNS = [
    re.compile(r"^(Got it|Sure|OK|Alright|Let me)[.!,]?\s*(I'll|Let me|Checking|Looking|checking|looking).*$", re.IGNORECASE),
    re.compile(r"^(Thinking through|Analyzing|Processing|Searching|Looking up|Checking).*\.\.\.*$", re.IGNORECASE),
    re.compile(r"^Using \*\*.*\*\* to help.*$", re.IGNORECASE),
    re.compile(r"^(Research complete|Synthesizing findings|Done)[.!]*\s*$", re.IGNORECASE),
    re.compile(r"^(Hi|Hello|Hey)[!.]?\s*(Let me|I'll|Checking).*$", re.IGNORECASE),
    re.compile(r"^Thinking through your request.*$", re.IGNORECASE),
    re.compile(r"^Let me (check|look|search|find|get|pull up).*$", re.IGNORECASE),
    re.compile(r"^I'll (check|look|search|find|get|pull up).*$", re.IGNORECASE),
]

# Cache: maps content_hash -> (summary_text, timestamp)
_SUMMARY_CACHE: OrderedDict[str, Tuple[str, float]] = OrderedDict()
_CACHE_MAX_SIZE = 200
_CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class CompressionResult:
    """Result of conversation history compression."""
    summary: Optional[str] = None
    recent_messages: List[Dict[str, str]] = field(default_factory=list)
    was_compressed: bool = False
    original_char_count: int = 0
    compressed_char_count: int = 0
    messages_compressed: int = 0
    messages_kept: int = 0
    cache_hit: bool = False


def _content_hash(messages: List[Dict[str, str]]) -> str:
    """Stable hash of message list for caching."""
    parts = [f"{m.get('role', '')}:{m.get('content', '')}" for m in messages]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:24]


def _is_noise_message(content: str) -> bool:
    """Return True if the message is agent placeholder/thinking noise."""
    stripped = content.strip()
    if not stripped:
        return True
    # Only apply noise detection to short messages (< 200 chars).
    # Long assistant messages always have real content even if they start
    # with an ack phrase.
    if len(stripped) > 200:
        return False
    for pat in _NOISE_PATTERNS:
        if pat.match(stripped):
            return True
    return False


def _is_duplicate_or_echo(content: str, prev_content: Optional[str]) -> bool:
    """Detect messages that merely echo the prior message."""
    if not prev_content:
        return False
    return content.strip() == prev_content.strip()


def _strip_leading_ack(content: str) -> str:
    """
    If an assistant message starts with a fast_ack line followed by real content,
    strip the ack prefix so only substantive content remains.
    """
    lines = content.split("\n", 2)
    if len(lines) >= 2:
        first = lines[0].strip()
        if first and _is_noise_message(first):
            remainder = content[len(lines[0]):].lstrip("\n").strip()
            if remainder:
                return remainder
    return content


def filter_history_for_compression(
    messages: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """
    Remove noise from conversation history before compression.

    Strips:
    - Empty or near-empty messages
    - Agent placeholder / acknowledgement messages (fast_ack style)
    - Thinking / routing status messages
    - Duplicate echo messages
    - Leading ack prefixes from combined two-phase responses
    """
    filtered: List[Dict[str, str]] = []
    prev_content: Optional[str] = None

    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "")

        if not content.strip():
            continue

        if role == "assistant":
            if _is_noise_message(content):
                continue
            content = _strip_leading_ack(content)

        if _is_duplicate_or_echo(content, prev_content):
            continue

        filtered.append({"role": role, "content": content})
        prev_content = content

    return filtered


def _evict_cache() -> None:
    """Evict expired and over-limit entries."""
    now = time.monotonic()
    expired = [k for k, (_, ts) in _SUMMARY_CACHE.items() if now - ts > _CACHE_TTL_SECONDS]
    for k in expired:
        _SUMMARY_CACHE.pop(k, None)
    while len(_SUMMARY_CACHE) > _CACHE_MAX_SIZE:
        _SUMMARY_CACHE.popitem(last=False)


class ContextCompressionService:
    """
    Service for compressing conversation history to fit context windows.

    Uses a hybrid approach:
    1. Always keep the last N message pairs in full
    2. When history exceeds threshold, compress older messages into a summary
    3. Cache summaries so repeated requests for the same history are instant
    """

    def __init__(self, config: Optional[ContextCompressionConfig] = None):
        self.config = config or DEFAULT_COMPRESSION_CONFIG

    def _count_chars(self, messages: List[Dict[str, str]]) -> int:
        return sum(len(msg.get("content", "")) for msg in messages)

    def _split_messages(
        self,
        messages: List[Dict[str, str]],
        keep_recent: int,
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
        if len(messages) <= keep_recent * 2:
            return [], messages
        split_point = len(messages) - (keep_recent * 2)
        return messages[:split_point], messages[split_point:]

    async def compress_history(
        self,
        messages: List[Dict[str, str]],
        config: Optional[ContextCompressionConfig] = None,
    ) -> CompressionResult:
        cfg = config or self.config

        if not cfg.enabled:
            return CompressionResult(
                recent_messages=messages,
                was_compressed=False,
                original_char_count=self._count_chars(messages),
                compressed_char_count=self._count_chars(messages),
                messages_kept=len(messages),
            )

        # --- pre-filter noise before measuring ---
        clean_messages = filter_history_for_compression(messages)
        original_chars = self._count_chars(clean_messages)

        if original_chars <= cfg.compression_threshold_chars:
            return CompressionResult(
                recent_messages=clean_messages,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=original_chars,
                messages_kept=len(clean_messages),
            )

        to_compress, to_keep = self._split_messages(
            clean_messages,
            cfg.recent_messages_to_keep,
        )

        if not to_compress:
            return CompressionResult(
                recent_messages=clean_messages,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=original_chars,
                messages_kept=len(clean_messages),
            )

        # --- check cache ---
        cache_key = _content_hash(to_compress)
        _evict_cache()
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached:
            summary, _ = cached
            _SUMMARY_CACHE.move_to_end(cache_key)
            compressed_chars = len(summary) + self._count_chars(to_keep)
            logger.info(
                "Compression cache hit (%d->%d chars, %d messages compressed)",
                original_chars,
                compressed_chars,
                len(to_compress),
            )
            return CompressionResult(
                summary=summary,
                recent_messages=to_keep,
                was_compressed=True,
                original_char_count=original_chars,
                compressed_char_count=compressed_chars,
                messages_compressed=len(to_compress),
                messages_kept=len(to_keep),
                cache_hit=True,
            )

        logger.info(
            "Compressing %d messages (%d chars), keeping %d recent",
            len(to_compress),
            self._count_chars(to_compress),
            len(to_keep),
        )

        try:
            conversation_text = self._format_for_compression(to_compress)
            summary = await self._call_compression_llm(conversation_text, cfg)

            if len(summary) > cfg.max_summary_chars:
                summary = summary[: cfg.max_summary_chars] + "..."

            _SUMMARY_CACHE[cache_key] = (summary, time.monotonic())

            compressed_chars = len(summary) + self._count_chars(to_keep)
            logger.info(
                "Compression done: %d -> %d chars (%.0f%% reduction)",
                original_chars,
                compressed_chars,
                (1 - compressed_chars / original_chars) * 100 if original_chars else 0,
            )

            return CompressionResult(
                summary=summary,
                recent_messages=to_keep,
                was_compressed=True,
                original_char_count=original_chars,
                compressed_char_count=compressed_chars,
                messages_compressed=len(to_compress),
                messages_kept=len(to_keep),
            )

        except Exception as e:
            logger.error("Compression failed, falling back to truncation: %s", e, exc_info=True)
            return CompressionResult(
                recent_messages=to_keep,
                was_compressed=False,
                original_char_count=original_chars,
                compressed_char_count=self._count_chars(to_keep),
                messages_compressed=0,
                messages_kept=len(to_keep),
            )

    async def _call_compression_llm(self, conversation_text: str, cfg: ContextCompressionConfig) -> str:
        """Direct LiteLLM call instead of PydanticAI Agent for lower overhead."""
        from busibox_common.llm import get_client

        client = get_client()
        t0 = time.monotonic()
        result = await client.chat_completion(
            model=cfg.compression_model or "fast",
            messages=[
                {"role": "system", "content": COMPRESSION_SYSTEM_PROMPT},
                {"role": "user", "content": f"Please summarize this conversation history:\n\n{conversation_text}"},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        elapsed = round((time.monotonic() - t0) * 1000)
        logger.info("Compression LLM call: %dms", elapsed)

        return (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )

    def _format_for_compression(self, messages: List[Dict[str, str]]) -> str:
        parts = []
        for msg in messages:
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
            parts.append(f"{role}: {content}")
        return "\n\n".join(parts)


def build_context_with_history(
    query: str,
    compression_result: CompressionResult,
    system_context: Optional[str] = None,
) -> str:
    """Build a complete context string including compressed history."""
    parts = []

    if compression_result.summary:
        parts.append("## Previous Conversation Summary")
        parts.append(compression_result.summary)
        parts.append("")

    if compression_result.recent_messages:
        parts.append("## Recent Conversation")
        for msg in compression_result.recent_messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role == "user":
                parts.append(f"**User**: {content}")
            elif role == "assistant":
                parts.append(f"**Assistant**: {content}")
            else:
                parts.append(f"**{role}**: {content}")
        parts.append("")

    if system_context:
        parts.append("## Additional Context")
        parts.append(system_context)
        parts.append("")

    parts.append("## Current Query")
    parts.append(query)

    return "\n".join(parts)


_default_service: Optional[ContextCompressionService] = None


def get_compression_service(
    config: Optional[ContextCompressionConfig] = None,
) -> ContextCompressionService:
    """Get the context compression service singleton."""
    global _default_service

    if config is not None:
        return ContextCompressionService(config)

    if _default_service is None:
        _default_service = ContextCompressionService()

    return _default_service
