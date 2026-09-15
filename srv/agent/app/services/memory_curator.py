"""
The memory curator: keeps a user's memory files current after their turns.

Runs after a chat turn completes, in the background, under the same
``Principal`` the turn ran under (so the store and the keystore see the
user, not a service). Two model calls at most:

1. **Gate** (``memory_gate_purpose``, the fast model): "does this exchange
   contain something durable the user stated about themselves or their
   work?" Most turns don't; they cost one short call and stop here.
2. **Curate** (``memory_curator_purpose``, local ``agent`` model by
   default): sees the file listing, the core files, any files whose
   description overlaps the exchange, and the exchange itself, and returns
   a short list of edit operations — write / str_replace / append / delete
   — that ``MemoryStore`` applies. It updates rather than accumulates, and
   it is bound by the same privacy rules the store enforces mechanically.

Nothing the curator reads or writes is logged; the log line says how many
operations were applied, not what they were.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.config.settings import get_settings
from app.schemas.auth import Principal
from app.services.user_memory import (
    CORE_FILES,
    MemoryError,
    MemoryStore,
    NotFound,
    VersionConflict,
    is_core,
    memory_enabled_for,
)

logger = logging.getLogger(__name__)

GATE_SYSTEM = """You screen chat exchanges for a personal-memory system.
Answer with JSON only: {"durable": true|false, "why": "<8 words>"}.

"durable" is true only when the USER states, about themselves, something that
will still be true weeks from now and would change how an assistant should
help them next time: their role or team, how they like answers, a project or
responsibility they own, a tool or convention they use, a person they work
with, an explicit "remember this". It is false for: questions, one-off tasks,
things the assistant said, facts about the world, and anything about health,
money, identity documents, politics, religion, or another person's private
life."""

CURATOR_SYSTEM = """You maintain a user's personal memory: a few markdown
files the assistant reads at the start of every conversation so the user does
not have to repeat themselves. You will see the current files and one new
exchange. Decide what, if anything, to change, and answer with JSON only.

Files (paths are fixed):
- profile.md — who they are: role, team, what they work on; under 250 words
- preferences.md — how they want the assistant to answer (format, depth, tone)
- topics/<domain>.md — facts about them by subject (equipment, reporting, travel …)
- areas/<name>.md — an ongoing project, bid, rotation or responsibility
- people/<name>.md — a colleague or contact, as the user describes them
Names are lower-case with dashes. Every file starts with:
---
description: <one line saying what the file covers, for choosing when to read it>
---
followed by one fact per line, as bullets, in the user's own words, present
tense. Write "prefers", "works on", "uses" — never "seems", "probably", "may".

Rules:
1. Only what the user SAID. Not what the assistant said, not your inferences,
   not world facts. A single mention is "mentioned once", not a pattern.
2. Update, don't pile up: change the existing line (str_replace) instead of
   adding a contradicting one; merge near-duplicates; keep files short.
3. NEVER store: health, medical or mental-health details; income, salary,
   debts, balances or other personal finances; government-id, card or account
   numbers; race, ethnicity, religion, political views, sexual orientation,
   union membership, immigration status; anything about another person's
   private life; passwords or secrets. If the durable fact IS one of these,
   do nothing — no placeholder, no reworded version.
4. If the user asks to forget something, remove that line (or the file).
5. Most exchanges need no change. Return {"ops": []} then.

Operations (apply in order):
{"op": "write", "path": "...", "content": "<full file incl. frontmatter>"}   create or replace
{"op": "str_replace", "path": "...", "old": "<exact once>", "new": "..."}     edit one place
{"op": "append", "path": "...", "text": "- ..."}                              add a line (creates the file with a frontmatter you include in text if new)
{"op": "delete", "path": "..."}

