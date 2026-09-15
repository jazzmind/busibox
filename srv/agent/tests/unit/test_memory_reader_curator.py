"""Reader, curator and chat tools for personal memory — no database.

``FakeStore`` is an in-memory ``MemoryStore`` with the same surface; the
LLM calls the curator makes are replaced at ``memory_curator._llm_json``.
"""

from __future__ import annotations

import types
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytest

from app.schemas.auth import Principal
from app.services import memory_curator as mc
from app.services import memory_reader as mr
from app.services import user_memory as um
from app.tools import user_memory_tools as tools


class FakeStore:
    def __init__(self, files: Optional[Dict[str, str]] = None):
        self.files: Dict[str, str] = dict(files or {})
        self.versions: Dict[str, int] = {p: 1 for p in self.files}
        self.crypto = types.SimpleNamespace(enabled=True)
        self.user_id = "u1"

    def _info(self, p):
        return um.MemoryFileInfo(path=p, description=um.description_of(self.files[p]), version=self.versions[p],
                                 size_bytes=len(self.files[p]), updated_at=datetime(2026, 9, 16))

    async def list(self):
        return sorted([self._info(p) for p in self.files], key=lambda i: (0 if um.is_core(i.path) else 1, i.path))

    async def read(self, path):
        path = um.validate_path(path)
        if path not in self.files:
            raise um.NotFound(path)
        return um.MemoryFile(**self._info(path).__dict__, content=self.files[path])

    async def read_many(self, paths):
        return [await self.read(p) for p in paths if p in self.files]

    async def write(self, path, content, if_version=None):
        path = um.validate_path(path)
        content = content.strip() + "\n"
        um.privacy_check(content)
        if if_version == "new" and path in self.files:
            raise um.VersionConflict("exists", current=await self.read(path))
        if isinstance(if_version, int) and self.versions.get(path) != if_version:
            raise um.VersionConflict("stale", current=await self.read(path))
        self.files[path] = content
        self.versions[path] = self.versions.get(path, 0) + 1
        return self._info(path)

    async def str_replace(self, path, old, new, if_version=None):
        cur = await self.read(path)
        if cur.content.count(old) != 1:
            raise um.MemoryError("not exactly once")
        return await self.write(path, cur.content.replace(old, new), if_version=cur.version)

    async def append(self, path, text, if_version=None):
        if path not in self.files:
            return await self.write(path, text, if_version="new")
        cur = await self.read(path)
        return await self.write(path, cur.content.rstrip("\n") + "\n" + text.strip(), if_version=cur.version)

    async def delete(self, path):
        return self.files.pop(um.validate_path(path), None) is not None

    async def delete_all(self):
        n = len(self.files)
        self.files.clear()
        return n


PROFILE = "---\ndescription: Who I am\n---\n- Project engineer, dredging division\n- Based in Quincy, MA\n"
PREFS = "---\ndescription: How I want answers\n---\n- Short answers, tables for numbers\n"
BID = "---\ndescription: Boston Harbor maintenance dredging bid, due October\n---\n- Owns the production estimate\n"


@pytest.fixture
def principal():
    return Principal(sub="u1", email="u1@example.com", token="jwt")


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore({"profile.md": PROFILE, "preferences.md": PREFS, "areas/boston-harbor-bid.md": BID})

    async def _enabled(user_id, use_test_db=False):
        return fake.enabled_flag

    fake.enabled_flag = True
    for mod in (mr, mc, tools):
        monkeypatch.setattr(mod, "MemoryStore", lambda principal, **kw: fake)
        monkeypatch.setattr(mod, "memory_enabled_for", _enabled)
    monkeypatch.setattr(mr, "get_settings", lambda: types.SimpleNamespace(memory_core_max_chars=3000))
    monkeypatch.setattr(mc, "get_settings", lambda: types.SimpleNamespace(
        memory_enabled=True, memory_gate_purpose="fast", memory_curator_purpose="agent",
        memory_curate_max_turn_chars=6000, memory_max_files=40, memory_max_file_bytes=8000,
    ))
    return fake


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------


