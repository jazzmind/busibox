"""
Redis Streams client wrapper for job queue.

Handles job queuing and consumer group management.
"""

import json
from datetime import datetime
from typing import Dict, Optional

try:
    import redis.asyncio as redis
except ImportError:
    # Fallback for older redis versions
    import redis
    import asyncio
    
    # Wrap sync redis in async
    class AsyncRedisWrapper:
        def __init__(self, *args, **kwargs):
            self._client = redis.Redis(*args, **kwargs)
        
        async def ping(self):
            return self._client.ping()
        
        async def xadd(self, *args, **kwargs):
            return self._client.xadd(*args, **kwargs)
        
        async def xgroup_create(self, *args, **kwargs):
            return self._client.xgroup_create(*args, **kwargs)
        
        async def close(self):
            self._client.close()
    
    redis = type('redis', (), {'Redis': AsyncRedisWrapper})()
import structlog

logger = structlog.get_logger()


class RedisService:
    """Service for Redis Streams operations."""
    
    def __init__(self, config: dict):
        """Initialize Redis client."""
        self.config = config
        self.host = config.get("redis_host", "10.96.200.29")
        self.port = config.get("redis_port", 6379)
        self.stream_name = config.get("redis_stream", "jobs:ingestion")
        self.consumer_group = config.get("redis_consumer_group", "workers")
        
        self.client: Optional[redis.Redis] = None
    
    async def connect(self):
        """Connect to Redis."""
        if not self.client:
            self.client = await redis.Redis(
                host=self.host,
                port=self.port,
                decode_responses=True,
            )
            logger.info("Connected to Redis", host=self.host, port=self.port)
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.client:
            await self.client.close()
            self.client = None
            logger.info("Disconnected from Redis")
    
    async def check_health(self):
        """Check Redis connectivity."""
        if not self.client:
            await self.connect()
        
        try:
            await self.client.ping()
            return True
        except Exception as e:
            logger.error("Redis health check failed", error=str(e))
            raise
    
    async def add_job(
        self,
        file_id: str,
        user_id: str,
        storage_path: str,
        mime_type: str,
        original_filename: str,
        metadata: Optional[Dict] = None,
    ) -> str:
        """
        Add job to Redis Streams queue.
        
        Args:
            file_id: Unique file identifier
            user_id: User who uploaded the file
            storage_path: S3 path in MinIO
            mime_type: MIME type of the file
            original_filename: Original filename
            metadata: Optional metadata dict
        
        Returns:
            Message ID (stream entry ID)
        """
        if not self.client:
            await self.connect()
        
        job_data = {
            "job_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "storage_path": storage_path,
            "mime_type": mime_type,
            "original_filename": original_filename,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        if metadata:
            job_data["metadata"] = json.dumps(metadata)
        
        try:
            message_id = await self.client.xadd(
                self.stream_name,
                job_data,
                maxlen=10000,  # Limit stream to 10k messages
            )
            
            logger.info(
                "Job added to Redis Streams",
                stream=self.stream_name,
                file_id=file_id,
                message_id=message_id,
            )
            
            return message_id
        except Exception as e:
            logger.error(
                "Failed to add job to Redis",
                stream=self.stream_name,
                file_id=file_id,
                error=str(e),
            )
            raise
    
    async def ensure_consumer_group(self):
        """Ensure consumer group exists (idempotent)."""
        if not self.client:
            await self.connect()
        
        try:
            await self.client.xgroup_create(
                self.stream_name,
                self.consumer_group,
                id="0",
                mkstream=True,
            )
            logger.info(
                "Consumer group created",
                stream=self.stream_name,
                group=self.consumer_group,
            )
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                # Group already exists - this is fine
                logger.debug(
                    "Consumer group already exists",
                    stream=self.stream_name,
                    group=self.consumer_group,
                )
            else:
                raise

