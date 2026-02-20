"""Tests for chat optimization planning/streaming infrastructure."""

import asyncio
from typing import List

import pytest

from app.agents.chat_agent import (
    AgentContext,
    ChatAgent,
    ExecutionPlan,
    FastAckDecision,
    FeedbackPoint,
    PlanStep,
)
from app.schemas.streaming import StreamEvent
from app.services.chat_benchmark import ChatRunMetrics, benchmark_chat_flow, compare_chat_runs


class StreamCollector:
    def __init__(self):
        self.events: List[StreamEvent] = []

    async def __call__(self, event: StreamEvent):
        self.events.append(event)


@pytest.mark.asyncio
async def test_generate_plan_backfills_required_query_for_document_search(monkeypatch):
    """Planner output missing query should be normalized for document_search."""
    agent = ChatAgent()
    context = AgentContext()

    class FakeClient:
        async def chat_completion(self, **kwargs):
            return {
                "choices": [{
                    "message": {
                        "content": (
                            '{"summary":"Search docs","steps":[{"id":"step_1","tool":"document_search",'
                            '"objective":"Search user docs","run_mode":"serial","args":{"limit":5}}],'
                            '"parallel_groups":[],"feedback_points":[],"estimated_duration":"quick"}'
                        )
                    }
                }]
            }

    monkeypatch.setattr("app.agents.chat_agent.get_client", lambda: FakeClient())
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.has", lambda name: name == "document_search")
    monkeypatch.setattr("app.agents.chat_agent.ToolRegistry.get", lambda name: (lambda query, limit=5: None) if name == "document_search" else None)

    plan = await agent._generate_plan(
        query="do I have any resumes of people who are good at data analytics?",
        context=context,
        dispatch=FastAckDecision(action_type="search", needs_tools=True, response="Checking now."),
    )

    assert plan.steps
    assert plan.steps[0].tool == "document_search"
    assert plan.steps[0].args.get("query") == "do I have any resumes of people who are good at data analytics?"
    assert plan.steps[0].args.get("limit") == 5


@pytest.mark.asyncio
async def test_chat_agent_streams_plan_progress_and_interim(monkeypatch):
    """The two-phase flow should emit plan/progress/interim events before final content."""
    agent = ChatAgent()
    collector = StreamCollector()
    cancel = asyncio.Event()
    context = AgentContext()

    async def fake_setup_context(raw_context, stream, query):
        return context

    async def fake_resolve_attachments(query, stream, ctx):
        return None

    async def fake_fast_ack(query, ctx):
        return FastAckDecision(
            action_type="research",
            needs_tools=True,
            response="Got it - I'll gather the facts first.",
            confidence=0.9,
        )

    async def fake_plan(query, ctx, dispatch):
        return ExecutionPlan(
            summary="I will research then synthesize.",
            steps=[PlanStep(id="step_1", tool="web_search", objective="Search the web", args={"query": query})],
            parallel_groups=[],
            feedback_points=[FeedbackPoint(after_step_id="step_1", message="I found initial sources.", kind="interim")],
            estimated_duration="quick",
        )

    async def fake_execute_plan(query, stream, cancel, ctx, execution_plan):
        ctx.tool_results["web_search"] = {"result_count": 1, "context": "Synthetic source context"}
        await stream(
            StreamEvent(
                type="progress",
                source=agent.name,
                message="Completed 1/1: Search the web",
                data={"completed": 1, "total": 1},
            )
        )
        await stream(
            StreamEvent(
                type="interim",
                source=agent.name,
                message="I found initial sources.",
                data={"kind": "interim"},
            )
        )

    async def fake_execute_llm_driven(query, stream, cancel, ctx):
        ctx.tool_results["llm_response"] = "Final synthesized response."

    async def fake_synthesize(query, stream, cancel, ctx):
        await stream(
            StreamEvent(
                type="content",
                source=agent.name,
                message="Final synthesized response.",
                data={"phase": "synthesis"},
            )
        )
        return "Final synthesized response."

    monkeypatch.setattr(agent, "_setup_context", fake_setup_context)
    monkeypatch.setattr(agent, "_resolve_attachments", fake_resolve_attachments)
    monkeypatch.setattr(agent, "_generate_fast_ack", fake_fast_ack)
    monkeypatch.setattr(agent, "_generate_plan", fake_plan)
    monkeypatch.setattr(agent, "_execute_plan", fake_execute_plan)
    monkeypatch.setattr(agent, "_execute_llm_driven", fake_execute_llm_driven)
    monkeypatch.setattr(agent, "_synthesize", fake_synthesize)

    result = await agent.run_with_streaming(
        query="Research latest AI chips",
        stream=collector,
        cancel=cancel,
        context={},
    )

    event_types = [event.type for event in collector.events]
    assert "plan" in event_types
    assert "progress" in event_types
    assert "interim" in event_types
    assert "content" in event_types
    assert "Final synthesized response." in result


@pytest.mark.asyncio
async def test_benchmark_chat_flow_outputs_latency_metrics():
    """Benchmark helper should return standard optimization metrics."""

    async def fake_runner():
        return [
            {"type": "content", "timestamp_ms": 80},
            {"type": "plan", "timestamp_ms": 120},
            {"type": "progress", "timestamp_ms": 220},
            {"type": "content", "timestamp_ms": 460},
        ]

    metrics = await benchmark_chat_flow("candidate", fake_runner)

    assert metrics.label == "candidate"
    assert metrics.time_to_first_response_ms == 80
    assert metrics.time_to_plan_ms == 120
    assert metrics.event_count == 4
    assert metrics.total_latency_ms >= 0


def test_ab_comparison_framework_returns_deltas():
    """A/B helper should provide absolute and percent deltas."""
    baseline = ChatRunMetrics(
        label="baseline",
        time_to_first_response_ms=300,
        time_to_plan_ms=500,
        total_latency_ms=2200,
        event_count=9,
    )
    candidate = ChatRunMetrics(
        label="candidate",
        time_to_first_response_ms=180,
        time_to_plan_ms=320,
        total_latency_ms=1500,
        event_count=12,
    )
    comparison = compare_chat_runs(baseline, candidate)
    assert comparison["delta_ms"]["time_to_first_response_ms"] == -120
    assert comparison["delta_ms"]["time_to_plan_ms"] == -180
    assert comparison["delta_ms"]["total_latency_ms"] == -700

