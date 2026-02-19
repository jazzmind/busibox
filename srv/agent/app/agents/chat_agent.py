"""
Chat Agent.

A versatile chat agent with access to multiple tools for comprehensive assistance.
Uses LLM-driven tool selection to proactively help users with various tasks.

This agent extends BaseStreamingAgent with multi-tool access and LLM-driven
tool selection strategy.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    PipelineStep,
    ToolStrategy,
)
from app.schemas.streaming import content, error, thought
from pydantic import BaseModel, ValidationError

from busibox_common.llm import get_client

logger = logging.getLogger(__name__)


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

3. **Handle Ambiguous References**: When the user says "it", "that", "this topic", etc., look at the conversation history to understand what they're referring to.

4. **Cite Sources**: When using tools, include relevant sources (URLs for web, filenames for documents).

5. **Be Conversational**: Respond naturally and reference previous context when relevant.

6. **Handle Failures Gracefully**: If a tool fails or returns no results, explain and offer alternatives.

7. **Mobile-Friendly Responses**: Keep responses concise and easy to read in messaging apps:
   - Prefer short paragraphs and concise bullet lists
   - Avoid long walls of text
   - Start with the most important answer first
"""


class FastAckDecision(BaseModel):
    """Structured response from the fast-ack classifier."""

    needs_tools: bool
    response: str


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
                "create_task",
                "send_notification",
                "generate_image",
                "transcribe_audio",
                "text_to_speech",
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

        lines.append(f"Current user message: {query}")
        return "\n".join(lines)

    def _heuristic_fast_ack(self, query: str) -> FastAckDecision:
        """
        Fallback when fast LLM classification fails.
        Keeps first response varied and context-aware instead of constant text.
        """
        q = query.strip().lower()
        if any(token in q for token in ("hi", "hello", "hey")) and len(q.split()) <= 4:
            return FastAckDecision(needs_tools=False, response="Hi! How can I help?")
        if any(token in q for token in ("calendar", "schedule", "meeting", "today")):
            return FastAckDecision(needs_tools=True, response="Got it - checking your calendar now.")
        if any(token in q for token in ("weather", "forecast", "temperature")):
            return FastAckDecision(needs_tools=True, response="Sure - let me pull the latest weather.")
        if any(token in q for token in ("document", "file", "notes", "pdf")):
            return FastAckDecision(needs_tools=True, response="Okay - I’ll check your documents.")
        if any(token in q for token in ("news", "latest", "current", "search")):
            return FastAckDecision(needs_tools=True, response="On it - I’ll look that up.")
        return FastAckDecision(needs_tools=True, response="Got it. I’m working on that now.")

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
            "Return ONLY JSON with keys: needs_tools (boolean) and response (string).\n"
            "Rules:\n"
            "- needs_tools=true when external tools or fresh system data are useful "
            "(calendar, docs, web, weather, tasking, notifications, app data).\n"
            "- needs_tools=false for greetings/chitchat/simple acknowledgements where "
            "a direct response is enough.\n"
            "- response must be concise (max 1 sentence, max 120 chars).\n"
            "- If needs_tools=true, response should acknowledge and indicate you are checking.\n"
            "- If needs_tools=false, response should be a complete direct reply.\n\n"
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
            }
        )
        fast_response = decision.response.strip()
        if fast_response:
            await stream(content(
                source=self.name,
                message=fast_response,
                data={"phase": "fast_ack", "partial": False},
            ))

        if cancel.is_set():
            return ""

        # For simple conversational messages, the fast response is final.
        if not decision.needs_tools:
            return fast_response

        await stream(thought(
            source=self.name,
            message="Thinking through your request and selecting the best tools...",
            data={"phase": "deep_start"},
        ))

        logger.info("Chat resolving attachments")
        await self._resolve_attachments(query, stream, agent_context)

        try:
            t_deep = time.monotonic()
            logger.info(
                "Chat deep pass starting (strategy=%s, tools=%s)",
                self.config.tool_strategy.value,
                self.config.tools,
            )
            if self.config.tool_strategy == ToolStrategy.LLM_DRIVEN:
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
        total_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "Chat agent request complete",
            extra={
                "total_ms": total_ms,
                "had_tools": decision.needs_tools,
                "response_length": len(deep_response) if decision.needs_tools else len(fast_response),
            }
        )
        if fast_response:
            return f"{fast_response}\n\n{deep_response}".strip()
        return deep_response


# Singleton instance
chat_agent = ChatAgent()
