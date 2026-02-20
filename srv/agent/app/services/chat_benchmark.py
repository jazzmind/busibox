"""
Benchmark and A/B helpers for chat assistant optimization.
"""

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List


@dataclass
class ChatRunMetrics:
    """Structured metrics for a single chat execution."""

    label: str
    time_to_first_response_ms: float
    time_to_plan_ms: float
    total_latency_ms: float
    event_count: int

    def as_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "time_to_first_response_ms": round(self.time_to_first_response_ms, 2),
            "time_to_plan_ms": round(self.time_to_plan_ms, 2),
            "total_latency_ms": round(self.total_latency_ms, 2),
            "event_count": self.event_count,
        }


def compare_chat_runs(baseline: ChatRunMetrics, candidate: ChatRunMetrics) -> Dict[str, Any]:
    """Compare baseline and candidate metrics for A/B-style evaluations."""
    def pct_change(before: float, after: float) -> float:
        if before == 0:
            return 0.0
        return ((after - before) / before) * 100.0

    return {
        "baseline": baseline.as_dict(),
        "candidate": candidate.as_dict(),
        "delta_ms": {
            "time_to_first_response_ms": round(candidate.time_to_first_response_ms - baseline.time_to_first_response_ms, 2),
            "time_to_plan_ms": round(candidate.time_to_plan_ms - baseline.time_to_plan_ms, 2),
            "total_latency_ms": round(candidate.total_latency_ms - baseline.total_latency_ms, 2),
        },
        "delta_pct": {
            "time_to_first_response_ms": round(pct_change(baseline.time_to_first_response_ms, candidate.time_to_first_response_ms), 2),
            "time_to_plan_ms": round(pct_change(baseline.time_to_plan_ms, candidate.time_to_plan_ms), 2),
            "total_latency_ms": round(pct_change(baseline.total_latency_ms, candidate.total_latency_ms), 2),
        },
    }


async def benchmark_chat_flow(
    label: str,
    runner: Callable[[], Awaitable[List[Dict[str, Any]]]],
) -> ChatRunMetrics:
    """
    Run a benchmarked chat flow and extract standard latency metrics.

    The runner must return an ordered list of streamed events where each event has
    at least the keys: type and timestamp_ms (relative to start).
    """
    started = time.monotonic()
    events = await runner()
    total_latency_ms = (time.monotonic() - started) * 1000.0

    first_response = next(
        (event for event in events if event.get("type") in {"content", "interim"}),
        None,
    )
    first_plan = next((event for event in events if event.get("type") == "plan"), None)

    return ChatRunMetrics(
        label=label,
        time_to_first_response_ms=float(first_response.get("timestamp_ms", total_latency_ms) if first_response else total_latency_ms),
        time_to_plan_ms=float(first_plan.get("timestamp_ms", total_latency_ms) if first_plan else total_latency_ms),
        total_latency_ms=float(total_latency_ms),
        event_count=len(events),
    )

