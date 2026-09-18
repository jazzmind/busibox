"""
Chat tools over the user's personal memory (services/user_memory.py).

Three tools, all bound to the calling user's ``Principal`` from the tool
context — there is no argument for "which user":

- ``memory_recall``   read one file the prompt listed as not loaded
- ``memory_remember`` add a fact the user asked to be remembered
- ``memory_forget``   remove a line (or a whole file) the user asked to forget

Explicit requests ("remember that…", "forget what I said about…") are the
highest-signal input memory gets, so the chat agent handles them directly
with these tools instead of waiting for the curator.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.services.user_memory import (
    MemoryError,
    MemoryStore,
    NotFound,
    memory_enabled_for,
    validate_path,
)

logger = logging.getLogger(__name__)


class MemoryRecallOutput(BaseModel):
    success: bool
    path: str = ""
    content: str = ""
    error: Optional[str] = None


class MemoryWriteOutput(BaseModel):
    success: bool
    path: str = ""
    message: str = ""
    error: Optional[str] = None


def _principal(ctx: RunContext[Any]):
    deps = getattr(ctx, "deps", None)
    return getattr(deps, "principal", None)


async def _store(ctx: RunContext[Any]) -> MemoryStore:
    principal = _principal(ctx)
    if principal is None or not principal.sub:
        raise MemoryError("no authenticated user in this context")
    if not await memory_enabled_for(principal.sub):
        raise MemoryError("memory is turned off for this user")
    return MemoryStore(principal)


async def memory_recall(ctx: RunContext[Any], path: str) -> MemoryRecallOutput:
    """Read one of the user's memory files that the prompt listed as "not loaded".

    Use it when a listed file's description is relevant to the request —
    e.g. the user asks about a project and `areas/<project>.md` exists.
    `path` is exactly as listed (`topics/reporting.md`). profile.md and
    preferences.md are already in your context; do not recall them.
    Never quote the file back wholesale; use what it says.
    """
    try:
        store = await _store(ctx)
        f = await store.read(path)
        return MemoryRecallOutput(success=True, path=f.path, content=f.content)
    except NotFound as exc:
        return MemoryRecallOutput(success=False, path=path, error=str(exc))
    except MemoryError as exc:
        return MemoryRecallOutput(success=False, path=path, error=str(exc))


async def memory_remember(ctx: RunContext[Any], fact: str, path: str = "") -> MemoryWriteOutput:
    """Save something the user explicitly asked you to remember.

    Only for explicit requests ("remember that…", "note for next time…",
    "my manager is…"). Write `fact` as one compact line in the user's own
    words, present tense, no commentary. Choose `path` by subject:
    `profile.md` (who they are, their role), `preferences.md` (how they
    want you to answer), `topics/<domain>.md`, `areas/<project>.md`,
    `people/<name>.md` (lower-case, dashes). Leave `path` empty to let the
    store pick `topics/general.md`. Refuse (do not call) for health,
    finances, government or card numbers, or anything about a third
    person's private life — tell the user memory can't hold that.
    """
    try:
        store = await _store(ctx)
        target = validate_path(path) if path else "topics/general.md"
        line = "- " + " ".join((fact or "").split()).strip("- ").strip()
        if len(line) < 4:
            return MemoryWriteOutput(success=False, path=target, error="nothing to remember")
        info = await store.append(target, line)
        return MemoryWriteOutput(success=True, path=info.path, message=f"Saved to {info.path}.")
    except MemoryError as exc:
        return MemoryWriteOutput(success=False, path=path, error=str(exc))


async def memory_forget(ctx: RunContext[Any], text: str, path: str = "") -> MemoryWriteOutput:
    """Remove something the user asked you to forget.

    `text` is a distinctive fragment of the line to remove (the store
    removes every line containing it, case-insensitively). Give `path` when
    you know the file; leave it empty to search all files. To delete a whole
    file, pass its path and `text="*"`. Confirm to the user what was removed.
    """
    try:
        store = await _store(ctx)
        needle = (text or "").strip()
        if not needle:
            return MemoryWriteOutput(success=False, error="say what to forget")
        if needle == "*" and path:
            removed = await store.delete(path)
            return MemoryWriteOutput(success=removed, path=path, message=f"Deleted {path}." if removed else f"{path} did not exist.")
        paths: List[str] = [validate_path(path)] if path else [i.path for i in await store.list()]
        touched: List[str] = []
        for p in paths:
            try:
                f = await store.read(p)
            except NotFound:
                continue
            kept = [ln for ln in f.content.splitlines() if needle.lower() not in ln.lower()]
            if len(kept) == len(f.content.splitlines()):
                continue
            body = "\n".join(kept).strip()
            if not _has_prose(body):
                await store.delete(p)
            else:
                await store.write(p, body, if_version=f.version)
            touched.append(p)
        if not touched:
            return MemoryWriteOutput(success=False, path=path, error=f"nothing in memory mentions '{needle}'")
        return MemoryWriteOutput(success=True, path=", ".join(touched), message=f"Removed from {', '.join(touched)}.")
    except MemoryError as exc:
        return MemoryWriteOutput(success=False, path=path, error=str(exc))


def _has_prose(body: str) -> bool:
    """False when only frontmatter/headings remain."""
    in_fm = False
    for ln in body.splitlines():
        s = ln.strip()
        if s == "---":
            in_fm = not in_fm
            continue
        if in_fm or not s or s.startswith("#"):
            continue
        return True
    return False
