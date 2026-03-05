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
    ToolRegistry,
    ToolStrategy,
)
from app.schemas.streaming import content, error, interim, plan, progress, prompt, thought
from pydantic import BaseModel, ValidationError

from busibox_common.llm import get_client

import re

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)

_YES_NO_PATTERNS = [
    "would you like me to",
    "shall i",
    "do you want me to",
    "should i",
    "would you like to",
]


def _ends_with_yes_no_question(text: str) -> bool:
    """Return True if *text* ends with a question that looks like a yes/no prompt."""
    stripped = text.rstrip()
    if not stripped.endswith("?"):
        return False
    last_sentence = stripped.rsplit("\n", 1)[-1].lower()
    return any(p in last_sentence for p in _YES_NO_PATTERNS)


def _strip_think_tags(text: str) -> tuple:
    """Strip ``<think>`` blocks and return (clean_text, think_content_or_None)."""
    matches = _THINK_RE.findall(text)
    if not matches:
        return text, None
    think_text = "\n".join(m.strip() for m in matches)
    cleaned = _THINK_RE.sub("", text).strip()
    return cleaned, think_text


# Chat agent system prompt - focused on behavior, tools are auto-documented by PydanticAI
CHAT_SYSTEM_PROMPT = """You are a versatile chat assistant that helps users by using available tools when appropriate.

**Key Behaviors:**

1. **Use Conversation Context**: The conversation history is provided with each message. Use it to:
   - Understand follow-up questions (e.g., "tell me more about it" refers to the previous topic)
   - Remember what was discussed earlier
   - Maintain continuity across turns

2. **Use Tools Proactively**: Don't wait for explicit tool requests:
   - Questions about current events, news, prices → search the web
   - Questions about weather → get weather
   - Questions about "my documents" or specific files → search documents
   - Requests for recurring tasks → create task

3. **Gather Profile Context Opportunistically**:
   - If pending follow-up questions or missing profile fields are provided in context, ask at most one short follow-up question naturally.
   - Do not interrupt urgent task completion; weave the question into natural transitions.
   - Keep profile prompts optional and friendly (e.g. "Quick preference check: do you prefer concise or detailed responses?")
   - If a pending profile question is present, prioritize that phrasing.

4. **Handle Ambiguous References**: When the user says "it", "that", "this topic", etc., look at the conversation history to understand what they're referring to.

5. **Cite Sources**: When using tools, include relevant sources (URLs for web, filenames for documents).

6. **Be Conversational**: Respond naturally and reference previous context when relevant.

7. **Handle Failures Gracefully**: If a tool fails or returns no results, explain and offer alternatives.

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


class PlanStep(BaseModel):
    """A concrete tool step in a generated execution plan."""

    id: str
    tool: str
    objective: str
    run_mode: str = "serial"  # serial | parallel
    args: Dict[str, Any] = {}


class FeedbackPoint(BaseModel):
    """A user-facing update point during execution."""

    after_step_id: str
    message: str
    kind: str = "interim"  # interim | clarify


class ExecutionPlan(BaseModel):
    """Structured plan produced before tool execution."""

    summary: str
    steps: List[PlanStep] = []
    parallel_groups: List[List[str]] = []
    feedback_points: List[FeedbackPoint] = []
    estimated_duration: str = "quick"


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
            tools=[
                "web_search",
                "get_weather",
                "document_search",
                "list_data_documents",
                "get_data_document",
                "query_data",
                "create_task",
                "send_notification",
                "generate_image",
                "transcribe_audio",
                "memory_search",
                "memory_save",
            ],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,  # Let LLM decide which tools to use
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
                lines.append(f"- {filename} ({mime_type})")
            lines.append("")

        if context.pending_questions:
            lines.append("Pending follow-up prompts:")
            for item in context.pending_questions[:2]:
                question = str(item.get("content", "")).strip()
                if question:
                    lines.append(f"- {question}")
            lines.append("")

        if context.missing_profile_fields:
            lines.append(f"Missing profile fields: {', '.join(context.missing_profile_fields)}")
            lines.append("")

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
            "weather": "get_weather",
            "get_weather": "get_weather",
            "task": "create_task",
            "create_task": "create_task",
            "notify": "send_notification",
            "send_notification": "send_notification",
            "image": "generate_image",
            "generate_image": "generate_image",
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

    async def _generate_fast_ack(self, query: str, context: AgentContext) -> FastAckDecision:
        """
        Generate a fast first response and decide whether we need a deeper tool pass.
        """
        default = self._heuristic_fast_ack(query)
        prompt = (
            "You are deciding how to handle a user message.\n"
            "Return ONLY JSON with keys: action_type, needs_tools, response, follow_up_question, confidence.\n"
            "Rules:\n"
            "- action_type must be one of: direct, research, search, analysis, clarify, multi_step.\n"
            "- needs_tools=true when external tools or fresh system data are useful "
            "(calendar, docs, web, weather, tasking, notifications, app data).\n"
            "- needs_tools=false for greetings/chitchat/simple acknowledgements where "
            "a direct response is enough.\n"
            "- use action_type=clarify when the request is ambiguous or underspecified.\n"
            "- if action_type=clarify, set needs_tools=false and provide a follow_up_question.\n"
            "- response must be concise (max 1 sentence, max 120 chars).\n"
            "- If needs_tools=true, response should acknowledge and indicate you are checking.\n"
            "- If needs_tools=false, response should be a complete direct reply.\n\n"
            "Intent guidance (IMPORTANT):\n"
            "- Queries about owned records/documents/candidates/resumes (e.g. 'do I have resumes for data analytics?') MUST set action_type=search and needs_tools=true.\n"
            "- If user asks to find/list/show/filter internal data, do NOT answer directly; use tools.\n"
            "- Prefer false positives (using tools) over false negatives (missing a search).\n\n"
            f"{self._build_fast_ack_context(query, context)}"
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
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
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
            parsed.action_type = self._normalize_action_type(parsed.action_type)
            if parsed.action_type == "clarify":
                parsed.needs_tools = False
                if not parsed.follow_up_question:
                    parsed.follow_up_question = "Could you clarify what you want me to focus on?"
            parsed.routing_source = "llm"
            return parsed
        except (json.JSONDecodeError, ValidationError, Exception) as exc:
            logger.warning("Fast ack generation fallback after %dms: %s", round((time.monotonic() - t_llm) * 1000) if 't_llm' in dir() else -1, exc)
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
            "Summarize these tool findings in 1-2 short sentences for a chat user.\n"
            "Be concrete and avoid mentioning internal tooling.\n"
            f"User query: {query}\n"
            f"Findings: {json.dumps(compact)}"
        )
        try:
            client = get_client()
            logger.info("quick_findings: calling LLM (model=fast)")
            t_qf = time.monotonic()
            result = await client.chat_completion(
                model="fast",
                messages=[
                    {"role": "system", "content": "You write concise interim progress summaries."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
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
        document_library_intent = any(
            phrase in ql for phrase in (
                "document",
                "documents",
                "file",
                "files",
                "pdf",
                "resume",
                "resumes",
                "candidate",
                "candidates",
            )
        )

        # Always search user's personal and shared documents for context
        if "document_search" in enabled_tools:
            fallback_steps.append(
                PlanStep(
                    id="step_1",
                    tool="document_search",
                    objective="Search user's personal and shared documents for relevant context",
                    args={"query": query},
                )
            )

        if (
            dispatch.action_type in {"research", "search"}
            and "web_search" in enabled_tools
            and not document_library_intent
        ):
            step_id = f"step_{len(fallback_steps) + 1}"
            fallback_steps.append(
                PlanStep(id=step_id, tool="web_search", objective="Gather external context", args={"query": query})
            )
        if data_document_list_intent and "list_data_documents" in enabled_tools:
            fallback_steps.append(
                PlanStep(
                    id="step_2" if fallback_steps else "step_1",
                    tool="list_data_documents",
                    objective="List available structured data documents",
                    args={"limit": 50},
                )
            )
        if not fallback_steps and enabled_tools:
            fallback_steps.append(
                PlanStep(id="step_1", tool=enabled_tools[0], objective="Collect supporting context", args={"query": query})
            )

        fallback = ExecutionPlan(
            summary="I'll gather the most relevant information first, then synthesize the final answer.",
            steps=fallback_steps,
            parallel_groups=[[]],
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
        has_attachments = bool(context.attachment_metadata)
        has_audio = has_attachments and any(
            a.get("mime_type", "").startswith("audio/") for a in context.attachment_metadata
        )
        has_image_request = any(
            kw in query.lower() for kw in ("generate image", "create image", "draw", "make a picture", "make an image")
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
            "- Do NOT include `create_task` unless the user explicitly asked to create a scheduled task.\n"
            "- Do NOT include `send_notification` unless the user explicitly asked to send a notification.\n"
            "- Do NOT include `memory_search` or `memory_save` unless the user asks about previous conversations or preferences.\n"
            "- ALWAYS include `document_search` as the first step to search the user's personal and shared documents for relevant context. Add `web_search` as a second step when external information is also needed.\n"
            "- Use `list_data_documents`, `get_data_document`, or `query_data` ONLY when the user explicitly asks about structured data tables/records.\n\n"
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
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
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
        )

    def _format_plan_summary(self, execution_plan: ExecutionPlan) -> str:
        if not execution_plan.steps:
            return "No tools needed. I'll respond directly."
        bullets = [f"{idx}. {step.objective} (`{step.tool}`)" for idx, step in enumerate(execution_plan.steps, start=1)]
        return (
            f"{execution_plan.summary}\n\n"
            f"Estimated duration: {execution_plan.estimated_duration}\n"
            "Planned steps:\n- " + "\n- ".join(bullets)
        )

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

        step_by_id = {step.id: step for step in execution_plan.steps}
        group_map: Dict[str, int] = {}
        for group_idx, group in enumerate(execution_plan.parallel_groups):
            for step_id in group:
                group_map[step_id] = group_idx

        completed: Set[str] = set()
        total = len(execution_plan.steps)

        while len(completed) < total:
            if cancel.is_set():
                return

            pending = [step for step in execution_plan.steps if step.id not in completed]
            if not pending:
                break

            next_step = pending[0]
            group_idx = group_map.get(next_step.id)
            if group_idx is not None:
                group_ids = [sid for sid in execution_plan.parallel_groups[group_idx] if sid not in completed]
                runnable = [step_by_id[sid] for sid in group_ids if sid in step_by_id]
            else:
                runnable = [next_step]

            tasks = []
            for step in runnable:
                pipeline_step = PipelineStep(tool=step.tool, args=step.args)
                tasks.append(self._execute_step(pipeline_step, stream, cancel, agent_context))

            await asyncio.gather(*tasks, return_exceptions=True)

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

        t_ack = time.monotonic()
        decision = await self._generate_fast_ack(query, agent_context)
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

        # If dispatch asks a clarifying question, stop after first response.
        if decision.action_type == "clarify":
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

        await stream(thought(
            source=self.name,
            message="Thinking through your request and selecting the best tools...",
            data={"phase": "deep_start"},
        ))

        logger.info("Chat resolving attachments")
        await self._resolve_attachments(query, stream, agent_context)

        execution_plan = await self._generate_plan(query, agent_context, decision)
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
            if execution_plan.steps:
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

        if cancel.is_set():
            return ""

        # For LLM-driven chat, _execute_llm_driven stores final text in llm_response,
        # and base _synthesize emits one full content event. To preserve incremental
        # second-phase UX, stream it in chunks ourselves.
        if "llm_response" in agent_context.tool_results:
            await stream(thought(
                source=self.name,
                message="Synthesizing findings...",
                data={"phase": "synthesis"},
            ))
            quick_findings = await self._generate_quick_findings(query, agent_context.tool_results)
            if quick_findings:
                await stream(content(
                    source=self.name,
                    message=quick_findings,
                    data={"phase": "quick_findings", "partial": False},
                ))
            deep_response = str(agent_context.tool_results["llm_response"] or "").strip()
            deep_response, deep_think = _strip_think_tags(deep_response)
            if deep_think:
                await stream(thought(
                    source=self.name,
                    message=deep_think,
                    data={"phase": "model_reasoning"},
                ))
            if deep_response:
                for chunk in self._stream_chunks(deep_response):
                    if cancel.is_set():
                        return ""
                    await stream(content(
                        source=self.name,
                        message=chunk,
                        data={"phase": "deep_response", "streaming": True, "partial": True},
                    ))
                    await asyncio.sleep(0.03)
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
        # Emit quick-reply prompt when the final response ends with a yes/no question.
        final_text = f"{fast_response}\n\n{deep_response}".strip() if fast_response else deep_response
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
