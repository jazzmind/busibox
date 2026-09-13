"""
Load monitor for tracking active LLM request counts per model purpose.

Provides load-aware model selection: when the active request count for a
purpose reaches a configurable threshold, callers can switch to the
fallback (cloud) model to avoid overloading the local inference server.

Thread-safe via asyncio primitives — designed for single-process FastAPI.
"""

import asyncio
import logging
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Optional, Set

logger = logging.getLogger(__name__)

# Purposes that share local GPU capacity and should participate in
# load-based fallback.  Non-LLM purposes (embedding, reranking, etc.)
# are excluded.
_LLM_PURPOSES: Set[str] = {
    "agent", "chat", "research", "default", "tool_calling",
    "vision", "parsing", "cleanup", "fast", "test", "classify",
    # Deep-research workers: up to research_max_workers loops at once.
    "research_worker",
}


@dataclass
class PurposeMetrics:
    """Live metrics for a single model purpose."""
    active: int = 0
    total_requests: int = 0
    total_fallbacks: int = 0
    peak_active: int = 0
    last_fallback_time: Optional[float] = None


class LoadMonitor:
    """
    Tracks active LLM request counts per purpose and decides when to
    fall back to a cloud model.

    Usage::

        monitor = LoadMonitor(threshold=6)

        async with monitor.track("agent") as should_fallback:
            model = settings.fallback_model if should_fallback else "agent"
            # ... run the LLM call with `model` ...
    """

    def __init__(self, threshold: int = 6) -> None:
        self._threshold = threshold
        self._lock = asyncio.Lock()
        self._metrics: Dict[str, PurposeMetrics] = defaultdict(PurposeMetrics)

    @property
    def threshold(self) -> int:
        return self._threshold

    @threshold.setter
    def threshold(self, value: int) -> None:
        self._threshold = max(1, value)

    def _total_llm_active(self) -> int:
        """Sum of active requests across all LLM purposes."""
        return sum(
            m.active for p, m in self._metrics.items() if p in _LLM_PURPOSES
        )

    async def acquire(self, purpose: str) -> bool:
        """
        Increment the active count for *purpose*.

        Returns True if the caller should use the fallback model (i.e. the
        total LLM load is at or above the threshold).
        """
        async with self._lock:
            m = self._metrics[purpose]
            m.active += 1
            m.total_requests += 1
            m.peak_active = max(m.peak_active, m.active)

            should_fallback = (
                purpose in _LLM_PURPOSES
                and self._total_llm_active() > self._threshold
            )
            if should_fallback:
                m.total_fallbacks += 1
                m.last_fallback_time = time.monotonic()
                logger.info(
                    "Load fallback triggered for purpose=%s "
                    "(active=%d, threshold=%d, total_llm=%d)",
                    purpose, m.active, self._threshold,
                    self._total_llm_active(),
                )
            return should_fallback

    async def release(self, purpose: str) -> None:
        """Decrement the active count for *purpose*."""
        async with self._lock:
            m = self._metrics[purpose]
            m.active = max(0, m.active - 1)

    def should_fallback(self, purpose: str) -> bool:
        """
        Non-async check: True when current LLM load is at or above threshold.

        Safe to call without await for quick read — slight race is acceptable
        since acquire() makes the authoritative decision.
        """
        if purpose not in _LLM_PURPOSES:
            return False
        return self._total_llm_active() >= self._threshold

    @asynccontextmanager
    async def track(self, purpose: str) -> AsyncIterator[bool]:
        """
        Context manager that acquires on entry and releases on exit.

        Yields True if the caller should use the fallback model.
        """
        should_fb = await self.acquire(purpose)
        try:
            yield should_fb
        finally:
            await self.release(purpose)

    def get_metrics(self) -> Dict[str, any]:
        """Return a snapshot of current load metrics for all tracked purposes."""
        total_llm = self._total_llm_active()
        per_purpose = {}
        for purpose, m in sorted(self._metrics.items()):
            per_purpose[purpose] = {
                "active": m.active,
                "total_requests": m.total_requests,
                "total_fallbacks": m.total_fallbacks,
                "peak_active": m.peak_active,
            }
        return {
            "total_llm_active": total_llm,
            "threshold": self._threshold,
            "fallback_active": total_llm >= self._threshold,
            "purposes": per_purpose,
        }

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        self._metrics.clear()


# Module-level singleton — created once, imported everywhere.
_load_monitor: Optional[LoadMonitor] = None


def get_load_monitor() -> LoadMonitor:
    """
    Return the global LoadMonitor singleton.

    Lazily initializes with the threshold from settings on first call.
    """
    global _load_monitor
    if _load_monitor is None:
        from app.config.settings import get_settings
        settings = get_settings()
        _load_monitor = LoadMonitor(threshold=settings.load_fallback_threshold)
    return _load_monitor


def reset_load_monitor() -> None:
    """Reset the global singleton (for testing)."""
    global _load_monitor
    _load_monitor = None
