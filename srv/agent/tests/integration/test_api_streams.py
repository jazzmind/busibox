"""
Integration tests for /streams SSE endpoints.
"""

import asyncio
import json
import uuid
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.main import app
from app.models.domain import AgentDefinition, RunRecord


@pytest.fixture
async def test_agent(test_session):
    """Create a test agent definition."""
    agent = AgentDefinition(
        name="test-agent",
        display_name="Test Agent",
        model="agent",
        instructions="Test instructions",
        is_active=True,
    )
    test_session.add(agent)
    await test_session.commit()
    await test_session.refresh(agent)
    return agent


@pytest.fixture
async def test_run(test_session, test_agent, mock_principal):
    """Create a test run record."""
    run_record = RunRecord(
        agent_id=test_agent.id,
        status="pending",
        input={"prompt": "test"},
        events=[],
        created_by=mock_principal.sub,
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)
    return run_record


@pytest.mark.asyncio
async def test_stream_run_not_found(test_session, mock_principal):
    """Test GET /streams/runs/{run_id} returns 404 for non-existent run."""
    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/streams/runs/{uuid.uuid4()}")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_run_access_denied(test_session, test_agent):
    """Test GET /streams/runs/{run_id} returns 403 for unauthorized access."""
    # Create run owned by different user
    run_record = RunRecord(
        agent_id=test_agent.id,
        status="pending",
        input={"prompt": "test"},
        created_by="other-user",
    )
    test_session.add(run_record)
    await test_session.commit()
    await test_session.refresh(run_record)

    # Mock principal as different user without admin role
    from app.schemas.auth import Principal

    other_principal = Principal(sub="requesting-user", roles=[], scopes=[], token="test")

    with patch("app.api.streams.get_principal", return_value=other_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            response = await client.get(f"/streams/runs/{run_record.id}")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_stream_run_emits_status_changes(test_session, test_run, mock_principal):
    """Test SSE stream emits status change events."""
    events_received = []

    async def update_run_status():
        """Update run status after a short delay."""
        await asyncio.sleep(0.2)
        test_run.status = "running"
        test_session.add(test_run)
        await test_session.commit()
        
        await asyncio.sleep(0.2)
        test_run.status = "succeeded"
        test_run.output = {"message": "done"}
        test_session.add(test_run)
        await test_session.commit()

    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            # Start background task to update run
            update_task = asyncio.create_task(update_run_status())
            
            try:
                async with client.stream("GET", f"/streams/runs/{test_run.id}") as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
                    
                    # Collect events (with timeout)
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            events_received.append({"event": event_type, "data": data})
                            
                            # Stop after complete event
                            if event_type == "complete":
                                break
            finally:
                await update_task

    # Verify we received status events
    status_events = [e for e in events_received if e["event"] == "status"]
    assert len(status_events) >= 2
    
    # Verify status progression
    statuses = [e["data"]["status"] for e in status_events]
    assert "running" in statuses
    assert "succeeded" in statuses
    
    # Verify complete event
    complete_events = [e for e in events_received if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_stream_run_emits_events(test_session, test_run, mock_principal):
    """Test SSE stream emits run events."""
    events_received = []

    async def add_run_events():
        """Add events to run after a short delay."""
        await asyncio.sleep(0.2)
        test_run.events = [
            {"type": "tool_call", "timestamp": "2025-01-01T00:00:00Z", "data": {"tool": "search"}}
        ]
        test_run.status = "running"
        test_session.add(test_run)
        await test_session.commit()
        
        await asyncio.sleep(0.2)
        test_run.events.append(
            {"type": "completion", "timestamp": "2025-01-01T00:00:05Z"}
        )
        test_run.status = "succeeded"
        test_run.output = {"message": "done"}
        test_session.add(test_run)
        await test_session.commit()

    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            update_task = asyncio.create_task(add_run_events())
            
            try:
                async with client.stream("GET", f"/streams/runs/{test_run.id}") as response:
                    assert response.status_code == 200
                    
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            events_received.append({"event": event_type, "data": data})
                            
                            if event_type == "complete":
                                break
            finally:
                await update_task

    # Verify we received event emissions
    event_emissions = [e for e in events_received if e["event"] == "event"]
    assert len(event_emissions) >= 2
    
    # Verify event types
    event_types = [e["data"]["type"] for e in event_emissions]
    assert "tool_call" in event_types
    assert "completion" in event_types


@pytest.mark.asyncio
async def test_stream_run_emits_output(test_session, test_run, mock_principal):
    """Test SSE stream emits final output."""
    events_received = []

    async def complete_run():
        """Complete run with output."""
        await asyncio.sleep(0.2)
        test_run.status = "succeeded"
        test_run.output = {"message": "Test response", "tool_calls": []}
        test_session.add(test_run)
        await test_session.commit()

    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            update_task = asyncio.create_task(complete_run())
            
            try:
                async with client.stream("GET", f"/streams/runs/{test_run.id}") as response:
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            events_received.append({"event": event_type, "data": data})
                            
                            if event_type == "complete":
                                break
            finally:
                await update_task

    # Verify we received output event
    output_events = [e for e in events_received if e["event"] == "output"]
    assert len(output_events) == 1
    assert output_events[0]["data"]["message"] == "Test response"


@pytest.mark.asyncio
async def test_stream_run_terminates_on_failure(test_session, test_run, mock_principal):
    """Test SSE stream terminates when run fails."""
    events_received = []

    async def fail_run():
        """Fail the run."""
        await asyncio.sleep(0.2)
        test_run.status = "failed"
        test_run.output = {"error": "Test error", "error_type": "TestError"}
        test_session.add(test_run)
        await test_session.commit()

    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            update_task = asyncio.create_task(fail_run())
            
            try:
                async with client.stream("GET", f"/streams/runs/{test_run.id}") as response:
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            events_received.append({"event": event_type, "data": data})
                            
                            if event_type == "complete":
                                break
            finally:
                await update_task

    # Verify stream terminated with failed status
    complete_events = [e for e in events_received if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["status"] == "failed"
    
    # Verify output contains error
    output_events = [e for e in events_received if e["event"] == "output"]
    assert len(output_events) == 1
    assert "error" in output_events[0]["data"]


@pytest.mark.asyncio
async def test_stream_run_terminates_on_timeout(test_session, test_run, mock_principal):
    """Test SSE stream terminates when run times out."""
    events_received = []

    async def timeout_run():
        """Timeout the run."""
        await asyncio.sleep(0.2)
        test_run.status = "timeout"
        test_run.output = {"error": "Execution timeout", "timeout": 30}
        test_session.add(test_run)
        await test_session.commit()

    with patch("app.api.streams.get_principal", return_value=mock_principal):
        async with AsyncClient(app=app, base_url="http://test") as client:
            update_task = asyncio.create_task(timeout_run())
            
            try:
                async with client.stream("GET", f"/streams/runs/{test_run.id}") as response:
                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            event_type = line.split(":", 1)[1].strip()
                        elif line.startswith("data:"):
                            data = json.loads(line.split(":", 1)[1].strip())
                            events_received.append({"event": event_type, "data": data})
                            
                            if event_type == "complete":
                                break
            finally:
                await update_task

    # Verify stream terminated with timeout status
    complete_events = [e for e in events_received if e["event"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["status"] == "timeout"








