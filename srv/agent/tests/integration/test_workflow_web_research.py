"""
Integration tests for the Web Research Workflow.

Tests the workflow execution including:
1. Tool step execution (web_search, web_scraper, search, ingest)
2. Agent step execution
3. Condition step evaluation
4. Loop step iteration
5. URL extraction utility
6. Full workflow execution
"""

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.workflows.definitions.web_research import (
    WEB_RESEARCH_WORKFLOW_DEFINITION,
    WEB_RESEARCH_SIMPLE_WORKFLOW,
    create_web_research_workflow,
    get_default_input_data,
)
from app.workflows.enhanced_engine import (
    _extract_urls_from_result,
    _execute_tool_step,
    execute_step,
)
from app.workflows.engine import (
    UsageLimits,
    _resolve_value,
    _resolve_args,
    _evaluate_condition,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def mock_busibox_client():
    """Create a mock BusiboxClient."""
    client = MagicMock()
    client.search = AsyncMock(return_value={"results": [], "total_count": 0})
    client.ingest_document = AsyncMock(return_value={"id": str(uuid.uuid4()), "status": "ingested"})
    client.rag_query = AsyncMock(return_value={"answer": "test answer"})
    return client


@pytest.fixture
def mock_principal():
    """Create a mock Principal."""
    from app.schemas.auth import Principal
    return Principal(
        sub="test-user-123",
        scopes=["agent:read", "agent:write", "search.read", "ingest.write"],
        token="mock-token",
    )


@pytest.fixture
def usage_limits():
    """Create UsageLimits with default guardrails."""
    return UsageLimits({
        "request_limit": 50,
        "tool_calls_limit": 100,
        "max_cost_dollars": 2.0,
    })


@pytest.fixture
def mock_search_result():
    """Create a mock web search result."""
    return {
        "found": True,
        "result_count": 3,
        "results": [
            {
                "title": "First Result",
                "url": "https://example.com/article1",
                "snippet": "First article about the topic",
            },
            {
                "title": "Second Result",
                "url": "https://example.com/article2",
                "snippet": "Second article with more details",
            },
            {
                "title": "Third Result",
                "url": "https://example.com/article3",
                "snippet": "Third article with analysis",
            },
        ],
        "providers_used": ["duckduckgo"],
    }


@pytest.fixture
def mock_scrape_result():
    """Create a mock web scraper result."""
    return {
        "success": True,
        "url": "https://example.com/article1",
        "title": "First Article Title",
        "content": "This is the full content of the article with detailed information.",
        "word_count": 12,
    }


# =============================================================================
# URL Extraction Tests
# =============================================================================


class TestUrlExtraction:
    """Tests for the _extract_urls_from_result utility function."""
    
    def test_extract_from_urls_field(self):
        """Test extraction when urls field is present."""
        result = _extract_urls_from_result({
            "urls": ["https://a.com", "https://b.com"]
        })
        assert result["urls"] == ["https://a.com", "https://b.com"]
        assert result["url_count"] == 2
    
    def test_extract_from_results_array(self):
        """Test extraction from results array with url objects."""
        result = _extract_urls_from_result({
            "results": [
                {"url": "https://a.com", "title": "A"},
                {"url": "https://b.com", "title": "B"},
            ]
        })
        assert result["urls"] == ["https://a.com", "https://b.com"]
        assert result["url_count"] == 2
    
    def test_extract_from_text_result(self):
        """Test extraction from text content."""
        result = _extract_urls_from_result({
            "result": "Found articles at https://example.com/1 and https://example.com/2"
        })
        assert "https://example.com/1" in result["urls"]
        assert "https://example.com/2" in result["urls"]
    
    def test_deduplicates_urls(self):
        """Test that duplicate URLs are removed."""
        result = _extract_urls_from_result({
            "urls": ["https://a.com", "https://b.com", "https://a.com"]
        })
        assert result["urls"] == ["https://a.com", "https://b.com"]
        assert result["url_count"] == 2
    
    def test_handles_empty_input(self):
        """Test handling of empty input."""
        result = _extract_urls_from_result({})
        assert result["urls"] == []
        assert result["url_count"] == 0
    
    def test_handles_list_input(self):
        """Test handling of list input."""
        result = _extract_urls_from_result([
            {"url": "https://a.com"},
            "https://b.com",
        ])
        assert "https://a.com" in result["urls"]
        assert "https://b.com" in result["urls"]


# =============================================================================
# Workflow Definition Tests
# =============================================================================


class TestWorkflowDefinition:
    """Tests for workflow definition structure."""
    
    def test_main_workflow_has_required_fields(self):
        """Test that the main workflow has all required fields."""
        workflow = WEB_RESEARCH_WORKFLOW_DEFINITION
        
        assert "name" in workflow
        assert "description" in workflow
        assert "steps" in workflow
        assert "guardrails" in workflow
        
        assert workflow["name"] == "web-research-workflow"
        assert len(workflow["steps"]) > 0
    
    def test_simple_workflow_has_required_fields(self):
        """Test that the simple workflow has all required fields."""
        workflow = WEB_RESEARCH_SIMPLE_WORKFLOW
        
        assert "name" in workflow
        assert workflow["name"] == "web-research-simple"
        assert len(workflow["steps"]) == 1
    
    def test_create_workflow_deep_mode(self):
        """Test creating workflow with deep mode enabled."""
        workflow = create_web_research_workflow(deep=True)
        
        assert workflow["config"]["deep"] is True
        assert workflow["config"]["min_results"] == 15
        
        # Check that loop iterations increased
        for step in workflow["steps"]:
            if step["id"] == "scrape_loop" and "loop_config" in step:
                assert step["loop_config"]["max_iterations"] == 30
    
    def test_create_workflow_custom_guardrails(self):
        """Test creating workflow with custom guardrails."""
        custom = {"max_cost_dollars": 5.0}
        workflow = create_web_research_workflow(custom_guardrails=custom)
        
        assert workflow["guardrails"]["max_cost_dollars"] == 5.0
    
    def test_get_default_input_data(self):
        """Test default input data generation."""
        input_data = get_default_input_data(
            query="test query",
            deep=False,
        )
        
        assert input_data["query"] == "test query"
        assert input_data["deep"] is False
        assert input_data["min_results"] == 5
        assert input_data["store_results"] is True
    
    def test_get_default_input_data_deep_mode(self):
        """Test default input data in deep mode."""
        input_data = get_default_input_data(
            query="deep search",
            deep=True,
            recency="7d",
        )
        
        assert input_data["min_results"] == 15
        assert input_data["recency"] == "7d"


# =============================================================================
# Tool Step Execution Tests
# =============================================================================


@pytest.mark.asyncio
class TestToolStepExecution:
    """Tests for tool step execution in workflows."""
    
    async def test_execute_web_search_tool(
        self,
        mock_busibox_client,
        usage_limits,
        mock_search_result,
    ):
        """Test executing web_search tool step."""
        step = {
            "tool": "web_search",
            "tool_args": {"query": "test query", "max_results": 5},
        }
        context = {"input": {"query": "test query"}}
        
        # Patch at the source module, not the import location
        with patch('app.tools.web_search_tool.search_web') as mock_search:
            mock_search.return_value = MagicMock(**mock_search_result)
            mock_search.return_value.model_dump = MagicMock(return_value=mock_search_result)
            
            result = await _execute_tool_step(
                step, context, mock_busibox_client, usage_limits
            )
        
        assert result is not None
        assert usage_limits.tool_calls == 1
    
    async def test_execute_web_scraper_tool(
        self,
        mock_busibox_client,
        usage_limits,
        mock_scrape_result,
    ):
        """Test executing web_scraper tool step."""
        step = {
            "tool": "web_scraper",
            "tool_args": {"url": "https://example.com/article"},
        }
        context = {}
        
        # Patch at the source module, not the import location
        with patch('app.tools.web_scraper_tool.scrape_webpage') as mock_scrape:
            mock_scrape.return_value = MagicMock(**mock_scrape_result)
            mock_scrape.return_value.model_dump = MagicMock(return_value=mock_scrape_result)
            
            result = await _execute_tool_step(
                step, context, mock_busibox_client, usage_limits
            )
        
        assert result is not None
        assert usage_limits.tool_calls == 1
    
    async def test_execute_extract_urls_tool(
        self,
        mock_busibox_client,
        usage_limits,
    ):
        """Test executing extract_urls_from_agent_result tool step."""
        step = {
            "tool": "extract_urls_from_agent_result",
            "tool_args": {
                "agent_result": {
                    "results": [
                        {"url": "https://a.com"},
                        {"url": "https://b.com"},
                    ]
                }
            },
        }
        context = {}
        
        result = await _execute_tool_step(
            step, context, mock_busibox_client, usage_limits
        )
        
        assert result is not None
        assert "urls" in result
        assert len(result["urls"]) == 2
    
    async def test_execute_search_tool_via_client(
        self,
        mock_busibox_client,
        usage_limits,
    ):
        """Test executing search tool via BusiboxClient."""
        step = {
            "tool": "search",
            "tool_args": {"query": "test", "limit": 10},
        }
        context = {}
        
        mock_busibox_client.search.return_value = {"results": [], "total_count": 0}
        
        result = await _execute_tool_step(
            step, context, mock_busibox_client, usage_limits
        )
        
        mock_busibox_client.search.assert_called_once()
        assert result is not None
    
    async def test_unknown_tool_raises_error(
        self,
        mock_busibox_client,
        usage_limits,
    ):
        """Test that unknown tool raises WorkflowExecutionError."""
        from app.workflows.engine import WorkflowExecutionError
        
        step = {
            "tool": "unknown_tool",
            "tool_args": {},
        }
        context = {}
        
        with pytest.raises(WorkflowExecutionError) as exc_info:
            await _execute_tool_step(
                step, context, mock_busibox_client, usage_limits
            )
        
        assert "Unknown tool" in str(exc_info.value)


# =============================================================================
# Condition Evaluation Tests
# =============================================================================


class TestConditionEvaluation:
    """Tests for condition evaluation in workflows."""
    
    def test_evaluate_lt_condition_true(self):
        """Test less-than condition that passes."""
        condition = {
            "field": "$.step1.count",
            "operator": "lt",
            "value": 10,
        }
        context = {"step1": {"count": 5}}
        
        result = _evaluate_condition(condition, context)
        assert result is True
    
    def test_evaluate_lt_condition_false(self):
        """Test less-than condition that fails."""
        condition = {
            "field": "$.step1.count",
            "operator": "lt",
            "value": 5,
        }
        context = {"step1": {"count": 10}}
        
        result = _evaluate_condition(condition, context)
        assert result is False
    
    def test_evaluate_eq_condition(self):
        """Test equality condition."""
        condition = {
            "field": "$.input.deep",
            "operator": "eq",
            "value": True,
        }
        context = {"input": {"deep": True}}
        
        result = _evaluate_condition(condition, context)
        assert result is True
    
    def test_evaluate_gt_condition(self):
        """Test greater-than condition."""
        condition = {
            "field": "$.results.total",
            "operator": "gt",
            "value": 0,
        }
        context = {"results": {"total": 5}}
        
        result = _evaluate_condition(condition, context)
        assert result is True
    
    def test_evaluate_exists_condition(self):
        """Test exists condition."""
        condition = {
            "field": "$.input.recency",
            "operator": "exists",
        }
        
        # Field exists
        context_with = {"input": {"recency": "7d"}}
        assert _evaluate_condition(condition, context_with) is True
        
        # Field doesn't exist
        context_without = {"input": {}}
        assert _evaluate_condition(condition, context_without) is False


# =============================================================================
# Value Resolution Tests
# =============================================================================


class TestValueResolution:
    """Tests for JSONPath-like value resolution."""
    
    def test_resolve_simple_path(self):
        """Test resolving a simple path."""
        context = {"input": {"query": "test"}}
        result = _resolve_value("$.input.query", context)
        assert result == "test"
    
    def test_resolve_nested_path(self):
        """Test resolving a nested path."""
        context = {
            "step1": {
                "results": {
                    "items": ["a", "b", "c"]
                }
            }
        }
        result = _resolve_value("$.step1.results.items", context)
        assert result == ["a", "b", "c"]
    
    def test_resolve_literal(self):
        """Test that literals are returned as-is."""
        context = {}
        result = _resolve_value("literal string", context)
        assert result == "literal string"
    
    def test_resolve_missing_path(self):
        """Test resolving a missing path returns None."""
        context = {"input": {}}
        result = _resolve_value("$.input.missing", context)
        assert result is None
    
    def test_resolve_args(self):
        """Test resolving multiple args."""
        context = {
            "input": {"query": "test", "limit": 10}
        }
        args = {
            "query": "$.input.query",
            "max_results": "$.input.limit",
            "literal": "fixed value",
        }
        
        resolved = _resolve_args(args, context)
        
        assert resolved["query"] == "test"
        assert resolved["max_results"] == 10
        assert resolved["literal"] == "fixed value"


# =============================================================================
# Usage Limits Tests
# =============================================================================


class TestUsageLimits:
    """Tests for usage tracking and guardrails."""
    
    def test_update_increments_counters(self):
        """Test that update increments all counters."""
        limits = UsageLimits({})
        limits.update(requests=1, tool_calls=2, input_tokens=100, output_tokens=50)
        
        assert limits.requests == 1
        assert limits.tool_calls == 2
        assert limits.input_tokens == 100
        assert limits.output_tokens == 50
    
    def test_exceeding_request_limit_raises(self):
        """Test that exceeding request limit raises error."""
        from app.workflows.engine import GuardrailsExceededError
        
        limits = UsageLimits({"request_limit": 5})
        
        # Should succeed up to limit
        for _ in range(5):
            limits.update(requests=1)
        
        # Should fail on exceeding
        with pytest.raises(GuardrailsExceededError):
            limits.update(requests=1)
    
    def test_exceeding_tool_calls_limit_raises(self):
        """Test that exceeding tool calls limit raises error."""
        from app.workflows.engine import GuardrailsExceededError
        
        limits = UsageLimits({"tool_calls_limit": 3})
        
        limits.update(tool_calls=3)
        
        with pytest.raises(GuardrailsExceededError):
            limits.update(tool_calls=1)
    
    def test_get_usage_dict(self):
        """Test getting usage as dictionary."""
        limits = UsageLimits({})
        limits.update(requests=5, tool_calls=10, input_tokens=1000, output_tokens=500)
        
        usage = limits.get_usage_dict()
        
        assert usage["requests"] == 5
        assert usage["tool_calls"] == 10
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 500


# =============================================================================
# Full Workflow Integration Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
class TestFullWorkflowExecution:
    """
    Integration tests for full workflow execution.
    
    These tests require more setup and mock the entire execution flow.
    """
    
    async def test_simple_workflow_execution(
        self,
        test_session: AsyncSession,
        mock_principal,
        mock_search_result,
    ):
        """Test executing the simple workflow end-to-end."""
        from app.models.domain import WorkflowDefinition, WorkflowExecution
        from app.workflows.enhanced_engine import execute_enhanced_workflow
        
        # Create workflow definition in database
        workflow_def = WorkflowDefinition(
            name=f"test-simple-{uuid.uuid4().hex[:8]}",
            description="Test simple workflow",
            steps=WEB_RESEARCH_SIMPLE_WORKFLOW["steps"],
            guardrails=WEB_RESEARCH_SIMPLE_WORKFLOW["guardrails"],
            is_active=True,
            created_by=mock_principal.sub,
        )
        test_session.add(workflow_def)
        await test_session.commit()
        await test_session.refresh(workflow_def)
        
        try:
            # Mock the web search agent and related dependencies
            with patch('app.workflows.enhanced_engine.agent_registry') as mock_registry:
                mock_agent = MagicMock()
                mock_agent.run = AsyncMock(return_value=MagicMock(
                    data="Test search results summary"
                ))
                mock_registry.get.return_value = mock_agent
                
                with patch('app.workflows.enhanced_engine.get_or_exchange_token') as mock_token:
                    mock_token.return_value = MagicMock(access_token="test-token")
                    
                    # Execute workflow
                    execution = await execute_enhanced_workflow(
                        session=test_session,
                        principal=mock_principal,
                        workflow_id=workflow_def.id,
                        input_data={"query": "test query"},
                        scopes=["agent:read"],
                        purpose="test",
                    )
            
            # Verify execution completed
            assert execution is not None
            assert execution.workflow_id == workflow_def.id
            # Status depends on implementation details
            
        finally:
            # Cleanup
            await test_session.delete(workflow_def)
            await test_session.commit()
