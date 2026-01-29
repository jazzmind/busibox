"""
Unit tests for the CacheManager class.

Tests Redis caching logic with mocked Redis client.
"""

import json
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch

from api.services.cache_manager import CacheManager, LockError


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.hgetall = AsyncMock(return_value={})
    redis.hset = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock()
    redis.exists = AsyncMock(return_value=0)
    redis.expire = AsyncMock()
    redis.ttl = AsyncMock(return_value=300)
    redis.incr = AsyncMock(return_value=1)
    redis.scan = AsyncMock(return_value=(0, []))
    redis.pipeline = MagicMock(return_value=AsyncMock())
    redis.hdel = AsyncMock()
    return redis


@pytest.fixture
def cache_manager(mock_redis):
    """Create a CacheManager with mocked Redis."""
    return CacheManager(mock_redis)


@pytest.fixture
def cache_manager_with_flush(mock_redis):
    """Create a CacheManager with flush callback."""
    flush_callback = AsyncMock()
    return CacheManager(mock_redis, flush_callback=flush_callback)


class TestKeyGeneration:
    """Test cache key generation."""
    
    def test_meta_key(self, cache_manager):
        key = cache_manager._meta_key("doc-123")
        assert key == "data:doc-123:meta"
    
    def test_records_key(self, cache_manager):
        key = cache_manager._records_key("doc-123")
        assert key == "data:doc-123:records"
    
    def test_lock_key(self, cache_manager):
        key = cache_manager._lock_key("doc-123")
        assert key == "data:doc-123:lock"
    
    def test_access_key(self, cache_manager):
        key = cache_manager._access_key("doc-123")
        assert key == "data:doc-123:access"


class TestCacheDocument:
    """Test document caching."""
    
    @pytest.mark.asyncio
    async def test_cache_document_success(self, cache_manager, mock_redis):
        document_id = "doc-123"
        data = {
            "schema": {"fields": {}},
            "records": [{"id": "1", "name": "Test"}],
            "version": 1,
        }
        
        result = await cache_manager.cache_document(document_id, data)
        
        assert result is True
        mock_redis.hset.assert_called()
        mock_redis.set.assert_called()
        mock_redis.expire.assert_called()
    
    @pytest.mark.asyncio
    async def test_cache_document_with_ttl(self, cache_manager, mock_redis):
        document_id = "doc-123"
        data = {"records": [], "version": 1}
        custom_ttl = 600
        
        await cache_manager.cache_document(document_id, data, ttl=custom_ttl)
        
        # Check that expire was called with custom TTL
        calls = mock_redis.expire.call_args_list
        assert any(custom_ttl in call.args for call in calls)
    
    @pytest.mark.asyncio
    async def test_cache_document_empty_records(self, cache_manager, mock_redis):
        document_id = "doc-123"
        data = {"records": [], "version": 1}
        
        result = await cache_manager.cache_document(document_id, data)
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_cache_document_error(self, cache_manager, mock_redis):
        mock_redis.hset.side_effect = Exception("Redis error")
        
        result = await cache_manager.cache_document("doc-123", {"records": []})
        
        assert result is False


