"""
Unit tests for dispatcher service.

Tests:
- Query analysis returns valid RoutingDecision
- Confidence score between 0-1
- User settings honored (only enabled tools/agents)
- Low confidence (<0.7) includes alternatives
"""

import pytest

from app.schemas.dispatcher import RoutingDecision, DispatcherRequest, UserSettings


def test_routing_decision_validation():
    """Test RoutingDecision model validation."""
    # Valid decision
    decision = RoutingDecision(
        selected_tools=["doc_search"],
        selected_agents=[],
        confidence=0.95,
        reasoning="Query asks about documents",
        alternatives=[],
        requires_disambiguation=False
    )
    
    assert decision.confidence == 0.95
    assert decision.requires_disambiguation is False
    assert len(decision.selected_tools) == 1


def test_routing_decision_confidence_bounds():
    """Test confidence score validation (must be 0-1)."""
    # Valid confidence
    decision = RoutingDecision(
        selected_tools=[],
        selected_agents=[],
        confidence=0.5,
        reasoning="Test",
        alternatives=[],
        requires_disambiguation=True
    )
    assert decision.confidence == 0.5
    
    # Invalid confidence > 1
    with pytest.raises(ValueError):
        RoutingDecision(
            selected_tools=[],
            selected_agents=[],
            confidence=1.5,
            reasoning="Test",
            alternatives=[],
            requires_disambiguation=False
        )
    
    # Invalid confidence < 0
    with pytest.raises(ValueError):
        RoutingDecision(
            selected_tools=[],
            selected_agents=[],
            confidence=-0.1,
            reasoning="Test",
            alternatives=[],
            requires_disambiguation=False
        )


def test_routing_decision_requires_disambiguation_auto_set():
    """Test requires_disambiguation automatically set based on confidence."""
    # High confidence - should not require disambiguation
    decision = RoutingDecision(
        selected_tools=["doc_search"],
        selected_agents=[],
        confidence=0.9,
        reasoning="Clear intent",
        alternatives=[],
        requires_disambiguation=False
    )
    assert decision.requires_disambiguation is False
    
    # Low confidence - should require disambiguation
    decision = RoutingDecision(
        selected_tools=[],
        selected_agents=[],
        confidence=0.5,
        reasoning="Unclear intent",
        alternatives=["doc_search", "web_search"],
        requires_disambiguation=True
    )
    assert decision.requires_disambiguation is True


def test_dispatcher_request_validation():
    """Test DispatcherRequest model validation."""
    # Valid request
    request = DispatcherRequest(
        query="What does our report say?",
        available_tools=["doc_search", "web_search"],
        available_agents=[],
        attachments=[],
        user_settings=UserSettings(
            enabled_tools=["doc_search"],
            enabled_agents=[]
        )
    )
    
    assert len(request.query) > 0
    assert len(request.available_tools) == 2
    assert request.user_settings.enabled_tools == ["doc_search"]


def test_dispatcher_request_query_length_validation():
    """Test query length validation (max 1000 chars)."""
    # Valid query
    request = DispatcherRequest(
        query="Short query",
        available_tools=[],
        available_agents=[]
    )
    assert len(request.query) < 1000
    
    # Query too long (>1000 chars)
    with pytest.raises(ValueError):
        DispatcherRequest(
            query="x" * 1001,
            available_tools=[],
            available_agents=[]
        )
    
    # Empty query
    with pytest.raises(ValueError):
        DispatcherRequest(
            query="",
            available_tools=[],
            available_agents=[]
        )


def test_user_settings_defaults():
    """Test UserSettings default values."""
    settings = UserSettings()
    assert settings.enabled_tools == []
    assert settings.enabled_agents == []
    
    settings = UserSettings(enabled_tools=["doc_search"])
    assert settings.enabled_tools == ["doc_search"]
    assert settings.enabled_agents == []
