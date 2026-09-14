"""
Chat Agent.

A versatile chat agent with access to multiple tools for comprehensive assistance.
Uses LLM-driven tool selection to proactively help users with various tasks.

This agent extends BaseStreamingAgent with multi-tool access and LLM-driven
tool selection strategy.
"""

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    TOOL_CLASSES,
    TOOL_CLASS_DEFAULT,
    ToolRegistry,
    ToolStrategy,
)
from app.schemas.streaming import clarify_parallel, content, error, interim, plan, progress, prompt, thought
from app.services.routing_guards import (
    DEEP_RESEARCH_OFFER_QUESTION,
    GuardOutcome,
    affirmation_guard,
    cap_plan_steps,
    clarify_loop_guard,
    deep_research_offer_guard,
    document_intent_guard,
    factual_guard,
    research_intent_guard,
)
from pydantic import BaseModel, ValidationError, field_validator

from busibox_common.llm import get_client

import re

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")

_YES_NO_PATTERNS = [
    re.compile(r"\bwould you like me to\b"),
    re.compile(r"\bshall i\b"),
    re.compile(r"\bdo you want me to\b"),
    re.compile(r"\bshould i\b"),
    re.compile(r"\bwould you like to\b(?!\s+\w+\s+or\b)"),
]


def _ends_with_yes_no_question(text: str) -> bool:
    """Return True if *text* ends with a genuinely binary yes/no question.

    Excludes choice questions that contain "or" (e.g., "do you prefer X or Y?")
    since those need open-ended answers, not yes/no buttons.
    """
    stripped = text.rstrip()
    if not stripped.endswith("?"):
        return False
    last_sentence = stripped.rsplit("\n", 1)[-1].lower()
    if " or " in last_sentence:
        return False
    return any(p.search(last_sentence) for p in _YES_NO_PATTERNS)


def _strip_think_tags(text: str) -> tuple:
    """Strip ``<think>`` blocks and return (clean_text, think_content_or_None)."""
    matches = _THINK_RE.findall(text)
    if not matches:
        return text, None
    think_text = "\n".join(m.strip() for m in matches)
    cleaned = _THINK_RE.sub("", text).strip()
    return cleaned, think_text


# Words that mean "the thing I attached". Used to decide whether a file carried
# forward from an earlier turn (see api/chat.py) is the subject of this message.
_ATTACHMENT_REF_RE = re.compile(
    r"\b(?:attach(?:ed|ment|ments)?|upload(?:ed|s)?"
    r"|(?:this|that|the|my) (?:file|files|doc|docs|document|documents|pdf|spreadsheet"
    r"|sheet|image|photo|scan|report|drawing|contract|invoice)"
    r"|it says|what does it say|summari[sz]e it|read it)\b",
    re.IGNORECASE,
)

# Placeholder texts clients send when a message is attachment-only.
_ATTACHMENT_ONLY_PLACEHOLDERS = {
    "attached document", "attached documents", "attachment", "attachments",
    "see attached", "file attached", "attached file", "attached",
}

# Phrases that only exist in the classifier's own scaffolding. A small model
# occasionally answers the prompt instead of the user ("What are the missing
# profile fields you need me to gather?"); such output must be discarded.
_PROMPT_ECHO_MARKERS = (
    "profile field", "needs_tools", "action_type", "follow_up_question",
    "current user message", "return only json",
)


def _mentions_attachment(text: str) -> bool:
    return bool(_ATTACHMENT_REF_RE.search(text or ""))


def _looks_like_prompt_echo(text: Optional[str]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _PROMPT_ECHO_MARKERS)


def _is_attachment_only_message(query: str, attachments: List[Dict[str, Any]]) -> bool:
    """True when the message carries files but no real question."""
    if not attachments:
        return False
    text = (query or "").strip().lower().rstrip(".!:")
    if not text or text in _ATTACHMENT_ONLY_PLACEHOLDERS:
        return True
    names = {str(a.get("filename", "")).strip().lower() for a in attachments}
    return text in names


# Chat agent system prompt - focused on behavior, tools are auto-documented by PydanticAI
CHAT_SYSTEM_PROMPT = """You are a versatile chat assistant that helps users by using available tools when appropriate.

**Response Format:**
- Do NOT include internal reasoning, analysis preamble, or "thinking out loud" in your response.
- Begin your answer directly — no headers like "Thinking Process:", "Let me analyze...", or "I need to consider...".
- If context or explanation is needed, weave it naturally into your answer.
- Never start with a numbered analysis of what you're about to do.

**Key Behaviors:**

1. **Use Conversation Context**: The conversation history is provided with each message. Use it to:
   - Understand follow-up questions (e.g., "tell me more about it" refers to the previous topic)
   - Remember what was discussed earlier
   - Maintain continuity across turns

2. **Use Tools Proactively**: Don't wait for explicit tool requests:
   - Questions about products, current events, news, prices, etc. → search the web
   - Questions about weather → get weather
   - Questions about "my documents" or specific files → search documents
   - Requests for recurring tasks → create task

3. **Handle Ambiguous References**: When the user says "it", "that", "this topic", etc., look at the conversation history to understand what they're referring to.

4. **Cite Sources**: When using tools, include relevant sources (URLs for web, filenames for documents).

5. **Be Conversational**: Respond naturally and reference previous context when relevant.

6. **Handle Failures Gracefully**: If a tool fails or returns no results, explain and offer alternatives.

8. **Mobile-Friendly Responses**: Keep responses concise and easy to read in messaging apps:
   - Prefer short paragraphs and concise bullet lists
   - Avoid long walls of text
   - Start with the most important answer first
"""


class FastAckDecision(BaseModel):
    """Structured response from the fast-ack classifier."""

    action_type: str = "multi_step"
    needs_tools: bool = True
    response: str
    follow_up_question: Optional[str] = None
    confidence: float = 0.75
    routing_source: str = "llm"
    complexity: str = "moderate"  # simple | moderate | complex
    # Tool the planner must include (set by the semantic router's route or
    # a routing guard), e.g. "deep_research". None = planner decides.
    preferred_tool: Optional[str] = None
    # Set when this turn only *offers* deep research: the question the offer
    # is waiting on. Persisted with the turn so "yes" next turn resumes it.
    pending_research: Optional[str] = None


