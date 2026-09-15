"""Unit tests for the chat-turn event log (services/turn_events.py).

MemoryEventLog is exercised directly; RedisEventLog is exercised against a
small in-process fake of the four stream commands it uses, so the id /
cursor / decoding logic is covered without a Redis server.
"""

from __future__ import annotations

import asyncio
from typing import Dict, List, Tuple

import pytest

from app.services.turn_events import (
    TERMINAL_EVENT,
    LoggedEvent,
    MemoryEventLog,
    RedisEventLog,
    get_event_log,
    set_event_log,
)


async def _collect(agen, limit: int = 100, timeout: float = 3.0) -> List[LoggedEvent]:
    out: List[LoggedEvent] = []

    async def _drain():
        async for e in agen:
            out.append(e)
            if len(out) >= limit:
                break

    await asyncio.wait_for(_drain(), timeout=timeout)
    return out


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------


def test_sse_frame_carries_id_event_and_data():
    frame = LoggedEvent(id="42", type="content", data='{"message":"hi"}').sse()
    assert frame == 'id: 42\nevent: content\ndata: {"message":"hi"}\n\n'


# ---------------------------------------------------------------------------
# MemoryEventLog
# ---------------------------------------------------------------------------


async def test_memory_append_returns_monotonic_ids_and_replay_honours_cursor():
    log = MemoryEventLog()
    ids = [await log.append("t1", "content", f'{{"n":{i}}}') for i in range(3)]
    assert ids == ["1", "2", "3"]

    everything = await log.replay("t1")
    assert [e.id for e in everything] == ["1", "2", "3"]
    assert everything[0].type == "content" and everything[0].data == '{"n":0}'

    after_two = await log.replay("t1", after="2")
    assert [e.id for e in after_two] == ["3"]
    assert await log.replay("unknown-turn") == []


async def test_memory_follow_yields_backlog_then_live_and_stops_at_terminal():
    log = MemoryEventLog()
    await log.append("t1", "turn_started", "{}")
    await log.append("t1", "content", '{"a":1}')

    stop = asyncio.Event()
    seen: List[Tuple[str, str]] = []

    async def _follower():
        async for e in log.follow("t1", None, stop):
            seen.append((e.id, e.type))

    task = asyncio.create_task(_follower())
    await asyncio.sleep(0.05)  # backlog delivered, now blocked waiting
    assert seen == [("1", "turn_started"), ("2", "content")]

    await log.append("t1", "content", '{"a":2}')
    await log.append("t1", TERMINAL_EVENT, '{"status":"completed"}')
    await asyncio.wait_for(task, timeout=3)

    assert [t for _, t in seen] == ["turn_started", "content", "content", TERMINAL_EVENT]
    assert await log.finished("t1") is True
    assert await log.finished("t2") is False


async def test_memory_follow_from_cursor_skips_already_seen_events():
    log = MemoryEventLog()
    for i in range(4):
        await log.append("t1", "content", str(i))
    await log.append("t1", TERMINAL_EVENT, "{}")

    events = await _collect(log.follow("t1", "2", asyncio.Event()))
    assert [e.id for e in events] == ["3", "4", "5"]


async def test_memory_follow_exits_when_stop_is_set_without_terminal():
    log = MemoryEventLog()
    await log.append("t1", "content", "x")
    stop = asyncio.Event()

    async def _follower():
        return [e.id async for e in log.follow("t1", None, stop)]

    task = asyncio.create_task(_follower())
    await asyncio.sleep(0.05)
    stop.set()
    assert await asyncio.wait_for(task, timeout=3) == ["1"]


async def test_memory_maxlen_trims_oldest_but_ids_stay_absolute():
    log = MemoryEventLog(maxlen=3)
    for i in range(5):
        await log.append("t1", "content", str(i))
    kept = await log.replay("t1")
    assert [e.id for e in kept] == ["3", "4", "5"]
    # A cursor that predates the trim still works: it simply gets what is left.
    assert [e.id for e in await log.replay("t1", after="1")] == ["3", "4", "5"]


async def test_memory_forget_drops_turn():
    log = MemoryEventLog()
    await log.append("t1", TERMINAL_EVENT, "{}")
    log.forget("t1")
    assert await log.replay("t1") == []
    assert await log.finished("t1") is False


