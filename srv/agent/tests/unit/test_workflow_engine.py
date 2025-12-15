"""
Unit tests for workflow execution engine.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workflows.engine import (
    WorkflowExecutionError,
    _add_workflow_event,
    _resolve_args,
    _resolve_value,
    validate_workflow_steps,
)
from app.models.domain import RunRecord


def test_resolve_value_literal():
    """Test _resolve_value with literal values."""
    context = {"input": {"path": "/doc.pdf"}}
    
    assert _resolve_value("literal", context) == "literal"
    assert _resolve_value(123, context) == 123
    assert _resolve_value(True, context) is True


def test_resolve_value_jsonpath():
    """Test _resolve_value with JSONPath references."""
    context = {
        "input": {"path": "/doc.pdf", "metadata": {"author": "test"}},
        "step1": {"document_id": "doc123"},
    }
    
    assert _resolve_value("$.input.path", context) == "/doc.pdf"
    assert _resolve_value("$.input.metadata.author", context) == "test"
    assert _resolve_value("$.step1.document_id", context) == "doc123"


def test_resolve_value_missing_path():
    """Test _resolve_value returns None for missing paths."""
    context = {"input": {"path": "/doc.pdf"}}
    
    assert _resolve_value("$.missing.field", context) is None
    assert _resolve_value("$.input.missing", context) is None


def test_resolve_args():
    """Test _resolve_args resolves all arguments."""
    context = {
        "input": {"query": "test query"},
        "step1": {"doc_id": "doc123"},
    }
    
    args = {
        "query": "$.input.query",
        "doc_id": "$.step1.doc_id",
        "top_k": 5,
        "metadata": {"source": "workflow"},
    }
    
    resolved = _resolve_args(args, context)
    
    assert resolved["query"] == "test query"
    assert resolved["doc_id"] == "doc123"
    assert resolved["top_k"] == 5
    assert resolved["metadata"] == {"source": "workflow"}


def test_resolve_args_nested():
    """Test _resolve_args handles nested dicts."""
    context = {"input": {"value": "test"}}
    
    args = {
        "nested": {
            "field1": "$.input.value",
            "field2": "literal",
        }
    }
    
    resolved = _resolve_args(args, context)
    
    assert resolved["nested"]["field1"] == "test"
    assert resolved["nested"]["field2"] == "literal"


def test_resolve_args_with_lists():
    """Test _resolve_args handles lists."""
    context = {"input": {"id": "123"}}
    
    args = {
        "ids": ["$.input.id", "456", "789"]
    }
    
    resolved = _resolve_args(args, context)
    
    assert resolved["ids"] == ["123", "456", "789"]


def test_add_workflow_event():
    """Test _add_workflow_event adds event to run record."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="running",
        input={},
        output={},
        created_by="test",
        events=[],
    )
    
    _add_workflow_event(
        run_record,
        step_id="step1",
        event_type="step_started",
        data={"type": "tool"},
    )
    
    assert len(run_record.events) == 1
    event = run_record.events[0]
    assert event["step_id"] == "step1"
    assert event["type"] == "step_started"
    assert event["data"] == {"type": "tool"}
    assert "timestamp" in event


def test_add_workflow_event_with_error():
    """Test _add_workflow_event includes error message."""
    run_record = RunRecord(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        status="running",
        input={},
        output={},
        created_by="test",
        events=[],
    )
    
    _add_workflow_event(
        run_record,
        step_id="step1",
        event_type="step_failed",
        error="Tool execution failed",
    )
    
    assert len(run_record.events) == 1
    event = run_record.events[0]
    assert event["error"] == "Tool execution failed"


def test_validate_workflow_steps_valid():
    """Test validate_workflow_steps accepts valid workflows."""
    # Tool step
    steps = [
        {"id": "search", "type": "tool", "tool": "search", "args": {"query": "test"}}
    ]
    validate_workflow_steps(steps)  # Should not raise
    
    # Agent step
    steps = [
        {"id": "analyze", "type": "agent", "agent": "analyzer", "input": "test"}
    ]
    validate_workflow_steps(steps)  # Should not raise
    
    # Multiple steps
    steps = [
        {"id": "ingest", "type": "tool", "tool": "ingest", "args": {"path": "/doc.pdf"}},
        {"id": "analyze", "type": "agent", "agent": "analyzer", "input": "$.ingest.doc_id"},
    ]
    validate_workflow_steps(steps)  # Should not raise


def test_validate_workflow_steps_empty():
    """Test validate_workflow_steps rejects empty workflow."""
    with pytest.raises(ValueError, match="must have at least one step"):
        validate_workflow_steps([])


def test_validate_workflow_steps_missing_id():
    """Test validate_workflow_steps rejects steps without ID."""
    steps = [
        {"type": "tool", "tool": "search"}  # Missing id
    ]
    
    with pytest.raises(ValueError, match="missing required field: id"):
        validate_workflow_steps(steps)


def test_validate_workflow_steps_missing_type():
    """Test validate_workflow_steps rejects steps without type."""
    steps = [
        {"id": "step1", "tool": "search"}  # Missing type
    ]
    
    with pytest.raises(ValueError, match="missing required field: type"):
        validate_workflow_steps(steps)


def test_validate_workflow_steps_invalid_type():
    """Test validate_workflow_steps rejects invalid step types."""
    steps = [
        {"id": "step1", "type": "invalid_type"}
    ]
    
    with pytest.raises(ValueError, match="invalid type: invalid_type"):
        validate_workflow_steps(steps)


def test_validate_workflow_steps_duplicate_ids():
    """Test validate_workflow_steps rejects duplicate step IDs."""
    steps = [
        {"id": "step1", "type": "tool", "tool": "search"},
        {"id": "step1", "type": "tool", "tool": "ingest"},  # Duplicate
    ]
    
    with pytest.raises(ValueError, match="Duplicate step ID: step1"):
        validate_workflow_steps(steps)


def test_validate_workflow_steps_tool_missing_tool_field():
    """Test validate_workflow_steps rejects tool steps without tool field."""
    steps = [
        {"id": "step1", "type": "tool", "args": {}}  # Missing tool
    ]
    
    with pytest.raises(ValueError, match="missing required field: tool"):
        validate_workflow_steps(steps)


def test_validate_workflow_steps_agent_missing_agent_field():
    """Test validate_workflow_steps rejects agent steps without agent field."""
    steps = [
        {"id": "step1", "type": "agent", "input": "test"}  # Missing agent
    ]
    
    with pytest.raises(ValueError, match="missing required field: agent"):
        validate_workflow_steps(steps)





