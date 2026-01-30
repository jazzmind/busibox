"""
Cache Manager for structured data documents.

Provides Redis-based caching for high-frequency access to data documents.
Implements:
- Automatic cache activation based on access patterns
- Dirty tracking and periodic flushing
- Cache eviction on memory pressure
- Document-level locking for concurrent access

Cache Structure:
- data:{document_id}:meta     -> Hash (schema, version, record_count, dirty, etc.)
- data:{document_id}:records  -> String (JSON array of records)
- data:{document_id}:lock     -> String (lock holder ID for write operations)

Activation Logic:
- Document becomes active when:
  - Created with cache=true option
  - Access count exceeds threshold in time window
  - Explicitly activated via API

Flush Strategy:
- Immediate: On explicit save or document deactivation
- Periodic: Every 30 seconds if dirty
- Inactivity: After 5 minutes of no access
- Eviction: When memory pressure detected
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
import structlog

logger = structlog.get_logger()


class CacheManager:
    """
    Redis-based cache manager for data documents.
    
    Provides high-performance caching with automatic dirty tracking,
    periodic flushing, and access-based activation.
    """
    
    # Cache key prefixes
    PREFIX = "data"
    META_SUFFIX = "meta"
    RECORDS_SUFFIX = "records"
    LOCK_SUFFIX = "lock"
    
    # Configuration
    DEFAULT_TTL = 300  # 5 minutes inactivity timeout
    LOCK_TTL = 30  # 30 seconds lock timeout
    FLUSH_INTERVAL = 30  # 30 seconds periodic flush
    ACTIVATION_THRESHOLD = 5  # Number of accesses to trigger auto-cache
    ACTIVATION_WINDOW = 60  # Time window for access counting (seconds)
    MAX_DIRTY_DURATION = 60  # Maximum time a document can stay dirty (seconds)
    
    def __init__(
        self,
        redis_client: redis_async.Redis,
        flush_callback=None,
        ttl: int = None,
    ):
        """
        Initialize the cache manager.
        
        Args:
            redis_client: Redis async client
            flush_callback: Async callback function to flush data to database
                           Signature: async def flush(document_id: str, data: Dict) -> None
            ttl: Optional custom TTL in seconds
        """
        self.redis = redis_client
        self.flush_callback = flush_callback
        self.ttl = ttl or self.DEFAULT_TTL
        
        # Background task for periodic flushing
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False
    
    # ========================================================================
    # Key Generation
    # ========================================================================
    
    def _meta_key(self, document_id: str) -> str:
        return f"{self.PREFIX}:{document_id}:{self.META_SUFFIX}"
    
    def _records_key(self, document_id: str) -> str:
        return f"{self.PREFIX}:{document_id}:{self.RECORDS_SUFFIX}"
    
    def _lock_key(self, document_id: str) -> str:
        return f"{self.PREFIX}:{document_id}:{self.LOCK_SUFFIX}"
    
    def _access_key(self, document_id: str) -> str:
        return f"{self.PREFIX}:{document_id}:access"
    
    # ========================================================================
    # Lifecycle Management
    # ========================================================================
    
    async def start(self):
        """Start the background flush task."""
        if self._running:
            return
        
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush_loop())
        logger.info("Cache manager started")
    
    async def stop(self):
        """Stop the background flush task and flush all dirty documents."""
        self._running = False
        
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        
        # Flush all dirty documents
        await self.flush_all_dirty()
        logger.info("Cache manager stopped")
    
    async def _periodic_flush_loop(self):
        """Background loop for periodic flushing."""
        while self._running:
            try:
                await asyncio.sleep(self.FLUSH_INTERVAL)
                await self._flush_dirty_documents()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in periodic flush", error=str(e))
    
    async def _flush_dirty_documents(self):
        """Flush all dirty documents that exceed the dirty duration threshold."""
        if not self.flush_callback:
            return
        
        # Find all cached documents
        pattern = f"{self.PREFIX}:*:{self.META_SUFFIX}"
        cursor = 0
        flushed = 0
        
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                try:
                    meta = await self.redis.hgetall(key)
                    if not meta:
                        continue
                    
                    # Check if dirty and exceeds threshold
                    if meta.get("dirty") == "1":
                        dirty_since = float(meta.get("dirty_since", 0))
                        if time.time() - dirty_since >= self.MAX_DIRTY_DURATION:
                            # Extract document_id from key
                            document_id = key.split(":")[1]
                            await self._flush_document(document_id)
                            flushed += 1
                except Exception as e:
                    logger.warning("Error checking dirty document", key=key, error=str(e))
            
            if cursor == 0:
                break
        
        if flushed > 0:
            logger.info("Periodic flush completed", flushed=flushed)
    
    # ========================================================================
    # Document Caching
    # ========================================================================
    
    async def cache_document(
        self,
        document_id: str,
        data: Dict,
        ttl: int = None,
    ) -> bool:
        """
        Cache a document in Redis.
        
        Args:
            document_id: Document UUID
            data: Document data with schema, records, version
            ttl: Optional custom TTL
            
        Returns:
            True if cached successfully
        """
        meta_key = self._meta_key(document_id)
        records_key = self._records_key(document_id)
        ttl = ttl or self.ttl
        
        try:
            # Store metadata
            meta = {
                "schema": json.dumps(data.get("schema")),
                "version": str(data.get("version", 1)),
                "record_count": str(len(data.get("records", []))),
                "cached_at": str(time.time()),
                "last_accessed": str(time.time()),
                "access_count": "1",
                "dirty": "0",
            }
            await self.redis.hset(meta_key, mapping=meta)
            await self.redis.expire(meta_key, ttl)
            
            # Store records
            await self.redis.set(
                records_key,
                json.dumps(data.get("records", [])),
                ex=ttl,
            )
            
            logger.debug(
                "Document cached",
                document_id=document_id,
                record_count=len(data.get("records", [])),
            )
            
            return True
        except Exception as e:
            logger.error("Failed to cache document", document_id=document_id, error=str(e))
            return False
    
    async def get_document(
        self,
        document_id: str,
        update_access: bool = True,
    ) -> Optional[Dict]:
        """
        Get a cached document.
        
        Args:
            document_id: Document UUID
            update_access: Whether to update access timestamp
            
        Returns:
            Document data or None if not cached
        """
        meta_key = self._meta_key(document_id)
        records_key = self._records_key(document_id)
        
        try:
            # Check if cached
            meta = await self.redis.hgetall(meta_key)
            if not meta:
                return None
            
            # Get records
            records_json = await self.redis.get(records_key)
            if not records_json:
                return None
            
            # Update access info
            if update_access:
                await self.redis.hset(meta_key, mapping={
                    "last_accessed": str(time.time()),
                    "access_count": str(int(meta.get("access_count", 0)) + 1),
                })
                # Refresh TTL
                await self.redis.expire(meta_key, self.ttl)
                await self.redis.expire(records_key, self.ttl)
            
            return {
                "id": document_id,
                "schema": json.loads(meta.get("schema", "null")),
                "version": int(meta.get("version", 1)),
                "records": json.loads(records_json),
                "dirty": meta.get("dirty") == "1",
                "cached_at": float(meta.get("cached_at", 0)),
                "last_accessed": float(meta.get("last_accessed", 0)),
                "access_count": int(meta.get("access_count", 0)),
            }
        except Exception as e:
            logger.error("Failed to get cached document", document_id=document_id, error=str(e))
            return None
    
    async def update_records(
        self,
        document_id: str,
        records: List[Dict],
        version: int = None,
    ) -> bool:
        """
        Update cached records (marks document as dirty).
        
        Args:
            document_id: Document UUID
            records: New records list
            version: Optional new version number
            
        Returns:
            True if updated successfully
        """
        meta_key = self._meta_key(document_id)
        records_key = self._records_key(document_id)
        
        try:
            # Check if cached
            exists = await self.redis.exists(meta_key)
            if not exists:
                return False
            
            # Update records
            await self.redis.set(records_key, json.dumps(records), ex=self.ttl)
            
            # Update metadata
            updates = {
                "record_count": str(len(records)),
                "last_accessed": str(time.time()),
                "dirty": "1",
            }
            
            # Set dirty_since only if not already dirty
            meta = await self.redis.hgetall(meta_key)
            if meta.get("dirty") != "1":
                updates["dirty_since"] = str(time.time())
            
            if version is not None:
                updates["version"] = str(version)
            
            await self.redis.hset(meta_key, mapping=updates)
            await self.redis.expire(meta_key, self.ttl)
            
            return True
        except Exception as e:
            logger.error("Failed to update cached records", document_id=document_id, error=str(e))
            return False
    
    async def invalidate_document(self, document_id: str) -> bool:
        """
        Invalidate (remove) a cached document.
        
        If the document is dirty, flushes to database first.
        
        Args:
            document_id: Document UUID
            
        Returns:
            True if invalidated
        """
        meta_key = self._meta_key(document_id)
        records_key = self._records_key(document_id)
        
        try:
            # Check if dirty and flush first
            meta = await self.redis.hgetall(meta_key)
            if meta and meta.get("dirty") == "1":
                await self._flush_document(document_id)
            
            # Remove from cache
            await self.redis.delete(meta_key, records_key)
            
            logger.debug("Document invalidated", document_id=document_id)
            return True
        except Exception as e:
            logger.error("Failed to invalidate document", document_id=document_id, error=str(e))
            return False
    
    # ========================================================================
    # Flushing
    # ========================================================================
    
    async def _flush_document(self, document_id: str) -> bool:
        """
        Flush a cached document to the database.
        
        Args:
            document_id: Document UUID
            
        Returns:
            True if flushed successfully
        """
        if not self.flush_callback:
            logger.warning("No flush callback configured")
            return False
        
        meta_key = self._meta_key(document_id)
        records_key = self._records_key(document_id)
        
        try:
            # Get cached data
            meta = await self.redis.hgetall(meta_key)
            records_json = await self.redis.get(records_key)
            
            if not meta or not records_json:
                return False
            
            # Prepare data for flush
            data = {
                "schema": json.loads(meta.get("schema", "null")),
                "version": int(meta.get("version", 1)),
                "records": json.loads(records_json),
            }
            
            # Call flush callback
            await self.flush_callback(document_id, data)
            
            # Mark as clean
            await self.redis.hset(meta_key, mapping={
                "dirty": "0",
            })
            await self.redis.hdel(meta_key, "dirty_since")
            
            logger.debug("Document flushed", document_id=document_id)
            return True
        except Exception as e:
            logger.error("Failed to flush document", document_id=document_id, error=str(e))
            return False
    
    async def flush_document(self, document_id: str) -> bool:
        """
        Explicitly flush a document to the database.
        
        Public wrapper around _flush_document.
        """
        return await self._flush_document(document_id)
    
    async def flush_all_dirty(self) -> int:
        """
        Flush all dirty documents.
        
        Returns:
            Number of documents flushed
        """
        if not self.flush_callback:
            return 0
        
        pattern = f"{self.PREFIX}:*:{self.META_SUFFIX}"
        cursor = 0
        flushed = 0
        
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                try:
                    meta = await self.redis.hgetall(key)
                    if meta and meta.get("dirty") == "1":
                        document_id = key.split(":")[1]
                        if await self._flush_document(document_id):
                            flushed += 1
                except Exception as e:
                    logger.warning("Error flushing document", key=key, error=str(e))
            
            if cursor == 0:
                break
        
        logger.info("Flushed all dirty documents", count=flushed)
        return flushed
    
    # ========================================================================
    # Locking
    # ========================================================================
    
    @asynccontextmanager
    async def document_lock(
        self,
        document_id: str,
        holder_id: str,
        timeout: int = None,
    ):
        """
        Acquire a lock on a document for exclusive write access.
        
        Args:
            document_id: Document UUID
            holder_id: Identifier for the lock holder
            timeout: Lock timeout in seconds
            
        Yields:
            True if lock acquired
            
        Raises:
            LockError: If lock cannot be acquired
        """
        lock_key = self._lock_key(document_id)
        timeout = timeout or self.LOCK_TTL
        
        try:
            # Try to acquire lock
            acquired = await self.redis.set(
                lock_key,
                holder_id,
                nx=True,  # Only set if not exists
                ex=timeout,
            )
            
            if not acquired:
                # Check if we already hold the lock
                current_holder = await self.redis.get(lock_key)
                if current_holder != holder_id:
                    raise LockError(f"Document {document_id} is locked by another process")
            
            yield True
            
        finally:
            # Release lock (only if we hold it)
            current_holder = await self.redis.get(lock_key)
            if current_holder == holder_id:
                await self.redis.delete(lock_key)
    
    async def is_locked(self, document_id: str) -> bool:
        """Check if a document is locked."""
        lock_key = self._lock_key(document_id)
        return await self.redis.exists(lock_key) > 0
    
    async def get_lock_holder(self, document_id: str) -> Optional[str]:
        """Get the ID of the current lock holder."""
        lock_key = self._lock_key(document_id)
        return await self.redis.get(lock_key)
    
    # ========================================================================
    # Auto-Activation
    # ========================================================================
    
    async def track_access(self, document_id: str) -> bool:
        """
        Track document access for auto-activation.
        
        Returns:
            True if document should be activated (threshold exceeded)
        """
        access_key = self._access_key(document_id)
        
        try:
            # Increment access counter with sliding window
            pipe = self.redis.pipeline()
            pipe.incr(access_key)
            pipe.expire(access_key, self.ACTIVATION_WINDOW)
            results = await pipe.execute()
            
            count = results[0]
            
            return count >= self.ACTIVATION_THRESHOLD
        except Exception as e:
            logger.warning("Failed to track access", document_id=document_id, error=str(e))
            return False
    
    async def should_cache(self, document_id: str) -> bool:
        """
        Check if a document should be cached based on access patterns.
        
        Returns:
            True if caching is recommended
        """
        # Already cached?
        meta_key = self._meta_key(document_id)
        if await self.redis.exists(meta_key):
            return False  # Already cached
        
        # Check access count
        access_key = self._access_key(document_id)
        count = await self.redis.get(access_key)
        
        if count and int(count) >= self.ACTIVATION_THRESHOLD:
            return True
        
        return False
    
    # ========================================================================
    # Stats and Monitoring
    # ========================================================================
    
    async def get_cache_stats(self) -> Dict:
        """
        Get cache statistics.
        
        Returns:
            Dict with cache stats
        """
        pattern = f"{self.PREFIX}:*:{self.META_SUFFIX}"
        cursor = 0
        total = 0
        dirty = 0
        total_records = 0
        
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            
            for key in keys:
                total += 1
                try:
                    meta = await self.redis.hgetall(key)
                    if meta:
                        if meta.get("dirty") == "1":
                            dirty += 1
                        total_records += int(meta.get("record_count", 0))
                except Exception:
                    pass
            
            if cursor == 0:
                break
        
        return {
            "cached_documents": total,
            "dirty_documents": dirty,
            "total_records": total_records,
            "ttl_seconds": self.ttl,
            "flush_interval_seconds": self.FLUSH_INTERVAL,
        }
    
    async def get_document_stats(self, document_id: str) -> Optional[Dict]:
        """
        Get stats for a specific cached document.
        
        Returns:
            Dict with document stats or None if not cached
        """
        meta_key = self._meta_key(document_id)
        
        try:
            meta = await self.redis.hgetall(meta_key)
            if not meta:
                return None
            
            ttl = await self.redis.ttl(meta_key)
            
            return {
                "document_id": document_id,
                "cached": True,
                "version": int(meta.get("version", 1)),
                "record_count": int(meta.get("record_count", 0)),
                "dirty": meta.get("dirty") == "1",
                "cached_at": datetime.fromtimestamp(float(meta.get("cached_at", 0))).isoformat(),
                "last_accessed": datetime.fromtimestamp(float(meta.get("last_accessed", 0))).isoformat(),
                "access_count": int(meta.get("access_count", 0)),
                "ttl_remaining": ttl,
            }
        except Exception as e:
            logger.error("Failed to get document stats", document_id=document_id, error=str(e))
            return None


class LockError(Exception):
    """Exception raised when a lock cannot be acquired."""
    pass
