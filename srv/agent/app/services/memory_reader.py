"""
Reading a user's memory into their own chat turn — and nowhere else.

``load_memory_context`` is called by the dispatcher at the start of a turn,
with the turn's own ``Principal``. It returns the two core files
(``profile.md``, ``preferences.md``) verbatim under a size cap plus a
listing of the other files (path + one-line description) so the model can
open one with ``memory_recall`` when it matters. The result lives in the
``AgentContext`` for the duration of the turn: it is rendered into the
system prompt and dropped. It is never written to ``messages``,
``run_records`` or the event stream, and never logged — the stream gets a
single "Using your memory (N files)" thought so the user can see memory was
consulted.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from app.config.settings import get_settings
from app.schemas.auth import Principal
from app.services.user_memory import CORE_FILES, MemoryFile, MemoryFileInfo, MemoryStore, is_core, memory_enabled_for

logger = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    core: List[MemoryFile] = field(default_factory=list)
    others: List[MemoryFileInfo] = field(default_factory=list)
    truncated: bool = False

    @property
    def file_count(self) -> int:
        return len(self.core) + len(self.others)

    @property
    def empty(self) -> bool:
        return self.file_count == 0

    def prompt_block(self, tools_available: bool = True) -> str:
        """The system-prompt section. Kept factual and short: the files speak
        for themselves and the model is told how far to trust them."""
        parts = [
            "## What you know about this user (their personal memory)",
            "The user keeps these notes so you can help them without being told the same things twice. "
            "They wrote or approved every line; treat them as true, apply them where they change your "
            "answer, and do not recite them back or mention 'memory' unless the user asks what you remember. "
            "If something here conflicts with what the user says now, the user's current message wins.",
        ]
        for f in self.core:
            parts.append("")
            parts.append(f"### {f.path}")
            parts.append(_strip_frontmatter(f.content).strip())
        if self.truncated:
            parts.append("")
            parts.append("(core files shortened to fit)")
        if self.others:
            parts.append("")
            parts.append("### Other memory files (not loaded)")
            for info in self.others:
                parts.append(f"- `{info.path}` — {info.description or 'no description'}")
            if tools_available:
                parts.append(
                    "Call `memory_recall` with a path when one of these is relevant to the request. "
                    "When the user asks you to remember or forget something, use `memory_remember` / "
                    "`memory_forget`; do not promise to remember without calling the tool."
                )
        elif tools_available:
            parts.append("")
            parts.append(
                "When the user asks you to remember or forget something, use `memory_remember` / "
                "`memory_forget`; do not promise to remember without calling the tool."
            )
        return "\n".join(parts)


def _strip_frontmatter(content: str) -> str:
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            return content[end + 4:]
    return content


async def load_memory_context(principal: Optional[Principal], *, use_test_db: bool = False) -> Optional[MemoryContext]:
    """Core files + listing for this principal, or ``None`` when memory is
    off (platform or user) or empty. Never raises: a memory outage must not
    fail a chat turn."""
    if principal is None or not principal.sub:
        return None
    try:
        if not await memory_enabled_for(principal.sub, use_test_db):
            return None
        store = MemoryStore(principal, use_test_db=use_test_db)
        infos = await store.list()
        if not infos:
            return None
        ctx = MemoryContext(others=[i for i in infos if not is_core(i.path)])
        cap = int(getattr(get_settings(), "memory_core_max_chars", 3000))
        used = 0
        for f in await store.read_many([p for p in CORE_FILES if any(i.path == p for i in infos)]):
            body = f.content
            room = cap - used
            if room <= 0:
                ctx.truncated = True
                break
            if len(body) > room:
                body = body[:room].rsplit("\n", 1)[0]
                ctx.truncated = True
            used += len(body)
            ctx.core.append(MemoryFile(**{**f.__dict__, "content": body}))
        return ctx if not ctx.empty else None
    except Exception as exc:  # noqa: BLE001 — memory is best-effort
        logger.warning("memory: could not load context (%s: %s)", type(exc).__name__, exc)
        return None