async def test_reader_loads_core_verbatim_and_lists_the_rest(store, principal):
    ctx = await mr.load_memory_context(principal)
    assert ctx is not None and ctx.file_count == 3
    assert [f.path for f in ctx.core] == ["profile.md", "preferences.md"]
    assert [i.path for i in ctx.others] == ["areas/boston-harbor-bid.md"]

    block = ctx.prompt_block(tools_available=True)
    assert block.startswith("## What you know about this user")
    assert "Project engineer, dredging division" in block and "Short answers, tables" in block
    assert "description: Who I am" not in block  # frontmatter stripped
    assert "`areas/boston-harbor-bid.md` — Boston Harbor maintenance dredging bid, due October" in block
    assert "memory_recall" in block and "memory_remember" in block
    assert "memory_recall" not in ctx.prompt_block(tools_available=False)


async def test_reader_truncates_core_to_the_cap(store, principal, monkeypatch):
    monkeypatch.setattr(mr, "get_settings", lambda: types.SimpleNamespace(memory_core_max_chars=60))
    ctx = await mr.load_memory_context(principal)
    assert ctx.truncated is True
    assert sum(len(f.content) for f in ctx.core) <= 60
    assert "shortened" in ctx.prompt_block()


async def test_reader_returns_none_when_off_or_empty(store, principal):
    store.enabled_flag = False
    assert await mr.load_memory_context(principal) is None
    store.enabled_flag = True
    store.files.clear()
    assert await mr.load_memory_context(principal) is None
    assert await mr.load_memory_context(None) is None