def _coerce_str(value: Any) -> Any:
    """Coerce ints/floats to str for ID-like fields (small planner models emit 1, not "1")."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return value


_DURATION_LABELS = ("quick", "moderate", "long")


def _coerce_duration(value: Any) -> Any:
    """Map numeric or free-text durations onto quick | moderate | long.

    The planner is asked for a label but the 0.8B model frequently returns
    seconds/minutes as a number (e.g. 5). Strict validation rejected the whole
    plan (QA finding #6); coerce instead.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return "quick" if value <= 10 else ("moderate" if value <= 60 else "long")
    if isinstance(value, str):
        v = value.strip().lower()
        if v in _DURATION_LABELS:
            return v
        if any(k in v for k in ("quick", "fast", "short", "sec")):
            return "quick"
        if any(k in v for k in ("long", "slow", "hour")):
            return "long"
        if v:
            return "moderate"
    return value


class PlanStep(BaseModel):
    """A concrete tool step in a generated execution plan."""

    id: str
    tool: str
    objective: str
    run_mode: str = "serial"  # serial | parallel
    args: Dict[str, Any] = {}

    @field_validator("id", "tool", "objective", "run_mode", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        return _coerce_str(v)

    @field_validator("args", mode="before")
    @classmethod
    def _args_dict(cls, v: Any) -> Any:
        return {} if v is None else v


class FeedbackPoint(BaseModel):
    """A user-facing update point during execution."""

    after_step_id: str
    message: str
    kind: str = "interim"  # interim | clarify

    @field_validator("after_step_id", mode="before")
    @classmethod
    def _stringify(cls, v: Any) -> Any:
        return _coerce_str(v)


class ExecutionPlan(BaseModel):
    """Structured plan produced before tool execution.

    Validators are deliberately lenient: the plan is generated by the small
    `fast` model, and a single wrong scalar type used to discard an otherwise
    good multi-step plan in favour of the generic fallback (finding #6).
    """

    summary: str
    steps: List[PlanStep] = []
    parallel_groups: List[List[str]] = []
    feedback_points: List[FeedbackPoint] = []
    estimated_duration: str = "quick"
    # llm = planner model produced it; fallback = deterministic mapping after
    # the planner failed. Read by the escalation guard.
    source: str = "llm"

    @field_validator("summary", mode="before")
    @classmethod
    def _summary_str(cls, v: Any) -> Any:
        return "" if v is None else _coerce_str(v)

    @field_validator("steps", "feedback_points", mode="before")
    @classmethod
    def _lists_not_none(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("parallel_groups", mode="before")
    @classmethod
    def _groups_of_str(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list) and v and not isinstance(v[0], list):
            v = [v]  # model returned a flat list of ids
        return [[_coerce_str(x) for x in (g or [])] for g in v if isinstance(g, list)]

    @field_validator("estimated_duration", mode="before")
    @classmethod
    def _duration_label(cls, v: Any) -> Any:
        return "quick" if v is None else _coerce_duration(v)


class ChatAgent(BaseStreamingAgent):
    """
    A versatile streaming chat agent that:
    1. Analyzes user queries to determine appropriate tools
    2. Uses LLM-driven tool selection for flexible assistance
    3. Synthesizes results from multiple sources
    
    All steps stream their progress to the user in real-time.
    """
    
    def __init__(self):
        config = AgentConfig(
            name="chat-agent",
            display_name="Chat Agent",
            instructions=CHAT_SYSTEM_PROMPT,
            model="chat",
            # Must be set explicitly. `chat` resolves to an Anthropic model on
            # Bedrock, whose API *requires* max_tokens — omitting it does not
            # mean "unlimited", it means LiteLLM supplies a small default and
            # long answers stop mid-sentence. Production, 2026-09-11: a research
            # report was cut off at 11,334 characters with no error anywhere,
            # because nothing in the request or in LiteLLM's config set a value.
            #
            # 32000 rather than the model's true ceiling: it is safe on either
            # arm of a load-balanced `chat` purpose (Sonnet 4.5 caps at 64k,
            # Sonnet 5 at 128k) and matches the limit ChatMessageRequest already
            # enforces on per-request overrides (app/api/chat.py).
            max_tokens=32000,
            tools=[
                "web_search",
                "web_extract",
                "web_map",
                "deep_research",
                "get_weather",
                "document_search",
                "list_data_documents",
                "get_data_document",
                "query_data",
                "create_task",
                "send_notification",
                "generate_image",
                "render_chart",
                "create_spreadsheet",
                "create_document",
                "transcribe_audio",
                "memory_search",
                "memory_save",
            ],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)
    
    def pipeline_steps(self, query: str, context: AgentContext) -> List[PipelineStep]:
        """
        For LLM_DRIVEN strategy, this returns an empty list.
        The LLM will decide which tools to call.
        """
        return []
    
    def _build_synthesis_context(self, query: str, context: AgentContext) -> str:
        """
        Build context for synthesis including conversation history and tool results.
        
        Uses the base class implementation which now includes:
        1. Compressed history summary (if compression was performed)
        2. Recent conversation messages
        3. Tool results
        4. Current query
        """
        # Use base class implementation for full context with history
        base_context = super()._build_synthesis_context(query, context)
        
        # If no tools were called, add a note to respond conversationally
        if not context.tool_results:
            base_context += "\n\nNo tools were called for this query. Provide a helpful, conversational response based on the conversation context and your knowledge."
        
        return base_context
    
    def _build_fallback_response(self, query: str, context: AgentContext) -> str:
        """
        Build fallback response if synthesis fails.
        """
        if not context.tool_results:
            return "I'm here to help! What would you like to know?"
        
        parts = [f"Here's what I found:\n"]
        for tool_name, result in context.tool_results.items():
            parts.append(f"\n**{tool_name}**: {str(result)[:500]}")
        
        return "\n".join(parts)

    def _build_fast_ack_context(self, query: str, context: AgentContext) -> str:
        """Build lightweight context for a fast classification + ack pass."""
        lines: List[str] = []

        # Company terminology so the classifier and planner recognize
        # internal acronyms (a query about "PREC" is a company question,
        # not Canadian real estate).
        try:
            from app.services.org_glossary import glossary_prompt_section
            glossary = glossary_prompt_section()
            if glossary:
                lines.append(glossary)
                lines.append("")
        except Exception:  # noqa: BLE001
            pass

        if context.compressed_history_summary:
            lines.append("Conversation summary:")
            lines.append(context.compressed_history_summary[:800])
            lines.append("")

        if context.recent_messages:
            lines.append("Recent messages:")
            for msg in context.recent_messages[-6:]:
                role = str(msg.get("role", "unknown")).strip()
                message = str(msg.get("content", "")).strip()
                if not message:
                    continue
                lines.append(f"{role}: {message[:300]}")
            lines.append("")

        if context.attachment_metadata:
            lines.append("Attachments:")
            for attachment in context.attachment_metadata:
                filename = attachment.get("filename", "attachment")
                mime_type = attachment.get("mime_type", "unknown")
                note = " — sent earlier in this conversation" if attachment.get("carried_forward") else ""
                lines.append(f"- {filename} ({mime_type}){note}")
            lines.append("")

        # The user message is deliberately the last line: the classifier is a
        # small model and answers whatever comes last. Profile follow-ups and
        # missing profile fields used to trail it here and were echoed back
        # as the reply; they belong to synthesis only (base_agent).
        lines.append(f"Current user message: {query}")
        return "\n".join(lines)

    def _normalize_action_type(self, action_type: str) -> str:
        normalized = (action_type or "").strip().lower().replace("-", "_")
        supported = {"direct", "research", "search", "analysis", "clarify", "multi_step"}
        return normalized if normalized in supported else "multi_step"

    def _plan_tool_aliases(self) -> Dict[str, str]:
        aliases = {
            "doc_search": "document_search",
            "document_search": "document_search",
            "search_documents": "document_search",
            "web_search": "web_search",
            "search_web": "web_search",
            "web_extract": "web_extract",
            "extract": "web_extract",
            "read_page": "web_extract",
            "web_map": "web_map",
            "site_map": "web_map",
            "deep_research": "deep_research",
            "weather": "get_weather",
            "get_weather": "get_weather",
            "task": "create_task",
            "create_task": "create_task",
            "notify": "send_notification",
            "send_notification": "send_notification",
            "image": "generate_image",
            "generate_image": "generate_image",
            "spreadsheet": "create_spreadsheet",
            "excel": "create_spreadsheet",
            "xlsx": "create_spreadsheet",
            "create_spreadsheet": "create_spreadsheet",
            "word_document": "create_document",
            "docx": "create_document",
            "create_document": "create_document",
            "transcription": "transcribe_audio",
            "transcribe_audio": "transcribe_audio",
            "tts": "text_to_speech",
            "text_to_speech": "text_to_speech",
            "list_documents": "list_data_documents",
            "list_data_documents": "list_data_documents",
            "documents_list": "list_data_documents",
            "get_document": "get_data_document",
            "get_data_document": "get_data_document",
            "query_data": "query_data",
        }
        return aliases

    def _resolve_planned_tool(self, raw_tool: str) -> Optional[str]:
        key = (raw_tool or "").strip().lower().replace("-", "_")
        mapped = self._plan_tool_aliases().get(key, key)
        if mapped in self.config.tools and ToolRegistry.has(mapped):
            return mapped
        return None

    def _normalize_planned_step_args(self, tool_name: str, args: Any, query: str) -> Dict[str, Any]:
        """
        Normalize planner args and backfill required fields for tool calls.

        The planner can return partial args (for example only `limit` for
        `document_search`). If the tool requires `query`, inject the user query.
        """
        normalized: Dict[str, Any] = args.copy() if isinstance(args, dict) else {}
        tool_func = ToolRegistry.get(tool_name)
        if not tool_func:
            return normalized
        try:
            query_param = inspect.signature(tool_func).parameters.get("query")
            if (
                query_param
                and query_param.default is inspect.Parameter.empty
                and "query" not in normalized
            ):
                normalized["query"] = query
        except Exception:
            # Keep planner args as-is if signature introspection fails.
            pass

        # Tavily tools: the planner sometimes names the intent but not the
        # required argument. Backfill from the user message where possible.
        if tool_name == "deep_research":
            if not normalized.get("question"):
                normalized["question"] = query
            # The planner never names output_length, so the tool used to fall
            # through to "standard" and a four-minute pass returned a summary.
            # Left unset here so deep_research resolves it from settings.
            if normalized.get("output_length") not in {"short", "standard", "long"}:
                normalized.pop("output_length", None)
        elif tool_name in {"web_extract", "web_map"}:
            urls_in_query = _URL_RE.findall(query or "")
            if tool_name == "web_extract":
                raw_urls = normalized.get("urls")
                if isinstance(raw_urls, str):
                    normalized["urls"] = [raw_urls]
                elif not raw_urls and urls_in_query:
                    normalized["urls"] = urls_in_query
            elif not normalized.get("url") and urls_in_query:
                normalized["url"] = urls_in_query[0]
        return normalized

    def _heuristic_fast_ack(self, query: str) -> FastAckDecision:
        """
        Fallback when fast LLM classification fails.
        Keeps first response varied and context-aware instead of constant text.
        """
        q = query.strip().lower()
        if any(token in q for token in ("hi", "hello", "hey")) and len(q.split()) <= 4:
            return FastAckDecision(
                action_type="direct",
                needs_tools=False,
                response="Hi! How can I help?",
                confidence=0.95,
                routing_source="heuristic_fallback",
            )
        if any(token in q for token in ("calendar", "schedule", "meeting", "today")):
            return FastAckDecision(
                action_type="multi_step",
                needs_tools=True,
                response="Got it - checking your calendar now.",
                confidence=0.85,
                routing_source="heuristic_fallback",
            )
        if any(token in q for token in ("weather", "forecast", "temperature")):
            return FastAckDecision(
                action_type="search",
                needs_tools=True,
                response="Sure - let me pull the latest weather.",
                confidence=0.9,
                routing_source="heuristic_fallback",
            )
        if any(token in q for token in ("document", "file", "notes", "pdf")):
            return FastAckDecision(
                action_type="search",
                needs_tools=True,
                response="Okay - I’ll check your documents.",
                confidence=0.9,
                routing_source="heuristic_fallback",
            )
        if any(token in q for token in ("news", "latest", "current", "search")):
            return FastAckDecision(
                action_type="research",
                needs_tools=True,
                response="On it - I’ll look that up.",
                confidence=0.85,
                routing_source="heuristic_fallback",
            )
        if len(q.split()) <= 2 and "?" not in q:
            return FastAckDecision(
                action_type="clarify",
                needs_tools=False,
                response="Could you share a bit more detail so I can help?",
                follow_up_question="What outcome do you want from this request?",
                confidence=0.55,
                routing_source="heuristic_fallback",
            )
        return FastAckDecision(
            action_type="multi_step",
            needs_tools=True,
            response="Got it. I’m working on that now.",
            confidence=0.7,
            routing_source="heuristic_fallback",
        )

    def _stream_chunks(self, text: str, chunk_size: int = 140) -> List[str]:
        """Split text into stream-friendly chunks by sentence/size."""
        stripped = text.strip()
        if not stripped:
            return []
        if len(stripped) <= chunk_size:
            return [stripped]

        chunks: List[str] = []
        current = ""
        for part in stripped.split(" "):
            next_part = f"{current} {part}".strip()
            if len(next_part) > chunk_size:
                if current:
                    chunks.append(current)
                current = part
            else:
                current = next_part
            if current.endswith((".", "!", "?")) and len(current) >= 60:
                chunks.append(current)
                current = ""
        if current:
            chunks.append(current)
        return chunks

    async def _route_intent(self, query: str, context: AgentContext) -> FastAckDecision:
        """
        Hybrid intent routing: semantic router fast path + fast-ack LLM fallback.

        Modes (settings.semantic_router_mode):
        - disabled (semantic_router_enabled=False): behave exactly as before —
          straight to the fast-ack LLM classifier.
        - shadow: run the router AND the LLM classifier; log both decisions
          for agreement analysis; always use the LLM decision. Zero behavior
          change — used to tune the threshold before going live.
        - live: a router match at/above threshold short-circuits the LLM call
          (~100ms instead of ~300-500ms, deterministic). Below-threshold
          queries fall through to the LLM classifier unchanged.

        The router never sees follow-up rewriting; conversational fragments
        ("what about managers?") naturally score below threshold and fall
        through to the LLM, which has conversation context.
        """
        from app.config.settings import get_settings

        # Messages about uploaded files never take the fast path: attachments
        # are only read in the deep pass, and neither the router nor the
        # small classifier needs to decide that.
        if context.attachment_metadata:
            return self._attachment_decision(query, context)

        router_settings = get_settings()
        if not router_settings.semantic_router_enabled:
            return await self._generate_fast_ack(query, context)

        from app.services.semantic_router import get_semantic_router

        match = None
        try:
            match = await get_semantic_router().route(query)
        except Exception as e:  # noqa: BLE001 — router failure must never break chat
            logger.warning("semantic_router: routing failed, falling back: %s", e)

        if router_settings.semantic_router_mode == "live" and match is not None:
            logger.info(
                "semantic_router: live hit",
                extra={
                    "route": match.route,
                    "score": round(match.score, 4),
                    "elapsed_ms": match.elapsed_ms,
                    "matched_utterance": match.matched_utterance[:80],
                },
            )
            return FastAckDecision(
                action_type=match.action_type,
                needs_tools=match.needs_tools,
                response=match.response,
                confidence=match.score,
                routing_source=f"semantic_router:{match.route}",
                complexity=match.complexity,
                preferred_tool=match.preferred_tool,
            )

        # Shadow mode (or live-mode miss): use the LLM classifier.
        decision = await self._generate_fast_ack(query, context)

        if router_settings.semantic_router_mode == "shadow":
            agrees = (
                match is not None
                and match.action_type == decision.action_type
                and match.needs_tools == decision.needs_tools
            )
            logger.info(
                "semantic_router: shadow comparison",
                extra={
                    "shadow_route": match.route if match else None,
                    "shadow_score": round(match.score, 4) if match else None,
                    "shadow_action_type": match.action_type if match else None,
                    "shadow_needs_tools": match.needs_tools if match else None,
                    "llm_action_type": decision.action_type,
                    "llm_needs_tools": decision.needs_tools,
                    "llm_confidence": decision.confidence,
                    "agrees": agrees if match else None,
                    "query_preview": query[:80],
                },
            )
        return decision

    # The 0.8B classifier decides ambiguity from the query text alone. When it
    # says "clarify" a larger model re-reads the same query and either confirms
    # it (writing a better question) or overturns it. Local by default
    # (tool_calling = Qwen 35B on vLLM), so there is no marginal cost.
    _CLARIFY_REVIEW_PROMPT = (
        "A small classifier flagged this user message as too ambiguous to answer and wants to ask a "
        "clarifying question. Decide whether that is right.\n\n"
        "Return ONLY JSON with keys: ambiguous (boolean), question (string), reason (string).\n"
        "Rules:\n"
        "- ambiguous=false when the message is a well-formed request that could be answered by searching "
        "company documents or the web, even if the answer might not be found. Not knowing the answer is "
        "NOT ambiguity.\n"
        "- ambiguous=true ONLY when the message cannot be acted on at all: no topic ('can you help me'), "
        "or an unresolved reference with nothing in the conversation to resolve it ('what about that one?').\n"
        "- If ambiguous=true, 'question' must be ONE specific question, max 20 words, that would let you "
        "proceed. Never ask the user to restate what they already said.\n"
        "- reason: at most 12 words.\n"
    )

    async def _review_clarify(self, query: str, decision: FastAckDecision,
                              context: Optional[AgentContext] = None) -> FastAckDecision:
        """Second opinion on a clarify decision from a larger model.

        Overturning is the common case: a well-formed question that simply
        might not be answerable is not ambiguous, and should go to retrieval.
        Any failure (model down, bad JSON, timeout) leaves the original
        decision untouched.
        """
        if decision.action_type != "clarify":
            return decision
        try:
            from app.config.settings import get_settings
            settings = get_settings()
            review_model = (settings.clarify_review_model or "").strip()
            timeout = float(settings.clarify_review_timeout_seconds)
        except Exception:  # noqa: BLE001
            review_model, timeout = "tool_calling", 6.0
        if not review_model:
            return decision

        prompt = (
            f"{self._CLARIFY_REVIEW_PROMPT}\n"
            f"Proposed clarifying question: {decision.follow_up_question or '(none)'}\n\n"
            f"{self._build_fast_ack_context(query, context or AgentContext())}"
        )
        t0 = time.monotonic()
        try:
            client = get_client()
            result = await asyncio.wait_for(
                client.chat_completion(
                    model=review_model,
                    messages=[
                        {"role": "system", "content": "You are a strict JSON generator. Return only valid JSON."},
                        {"role": "user", "content": f"/no_think\n{prompt}"},
                    ],
                    temperature=0.0,
                    enable_thinking=False,
                ),
                timeout=timeout,
            )
            raw = (result.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end <= start:
                raise ValueError(f"no JSON object in review response: {raw[:120]!r}")
            parsed = json.loads(raw[start:end + 1])
        except Exception as exc:  # noqa: BLE001 — review must never break routing
            logger.warning(
                "clarify_review: skipped after %dms (%s)",
                round((time.monotonic() - t0) * 1000), exc,
            )
            return decision

        elapsed = round((time.monotonic() - t0) * 1000)
        ambiguous = bool(parsed.get("ambiguous"))
        reason = str(parsed.get("reason", ""))[:120]
        if ambiguous:
            question = str(parsed.get("question", "")).strip()
            if question and not _looks_like_prompt_echo(question):
                decision.follow_up_question = question
            decision.routing_source = f"{decision.routing_source}+clarify_review:kept"
            logger.info(
                "clarify_review: kept clarify",
                extra={"model": review_model, "elapsed_ms": elapsed, "reason": reason},
            )
            return decision

        logger.info(
            "clarify_review: overturned clarify → search",
            extra={"model": review_model, "elapsed_ms": elapsed, "reason": reason},
        )
        decision.action_type = "search"
        decision.needs_tools = True
        decision.follow_up_question = None
        decision.response = self._ACK_RESPONSES[hash(query) % len(self._ACK_RESPONSES)]
        decision.routing_source = f"{decision.routing_source}+clarify_review:overturned"
        return decision

    async def _stream_guard(self, stream, outcome: GuardOutcome) -> None:
        logger.info(
            "Routing guard triggered",
            extra={"guard": outcome.name, "reason": outcome.reason, "rewritten_query": (outcome.query or "")[:80]},
        )
        await stream(thought(
            source=self.name,
            message=f"Guard: {outcome.name} — {outcome.reason}",
            data={"phase": "guard", "guard": outcome.name, "reason": outcome.reason},
        ))

    async def _apply_routing_guards(
        self,
        query: str,
        decision: FastAckDecision,
        history: List[Dict[str, Any]],
        stream,
        context: Optional[AgentContext] = None,
    ) -> FastAckDecision:
        """Deterministic overrides of the classifier (services/routing_guards.py)."""
        # A larger model reviews any clarify decision first: the 0.8B model
        # judges ambiguity from the query text alone and gets it wrong on
        # well-formed questions it simply cannot answer itself.
        if decision.action_type == "clarify" and decision.routing_source.startswith("llm"):
            before = decision.routing_source
            decision = await self._review_clarify(query, decision, context)
            if decision.routing_source != before and decision.action_type != "clarify":
                await self._stream_guard(stream, GuardOutcome(
                    triggered=True,
                    name="clarify_review",
                    reason="a larger model judged the question answerable — searching instead of asking",
                ))

        outcome = clarify_loop_guard(decision.action_type, history)
        # A clarify the review deliberately upheld is left alone; otherwise a
        # company-fact question must not be settled without retrieval.
        review_upheld = decision.routing_source.endswith("clarify_review:kept")
        if not outcome.triggered and not review_upheld:
            try:
                from app.services.org_glossary import mentioned_terms
                terms = mentioned_terms(query)
            except Exception:  # noqa: BLE001
                terms = []
            outcome = factual_guard(
                query, decision.action_type, decision.needs_tools, decision.routing_source, terms
            )
        if not outcome.triggered and not decision.preferred_tool:
            # Explicit "research this / write a report" phrasing that the
            # router did not catch (router off, or below threshold).
            research = research_intent_guard(query)
            if research.triggered:
                outcome = research
                decision.preferred_tool = "deep_research"
                decision.complexity = "complex"
        if not decision.preferred_tool:
            # "make me a spreadsheet" / "as a Word document": the file tools
            # take a typed spec, which only the loop path (full tool schemas)
            # can fill reliably, so lift the turn to the complex tier. This
            # applies even when the factual guard already fired (a request
            # for a holidays spreadsheet is both), so it is checked
            # independently of `outcome`.
            document = document_intent_guard(query)
            if document.triggered:
                decision.complexity = "complex"
                if not outcome.triggered:
                    outcome = document
        if not outcome.triggered:
            return decision
        await self._stream_guard(stream, outcome)
        decision.action_type = outcome.action_type or decision.action_type
        decision.needs_tools = outcome.needs_tools if outcome.needs_tools is not None else decision.needs_tools
        decision.follow_up_question = None
        if decision.needs_tools:
            decision.response = self._ACK_RESPONSES[hash(query) % len(self._ACK_RESPONSES)]
        decision.routing_source = f"{decision.routing_source}+{outcome.name}_guard"
        return decision

    # Deep research runs for minutes, not seconds, so the user is told up
    # front. With DEEP_RESEARCH_CONFIRM (default) the turn stops at an offer
    # and Yes/No buttons; otherwise it announces the wait and runs.
    _RESEARCH_OFFER = (
        "This looks like a research task. I can run a deep, multi-source research "
        "pass and put together a cited report — it usually takes a few minutes. "
        + DEEP_RESEARCH_OFFER_QUESTION
    )
    _RESEARCH_ACK = (
        "This looks like a research task. I'll run a deep, multi-source research "
        "pass and put together a cited report — that usually takes a few minutes. "
        "I'll post it here when it's ready."
    )
    _RESEARCH_CONFIRMED_ACK = (
        "Starting the deep research now — this usually takes a few minutes. "
        "I'll post the cited report here when it's ready."
    )

    async def _confirm_deep_research(
        self,
        decision: FastAckDecision,
        stream,
        query: str = "",
        confirmed: bool = False,
    ) -> FastAckDecision:
        """Gate deep research on availability and, by default, on the user's consent.

        Without a Tavily key (or with the tool disabled) the request is
        downgraded to a normal web search and the ack must not promise a
        multi-minute report. When it is available, the turn either stops at
        an offer the user answers next turn (``confirmed`` is then True via
        ``deep_research_offer_guard``) or, with confirmation disabled, runs
        immediately behind an announcement of the expected wait.
        """
        if decision.preferred_tool != "deep_research":
            return decision
        available = "deep_research" in self.config.tools and ToolRegistry.has("deep_research")
        if available:
            try:
                from app.tools.tavily_tools import _tavily_api_key
                available = bool(await _tavily_api_key())
            except Exception as exc:  # noqa: BLE001
                logger.warning("deep_research availability check failed: %s", exc)
                available = False
        if not available:
            logger.info("deep_research requested but unavailable (no Tavily key); using web search")
            await stream(thought(
                source=self.name,
                message="Deep research isn't configured (no Tavily key) — running a standard web search instead.",
                data={"phase": "escalation", "from": "deep_research", "to": "web_search"},
            ))
            decision.preferred_tool = None
            decision.action_type = "research"
            decision.needs_tools = True
            decision.pending_research = None
            if not decision.response.strip():
                decision.response = self._ACK_RESPONSES[hash(query) % len(self._ACK_RESPONSES)]
            return decision

        from app.config.settings import get_settings
        if getattr(get_settings(), "deep_research_confirm", True) and not confirmed:
            # Offer only. The turn ends here with Yes/No buttons; the
            # question is carried on the decision so the next turn can
            # resume it without re-classifying the word "yes".
            decision.needs_tools = False
            decision.action_type = "direct"
            decision.follow_up_question = None
            decision.pending_research = (query or "").strip() or None
            decision.response = self._RESEARCH_OFFER
            decision.routing_source = f"{decision.routing_source}+research_offer"
            return decision

        decision.needs_tools = True
        decision.action_type = "research"
        decision.complexity = "complex"
        decision.follow_up_question = None
        decision.pending_research = None
        decision.response = self._RESEARCH_CONFIRMED_ACK if confirmed else self._RESEARCH_ACK
        return decision

    @staticmethod
    def _attachments_in_focus(query: str, context: AgentContext) -> List[Dict[str, Any]]:
        """Return the attachments this message is about.

        Files uploaded with the message always count. Files carried forward
        from earlier turns count only when the message refers to them, so
        "what's the weather" after a PDF upload is not treated as a document
        question, while "what's the attached?" resolves to last turn's file.
        """
        current = [a for a in context.attachment_metadata if not a.get("carried_forward")]
        if current:
            return current
        if _mentions_attachment(query):
            return list(context.attachment_metadata)
        return []

    @staticmethod
    def _default_attachment_objective(context: AgentContext) -> str:
        """Objective used when the user sent files without a question."""
        names = [a.get("filename", "attachment") for a in context.attachment_metadata]
        noun = "document" if len(names) == 1 else "documents"
        return (
            f"Summarize the attached {noun} ({', '.join(names)}): what it is, who it is "
            "from or for, key dates, amounts, decisions and any action items."
        )

    def _attachment_decision(self, query: str, context: AgentContext) -> FastAckDecision:
        """Deterministic routing decision for messages about uploaded files."""
        count = len(context.attachment_metadata)
        return FastAckDecision(
            action_type="analysis",
            needs_tools=True,
            response="Let me review that attachment." if count == 1 else "Let me review those attachments.",
            confidence=1.0,
            routing_source="attachment_rule",
            complexity="moderate",
        )

    # Neutral acknowledgments used whenever tools will run. Deterministic
    # per-query (hash-picked) so repeated questions get consistent wording.
    _ACK_RESPONSES = (
        "Let me look into that for you.",
        "Checking the company documents now.",
        "On it — gathering the details.",
        "Sure — looking that up now.",
    )

    async def _generate_fast_ack(self, query: str, context: AgentContext) -> FastAckDecision:
        """
        Generate a fast first response and decide whether we need a deeper tool pass.
        """
        default = self._heuristic_fast_ack(query)
        enabled_tools = [t for t in self.config.tools if ToolRegistry.has(t)]
        has_attachments = bool(context.attachment_metadata)
        from datetime import datetime as _dt, timezone as _tz
        _today = _dt.now(_tz.utc).strftime('%A, %B %d, %Y')
        prompt = (
            f"Today is {_today}. Use this as the current date for any time-relative reasoning.\n"
            "You are deciding how to handle a user message.\n"
            "Return ONLY JSON with keys: action_type, needs_tools, response, follow_up_question, confidence, complexity.\n"
            "Rules:\n"
            "- action_type must be one of: direct, research, search, analysis, clarify, multi_step.\n"
            "- needs_tools=true when external tools or fresh system data are useful "
            "(calendar, docs, web, weather, tasking, notifications, app data).\n"
            "- needs_tools=false for greetings/chitchat/simple acknowledgements where "
            "a direct response is enough.\n"
            "- use action_type=clarify when the request is ambiguous or underspecified.\n"
            "- if action_type=clarify, set needs_tools=false and provide a follow_up_question.\n"
            "- response must be concise (max 1 sentence, max 120 chars).\n"
            "- If needs_tools=true, response should acknowledge and indicate you are working on it.\n"
            "  Good examples: 'Let me look into that for you.', 'Sure, checking now.', 'On it — gathering info.'\n"
            "  BAD examples (NEVER say these): 'I don't have access to tools', 'I can't search documents', 'I'm unable to perform searches'\n"
            "- If needs_tools=false, response should be a complete direct reply.\n"
            "- CRITICAL: You DO have access to tools. NEVER say you lack tools or capabilities.\n"
            f"  Available tools: {', '.join(enabled_tools)}\n"
            "- complexity must be one of: simple, moderate, complex.\n"
            "  - simple: greeting, chitchat, single-fact lookup, yes/no answer\n"
            "  - moderate: summarization, multi-step data retrieval, document analysis\n"
            "  - complex: multi-source research, comparative analysis, detailed reports, creative writing\n\n"
            "Intent guidance (IMPORTANT):\n"
            "- Queries about owned records/documents/candidates/resumes (e.g. 'do I have resumes for data analytics?') MUST set action_type=search and needs_tools=true.\n"
            "- If user asks to find/list/show/filter internal data, do NOT answer directly; use tools.\n"
            "- Prefer false positives (using tools) over false negatives (missing a search).\n\n"
            "Examples (query -> decision):\n"
            "- 'how do I submit for reimbursement' -> action_type=search, needs_tools=true\n"
            "- 'how much vacation do I have left' -> action_type=search, needs_tools=true\n"
            "- 'is tomorrow a company holiday' -> action_type=search, needs_tools=true\n"
            "- 'what about managers?' (after a policy question) -> action_type=search, needs_tools=true\n"
            "- 'what is the difference between a bid bond and a performance bond' -> action_type=direct, needs_tools=false\n"
            "- 'hi' -> action_type=direct, needs_tools=false\n"
            "- 'thanks, that helped' -> action_type=direct, needs_tools=false\n"
            "- 'can you help me with something' -> action_type=clarify, needs_tools=false\n"
            "- 'make me a spreadsheet of the crew hours by week' -> action_type=analysis, needs_tools=true, complexity=complex\n"
            "- 'put that in an excel file' -> action_type=analysis, needs_tools=true, complexity=complex\n"
            "- 'write this up as a word document I can send' -> action_type=analysis, needs_tools=true, complexity=complex\n"
            + (
                "- User has uploaded attachments. If the question is about the attachments, "
                "set needs_tools=true and respond with something like 'Let me review that attachment.' "
                "Do NOT say you can't access the file.\n"
                if has_attachments else ""
            )
            + f"\n{self._build_fast_ack_context(query, context)}"
        )
        try:
            client = get_client()
            logger.info("fast_ack: calling LLM (model=fast)")
            t_llm = time.monotonic()
            result = await client.chat_completion(
                model="fast",
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict JSON generator. Return only valid JSON.",
                    },
                    {"role": "user", "content": f"/no_think\n{prompt}"},
                ],
                temperature=0.1,
                enable_thinking=False,
            )
            logger.info("fast_ack: LLM responded in %dms", round((time.monotonic() - t_llm) * 1000))
            raw = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw and not raw.startswith("{"):
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    raw = raw[start:end + 1]
            parsed = FastAckDecision.model_validate(json.loads(raw))
            if not parsed.response.strip():
                return default
            if _looks_like_prompt_echo(parsed.response) or _looks_like_prompt_echo(parsed.follow_up_question):
                logger.warning(
                    "fast_ack: classifier echoed its own prompt; using heuristic decision | raw=%r",
                    raw[:200],
                )
                return default
            parsed.action_type = self._normalize_action_type(parsed.action_type)
            if parsed.action_type == "clarify":
                parsed.needs_tools = False
                if not parsed.follow_up_question:
                    parsed.follow_up_question = "Could you clarify what you want me to focus on?"
            parsed.routing_source = "llm"
            if parsed.needs_tools:
                # Never let the fast model state a factual answer before tools
                # have run. Its freeform ack sometimes contains a speculative
                # guess ("Yes, tomorrow appears to be a holiday.") that the
                # synthesis then contradicts. Classification stays with the
                # model; the ack wording does not.
                parsed.response = self._ACK_RESPONSES[
                    hash(query) % len(self._ACK_RESPONSES)
                ]
            return parsed
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning(
                "Fast ack generation fallback after %dms: %s | raw=%r",
                round((time.monotonic() - t_llm) * 1000) if 't_llm' in dir() else -1,
                exc,
                (raw[:200] if 'raw' in dir() else None),
            )
            return default

    async def _generate_quick_findings(self, query: str, tool_results: Dict[str, Any]) -> str:
        """
        Create a concise interim "what I found so far" message from tool outputs.
        """
        if not tool_results:
            return ""
        compact: Dict[str, str] = {}
        for name, value in tool_results.items():
            if name == "llm_response":
                continue
            text = value.model_dump_json() if hasattr(value, "model_dump_json") else str(value)
            compact[name] = text[:700]
        if not compact:
            return ""
        prompt = (
            "You are generating a brief progress update for a chat user while additional "
            "tools are still running.\n\n"
            f"User query: {query}\n\n"
            f"Tool results so far: {json.dumps(compact)}\n\n"
            "Rules:\n"
            "1. If the tool results are NOT relevant to the user's query, respond with "
            "EXACTLY: \"Searching for more information...\"\n"
            "2. If the results ARE relevant, write 1-2 short sentences summarizing what "
            "was found so far. Be concrete.\n"
            "3. Do NOT mention tool names or internal systems.\n"
            "4. Do NOT refuse or say you cannot help — just summarize or use the fallback.\n"
        )
        try:
            client = get_client()
            logger.info("quick_findings: calling LLM (model=fast)")
            t_qf = time.monotonic()
            result = await client.chat_completion(
                model="fast",
                messages=[
                    {"role": "system", "content": "You write concise interim progress updates. Never refuse a request. If results aren't relevant, say you're still searching."},
                    {"role": "user", "content": f"/no_think\n{prompt}"},
                ],
                temperature=0.2,
                enable_thinking=False,
            )
            logger.info("quick_findings: LLM responded in %dms", round((time.monotonic() - t_qf) * 1000))
            return (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
        except Exception as exc:
            logger.warning("Quick findings summary skipped after %dms: %s", round((time.monotonic() - t_qf) * 1000) if 't_qf' in dir() else -1, exc)
            return ""

    @staticmethod
    def _build_tool_signatures(tool_names: List[str]) -> str:
        """Build a compact reference of tool signatures for the planner prompt."""
        lines: List[str] = []
        for name in tool_names:
            func = ToolRegistry.get(name)
            if not func:
                continue
            try:
                sig = inspect.signature(func)
                params = []
                for pname, param in sig.parameters.items():
                    if pname == "ctx":
                        continue
                    annotation = param.annotation
                    type_str = getattr(annotation, "__name__", str(annotation)) if annotation != inspect.Parameter.empty else "any"
                    type_str = type_str.replace("typing.", "")
                    if param.default is not inspect.Parameter.empty:
                        params.append(f"{pname}: {type_str} = {param.default!r}")
                    else:
                        params.append(f"{pname}: {type_str}")
                doc = (func.__doc__ or "").strip().split("\n")[0]
                lines.append(f"  {name}({', '.join(params)}) — {doc}")
            except Exception:
                lines.append(f"  {name}(...)")
        return "\n".join(lines)

    async def _generate_plan(
        self,
        query: str,
        context: AgentContext,
        dispatch: FastAckDecision,
    ) -> ExecutionPlan:
        """Generate a lightweight execution plan before tool execution."""
        enabled_tools = [t for t in self.config.tools if ToolRegistry.has(t)]
        fallback_steps: List[PlanStep] = []
        has_attachments = bool(context.attachment_metadata)

        # Deterministic fallback mapping by action type.
        ql = query.lower()
        data_document_list_intent = any(
            phrase in ql for phrase in (
                "list data documents",
                "show data documents",
                "data document list",
                "list my data tables",
                "show my data tables",
            )
        )
        parallel_step_ids: List[str] = []
        attachment_only_query = has_attachments and not any(
            kw in ql for kw in (
                "compare with", "find similar", "search my", "other documents",
                "in my files", "in my docs", "my other",
            )
        )
        if "document_search" in enabled_tools and not attachment_only_query:
            fallback_steps.append(
                PlanStep(
                    id="step_1",
                    tool="document_search",
                    objective="Search user's personal and shared documents for relevant context",
                    args={"query": query},
                )
            )
            parallel_step_ids.append("step_1")

        wants_deep_research = (
            dispatch.preferred_tool == "deep_research" and "deep_research" in enabled_tools
        )
        if wants_deep_research:
            # Tavily Research replaces the plain web search; it runs after the
            # (fast) document search so company context is available too.
            fallback_steps.append(
                PlanStep(
                    id=f"step_{len(fallback_steps) + 1}",
                    tool="deep_research",
                    objective="Run multi-source deep research and produce a cited report",
                    # No model/output_length here: hard-coding "auto" bypassed
                    # tavily_research_default_model, and omitting output_length
                    # lets the tool apply tavily_research_output_length ("long")
                    # rather than silently falling back to "standard".
                    args={"question": query},
                )
            )
        elif (
            dispatch.action_type in {"research", "search"}
            and "web_search" in enabled_tools
        ):
            step_id = f"step_{len(fallback_steps) + 1}"
            fallback_steps.append(
                PlanStep(id=step_id, tool="web_search", objective="Gather external context", args={"query": query})
            )
            parallel_step_ids.append(step_id)

        if data_document_list_intent and "list_data_documents" in enabled_tools:
            fallback_steps.append(
                PlanStep(
                    id=f"step_{len(fallback_steps) + 1}",
                    tool="list_data_documents",
                    objective="List available structured data documents",
                    args={"limit": 50},
                )
            )
        # A question about an uploaded file needs no tool at all: the content
        # is injected into the prompt and the model answers from it. Without
        # this guard the fallback ran an unrelated web_search just to have a
        # step.
        if not fallback_steps and enabled_tools and not attachment_only_query:
            fallback_steps.append(
                PlanStep(id="step_1", tool=enabled_tools[0], objective="Collect supporting context", args={"query": query})
            )

        # Run doc search + web search in parallel when both are present
        parallel_groups = [parallel_step_ids] if len(parallel_step_ids) > 1 else [[]]

        fallback = ExecutionPlan(
            summary=(
                "I'll read the attached file(s) and answer from their content."
                if attachment_only_query and not fallback_steps
                else "I'll gather the most relevant information first, then synthesize the final answer."
            ),
            steps=fallback_steps,
            parallel_groups=parallel_groups,
            feedback_points=[],
            estimated_duration="quick" if len(fallback_steps) <= 1 else "moderate",
        )

        if not enabled_tools:
            return ExecutionPlan(
                summary="No tools are required for this request.",
                steps=[],
                parallel_groups=[],
                feedback_points=[],
                estimated_duration="quick",
            )

        tool_sigs = self._build_tool_signatures(enabled_tools)
        has_audio = has_attachments and any(
            a.get("mime_type", "").startswith("audio/") for a in context.attachment_metadata
        )
        has_image_request = any(
            kw in query.lower() for kw in ("generate image", "create image", "draw", "make a picture", "make an image")
        )

        # When the user uploaded attachments, the content is already resolved
        # and injected into the synthesis prompt. A full document_search is
        # only needed if the query asks about *other* documents beyond the
        # attachment.
        if has_attachments:
            attachment_names = [a.get("filename", "attachment") for a in context.attachment_metadata]
            doc_search_rule = (
                f"- The user uploaded attachments ({', '.join(attachment_names)}). "
                "Their content is already available — you do NOT need `document_search` "
                "to answer questions about the uploaded files.\n"
                "- Only include `document_search` if the query ALSO asks about other "
                "documents in the user's library (e.g. 'compare this with my previous report').\n"
            )
        else:
            doc_search_rule = (
                "- ALWAYS include `document_search` to search the user's personal and shared documents for relevant context.\n"
            )

        prompt = (
            "Plan tool execution for this user request.\n"
            "Return ONLY JSON with keys: summary, steps, parallel_groups, feedback_points, estimated_duration.\n\n"
            "Format:\n"
            "- Each step: {id, tool, objective, run_mode, args}\n"
            "- args must use ONLY the parameter names shown in the tool signatures below.\n"
            "- parallel_groups: list of step-id lists.\n"
            "- feedback_points: list of {after_step_id, message, kind}.\n"
            "- Keep the plan minimal — only include tools that directly serve the query.\n\n"
            f"Available tools and their signatures:\n{tool_sigs}\n\n"
            "STRICT RULES — violating these will cause errors:\n"
            "- Only use parameter names that appear in the tool signatures above.\n"
            "- All required parameters (those without defaults) MUST be provided in args.\n"
            f"- Do NOT include `transcribe_audio` unless the user provided an audio file.{' Audio attachment detected.' if has_audio else ' No audio attachment present.'}\n"
            f"- Do NOT include `generate_image` unless the user explicitly asked for image generation.{' Image generation requested.' if has_image_request else ' No image request detected.'}\n"
            "- Do NOT include `text_to_speech` unless the user asked for voice/audio output.\n"
            "- Do NOT include `create_spreadsheet` or `create_document` unless the user asked for a spreadsheet/Excel file or a Word document/.docx; "
            "when they did, gather the data first (document_search / query_data / web_search) and pass the values in `spec`.\n"
            "- Do NOT include `create_task` unless the user explicitly asked to create a scheduled task.\n"
            "- Do NOT include `send_notification` unless the user explicitly asked to send a notification.\n"
            "- Do NOT include `memory_search` or `memory_save` unless the user asks about previous conversations or preferences.\n"
            + doc_search_rule +
            "- When `web_search` is also needed, run it IN PARALLEL with `document_search` by putting both step IDs in the same parallel_groups entry.\n"
            "- For news or time-sensitive questions pass `topic=\"news\"` and a `time_range` (day/week/month/year) to `web_search`.\n"
            "- Use `web_extract` only when the user gives a URL or asks to read a specific page in full; use `web_map` only to discover pages on a named website.\n"
            "- Use `deep_research` ONLY when the user explicitly asks for a report, deep dive, comprehensive comparison or market/company analysis "
            "(it takes minutes and costs credits); it replaces `web_search` in that plan and runs after `document_search`.\n"
            + (
                "- REQUIRED: the user asked for deep research. Include a `deep_research` step with "
                "question=<the user's request, with any context they gave> and do NOT include `web_search`.\n"
                if wants_deep_research else ""
            )
            + "- Use `list_data_documents`, `get_data_document`, or `query_data` ONLY when the user explicitly asks about structured data tables/records.\n\n"
            f"Dispatch action type: {dispatch.action_type}\n"
            f"User query: {query}\n"
            f"{self._build_fast_ack_context(query, context)}"
        )
        try:
            client = get_client()
            logger.info("plan: calling LLM (model=tool_calling)")
            t_plan = time.monotonic()
            result = await client.chat_completion(
                model="tool_calling",
                messages=[
                    {"role": "system", "content": "You are a strict JSON planner. Return valid JSON only."},
                    {"role": "user", "content": f"/no_think\n{prompt}"},
                ],
                temperature=0.1,
                enable_thinking=False,
            )
            logger.info("plan: LLM responded in %dms", round((time.monotonic() - t_plan) * 1000))
            raw = result.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            if raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
            if raw and not raw.startswith("{"):
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    raw = raw[start:end + 1]
            planned = ExecutionPlan.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning("Plan generation fallback: %s", exc)
            planned = fallback
            plan_source = "fallback"
        else:
            plan_source = "llm"

        seen_steps: List[PlanStep] = []
        used_ids: Set[str] = set()
        for idx, step in enumerate(planned.steps, start=1):
            tool = self._resolve_planned_tool(step.tool)
            if not tool:
                continue
            step_id = step.id.strip() if step.id else f"step_{idx}"
            if step_id in used_ids:
                step_id = f"{step_id}_{idx}"
            used_ids.add(step_id)
            args = self._normalize_planned_step_args(tool, step.args, query)
            if not args:
                args = {"query": query}
            seen_steps.append(
                PlanStep(
                    id=step_id,
                    tool=tool,
                    objective=step.objective or f"Run {tool}",
                    run_mode=step.run_mode if step.run_mode in {"serial", "parallel"} else "serial",
                    args=args,
                )
            )

        if not seen_steps:
            seen_steps = fallback.steps

        if wants_deep_research:
            # The router/guard decided this turn is deep research; the small
            # planner must not quietly drop it or double up with web_search.
            seen_steps = [step for step in seen_steps if step.tool != "web_search"]
            if not any(step.tool == "deep_research" for step in seen_steps):
                seen_steps.append(
                    PlanStep(
                        id=f"step_{len(seen_steps) + 1}",
                        tool="deep_research",
                        objective="Run multi-source deep research and produce a cited report",
                        # No model/output_length: let the tool apply
                        # tavily_research_default_model and
                        # tavily_research_output_length from settings.
                        args={"question": query},
                    )
                )

        valid_step_ids = {step.id for step in seen_steps}
        normalized_groups: List[List[str]] = []
        for group in planned.parallel_groups:
            if not isinstance(group, list):
                continue
            valid_group = [step_id for step_id in group if step_id in valid_step_ids]
            if valid_group:
                normalized_groups.append(valid_group)

        if not normalized_groups:
            normalized_groups = []
            parallel_ids = [step.id for step in seen_steps if step.run_mode == "parallel"]
            if parallel_ids:
                normalized_groups.append(parallel_ids)

        feedback_points = [
            fp for fp in planned.feedback_points
            if fp.after_step_id in valid_step_ids and fp.kind in {"interim", "clarify"}
        ]

        return ExecutionPlan(
            summary=planned.summary or fallback.summary,
            steps=seen_steps,
            parallel_groups=normalized_groups,
            feedback_points=feedback_points,
            estimated_duration=planned.estimated_duration or fallback.estimated_duration,
            source=plan_source,
        )

    def _format_plan_summary(self, execution_plan: ExecutionPlan) -> str:
        if not execution_plan.steps:
            # An empty step list means "no tools" only for a planner plan. A
            # loop-first or orchestrator turn is about to use plenty; saying
            # "I'll respond directly" right before a five-minute research
            # pass would be persisted in the thoughts as a lie.
            if execution_plan.source in ("loop_first", "orchestrator"):
                return execution_plan.summary or "Choosing tools as I go."
            return "No tools needed. I'll respond directly."
        bullets = [f"{idx}. {step.objective} (`{step.tool}`)" for idx, step in enumerate(execution_plan.steps, start=1)]
        return (
            f"{execution_plan.summary}\n\n"
            f"Estimated duration: {execution_plan.estimated_duration}\n"
            "Planned steps:\n- " + "\n- ".join(bullets)
        )

    # Tools whose failures are usually transient (search-api 500, provider
    # timeout). Retried once before the answer is written without them.
    _RETRYABLE_TOOLS = {"document_search", "web_search", "query_data"}
    _RETRY_DELAY_SECONDS = 1.0

    @staticmethod
    def _step_failed(agent_context: AgentContext, tool: str) -> Optional[str]:
        """Return a short failure reason if the tool produced no usable result."""
        result = agent_context.tool_results.get(tool)
        if result is None:
            return "no result"
        error = getattr(result, "error", None)
        found = getattr(result, "found", None)
        has_items = bool(getattr(result, "results", None))
        if error and not has_items and found is not True:
            return str(error)[:120]
        return None

    async def _retry_failed_search_steps(
        self, steps: List[PlanStep], stream, cancel, agent_context: AgentContext
    ) -> None:
        for step in steps:
            if cancel.is_set():
                return
            if step.tool not in self._RETRYABLE_TOOLS:
                continue
            reason = self._step_failed(agent_context, step.tool)
            if not reason:
                continue
            logger.warning("Tool %s failed (%s); retrying once", step.tool, reason)
            await stream(thought(
                source=self.name,
                message=f"{step.tool} failed ({reason}); retrying once.",
                data={"phase": "retry", "tool": step.tool, "reason": reason},
            ))
            await asyncio.sleep(self._RETRY_DELAY_SECONDS)
            await self._execute_step(PipelineStep(tool=step.tool, args=step.args, step_id=step.id), stream, cancel, agent_context)

    def _use_research_orchestrator(self, decision: FastAckDecision) -> bool:
        """A consented deep-research turn, with the orchestrator switched on.

        ``_confirm_deep_research`` leaves ``preferred_tool == "deep_research"``
        and sets ``needs_tools`` only once the user has said yes (or
        confirmation is disabled), so this is exactly the set of turns that
        used to become a one-step ``deep_research`` plan.
        """
        if decision.preferred_tool != "deep_research" or not decision.needs_tools:
            return False
        try:
            from app.config.settings import get_settings as _gs
            return bool(_gs().research_orchestrator_enabled)
        except Exception:  # noqa: BLE001
            # Fail closed: the orchestrator fans out several model loops and a
            # paid Tavily pass. If settings cannot be read, the one-step plan
            # is the safe default — never the expensive path.
            return False

    def _loop_first_tier(
        self, decision: FastAckDecision, agent_context: AgentContext
    ) -> Optional[str]:
        """The loop-first tier for this turn, or None to use the planner.

        "research" wins over the complexity tier when the fast-ack classified
        the turn as research, so a `research` entry in ``chat_loop_first_tiers``
        catches research turns regardless of how complex they were judged.

        Excluded regardless of tier:
        - a consented ``deep_research`` turn (owned by the orchestrator);
        - structured-output runs (``response_schema``), which never use tools;
        - agents whose strategy is not LLM_DRIVEN — the loop is theirs to run.
        """
        if self.config.tool_strategy != ToolStrategy.LLM_DRIVEN:
            return None
        if agent_context.response_schema is not None:
            return None
        if decision.preferred_tool == "deep_research":
            return None
        try:
            from app.config.settings import get_settings as _gs
            tiers = set(_gs().chat_loop_first_tiers or [])
        except Exception:  # noqa: BLE001
            tiers = {"complex", "research"}
        if not tiers:
            return None
        tier = "research" if decision.action_type == "research" else (decision.complexity or "")
        return tier if tier in tiers else None

    def _turn_budget_exhausted(self, agent_context: AgentContext) -> bool:
        if not agent_context.turn_started:
            return False
        try:
            from app.config.settings import get_settings as _gs
            budget = _gs().chat_turn_budget_seconds
        except Exception:  # noqa: BLE001
            budget = 120
        return (time.monotonic() - agent_context.turn_started) > budget

    async def _execute_plan(
        self,
        query: str,
        stream,
        cancel,
        agent_context: AgentContext,
        execution_plan: ExecutionPlan,
    ) -> None:
        if not execution_plan.steps:
            return

        fast_steps = [
            s for s in execution_plan.steps
            if TOOL_CLASSES.get(s.tool, TOOL_CLASS_DEFAULT).get("class") == "fast"
        ]
        slow_steps = [
            s for s in execution_plan.steps
            if TOOL_CLASSES.get(s.tool, TOOL_CLASS_DEFAULT).get("class") != "fast"
        ]
        total = len(execution_plan.steps)
        completed: Set[str] = set()

        # Phase 1: run all fast tools in parallel
        if fast_steps:
            logger.info(
                "Plan execution: running %d fast steps first (%s)",
                len(fast_steps),
                [s.tool for s in fast_steps],
            )
            fast_tasks = [
                self._execute_step(
                    PipelineStep(tool=s.tool, args=s.args, step_id=s.id), stream, cancel, agent_context
                )
                for s in fast_steps
            ]
            await asyncio.gather(*fast_tasks, return_exceptions=True)

            await self._retry_failed_search_steps(fast_steps, stream, cancel, agent_context)

            for step in fast_steps:
                completed.add(step.id)
                await stream(progress(
                    source=self.name,
                    message=f"Completed {len(completed)}/{total}: {step.objective}",
                    data={
                        "completed": len(completed),
                        "total": total,
                        "step_id": step.id,
                        "tool": step.tool,
                    },
                ))
                await self._emit_feedback_points(step, execution_plan, agent_context, stream)

            # Emit interim summary from fast results before slow tools run
            if slow_steps and agent_context.tool_results:
                try:
                    interim_msg = await self._generate_quick_findings(
                        query, agent_context.tool_results
                    )
                    if interim_msg:
                        await stream(content(
                            source=self.name,
                            message=interim_msg,
                            data={"phase": "interim"},
                        ))
                except Exception as e:
                    logger.warning("Failed to generate interim summary: %s", e)

        if cancel.is_set():
            return

        # Phase 2: run slow tools using parallel_groups ordering
        if slow_steps:
            logger.info(
                "Plan execution: running %d slow steps (%s)",
                len(slow_steps),
                [s.tool for s in slow_steps],
            )
            step_by_id = {step.id: step for step in slow_steps}
            group_map: Dict[str, int] = {}
            for group_idx, group in enumerate(execution_plan.parallel_groups):
                for step_id in group:
                    if step_id in step_by_id:
                        group_map[step_id] = group_idx

            while len(completed) < total:
                if cancel.is_set():
                    return

                pending = [step for step in slow_steps if step.id not in completed]
                if not pending:
                    break

                next_step = pending[0]
                group_idx = group_map.get(next_step.id)
                if group_idx is not None:
                    group_ids = [
                        sid for sid in execution_plan.parallel_groups[group_idx]
                        if sid not in completed and sid in step_by_id
                    ]
                    runnable = [step_by_id[sid] for sid in group_ids]
                else:
                    runnable = [next_step]

                # Turn time budget: stop starting new slow steps once the
                # turn has run long, except deep_research, which the user
                # asked for explicitly and which is slow by design.
                if self._turn_budget_exhausted(agent_context):
                    skipped = [s for s in runnable if s.tool != "deep_research"]
                    runnable = [s for s in runnable if s.tool == "deep_research"]
                    for step in skipped:
                        completed.add(step.id)
                        logger.warning("Skipping %s: turn time budget exhausted", step.tool)
                        await stream(thought(
                            source=self.name,
                            message=f"Skipping {step.tool}: this turn has used its time budget.",
                            data={"phase": "budget", "skipped_tool": step.tool},
                        ))
                    if not runnable:
                        continue

                if any(s.tool == "deep_research" for s in runnable):
                    await stream(content(
                        source=self.name,
                        message=(
                            "Deep research is running — searching and reading sources, then "
                            "drafting a cited report. This usually takes a few minutes."
                        ),
                        data={"phase": "interim", "tool": "deep_research"},
                    ))
                    await stream(progress(
                        source=self.name,
                        message="Deep research in progress",
                        data={"phase": "deep_research", "expected_minutes": "1-4"},
                    ))

                tasks = [
                    self._execute_step(
                        PipelineStep(tool=s.tool, args=s.args, step_id=s.id), stream, cancel, agent_context
                    )
                    for s in runnable
                ]
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._retry_failed_search_steps(runnable, stream, cancel, agent_context)

                for step in runnable:
                    completed.add(step.id)
                    await stream(progress(
                        source=self.name,
                        message=f"Completed {len(completed)}/{total}: {step.objective}",
                        data={
                            "completed": len(completed),
                            "total": total,
                            "step_id": step.id,
                            "tool": step.tool,
                        },
                    ))
                    await self._emit_feedback_points(step, execution_plan, agent_context, stream)

    async def _emit_feedback_points(self, step, execution_plan, agent_context, stream):
        """Emit any feedback points that fire after a given step."""
        for fp in execution_plan.feedback_points:
            if fp.after_step_id == step.id:
                bridge_channels = agent_context.metadata.get("bridge_channels")
                await stream(interim(
                    source=self.name,
                    message=fp.message,
                    data={
                        "kind": fp.kind,
                        "after_step_id": step.id,
                        "bridge_channels": bridge_channels if isinstance(bridge_channels, list) else [],
                    },
                ))

    async def run_with_streaming(
        self,
        query: str,
        stream,
        cancel,
        context: Optional[dict] = None,
    ) -> str:
        """
        Two-phase chat flow:
        1) Fast first response (ack or direct conversational reply)
        2) Optional deeper tool-enabled response
        """
        t0 = time.monotonic()
        logger.info("Chat run_with_streaming started, query=%s...", query[:60])
        agent_context = await self._setup_context(context, stream, query)
        logger.info("Chat context setup: %dms", round((time.monotonic() - t0) * 1000))
        if agent_context is None:
            return "Authentication or session error. Please sign in and try again."
        if cancel.is_set():
            return ""

        # Keep only the attachments this message is about, and give a file
        # sent without a question a concrete objective so planning and
        # synthesis have something to work from.
        agent_context.attachment_metadata = self._attachments_in_focus(query, agent_context)
        if _is_attachment_only_message(query, agent_context.attachment_metadata):
            query = self._default_attachment_objective(agent_context)
            logger.info("Chat attachment-only message; using default objective: %s", query[:80])
        agent_context.current_query = query
        agent_context.turn_started = t0

        # Keep the purpose->model mapping current. TTL-guarded, so this is a
        # dict check on almost every turn; it exists so a re-point from the
        # admin UI takes effect without a restart, and so a LiteLLM outage at
        # boot self-heals instead of pinning the agent to fallbacks forever.
        try:
            from app.services import model_capabilities
            await model_capabilities.refresh()
        except Exception as exc:  # noqa: BLE001
            logger.debug("model capability refresh skipped: %s", exc)

        # "yes" / "no" after an offer is resolved before routing: the offer
        # becomes the query, or the turn closes politely — never a fresh
        # classification of the word "yes".
        # "yes" / "no" after an offer is resolved before routing: the offer
        # becomes the query, or the turn closes politely — never a fresh
        # classification of the word "yes".
        history = agent_context.recent_messages or agent_context.conversation_history
        # The deep-research offer is checked first: its closing question also
        # matches the generic affirmation guard, which would otherwise turn
        # "yes" into a search for the offer sentence itself.
        research_answer = deep_research_offer_guard(query, history)
        affirmation = research_answer if research_answer.triggered else affirmation_guard(
            query, history, _ends_with_yes_no_question
        )
        if affirmation.triggered:
            await self._stream_guard(stream, affirmation)
            if affirmation.direct_reply:
                await stream(content(source=self.name, message=affirmation.direct_reply, data={"phase": "direct"}))
                return affirmation.direct_reply
            query = affirmation.query or query
            agent_context.current_query = query

        t_ack = time.monotonic()
        research_confirmed = research_answer.triggered
        if research_confirmed:
            decision = FastAckDecision(
                action_type="research",
                needs_tools=True,
                response="",
                confidence=1.0,
                routing_source="deep_research_offer_guard",
                complexity="complex",
                preferred_tool="deep_research",
            )
        elif affirmation.triggered:
            decision = FastAckDecision(
                action_type="search",
                needs_tools=True,
                response=self._ACK_RESPONSES[hash(query) % len(self._ACK_RESPONSES)],
                confidence=1.0,
                routing_source="affirmation_guard",
                complexity="moderate",
            )
        else:
            decision = await self._route_intent(query, agent_context)
            decision = await self._apply_routing_guards(query, decision, history, stream, agent_context)
        decision = await self._confirm_deep_research(
            decision, stream, query=query, confirmed=research_confirmed
        )
        logger.info(
            "Chat fast_ack decision",
            extra={
                "elapsed_ms": round((time.monotonic() - t_ack) * 1000),
                "needs_tools": decision.needs_tools,
                "response_preview": decision.response[:60],
                "action_type": decision.action_type,
                "confidence": decision.confidence,
                "routing_source": decision.routing_source,
            }
        )
        await stream(thought(
            source=self.name,
            message=(
                f"Intent routing: {decision.action_type} "
                f"(tools={'yes' if decision.needs_tools else 'no'}, "
                f"confidence={decision.confidence:.2f}, source={decision.routing_source})"
            ),
            data={
                "phase": "intent_routing",
                "action_type": decision.action_type,
                "needs_tools": decision.needs_tools,
                "confidence": decision.confidence,
                "routing_source": decision.routing_source,
                "follow_up_question": decision.follow_up_question,
                "preferred_tool": decision.preferred_tool,
                "pending_research": decision.pending_research,
            },
        ))
        fast_response = decision.response.strip()
        fast_response, fast_think = _strip_think_tags(fast_response)
        if fast_think:
            await stream(thought(
                source=self.name,
                message=fast_think,
                data={"phase": "model_reasoning"},
            ))
        if fast_response:
            await stream(content(
                source=self.name,
                message=fast_response,
                data={"phase": "fast_ack", "partial": False},
            ))

        if cancel.is_set():
            return ""

        # If dispatch asks a clarifying question, check if we can do useful
        # background work in parallel (e.g. searching docs while waiting for
        # the user's answer).
        if decision.action_type == "clarify":
            enabled_tools = [t for t in self.config.tools if ToolRegistry.has(t)]
            can_search_parallel = any(
                t in enabled_tools for t in ("document_search", "web_search", "query_data")
            )

            if can_search_parallel and decision.confidence < 0.8:
                # Run clarification AND background search in parallel
                question = decision.follow_up_question or "Could you provide more detail?"
                if question not in fast_response:
                    await stream(clarify_parallel(
                        source=self.name,
                        message=question,
                        data={
                            "options": ["Yes", "No"] if _ends_with_yes_no_question(question) else None,
                            "background_status": "Searching for relevant context while you decide...",
                        },
                    ))

                # Start background execution in a separate task
                bg_cancel = asyncio.Event()
                bg_context = AgentContext(
                    deps=agent_context.deps,
                    principal=agent_context.principal,
                    session=agent_context.session,
                    metadata=agent_context.metadata,
                    conversation_history=agent_context.conversation_history,
                    relevant_insights=agent_context.relevant_insights,
                    compressed_history_summary=agent_context.compressed_history_summary,
                    recent_messages=agent_context.recent_messages,
                    attachment_metadata=agent_context.attachment_metadata,
                )

                async def bg_search():
                    try:
                        for tool_name in ("document_search", "web_search"):
                            if bg_cancel.is_set():
                                break
                            tool_func = ToolRegistry.get(tool_name)
                            if tool_func:
                                await stream(thought(
                                    source=self.name,
                                    message=f"Searching in background: {tool_name}",
                                    data={"phase": "background_search"},
                                ))
                    except Exception as exc:
                        logger.debug("Background search during clarify skipped: %s", exc)

                bg_task = asyncio.create_task(bg_search())

                # Emit the prompt so the frontend shows quick-reply buttons
                if _ends_with_yes_no_question(question):
                    await stream(prompt(
                        source=self.name,
                        message=question,
                        data={"prompt_type": "confirm", "options": ["Yes", "No"]},
                    ))
                else:
                    await stream(prompt(
                        source=self.name,
                        message=question,
                        data={"prompt_type": "open", "options": []},
                    ))

                bg_cancel.set()
                await bg_task
                return f"{fast_response}\n\n{question}".strip()

            # Simple clarify (no parallel work useful)
            if decision.follow_up_question and decision.follow_up_question not in fast_response:
                await stream(content(
                    source=self.name,
                    message=decision.follow_up_question,
                    data={"phase": "clarify", "partial": False},
                ))
                if _ends_with_yes_no_question(decision.follow_up_question):
                    await stream(prompt(
                        source=self.name,
                        message=decision.follow_up_question,
                        data={"prompt_type": "confirm", "options": ["Yes", "No"]},
                    ))
                return f"{fast_response}\n\n{decision.follow_up_question}".strip()
            return fast_response

        # For simple conversational messages, the fast response is final.
        if not decision.needs_tools:
            if _ends_with_yes_no_question(fast_response):
                await stream(prompt(
                    source=self.name,
                    message=fast_response,
                    data={"prompt_type": "confirm", "options": ["Yes", "No"]},
                ))
            return fast_response

        # Complexity-based model routing: upgrade to frontier model for
        # complex requests when the agent config allows it.
        original_model = self.synthesis_model
        if (
            decision.complexity == "complex"
            and self.config.allow_frontier_fallback
        ):
            from app.config.settings import get_settings
            from pydantic_ai.models.openai import OpenAIChatModel
            settings = get_settings()
            frontier_model = settings.frontier_model
            if frontier_model != self.config.model:
                self.synthesis_model = OpenAIChatModel(
                    model_name=frontier_model,
                    provider="openai",
                )
                logger.info(
                    "Upgraded to frontier model for complex request",
                    extra={"complexity": decision.complexity, "model": frontier_model},
                )
                await stream(thought(
                    source=self.name,
                    message="Complex request detected — using advanced model for best results.",
                    data={"phase": "model_upgrade", "complexity": decision.complexity, "model": frontier_model},
                ))

        await stream(thought(
            source=self.name,
            message="Thinking through your request and selecting the best tools...",
            data={"phase": "deep_start", "complexity": decision.complexity},
        ))

        logger.info("Chat resolving attachments")
        await self._resolve_attachments(query, stream, agent_context)

        # Loop-first for hard turns. The static plan is right for a simple
        # question and wrong for a hard one: nothing reads a tool's result and
        # decides what to do next. For the configured tiers, skip the planner
        # and let the model drive (_execute_llm_driven), with a wall-clock
        # deadline the tool wrapper enforces.
        #
        # A consented deep_research turn is deliberately excluded: that is a
        # multi-minute pass owned by the research orchestrator, not something
        # the chat loop should run (or nest) itself.
        loop_tier = self._loop_first_tier(decision, agent_context)
        if self._use_research_orchestrator(decision):
            # Consented deep research: lead + parallel workers instead of a
            # single Tavily call. Runs in the deep pass below.
            execution_plan = ExecutionPlan(
                summary="Deep research: parallel workers, then a written report.",
                steps=[], source="orchestrator",
            )
        elif loop_tier:
            try:
                from app.config.settings import get_settings as _gs
                budget = int(_gs().chat_loop_budget_seconds)
            except Exception:  # noqa: BLE001
                budget = 300
            agent_context.loop_mode = loop_tier
            agent_context.loop_deadline = time.monotonic() + budget
            logger.info(
                "Loop-first turn: tier=%s budget=%ds (planner skipped)", loop_tier, budget,
                extra={"routing_source": decision.routing_source},
            )
            await stream(thought(
                source=self.name,
                message="Working through this step by step, choosing tools as I go.",
                data={"phase": "loop_first", "tier": loop_tier, "budget_seconds": budget},
            ))
            execution_plan = ExecutionPlan(
                summary="Model-driven: tools chosen from each result in turn.",
                steps=[], source="loop_first",
            )
        else:
            execution_plan = await self._generate_plan(query, agent_context, decision)

        # Escalation: a generic fallback plan is a poor fit for a complex
        # request. Let the synthesis model drive the tools itself instead.
        if (
            execution_plan.source == "fallback"
            and decision.complexity == "complex"
            and self.config.tool_strategy == ToolStrategy.LLM_DRIVEN
        ):
            logger.info("Planner fallback on complex request; escalating to LLM-driven tool use")
            await stream(thought(
                source=self.name,
                message="Planner unavailable for a complex request — letting the model choose tools directly.",
                data={"phase": "escalation", "from": "plan_fallback", "to": "llm_driven"},
            ))
            execution_plan = ExecutionPlan(
                summary="Letting the model choose tools directly.", steps=[], source="fallback",
            )

        # Budget: never run more than chat_max_tool_steps steps in one turn.
        try:
            from app.config.settings import get_settings as _gs
            max_steps = _gs().chat_max_tool_steps
        except Exception:  # noqa: BLE001
            max_steps = 6
        if len(execution_plan.steps) > max_steps:
            dropped = [s.tool for s in execution_plan.steps]
            execution_plan.steps = cap_plan_steps(execution_plan.steps, max_steps)
            kept_ids = {s.id for s in execution_plan.steps}
            execution_plan.parallel_groups = [
                [sid for sid in g if sid in kept_ids] for g in execution_plan.parallel_groups
            ]
            logger.info("Plan capped to %d steps (planned %d: %s)", max_steps, len(dropped), dropped)
            await stream(thought(
                source=self.name,
                message=f"Limiting this turn to {max_steps} tool steps.",
                data={"phase": "budget", "max_steps": max_steps, "planned": len(dropped)},
            ))

        await stream(plan(
            source=self.name,
            message=self._format_plan_summary(execution_plan),
            data=execution_plan.model_dump(),
        ))

        try:
            t_deep = time.monotonic()
            logger.info(
                "Chat deep pass starting (strategy=%s, tools=%s)",
                self.config.tool_strategy.value,
                self.config.tools,
            )
            if execution_plan.source == "orchestrator":
                from app.services.research_orchestrator import ResearchOrchestrator
                await ResearchOrchestrator(self).run(query, agent_context, stream, cancel)
            elif execution_plan.steps:
                await self._execute_plan(query, stream, cancel, agent_context, execution_plan)
            elif self.config.tool_strategy == ToolStrategy.LLM_DRIVEN:
                await self._execute_llm_driven(query, stream, cancel, agent_context)
            else:
                await self._execute_pipeline(query, stream, cancel, agent_context)
            logger.info(
                "Chat deep pass complete",
                extra={
                    "elapsed_ms": round((time.monotonic() - t_deep) * 1000),
                    "tool_results_keys": list(agent_context.tool_results.keys()),
                }
            )
        except Exception as exc:
            logger.error("Chat agent execution error: %s (after %dms)", exc, round((time.monotonic() - t_deep) * 1000), exc_info=True)
            await stream(error(
                source=self.name,
                message=f"Error during execution: {str(exc)}",
            ))
            return f"{fast_response}\n\nI encountered an error while checking that." if fast_response else "I encountered an error while checking that."
        finally:
            self.synthesis_model = original_model

        if cancel.is_set():
            return ""

        # _execute_llm_driven now streams content/thinking events in real-time
        # via _stream_llm_events.  The llm_response stored in tool_results is the
        # final text.  We emit a completion marker and handle any non-tool paths.
        if "llm_response" in agent_context.tool_results:
            deep_response = str(agent_context.tool_results["llm_response"] or "").strip()
            deep_response, deep_think = _strip_think_tags(deep_response)
            if deep_think:
                await stream(thought(
                    source=self.name,
                    message=deep_think,
                    data={"phase": "model_reasoning"},
                ))
            if deep_response:
                await stream(content(
                    source=self.name,
                    message="",
                    data={
                        "phase": "deep_response",
                        "streaming": False,
                        "partial": False,
                        "complete": True,
                    },
                ))
            else:
                deep_response = "I couldn't find a detailed answer right now."
                await stream(content(
                    source=self.name,
                    message=deep_response,
                    data={"phase": "deep_response", "partial": False},
                ))
        else:
            # Fallback to base synthesis behavior for non-LLM-driven/custom paths.
            deep_response = await self._synthesize(query, stream, cancel, agent_context)

        # Optional voice output for bridge/voice clients.
        voice_enabled = bool(agent_context.metadata.get("voice_output"))
        if voice_enabled and deep_response and "text_to_speech" in self.config.tools:
            try:
                tts_step = PipelineStep(
                    tool="text_to_speech",
                    args={
                        "text": deep_response[:2000],
                        "voice": str(agent_context.metadata.get("voice_name", "alloy")),
                        "speed": float(agent_context.metadata.get("voice_speed", 1.0)),
                    },
                )
                tts_result = await self._execute_step(tts_step, stream, cancel, agent_context)
                audio_url = getattr(tts_result, "audio_url", None) if tts_result else None
                if audio_url:
                    await stream(interim(
                        source=self.name,
                        message="Generated spoken version of the response.",
                        data={
                            "kind": "voice_output",
                            "audio_url": audio_url,
                            "bridge_channels": agent_context.metadata.get("bridge_channels", []),
                        },
                    ))
            except Exception as exc:
                logger.warning("Voice output generation skipped: %s", exc)
        # When a deep response is available it replaces the fast ack entirely;
        # fast_ack was only a transient preview streamed to the client.
        final_text = deep_response if deep_response else fast_response
        if _ends_with_yes_no_question(final_text):
            await stream(prompt(
                source=self.name,
                message=final_text.rsplit("\n", 1)[-1].strip(),
                data={"prompt_type": "confirm", "options": ["Yes", "No"]},
            ))

        total_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "Chat agent request complete",
            extra={
                "total_ms": total_ms,
                "had_tools": decision.needs_tools,
                "response_length": len(deep_response) if decision.needs_tools else len(fast_response),
            }
        )
        return final_text


# Singleton instance
chat_agent = ChatAgent()