Answer: {"ops": [...], "note": "<what changed, 12 words max>"}"""

_JSON_RE = re.compile(r"\{.*\}", re.S)
_WORD_RE = re.compile(r"[a-z][a-z0-9-]{3,}")


@dataclass
class CurationResult:
    ran: bool = False
    gated_out: bool = False
    ops_requested: int = 0
    ops_applied: int = 0
    errors: List[str] = field(default_factory=list)
    note: str = ""


def _json_from(text: str) -> Optional[Dict[str, Any]]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        out = json.loads(m.group(0))
        return out if isinstance(out, dict) else None
    except ValueError:
        return None


async def _llm_json(purpose: str, system: str, user: str, *, temperature: float = 0.1, max_tokens: int = 1500) -> Optional[Dict[str, Any]]:
    from busibox_common.llm import get_client

    client = get_client()
    response = await client.chat_completion(
        model=purpose,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = response.get("choices", [{}])[0].get("message", {}).get("content", "") if isinstance(response, dict) else ""
    return _json_from(content)


def _exchange_text(user_message: str, assistant_text: str, cap: int) -> str:
    u = (user_message or "").strip()
    a = (assistant_text or "").strip()
    # The user's words matter most; give them the larger share.
    u_cap = int(cap * 0.6)
    if len(u) > u_cap:
        u = u[:u_cap] + " …"
    a_cap = max(0, cap - len(u))
    if len(a) > a_cap:
        a = a[:a_cap] + " …"
    return f"USER:\n{u}\n\nASSISTANT:\n{a}"


def _relevant_paths(listing, exchange: str, limit: int = 4) -> List[str]:
    """Non-core files whose description shares a word with the exchange."""
    words = set(_WORD_RE.findall(exchange.lower()))
    scored = []
    for info in listing:
        if is_core(info.path):
            continue
        desc_words = set(_WORD_RE.findall(f"{info.path} {info.description}".lower()))
        overlap = len(words & desc_words)
        if overlap:
            scored.append((overlap, info.path))
    scored.sort(reverse=True)
    return [p for _, p in scored[:limit]]


async def gate(user_message: str, assistant_text: str) -> bool:
    """True when the exchange is worth the curator's attention."""
    settings = get_settings()
    lowered = (user_message or "").lower()
    if any(k in lowered for k in ("remember that", "remember this", "note for next time", "don't forget", "forget what i said", "from now on")):
        return True
    if len((user_message or "").strip()) < 12:
        return False
    try:
        out = await _llm_json(
            getattr(settings, "memory_gate_purpose", "fast"), GATE_SYSTEM,
            _exchange_text(user_message, assistant_text, 2500), max_tokens=80,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("memory gate unavailable: %s", exc)
        return False
    return bool(out and out.get("durable") is True)


async def apply_ops(store: MemoryStore, ops: List[Dict[str, Any]], result: CurationResult) -> None:
    for op in ops[:12]:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("op", "")).lower()
        path = str(op.get("path", ""))
        result.ops_requested += 1
        try:
            if kind == "write":
                await store.write(path, str(op.get("content", "")))
            elif kind == "str_replace":
                await store.str_replace(path, str(op.get("old", "")), str(op.get("new", "")))
            elif kind == "append":
                await store.append(path, str(op.get("text", "")))
            elif kind == "delete":
                await store.delete(path)
            else:
                result.errors.append(f"unknown op {kind!r}")
                continue
            result.ops_applied += 1
        except (VersionConflict, NotFound, MemoryError) as exc:
            # Log the class of failure, never the content.
            result.errors.append(f"{kind} {path}: {type(exc).__name__}")


async def curate_after_turn(
    principal: Principal,
    user_message: str,
    assistant_text: str,
    *,
    use_test_db: bool = False,
) -> CurationResult:
    """Gate, then curate. Never raises."""
    result = CurationResult()
    settings = get_settings()
    try:
        if principal is None or not principal.sub:
            return result
        if not await memory_enabled_for(principal.sub, use_test_db):
            return result
        if not await gate(user_message, assistant_text):
            result.gated_out = True
            return result

        store = MemoryStore(principal, use_test_db=use_test_db)
        listing = await store.list()
        exchange = _exchange_text(user_message, assistant_text, int(getattr(settings, "memory_curate_max_turn_chars", 6000)))
        wanted = [p for p in CORE_FILES if any(i.path == p for i in listing)] + _relevant_paths(listing, exchange)
        files = await store.read_many(wanted)

        parts = ["## Current files"]
        if listing:
            for info in listing:
                parts.append(f"- {info.path} — {info.description or '(no description)'}")
        else:
            parts.append("(none yet)")
        for f in files:
            parts.append(f"\n### {f.path}\n```\n{f.content.rstrip()}\n```")
        parts.append("\n## New exchange\n" + exchange)
        parts.append(
            f"\n{len(listing)}/{int(getattr(settings, 'memory_max_files', 40))} files in use; "
            f"each under {int(getattr(settings, 'memory_max_file_bytes', 8000))} bytes."
        )

        out = await _llm_json(getattr(settings, "memory_curator_purpose", "agent"), CURATOR_SYSTEM, "\n".join(parts), max_tokens=2000)
        result.ran = True
        if not out:
            result.errors.append("curator returned no JSON")
            return result
        result.note = str(out.get("note", ""))[:120]
        ops = out.get("ops") or []
        if isinstance(ops, list):
            await apply_ops(store, ops, result)
        logger.info(
            "memory curated",
            extra={"user_id": principal.sub, "ops_requested": result.ops_requested, "ops_applied": result.ops_applied, "errors": len(result.errors)},
        )
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("memory curator failed: %s: %s", type(exc).__name__, exc)
        result.errors.append(type(exc).__name__)
        return result


def schedule_curation(principal: Principal, user_message: str, assistant_text: str, *, use_test_db: bool = False) -> Optional[asyncio.Task]:
    """Fire-and-forget from the end of a turn; the turn does not wait."""
    if not getattr(get_settings(), "memory_enabled", True):
        return None
    try:
        return asyncio.create_task(
            curate_after_turn(principal, user_message, assistant_text, use_test_db=use_test_db),
            name=f"memory-curate-{principal.sub[:8]}",
        )
    except RuntimeError:  # no running loop (sync test context)
        return None