async def test_reader_never_raises(store, principal, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("keystore down")

    monkeypatch.setattr(store, "list", boom)
    assert await mr.load_memory_context(principal) is None


# ---------------------------------------------------------------------------
# Curator
# ---------------------------------------------------------------------------


def test_json_from_tolerates_fences_and_prose():
    assert mc._json_from('```json\n{"durable": true}\n```') == {"durable": True}
    assert mc._json_from('Sure: {"ops": []} done') == {"ops": []}
    assert mc._json_from("nope") is None
    assert mc._json_from("[1,2]") is None


def test_relevant_paths_match_descriptions_to_the_exchange():
    listing = [
        um.MemoryFileInfo("profile.md", "Who I am", 1, 1, datetime(2026, 1, 1)),
        um.MemoryFileInfo("areas/boston-harbor-bid.md", "Boston Harbor maintenance dredging bid", 1, 1, datetime(2026, 1, 1)),
        um.MemoryFileInfo("topics/travel.md", "Travel preferences", 1, 1, datetime(2026, 1, 1)),
    ]
    assert mc._relevant_paths(listing, "USER: update the boston harbor bid estimate") == ["areas/boston-harbor-bid.md"]
    assert mc._relevant_paths(listing, "USER: hello") == []


async def test_gate_short_circuits_on_explicit_requests_and_short_messages(store, monkeypatch):
    calls = []

    async def fake_llm(purpose, system, user, **kw):
        calls.append(purpose)
        return {"durable": False}

    monkeypatch.setattr(mc, "_llm_json", fake_llm)
    assert await mc.gate("Remember that I report to Dana", "ok") is True
    assert calls == []  # no model call needed
    assert await mc.gate("hi", "hello") is False
    assert calls == []
    assert await mc.gate("What is the tide at Quincy tomorrow morning?", "About 9 ft") is False
    assert calls == ["fast"]


async def test_curate_applies_ops_and_respects_privacy(store, principal, monkeypatch):
    seen = {}

    async def fake_llm(purpose, system, user, **kw):
        if purpose == "fast":
            return {"durable": True}
        seen["prompt"] = user
        return {
            "ops": [
                {"op": "str_replace", "path": "profile.md", "old": "- Based in Quincy, MA", "new": "- Based in Quincy, MA; office at the yard"},
                {"op": "append", "path": "people/dana.md", "text": "---\ndescription: Dana, my manager\n---\n- Reports go to Dana on Fridays"},
                {"op": "append", "path": "topics/finance.md", "text": "- salary 4111111111111111"},
                {"op": "delete", "path": "areas/boston-harbor-bid.md"},
                {"op": "bogus", "path": "x"},
            ],
            "note": "manager, office, bid closed",
        }

    monkeypatch.setattr(mc, "_llm_json", fake_llm)
    out = await mc.curate_after_turn(principal, "From now on my reports go to Dana on Fridays; the harbor bid closed.", "Noted.")

    assert out.ran and not out.gated_out
    assert out.ops_requested == 5 and out.ops_applied == 3
    assert any("PrivacyRefused" in e for e in out.errors) and any("unknown op" in e for e in out.errors)
    assert "office at the yard" in store.files["profile.md"]
    assert store.files["people/dana.md"].endswith("- Reports go to Dana on Fridays\n")
    assert "topics/finance.md" not in store.files
    assert "areas/boston-harbor-bid.md" not in store.files
    assert out.note == "manager, office, bid closed"
    # The curator saw the listing, the core files and the exchange.
    assert "## Current files" in seen["prompt"] and "### profile.md" in seen["prompt"] and "USER:" in seen["prompt"]


async def test_curate_is_gated_and_never_raises(store, principal, monkeypatch):
    async def gate_no(purpose, system, user, **kw):
        return {"durable": False}

    monkeypatch.setattr(mc, "_llm_json", gate_no)
    out = await mc.curate_after_turn(principal, "How deep is the channel at Long Island Head?", "About 40 ft.")
    assert out.gated_out and not out.ran and out.ops_applied == 0

    async def boom(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(mc, "_llm_json", boom)
    out = await mc.curate_after_turn(principal, "Remember that I use metric units", "ok")
    assert out.errors == ["RuntimeError"] and not out.ran

    store.enabled_flag = False
    out = await mc.curate_after_turn(principal, "Remember that I use metric units", "ok")
    assert out == mc.CurationResult()


def test_curator_prompts_carry_the_privacy_rules():
    for banned in ("health", "salary", "card", "immigration", "another person"):
        assert banned in mc.CURATOR_SYSTEM
    assert "Only what the user SAID" in mc.CURATOR_SYSTEM
    assert '"durable"' in mc.GATE_SYSTEM


# ---------------------------------------------------------------------------
# Chat tools
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, principal):
        self.deps = types.SimpleNamespace(principal=principal)


async def test_recall_reads_a_listed_file(store, principal):
    out = await tools.memory_recall(_Ctx(principal), "areas/boston-harbor-bid.md")
    assert out.success and "production estimate" in out.content
    missing = await tools.memory_recall(_Ctx(principal), "topics/nope.md")
    assert not missing.success and missing.error
    bad = await tools.memory_recall(_Ctx(principal), "../etc/passwd")
    assert not bad.success


async def test_remember_appends_one_clean_line(store, principal):
    out = await tools.memory_remember(_Ctx(principal), "  I  report to Dana ", path="people/dana.md")
    assert out.success and out.path == "people/dana.md"
    assert store.files["people/dana.md"] == "- I report to Dana\n"
    out = await tools.memory_remember(_Ctx(principal), "metric units")
    assert out.path == "topics/general.md"
    refused = await tools.memory_remember(_Ctx(principal), "my card is 4111 1111 1111 1111")
    assert not refused.success and "cannot hold" in (refused.error or "")


async def test_forget_removes_matching_lines_or_whole_files(store, principal):
    out = await tools.memory_forget(_Ctx(principal), "Quincy")
    assert out.success and out.path == "profile.md"
    assert "Quincy" not in store.files["profile.md"] and "Project engineer" in store.files["profile.md"]

    out = await tools.memory_forget(_Ctx(principal), "production estimate")
    assert out.success and "areas/boston-harbor-bid.md" not in store.files  # only frontmatter left → file removed

    out = await tools.memory_forget(_Ctx(principal), "*", path="preferences.md")
    assert out.success and "preferences.md" not in store.files

    out = await tools.memory_forget(_Ctx(principal), "unicorns")
    assert not out.success


async def test_tools_refuse_without_a_principal_or_when_off(store, principal):
    out = await tools.memory_recall(types.SimpleNamespace(deps=None), "profile.md")
    assert not out.success and "no authenticated user" in out.error
    store.enabled_flag = False
    out = await tools.memory_remember(_Ctx(principal), "x y z")
    assert not out.success and "turned off" in out.error


def test_stream_payloads_for_memory_tools_are_redacted():
    from app.agents.base_agent import BaseAgent

    data = {"success": True, "path": "profile.md", "content": "secret stuff", "error": None}
    assert BaseAgent._stream_safe_result_data("memory_recall", data) == {
        "success": True, "path": "profile.md", "error": None, "redacted": True,
    }
    assert BaseAgent._stream_safe_result_data("web_search", data) == data
