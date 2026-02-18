"""
Builder Agent.

Claude Agent SDK-powered coding agent for Busibox app builder workflows.
"""

import asyncio
import logging
from typing import Any, Dict, Optional
from pathlib import Path

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    ToolStrategy,
)
from app.schemas.streaming import complete, content, error, thought, tool_result, tool_start

from .builder_prompts import BUILDER_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

try:
    from claude_agent_sdk import ClaudeAgentOptions, query
except Exception:  # pragma: no cover - handled at runtime
    ClaudeAgentOptions = None
    query = None


class BuilderAgent(BaseStreamingAgent):
    """
    Builder agent that delegates coding execution to Claude Agent SDK.
    """

    def __init__(self):
        config = AgentConfig(
            name="builder-agent",
            display_name="Builder Agent",
            instructions=BUILDER_SYSTEM_PROMPT,
            tools=[],  # Claude Agent SDK provides native coding tools
            model="agent",
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
            allow_frontier_fallback=True,
        )
        super().__init__(config)

    async def run_with_streaming(
        self,
        query_text: str,
        stream,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        """
        Execute Claude Agent SDK query and map streamed messages to Busibox StreamEvents.
        """
        if query is None or ClaudeAgentOptions is None:
            await stream(error(source=self.name, message="Claude Agent SDK is not available."))
            return "Claude Agent SDK is not available."

        agent_context = await self._setup_context(context, stream, query_text)
        if agent_context is None:
            return "Authentication or session error. Please sign in and try again."

        project_id = (agent_context.metadata or {}).get("projectId")
        if not project_id:
            await stream(error(source=self.name, message="Missing required metadata: projectId"))
            return "Missing required metadata: projectId"

        project_dir = f"/srv/projects/{project_id}"
        await stream(
            thought(
                source=self.name,
                message=f"Working in project directory `{project_dir}`.",
            )
        )

        options = ClaudeAgentOptions(
            cwd=project_dir,
            allowed_tools=["Read", "Write", "Edit", "Bash", "Glob"],
            system_prompt=BUILDER_SYSTEM_PROMPT,
            max_turns=50,
            include_partial_messages=True,
        )

        full_output: list[str] = []

        try:
            async for msg in query(prompt=self._build_prompt(query_text, agent_context), options=options):
                if cancel.is_set():
                    break
                await self._handle_sdk_message(msg, stream, full_output)

            # Error feedback loop: run a build check and feed failures back to the agent
            if not cancel.is_set():
                build_errors = await self._check_build_errors(Path(project_dir))
                if build_errors:
                    await stream(
                        thought(
                            source=self.name,
                            message="Detected build/runtime errors. Attempting an automatic fix pass.",
                        )
                    )
                    fix_prompt = (
                        "The latest implementation has build/runtime errors. "
                        "Fix them now and rerun checks.\n\n"
                        f"Build output:\n{build_errors}\n"
                    )
                    async for msg in query(prompt=fix_prompt, options=options):
                        if cancel.is_set():
                            break
                        await self._handle_sdk_message(msg, stream, full_output)
        except Exception as exc:
            logger.error("Builder agent SDK execution failed: %s", exc, exc_info=True)
            await stream(error(source=self.name, message=f"Builder execution failed: {exc}"))
            return f"Builder execution failed: {exc}"

        final_text = "".join(full_output).strip()
        await stream(complete(source=self.name, message="Builder run complete."))
        return final_text

    def _build_prompt(self, user_query: str, context: AgentContext) -> str:
        """
        Build SDK prompt with metadata and compressed history context.
        """
        parts = []
        if context.metadata:
            parts.append("Application metadata:")
            for key, value in context.metadata.items():
                parts.append(f"- {key}: {value}")
            parts.append("")

        if context.compressed_history_summary:
            parts.append("Conversation summary:")
            parts.append(context.compressed_history_summary)
            parts.append("")

        if context.recent_messages:
            parts.append("Recent conversation:")
            for message in context.recent_messages[-10:]:
                role = message.get("role", "unknown")
                msg = message.get("content", "")
                parts.append(f"{role}: {msg}")
            parts.append("")

        parts.append("Current task:")
        parts.append(user_query)
        return "\n".join(parts)

    async def _handle_sdk_message(self, msg: Any, stream, full_output: list[str]) -> None:
        """
        Best-effort mapping from Claude SDK messages to agentic stream events.
        """
        msg_type = getattr(msg, "type", None) or msg.__class__.__name__

        # Partial/raw stream event path
        if "StreamEvent" in msg.__class__.__name__:
            raw = getattr(msg, "event", None) or getattr(msg, "raw_event", None) or {}
            event_type = str(raw.get("type", ""))
            delta = ((raw.get("delta") or {}).get("text")) if isinstance(raw, dict) else None
            if event_type == "content_block_delta" and delta:
                full_output.append(delta)
                await stream(content(source=self.name, message=delta, data={"streaming": True, "partial": True}))
            return

        if "ToolUse" in msg_type or "tool_use" in str(msg_type).lower():
            tool_name = getattr(msg, "name", None) or getattr(msg, "tool_name", None) or "tool"
            tool_input = getattr(msg, "input", None) or getattr(msg, "arguments", None) or {}
            await stream(tool_start(source=self.name, message=f"Running {tool_name}", data={"tool": tool_name, "input": tool_input}))
            return

        if "ToolResult" in msg_type or "tool_result" in str(msg_type).lower():
            tool_name = getattr(msg, "tool_name", None) or "tool"
            result = getattr(msg, "output", None) or getattr(msg, "content", None) or str(msg)
            await stream(tool_result(source=self.name, message=f"Completed {tool_name}", data={"tool": tool_name, "result": result}))
            return

        # Assistant message with text blocks
        content_blocks = getattr(msg, "content", None)
        if isinstance(content_blocks, list):
            emitted = False
            for block in content_blocks:
                block_text = getattr(block, "text", None)
                if block_text:
                    emitted = True
                    full_output.append(block_text)
                    await stream(content(source=self.name, message=block_text, data={"streaming": False}))
            if emitted:
                return

        # Generic fallback
        text = getattr(msg, "text", None)
        if text:
            full_output.append(text)
            await stream(content(source=self.name, message=text, data={"streaming": False}))

    async def _check_build_errors(self, project_dir: Path) -> str:
        """
        Run a lightweight build check and return output if it fails.
        """
        proc = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            "npm run build",
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        output = (stdout or b"").decode(errors="ignore")
        if proc.returncode == 0:
            return ""
        return output[-12000:]


# Singleton instance discovered by builtin loader
builder_agent = BuilderAgent()

