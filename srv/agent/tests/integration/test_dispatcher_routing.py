"""
Integration tests for dispatcher routing (User Story 2).

Tests:
- Document queries route to doc_search with high confidence
- Web queries route to web_search
- Disabled tools are not selected
- No available tools returns confidence=0
- File attachments route to file-capable tools
- Routing accuracy meets 95%+ target (SC-002)
"""

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.schemas.dispatcher import DispatcherRequest


@pytest.fixture
def test_queries() -> dict:
    """Load test query dataset."""
    fixtures_path = Path(__file__).parent.parent / "fixtures" / "dispatcher_queries.json"
    with open(fixtures_path) as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_document_query_routes_to_doc_search(client: AsyncClient, mock_token: str):
    """
    Test: Document query routes to doc_search with high confidence.
    
    Acceptance Scenario 1: Given I have doc_search enabled and ask "What does our Q4 
    report say about revenue?", When the dispatcher analyzes my query, Then it routes 
    to doc_search with high confidence (>0.8).
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "What does our Q4 report say about revenue?",
            "available_tools": ["doc_search", "web_search"],
            "available_agents": [],
            "attachments": [],
            "user_settings": {
                "enabled_tools": ["doc_search", "web_search"],
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    assert "doc_search" in routing["selected_tools"], "Should route to doc_search for document query"
    assert routing["confidence"] > 0.8, f"Confidence should be >0.8, got {routing['confidence']}"
    assert routing["requires_disambiguation"] is False, "High confidence should not require disambiguation"
    assert len(routing["reasoning"]) > 0, "Should provide reasoning"


@pytest.mark.asyncio
async def test_web_query_routes_to_web_search(client: AsyncClient, mock_token: str):
    """
    Test: Web query routes to web_search with reasoning.
    
    Acceptance Scenario 2: Given I have both doc_search and web_search enabled and ask 
    "What's the weather today?", When the dispatcher analyzes my query, Then it routes 
    to web_search with reasoning explaining why.
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "What's the weather today?",
            "available_tools": ["doc_search", "web_search"],
            "available_agents": [],
            "attachments": [],
            "user_settings": {
                "enabled_tools": ["doc_search", "web_search"],
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    assert "web_search" in routing["selected_tools"], "Should route to web_search for weather query"
    assert routing["confidence"] > 0.7, f"Confidence should be >0.7, got {routing['confidence']}"
    assert len(routing["reasoning"]) > 0, "Should provide reasoning"
    assert "weather" in routing["reasoning"].lower() or "web" in routing["reasoning"].lower(), \
        "Reasoning should mention weather or web"


@pytest.mark.asyncio
async def test_disabled_tool_not_selected(client: AsyncClient, mock_token: str):
    """
    Test: Disabled tool is not selected even if relevant.
    
    Acceptance Scenario 3: Given I have doc_search disabled in my settings and ask a 
    document question, When the dispatcher analyzes my query, Then it does not route 
    to doc_search and suggests alternatives.
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "What does our Q4 report say?",
            "available_tools": ["doc_search", "web_search"],
            "available_agents": [],
            "attachments": [],
            "user_settings": {
                "enabled_tools": ["web_search"],  # doc_search disabled
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    assert "doc_search" not in routing["selected_tools"], "Should NOT select disabled doc_search"
    assert len(routing["alternatives"]) > 0, "Should suggest alternatives"


@pytest.mark.asyncio
async def test_no_available_tools_returns_zero_confidence(client: AsyncClient, mock_token: str):
    """
    Test: No available tools returns confidence=0 with empty selections.
    
    Acceptance Scenario (Edge Case): When all tools are disabled, dispatcher returns 
    confidence=0 with explanatory reasoning.
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "Help me analyze this data",
            "available_tools": ["doc_search", "web_search"],
            "available_agents": [],
            "attachments": [],
            "user_settings": {
                "enabled_tools": [],  # All tools disabled
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    assert routing["selected_tools"] == [], "Should have empty selections"
    assert routing["selected_agents"] == [], "Should have empty selections"
    assert routing["confidence"] == 0.0, "Confidence should be exactly 0.0"
    assert routing["requires_disambiguation"] is True, "Should require disambiguation"
    assert len(routing["alternatives"]) > 0, "Should suggest alternatives"
    assert "enable" in routing["reasoning"].lower() or "disabled" in routing["reasoning"].lower(), \
        "Reasoning should explain tools are disabled"


@pytest.mark.asyncio
async def test_low_confidence_includes_alternatives(client: AsyncClient, mock_token: str):
    """
    Test: Low confidence (<0.7) includes alternative suggestions.
    
    Acceptance Scenario 4: Given the dispatcher has low confidence (<0.7) about routing, 
    When it returns the decision, Then it includes alternative suggestions and requires 
    disambiguation.
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "Tell me about AI",  # Ambiguous query
            "available_tools": ["doc_search", "web_search"],
            "available_agents": [],
            "attachments": [],
            "user_settings": {
                "enabled_tools": ["doc_search", "web_search"],
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    # For ambiguous queries, confidence may be low
    if routing["confidence"] < 0.7:
        assert routing["requires_disambiguation"] is True, "Low confidence should require disambiguation"
        assert len(routing["alternatives"]) > 0, "Should provide alternatives"


@pytest.mark.asyncio
async def test_file_attachment_routes_to_file_capable_tool(client: AsyncClient, mock_token: str):
    """
    Test: File attachments route to file-capable tools.
    
    Edge Case: Dispatcher handles queries with file attachments by considering which 
    tools support file processing.
    """
    response = await client.post(
        "/dispatcher/route",
        json={
            "query": "Analyze this document",
            "available_tools": ["doc_search", "ingest"],
            "available_agents": [],
            "attachments": [
                {"name": "report.pdf", "type": "pdf", "url": "s3://bucket/report.pdf"}
            ],
            "user_settings": {
                "enabled_tools": ["doc_search", "ingest"],
                "enabled_agents": []
            }
        },
        headers={"Authorization": f"Bearer {mock_token}"},
    )
    
    assert response.status_code == 200
    data = response.json()
    
    routing = data["routing_decision"]
    # Should route to ingest for document processing
    assert "ingest" in routing["selected_tools"] or "doc_search" in routing["selected_tools"], \
        "Should route to file-capable tool when attachment present"


@pytest.mark.asyncio
async def test_dispatcher_routing_accuracy_on_test_set(
    client: AsyncClient,
    mock_token: str,
    test_queries: dict
):
    """
    Test: Dispatcher achieves 95%+ routing accuracy on test query set.
    
    Success Criterion SC-002: Dispatcher agent achieves 95%+ routing accuracy on test 
    query set covering document search, web search, and multi-tool scenarios.
    """
    queries = test_queries["test_queries"]
    accuracy_target = test_queries["accuracy_target"]
    
    correct_count = 0
    total_count = len(queries)
    
    for test_case in queries:
        response = await client.post(
            "/dispatcher/route",
            json={
                "query": test_case["query"],
                "available_tools": test_case["available_tools"],
                "available_agents": test_case.get("available_agents", []),
                "attachments": test_case.get("attachments", []),
                "user_settings": {
                    "enabled_tools": test_case["enabled_tools"],
                    "enabled_agents": test_case.get("enabled_agents", [])
                }
            },
            headers={"Authorization": f"Bearer {mock_token}"},
        )
        
        assert response.status_code == 200
        data = response.json()
        routing = data["routing_decision"]
        
        # Check if routing matches expected
        expected_tools = set(test_case.get("expected_tools", []))
        selected_tools = set(routing["selected_tools"])
        
        expected_agents = set(test_case.get("expected_agents", []))
        selected_agents = set(routing["selected_agents"])
        
        # Check confidence bounds if specified
        if "expected_confidence_min" in test_case:
            assert routing["confidence"] >= test_case["expected_confidence_min"], \
                f"Test {test_case['id']}: Confidence {routing['confidence']} < {test_case['expected_confidence_min']}"
        
        if "expected_confidence_max" in test_case:
            assert routing["confidence"] <= test_case["expected_confidence_max"], \
                f"Test {test_case['id']}: Confidence {routing['confidence']} > {test_case['expected_confidence_max']}"
        
        if "expected_confidence" in test_case:
            assert routing["confidence"] == test_case["expected_confidence"], \
                f"Test {test_case['id']}: Confidence {routing['confidence']} != {test_case['expected_confidence']}"
        
        # Check if tools match (for non-ambiguous cases)
        if expected_tools and "expected_confidence_min" in test_case and test_case["expected_confidence_min"] >= 0.7:
            if selected_tools == expected_tools:
                correct_count += 1
            else:
                print(f"Test {test_case['id']} FAILED: Expected {expected_tools}, got {selected_tools}")
                print(f"  Query: {test_case['query']}")
                print(f"  Reasoning: {routing['reasoning']}")
        
        # Check if agents match (for agent-specific queries)
        if expected_agents:
            if selected_agents == expected_agents:
                correct_count += 1
            else:
                print(f"Test {test_case['id']} FAILED: Expected agents {expected_agents}, got {selected_agents}")
    
    # Calculate accuracy
    # Only count tests with clear expected outcomes (confidence >= 0.7)
    testable_count = sum(1 for tc in queries if tc.get("expected_confidence_min", 0) >= 0.7 or tc.get("expected_agents"))
    accuracy = correct_count / testable_count if testable_count > 0 else 0.0
    
    print(f"\nDispatcher Routing Accuracy: {accuracy:.2%} ({correct_count}/{testable_count})")
    print(f"Target Accuracy: {accuracy_target:.2%}")
    
    assert accuracy >= accuracy_target, \
        f"Routing accuracy {accuracy:.2%} below target {accuracy_target:.2%}"


@pytest.mark.asyncio
async def test_dispatcher_response_time_under_2_seconds(client: AsyncClient, mock_token: str):
    """
    Test: Dispatcher response time is under 2 seconds for 95% of queries.
    
    Success Criterion SC-003: Dispatcher agent response time is under 2 seconds for 
    95% of queries at expected load (1000 queries/hour, 100-500 concurrent users).
    """
    import time
    
    response_times = []
    
    # Run 20 queries to measure p95 latency
    for i in range(20):
        start = time.time()
        
        response = await client.post(
            "/dispatcher/route",
            json={
                "query": f"Test query {i}: What does our report say?",
                "available_tools": ["doc_search", "web_search"],
                "available_agents": [],
                "attachments": [],
                "user_settings": {
                    "enabled_tools": ["doc_search", "web_search"],
                    "enabled_agents": []
                }
            },
            headers={"Authorization": f"Bearer {mock_token}"},
        )
        
        end = time.time()
        response_time = end - start
        response_times.append(response_time)
        
        assert response.status_code == 200
    
    # Calculate p95 latency
    response_times.sort()
    p95_index = int(len(response_times) * 0.95)
    p95_latency = response_times[p95_index]
    
    print(f"\nDispatcher Response Times:")
    print(f"  Min: {min(response_times):.3f}s")
    print(f"  Median: {response_times[len(response_times)//2]:.3f}s")
    print(f"  P95: {p95_latency:.3f}s")
    print(f"  Max: {max(response_times):.3f}s")
    
    assert p95_latency < 2.0, f"P95 latency {p95_latency:.3f}s exceeds 2s target"