class TestGetDocument:
    """Test document retrieval from cache."""
    
    @pytest.mark.asyncio
    async def test_get_document_not_cached(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {}
        
        result = await cache_manager.get_document("doc-123")
        
        assert result is None
    
    @pytest.mark.asyncio
    async def test_get_document_success(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {
            "schema": "null",
            "version": "1",
            "record_count": "2",
            "cached_at": str(time.time()),
            "last_accessed": str(time.time()),
            "access_count": "5",
            "dirty": "0",
        }
        mock_redis.get.return_value = json.dumps([
            {"id": "1", "name": "Record 1"},
            {"id": "2", "name": "Record 2"},
        ])
        
        result = await cache_manager.get_document("doc-123")
        
        assert result is not None
        assert result["version"] == 1
        assert len(result["records"]) == 2
        assert result["dirty"] is False
    
    @pytest.mark.asyncio
    async def test_get_document_updates_access(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {
            "schema": "null",
            "version": "1",
            "cached_at": str(time.time()),
            "last_accessed": str(time.time() - 100),
            "access_count": "5",
            "dirty": "0",
        }
        mock_redis.get.return_value = "[]"
        
        await cache_manager.get_document("doc-123", update_access=True)
        
        # Should update last_accessed and access_count
        mock_redis.hset.assert_called()
        mock_redis.expire.assert_called()
    
    @pytest.mark.asyncio
    async def test_get_document_no_access_update(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {
            "schema": "null",
            "version": "1",
            "cached_at": str(time.time()),
            "last_accessed": str(time.time()),
            "access_count": "5",
            "dirty": "0",
        }
        mock_redis.get.return_value = "[]"
        
        # Reset mock to clear any prior calls
        mock_redis.hset.reset_mock()
        
        await cache_manager.get_document("doc-123", update_access=False)
        
        # Should not update access info
        mock_redis.hset.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_get_document_missing_records(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {"version": "1"}
        mock_redis.get.return_value = None  # Records key missing
        
        result = await cache_manager.get_document("doc-123")
        
        assert result is None


class TestUpdateRecords:
    """Test updating cached records."""
    
    @pytest.mark.asyncio
    async def test_update_records_success(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 1
        mock_redis.hgetall.return_value = {"dirty": "0"}
        
        new_records = [{"id": "1", "name": "Updated"}]
        result = await cache_manager.update_records("doc-123", new_records)
        
        assert result is True
        mock_redis.set.assert_called()
        mock_redis.hset.assert_called()
    
    @pytest.mark.asyncio
    async def test_update_records_marks_dirty(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 1
        mock_redis.hgetall.return_value = {"dirty": "0"}
        
        await cache_manager.update_records("doc-123", [])
        
        # Check that dirty was set to "1"
        call_args = mock_redis.hset.call_args
        assert "dirty" in call_args.kwargs.get("mapping", {})
        assert call_args.kwargs["mapping"]["dirty"] == "1"
    
    @pytest.mark.asyncio
    async def test_update_records_not_cached(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 0
        
        result = await cache_manager.update_records("doc-123", [])
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_update_records_with_version(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 1
        mock_redis.hgetall.return_value = {"dirty": "0"}
        
        await cache_manager.update_records("doc-123", [], version=5)
        
        call_args = mock_redis.hset.call_args
        assert call_args.kwargs["mapping"]["version"] == "5"


class TestInvalidateDocument:
    """Test document cache invalidation."""
    
    @pytest.mark.asyncio
    async def test_invalidate_clean_document(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {"dirty": "0"}
        
        result = await cache_manager.invalidate_document("doc-123")
        
        assert result is True
        mock_redis.delete.assert_called()
    
    @pytest.mark.asyncio
    async def test_invalidate_dirty_document_flushes(self, cache_manager_with_flush, mock_redis):
        mock_redis.hgetall.return_value = {
            "dirty": "1",
            "schema": "null",
            "version": "1",
        }
        mock_redis.get.return_value = "[]"
        
        await cache_manager_with_flush.invalidate_document("doc-123")
        
        # Should have called flush callback
        cache_manager_with_flush.flush_callback.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_invalidate_nonexistent(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {}
        
        result = await cache_manager.invalidate_document("doc-123")
        
        assert result is True
        mock_redis.delete.assert_called()


class TestFlushDocument:
    """Test document flushing."""
    
    @pytest.mark.asyncio
    async def test_flush_calls_callback(self, cache_manager_with_flush, mock_redis):
        mock_redis.hgetall.return_value = {
            "schema": '{"fields": {}}',
            "version": "2",
            "dirty": "1",
        }
        mock_redis.get.return_value = '[{"id": "1"}]'
        
        result = await cache_manager_with_flush.flush_document("doc-123")
        
        assert result is True
        cache_manager_with_flush.flush_callback.assert_called_once()
        
        # Check the callback received correct data
        call_args = cache_manager_with_flush.flush_callback.call_args
        assert call_args.args[0] == "doc-123"
        data = call_args.args[1]
        assert data["version"] == 2
        assert len(data["records"]) == 1
    
    @pytest.mark.asyncio
    async def test_flush_marks_clean(self, cache_manager_with_flush, mock_redis):
        mock_redis.hgetall.return_value = {
            "schema": "null",
            "version": "1",
            "dirty": "1",
        }
        mock_redis.get.return_value = "[]"
        
        await cache_manager_with_flush.flush_document("doc-123")
        
        # Should mark as clean
        mock_redis.hset.assert_called()
        call_args = mock_redis.hset.call_args
        assert call_args.kwargs["mapping"]["dirty"] == "0"
    
    @pytest.mark.asyncio
    async def test_flush_no_callback(self, cache_manager, mock_redis):
        # Cache manager without flush callback
        result = await cache_manager.flush_document("doc-123")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_flush_not_cached(self, cache_manager_with_flush, mock_redis):
        mock_redis.hgetall.return_value = {}
        
        result = await cache_manager_with_flush.flush_document("doc-123")
        
        assert result is False


class TestLocking:
    """Test document locking."""
    
    @pytest.mark.asyncio
    async def test_acquire_lock_success(self, cache_manager, mock_redis):
        mock_redis.set.return_value = True
        
        async with cache_manager.document_lock("doc-123", "holder-1"):
            pass
        
        mock_redis.set.assert_called()
        mock_redis.delete.assert_called()
    
    @pytest.mark.asyncio
    async def test_acquire_lock_already_held_by_self(self, cache_manager, mock_redis):
        mock_redis.set.return_value = False  # Lock exists
        mock_redis.get.return_value = "holder-1"  # But we hold it
        
        async with cache_manager.document_lock("doc-123", "holder-1"):
            pass
    
    @pytest.mark.asyncio
    async def test_acquire_lock_held_by_other(self, cache_manager, mock_redis):
        mock_redis.set.return_value = False  # Lock exists
        mock_redis.get.return_value = "holder-2"  # Someone else holds it
        
        with pytest.raises(LockError):
            async with cache_manager.document_lock("doc-123", "holder-1"):
                pass
    
    @pytest.mark.asyncio
    async def test_is_locked_true(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 1
        
        result = await cache_manager.is_locked("doc-123")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_is_locked_false(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 0
        
        result = await cache_manager.is_locked("doc-123")
        
        assert result is False
    
    @pytest.mark.asyncio
    async def test_get_lock_holder(self, cache_manager, mock_redis):
        mock_redis.get.return_value = "holder-1"
        
        result = await cache_manager.get_lock_holder("doc-123")
        
        assert result == "holder-1"


class TestAccessTracking:
    """Test access tracking for auto-activation."""
    
    @pytest.mark.asyncio
    async def test_track_access_below_threshold(self, cache_manager, mock_redis):
        mock_pipeline = AsyncMock()
        mock_pipeline.incr = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[3, True])  # 3 accesses
        mock_redis.pipeline.return_value = mock_pipeline
        
        result = await cache_manager.track_access("doc-123")
        
        # Default threshold is 5, so should not activate
        assert result is False
    
    @pytest.mark.asyncio
    async def test_track_access_at_threshold(self, cache_manager, mock_redis):
        mock_pipeline = AsyncMock()
        mock_pipeline.incr = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[5, True])  # 5 accesses = threshold
        mock_redis.pipeline.return_value = mock_pipeline
        
        result = await cache_manager.track_access("doc-123")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_should_cache_not_cached_high_access(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 0  # Not cached
        mock_redis.get.return_value = "10"  # High access count
        
        result = await cache_manager.should_cache("doc-123")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_should_cache_already_cached(self, cache_manager, mock_redis):
        mock_redis.exists.return_value = 1  # Already cached
        
        result = await cache_manager.should_cache("doc-123")
        
        assert result is False


class TestStats:
    """Test cache statistics."""
    
    @pytest.mark.asyncio
    async def test_get_cache_stats_empty(self, cache_manager, mock_redis):
        mock_redis.scan.return_value = (0, [])
        
        stats = await cache_manager.get_cache_stats()
        
        assert stats["cached_documents"] == 0
        assert stats["dirty_documents"] == 0
        assert stats["total_records"] == 0
    
    @pytest.mark.asyncio
    async def test_get_cache_stats_with_documents(self, cache_manager, mock_redis):
        mock_redis.scan.return_value = (0, [
            "data:doc-1:meta",
            "data:doc-2:meta",
        ])
        mock_redis.hgetall.side_effect = [
            {"dirty": "0", "record_count": "5"},
            {"dirty": "1", "record_count": "10"},
        ]
        
        stats = await cache_manager.get_cache_stats()
        
        assert stats["cached_documents"] == 2
        assert stats["dirty_documents"] == 1
        assert stats["total_records"] == 15
    
    @pytest.mark.asyncio
    async def test_get_document_stats_cached(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {
            "version": "2",
            "record_count": "10",
            "dirty": "0",
            "cached_at": str(time.time()),
            "last_accessed": str(time.time()),
            "access_count": "25",
        }
        mock_redis.ttl.return_value = 250
        
        stats = await cache_manager.get_document_stats("doc-123")
        
        assert stats["cached"] is True
        assert stats["version"] == 2
        assert stats["record_count"] == 10
        assert stats["dirty"] is False
        assert stats["access_count"] == 25
        assert stats["ttl_remaining"] == 250
    
    @pytest.mark.asyncio
    async def test_get_document_stats_not_cached(self, cache_manager, mock_redis):
        mock_redis.hgetall.return_value = {}
        
        stats = await cache_manager.get_document_stats("doc-123")
        
        assert stats is None
