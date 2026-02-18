"""
Builder Local Agent.

Local-model fallback agent that executes Aider against the project workspace.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from app.agents.base_agent import (
    AgentConfig,
    BaseStreamingAgent,
    ExecutionMode,
    ToolStrategy,
)
from app.schemas.streaming import complete, content, error, thought

logger = logging.getLogger(__name__)


class BuilderLocalAgent(BaseStreamingAgent):
    def __init__(self):
        config = AgentConfig(
            name="builder-local-agent",
            display_name="Builder Local Agent",
            instructions="Local model coding assistant using Aider.",
            tools=[],
            model="fast",
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)

    async def run_with_streaming(
        self,
        query: str,
        stream,
        cancel: asyncio.Event,
        context: Optional[dict] = None,
    ) -> str:
        agent_context = await self._setup_context(context, stream, query)
        if agent_context is None:
            return "Authentication or session error."

        metadata = agent_context.metadata or {}
        project_id = metadata.get("projectId")
        if not project_id:
            await stream(error(source=self.name, message="Missing required metadata: projectId"))
            return "Missing required metadata: projectId"

        project_dir = Path("/srv/projects") / str(project_id)
        if not project_dir.exists():
            await stream(error(source=self.name, message=f"Project directory not found: {project_dir}"))
            return f"Project directory not found: {project_dir}"

        model = os.getenv("BUILDER_LOCAL_MODEL", "openai/fast")
        openai_base = os.getenv("LITELLM_BASE_URL", "http://litellm:4000/v1")
        openai_key = os.getenv("LITELLM_API_KEY", "sk-local-dev-key")

        await stream(thought(source=self.name, message="Running Aider with local model fallback..."))

        cmd = [
            "aider",
            "--message",
            query,
            "--yes",
            "--no-auto-commits",
            "--no-stream",
            "--model",
            model,
            "--openai-api-base",
            openai_base,
            "--openai-api-key",
            openai_key,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(project_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        lines: list[str] = []
        assert proc.stdout is not None

        while True:
            if cancel.is_set():
                proc.terminate()
                break

            line = await proc.stdout.readline()
            if not line:
                break

            text = line.decode(errors="ignore").rstrip()
            if not text:
                continue
            lines.append(text)
            await stream(content(source=self.name, message=f"{text}\n", data={"streaming": True, "partial": True}))

        rc = await proc.wait()
        output = "\n".join(lines).strip()

        if rc != 0:
            await stream(error(source=self.name, message="Aider execution failed."))
            return output or "Aider execution failed."

        await stream(complete(source=self.name, message="Local builder run complete."))
        return output


builder_local_agent = BuilderLocalAgent()

