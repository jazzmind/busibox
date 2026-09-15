"""
Append-only event log for chat turns, with replay.

Every SSE frame a turn produces is appended here under the turn id before it
reaches any client. A client that connects late — or reconnects after a
laptop sleep — asks for "everything after event N" and gets the backlog
followed by live events, so the browser is a subscriber rather than the
thing that keeps the turn alive.

Backends
--------
- ``RedisEventLog`` — Redis Streams (``XADD`` / ``XREAD BLOCK``). Survives
  agent-api restarts for ``chat_turn_event_ttl_seconds`` and is shared if the
  API ever runs more than one process.
- ``MemoryEventLog`` — per-process fallback used when Redis is unreachable
  (and in unit tests). Same interface; replay works for the life of the
  process.

Event ids are Redis stream ids (``"1726400000000-0"``) or, in memory,
monotonically increasing integers rendered as strings; clients treat them as
opaque cursors.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, List, Optional

logger = logging.getLogger(__name__)

TERMINAL_EVENT = "turn_finished"
_POLL_SECONDS = 0.5


@dataclass
class LoggedEvent:
    id: str
    type: str
    data: str  # JSON text, exactly as it will be sent in the SSE `data:` line

    def sse(self) -> str:
        return f"id: {self.id}\nevent: {self.type}\ndata: {self.data}\n\n"


class EventLog:
    """Interface. ``append`` returns the new event id; ``read`` yields events
    after ``after`` and keeps following until the terminal event or ``stop``."""

    async def append(self, turn_id: str, event_type: str, data: str) -> str:
        raise NotImplementedError

    async def replay(self, turn_id: str, after: Optional[str] = None) -> List[LoggedEvent]:
        raise NotImplementedError

    async def follow(self, turn_id: str, after: Optional[str], stop: asyncio.Event) -> AsyncIterator[LoggedEvent]:
        raise NotImplementedError

    async def finished(self, turn_id: str) -> bool:
        """True once the terminal event has been appended."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


@dataclass
class _MemoryTurn:
    events: List[LoggedEvent] = field(default_factory=list)
    cond: asyncio.Condition = field(default_factory=asyncio.Condition)
    next_id: int = 1
    finished: bool = False


class MemoryEventLog(EventLog):
    def __init__(self, maxlen: int = 5000):
        self._turns: Dict[str, _MemoryTurn] = {}
        self._maxlen = maxlen

    def _turn(self, turn_id: str) -> _MemoryTurn:
        if turn_id not in self._turns:
            self._turns[turn_id] = _MemoryTurn()
        return self._turns[turn_id]

    async def append(self, turn_id: str, event_type: str, data: str) -> str:
        t = self._turn(turn_id)
        async with t.cond:
            event_id = str(t.next_id)
            t.next_id += 1
            t.events.append(LoggedEvent(id=event_id, type=event_type, data=data))
            if len(t.events) > self._maxlen:
                del t.events[0]  # ids are absolute counters, so cursors stay valid
            if event_type == TERMINAL_EVENT:
                t.finished = True
            t.cond.notify_all()
        return event_id

    async def replay(self, turn_id: str, after: Optional[str] = None) -> List[LoggedEvent]:
        t = self._turns.get(turn_id)
        if t is None:
            return []
        cursor = int(after) if after else 0
        return [e for e in t.events if int(e.id) > cursor]

    async def follow(self, turn_id: str, after: Optional[str], stop: asyncio.Event) -> AsyncIterator[LoggedEvent]:
        t = self._turn(turn_id)
        cursor = int(after) if after else 0
        while not stop.is_set():
            batch = [e for e in t.events if int(e.id) > cursor]
            for e in batch:
                cursor = int(e.id)
                yield e
                if e.type == TERMINAL_EVENT:
                    return
            async with t.cond:
                try:
                    await asyncio.wait_for(t.cond.wait(), timeout=_POLL_SECONDS)
                except asyncio.TimeoutError:
                    pass

    async def finished(self, turn_id: str) -> bool:
        t = self._turns.get(turn_id)
        return bool(t and t.finished)

    def forget(self, turn_id: str) -> None:
        self._turns.pop(turn_id, None)


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------


