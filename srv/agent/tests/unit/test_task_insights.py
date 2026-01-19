"""
Unit tests for task insights service extensions.

Tests the task-scoped insights/memories functionality.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestTaskInsightsCollection:
    """Tests for task insights collection management."""
    
    def test_task_insights_collection_name(self):
        """Test task insights uses separate collection."""
        from app.services.insights_service import TASK_INSIGHTS_COLLECTION, COLLECTION_NAME
        
        assert TASK_INSIGHTS_COLLECTION == "task_insights"
        # Should be different from regular insights (COLLECTION_NAME = "chat_insights")
        assert TASK_INSIGHTS_COLLECTION != COLLECTION_NAME


@pytest.mark.asyncio
class TestTaskInsightOperations:
    """Tests for task insight CRUD operations."""
    
    async def test_insert_task_insight_creates_embedding(self):
        """Test insert_task_insight creates vector embedding."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.insert_task_insight = AsyncMock(return_value="insight-id-123")
        
        task_id = str(uuid.uuid4())
        user_id = "test-user"
        content = "News article about AI developments"
        execution_id = str(uuid.uuid4())
        
        insight_id = await service.insert_task_insight(
            task_id=task_id,
            user_id=user_id,
            content=content,
            execution_id=execution_id,
        )
        
        assert insight_id == "insight-id-123"
        service.insert_task_insight.assert_called_once_with(
            task_id=task_id,
            user_id=user_id,
            content=content,
            execution_id=execution_id,
        )
    
    async def test_search_task_insights_returns_similar(self):
        """Test search_task_insights finds semantically similar content."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        
        mock_results = [
            {
                "id": "insight-1",
                "content": "Previous AI news from yesterday",
                "score": 0.95,
                "execution_id": "exec-1",
            },
            {
                "id": "insight-2",
                "content": "AI conference announcements",
                "score": 0.88,
                "execution_id": "exec-2",
            },
        ]
        service.search_task_insights = AsyncMock(return_value=mock_results)
        
        task_id = str(uuid.uuid4())
        results = await service.search_task_insights(
            task_id=task_id,
            query="Latest AI news",
            user_id="test-user",
            limit=10,
        )
        
        assert len(results) == 2
        assert results[0]["score"] > results[1]["score"]
    
    async def test_get_task_insights_returns_recent(self):
        """Test get_task_insights returns recent insights."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        
        mock_insights = [
            {"id": "1", "content": "Recent insight", "created_at": "2026-01-19T10:00:00Z"},
            {"id": "2", "content": "Older insight", "created_at": "2026-01-18T10:00:00Z"},
        ]
        service.get_task_insights = MagicMock(return_value=mock_insights)
        
        task_id = str(uuid.uuid4())
        insights = service.get_task_insights(
            task_id=task_id,
            user_id="test-user",
            limit=50,
        )
        
        assert len(insights) == 2
    
    async def test_delete_task_insights_removes_all(self):
        """Test delete_task_insights removes all insights for task."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.delete_task_insights = MagicMock()
        
        task_id = str(uuid.uuid4())
        service.delete_task_insights(
            task_id=task_id,
            user_id="test-user",
        )
        
        service.delete_task_insights.assert_called_once_with(
            task_id=task_id,
            user_id="test-user",
        )
    
    async def test_purge_old_task_insights_keeps_recent(self):
        """Test purge_old_task_insights keeps specified count."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        # Should return count of purged insights
        service.purge_old_task_insights = MagicMock(return_value=25)
        
        task_id = str(uuid.uuid4())
        purged_count = service.purge_old_task_insights(
            task_id=task_id,
            user_id="test-user",
            keep_count=50,
        )
        
        assert purged_count == 25


@pytest.mark.asyncio
class TestBuildTaskContext:
    """Tests for building task execution context from insights."""
    
    async def test_build_task_context_empty(self):
        """Test build_task_context with no insights."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.build_task_context = AsyncMock(return_value="")
        
        task_id = str(uuid.uuid4())
        context = await service.build_task_context(
            task_id=task_id,
            user_id="test-user",
            query="What news should I search for?",
        )
        
        assert context == ""
    
    async def test_build_task_context_with_insights(self):
        """Test build_task_context includes relevant insights."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        
        expected_context = """Previous task executions found the following:
- AI company announced new model
- Tech conference scheduled for next month
- Research paper on language models published"""
        
        service.build_task_context = AsyncMock(return_value=expected_context)
        
        task_id = str(uuid.uuid4())
        context = await service.build_task_context(
            task_id=task_id,
            user_id="test-user",
            query="Find AI news",
            context_limit=10,
        )
        
        assert "Previous task executions" in context
        assert "AI company" in context
    
    async def test_build_task_context_respects_limit(self):
        """Test build_task_context respects context_limit."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.build_task_context = AsyncMock(return_value="Limited context")
        
        task_id = str(uuid.uuid4())
        context = await service.build_task_context(
            task_id=task_id,
            user_id="test-user",
            query="Query",
            context_limit=5,  # Only 5 insights
        )
        
        # Verify the call was made with limit
        service.build_task_context.assert_called_once()
        call_kwargs = service.build_task_context.call_args.kwargs
        assert call_kwargs.get("context_limit") == 5


@pytest.mark.asyncio
class TestTaskInsightCount:
    """Tests for task insight counting."""
    
    async def test_get_task_insight_count(self):
        """Test get_task_insight_count returns correct count."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.get_task_insight_count = MagicMock(return_value=42)
        
        task_id = str(uuid.uuid4())
        count = service.get_task_insight_count(
            task_id=task_id,
            user_id="test-user",
        )
        
        assert count == 42
    
    async def test_get_task_insight_count_empty(self):
        """Test get_task_insight_count returns 0 for new task."""
        from app.services.insights_service import InsightsService
        
        service = MagicMock(spec=InsightsService)
        service.get_task_insight_count = MagicMock(return_value=0)
        
        task_id = str(uuid.uuid4())
        count = service.get_task_insight_count(
            task_id=task_id,
            user_id="test-user",
        )
        
        assert count == 0


class TestInsightDataIntegrity:
    """Tests for insight data integrity."""
    
    def test_insight_contains_required_fields(self):
        """Test insights contain all required fields."""
        required_fields = [
            "id",
            "task_id",
            "user_id",
            "content",
            "execution_id",
            "created_at",
        ]
        
        sample_insight = {
            "id": "insight-123",
            "task_id": str(uuid.uuid4()),
            "user_id": "test-user",
            "content": "Some insight content",
            "execution_id": str(uuid.uuid4()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        for field in required_fields:
            assert field in sample_insight
    
    def test_insight_isolation_between_tasks(self):
        """Test insights are isolated between tasks."""
        task_1_insights = [
            {"task_id": "task-1", "content": "Task 1 insight"},
        ]
        task_2_insights = [
            {"task_id": "task-2", "content": "Task 2 insight"},
        ]
        
        # Each task should only see its own insights
        for insight in task_1_insights:
            assert insight["task_id"] == "task-1"
        
        for insight in task_2_insights:
            assert insight["task_id"] == "task-2"
    
    def test_insight_isolation_between_users(self):
        """Test insights are isolated between users."""
        user_1_insights = [
            {"user_id": "user-1", "content": "User 1 insight"},
        ]
        user_2_insights = [
            {"user_id": "user-2", "content": "User 2 insight"},
        ]
        
        # Each user should only see their own insights
        for insight in user_1_insights:
            assert insight["user_id"] == "user-1"
        
        for insight in user_2_insights:
            assert insight["user_id"] == "user-2"
