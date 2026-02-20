"""Integration coverage for agentic SSE event sequencing."""

import json

import pytest
from httpx import AsyncClient

from app.schemas.streaming import StreamEvent


@pytest.mark.asyncio
async def test_agentic_stream_sequence_includes_plan_progress_and_interim(
    async_client: AsyncClient,
    auth_headers: dict,
    monkeypatch,
):
    async def fake_dispatcher(*args, **kwargs):
        yield StreamEvent(type="thought", source="dispatcher", message="Analyzing")
        yield StreamEvent(type="plan", source="chat-agent", message="Plan ready", data={"steps": []})
        yield StreamEvent(type="progress", source="chat-agent", message="Completed 1/2", data={"completed": 1, "total": 2})
        yield StreamEvent(type="interim", source="chat-agent", message="Found initial results", data={"kind": "interim"})
        yield StreamEvent(type="content", source="chat-agent", message="Final answer")
        yield StreamEvent(type="complete", source="dispatcher", message="Done")

    monkeypatch.setattr("app.services.agentic_dispatcher.run_agentic_dispatcher", fake_dispatcher)

    events = []
    async with async_client.stream(
        "POST",
        "/chat/message/stream/agentic",
        json={"message": "test sequence"},
        headers=auth_headers,
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

        event_type = None
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:") and event_type:
                payload = json.loads(line.split(":", 1)[1].strip())
                events.append((event_type, payload))
                if event_type == "message_complete":
                    break

    event_names = [name for name, _ in events]
    # Ensure the stream includes the new planning/progress primitives before completion.
    assert "plan" in event_names
    assert "progress" in event_names
    assert "interim" in event_names
    assert "content" in event_names
    assert "message_complete" in event_names