class RedisEventLog(EventLog):
    def __init__(self, client, ttl_seconds: int = 86400, maxlen: int = 5000):
        self._r = client
        self._ttl = ttl_seconds
        self._maxlen = maxlen

    @staticmethod
    def key(turn_id: str) -> str:
        return f"chat:turn:{turn_id}"

    async def append(self, turn_id: str, event_type: str, data: str) -> str:
        key = self.key(turn_id)
        event_id = await self._r.xadd(key, {"t": event_type, "d": data}, maxlen=self._maxlen, approximate=True)
        # Sliding TTL: a turn stays replayable for ttl seconds after its last event.
        await self._r.expire(key, self._ttl)
        return event_id if isinstance(event_id, str) else event_id.decode()

    async def replay(self, turn_id: str, after: Optional[str] = None) -> List[LoggedEvent]:
        key = self.key(turn_id)
        start = f"({after}" if after else "-"
        rows = await self._r.xrange(key, min=start, max="+")
        return [self._row(rid, fields) for rid, fields in rows]

    async def follow(self, turn_id: str, after: Optional[str], stop: asyncio.Event) -> AsyncIterator[LoggedEvent]:
        key = self.key(turn_id)
        cursor = after or "0-0"
        # Backlog first (fast, no blocking), then block for new entries.
        for e in await self.replay(turn_id, after):
            cursor = e.id
            yield e
            if e.type == TERMINAL_EVENT:
                return
        while not stop.is_set():
            rows = await self._r.xread({key: cursor}, count=200, block=int(_POLL_SECONDS * 1000))
            if not rows:
                continue
            for _key, entries in rows:
                for rid, fields in entries:
                    e = self._row(rid, fields)
                    cursor = e.id
                    yield e
                    if e.type == TERMINAL_EVENT:
                        return

    async def finished(self, turn_id: str) -> bool:
        rows = await self._r.xrevrange(self.key(turn_id), max="+", min="-", count=1)
        if not rows:
            return False
        _rid, fields = rows[0]
        return _field(fields, "t") == TERMINAL_EVENT

    @staticmethod
    def _row(rid, fields) -> LoggedEvent:
        rid = rid if isinstance(rid, str) else rid.decode()
        return LoggedEvent(id=rid, type=_field(fields, "t"), data=_field(fields, "d"))


def _field(fields: dict, name: str) -> str:
    v = fields.get(name)
    if v is None:
        v = fields.get(name.encode())
    if isinstance(v, bytes):
        return v.decode()
    return v or ""


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_log: Optional[EventLog] = None
_lock = asyncio.Lock()


async def get_event_log() -> EventLog:
    """Process-wide event log: Redis when reachable, memory otherwise."""
    global _log
    if _log is not None:
        return _log
    async with _lock:
        if _log is not None:
            return _log
        from app.config.settings import get_settings

        settings = get_settings()
        ttl = int(getattr(settings, "chat_turn_event_ttl_seconds", 86400))
        maxlen = int(getattr(settings, "chat_turn_event_maxlen", 5000))
        url = os.getenv("REDIS_URL") or getattr(settings, "redis_url", None)
        if url:
            try:
                import redis.asyncio as aioredis  # type: ignore

                client = aioredis.from_url(url, decode_responses=True)
                await asyncio.wait_for(client.ping(), timeout=3)
                _log = RedisEventLog(client, ttl_seconds=ttl, maxlen=maxlen)
                logger.info("chat turn event log: Redis at %s", url.split("@")[-1])
                return _log
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat turn event log: Redis unavailable (%s); using in-process memory", exc)
        _log = MemoryEventLog(maxlen=maxlen)
        return _log


def set_event_log(log: Optional[EventLog]) -> None:
    """Test hook."""
    global _log
    _log = log
