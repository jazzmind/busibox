"""Unit tests for bridge-specific streaming event filtering."""

from app.api.chat import (
    BRIDGE_FILTERED_AGENTIC_EVENTS,
    BRIDGE_FILTERED_STANDARD_EVENTS,
    _is_bridge_request,
)


def test_is_bridge_request_detects_non_empty_bridge_channels() -> None:
    assert _is_bridge_request({"bridge_channels": ["telegram"]}) is True


def test_is_bridge_request_false_without_bridge_channels() -> None:
    assert _is_bridge_request(None) is False
    assert _is_bridge_request({}) is False
    assert _is_bridge_request({"bridge_channels": []}) is False
    assert _is_bridge_request({"bridge_channels": "telegram"}) is False


def test_bridge_filtered_sets_include_thinking_events() -> None:
    assert "thought" in BRIDGE_FILTERED_AGENTIC_EVENTS
    assert "plan" in BRIDGE_FILTERED_AGENTIC_EVENTS
    assert "progress" in BRIDGE_FILTERED_AGENTIC_EVENTS
    assert "tool_start" in BRIDGE_FILTERED_AGENTIC_EVENTS
    assert "tool_result" in BRIDGE_FILTERED_AGENTIC_EVENTS

    assert "planning" in BRIDGE_FILTERED_STANDARD_EVENTS
    assert "tool_start" in BRIDGE_FILTERED_STANDARD_EVENTS
    assert "tool_result" in BRIDGE_FILTERED_STANDARD_EVENTS