# ---------------------------------------------------------------------------
# RedisEventLog against a fake client
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Just enough of redis.asyncio for RedisEventLog: XADD / EXPIRE /
    XRANGE / XREAD / XREVRANGE with real stream-id semantics (ms-seq)."""

    def __init__(self, decode: bool = True):
        self.streams: Dict[str, List[Tuple[str, dict]]] = {}
        self.ttls: Dict[str, int] = {}
        self._seq = 0
        self.decode = decode

    def _wrap(self, v: str):
        return v if self.decode else v.encode()

    async def xadd(self, key, fields, maxlen=None, approximate=True):
        self._seq += 1
        rid = f"1700000000000-{self._seq}"
        stream = self.streams.setdefault(key, [])
        stream.append((rid, dict(fields)))
        if maxlen and len(stream) > maxlen:
            del stream[: len(stream) - maxlen]
        return self._wrap(rid)

    async def expire(self, key, ttl):
        self.ttls[key] = ttl
        return True

    @staticmethod
    def _key(rid: str) -> Tuple[int, int]:
        ms, seq = rid.split("-")
        return int(ms), int(seq)

    def _rows(self, key):
        return [(self._wrap(rid), {self._wrap(k): self._wrap(v) for k, v in f.items()}) for rid, f in self.streams.get(key, [])]

    async def xrange(self, key, min="-", max="+"):
        rows = self.streams.get(key, [])
        if min == "-":
            return self._rows_from(rows, None, exclusive=False)
        exclusive = min.startswith("(")
        return self._rows_from(rows, min.lstrip("("), exclusive=exclusive)

    def _rows_from(self, rows, start, exclusive):
        out = []
        for rid, f in rows:
            if start is not None:
                a, b = self._key(rid), self._key(start)
                if a < b or (exclusive and a == b):
                    continue
            out.append((self._wrap(rid), {self._wrap(k): self._wrap(v) for k, v in f.items()}))
        return out

    async def xread(self, streams, count=None, block=None):
        result = []
        for key, cursor in streams.items():
            rows = self._rows_from(self.streams.get(key, []), cursor, exclusive=True)
            if rows:
                result.append((self._wrap(key), rows[:count] if count else rows))
        if not result and block:
            await asyncio.sleep(min(block, 20) / 1000)
        return result

    async def xrevrange(self, key, max="+", min="-", count=None):
        rows = self._rows(key)
        rows.reverse()
        return rows[:count] if count else rows

    async def ping(self):
        return True


@pytest.mark.parametrize("decode", [True, False])
async def test_redis_append_replay_follow_and_finished(decode):
    fake = _FakeRedis(decode=decode)
    log = RedisEventLog(fake, ttl_seconds=123, maxlen=100)

    first = await log.append("abc", "turn_started", "{}")
    second = await log.append("abc", "content", '{"m":"a"}')
    assert isinstance(first, str) and first.endswith("-1") and second.endswith("-2")
    assert fake.ttls[RedisEventLog.key("abc")] == 123  # sliding TTL set on every append
    assert RedisEventLog.key("abc") == "chat:turn:abc"

    everything = await log.replay("abc")
    assert [(e.type, e.data) for e in everything] == [("turn_started", "{}"), ("content", '{"m":"a"}')]
    assert all(isinstance(e.id, str) for e in everything)

    # Cursor is exclusive: replay after `first` returns only the second event.
    assert [e.id for e in await log.replay("abc", after=first)] == [second]
    assert await log.finished("abc") is False

    # follow(): backlog, then live events, ends on the terminal event.
    stop = asyncio.Event()
    seen: List[str] = []

    async def _follower():
        async for e in log.follow("abc", None, stop):
            seen.append(e.type)

    task = asyncio.create_task(_follower())
    await asyncio.sleep(0.05)
    await log.append("abc", "content", '{"m":"b"}')
    await log.append("abc", TERMINAL_EVENT, '{"status":"completed"}')
    await asyncio.wait_for(task, timeout=3)
    assert seen == ["turn_started", "content", "content", TERMINAL_EVENT]
    assert await log.finished("abc") is True


async def test_redis_follow_resumes_from_cursor():
    fake = _FakeRedis()
    log = RedisEventLog(fake)
    ids = [await log.append("t", "content", str(i)) for i in range(3)]
    await log.append("t", TERMINAL_EVENT, "{}")
    events = await _collect(log.follow("t", ids[0], asyncio.Event()))
    assert [e.data for e in events] == ["1", "2", "{}"]


async def test_redis_maxlen_is_passed_to_xadd():
    fake = _FakeRedis()
    log = RedisEventLog(fake, maxlen=2)
    for i in range(4):
        await log.append("t", "content", str(i))
    assert [e.data for e in await log.replay("t")] == ["2", "3"]


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


async def test_set_event_log_overrides_factory():
    mem = MemoryEventLog()
    set_event_log(mem)
    try:
        assert await get_event_log() is mem
    finally:
        set_event_log(None)
