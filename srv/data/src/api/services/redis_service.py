"""
Redis Streams client wrapper for job queue.

Handles job queuing and consumer group management.
"""

import json
from datetime import datetime
from typing import Dict, Optional

import redis.asyncio as redis_async
from redis.exceptions import ResponseError
import structlog

logger = structlog.get_logger()


class RedisService:
    """Service for Redis Streams operations."""
    
    def __init__(self, config: dict):
        """Initialize Redis client."""
        self.config = config
        self.host = config.get("redis_host", "redis")
        self.port = config.get("redis_port", 6379)
        base_stream = config.get("redis_stream", "jobs:data")
        self.stream_name = base_stream  # kept for backward compat
        self.stream_high = f"{base_stream}:high"
        self.stream_low = f"{base_stream}:low"
        self.consumer_group = config.get("redis_consumer_group", "workers")
        
        self.client: Optional[redis_async.Redis] = None
    
    async def connect(self):
        """Connect to Redis."""
        if not self.client:
            self.client = await redis_async.Redis(
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
        processing_config: Optional[Dict] = None,
        visibility: str = "personal",
        role_ids: Optional[list] = None,
        delegation_token: Optional[str] = None,
        priority: str = "high",
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
            processing_config: Optional processing configuration dict
            visibility: Document visibility ('personal' or 'shared')
            role_ids: List of role UUIDs for shared documents
            delegation_token: Delegation JWT for worker to use for Zero Trust
                             token exchange during background processing
            priority: 'high' for Pass 1 jobs (default), 'low' for enhancement passes
        
        Returns:
            Message ID (stream entry ID)
        """
        if not self.client:
            await self.connect()
        
        stream = self.stream_high if priority == "high" else self.stream_low

        job_data = {
            "job_id": file_id,
            "file_id": file_id,
            "user_id": user_id,
            "storage_path": storage_path,
            "mime_type": mime_type,
            "original_filename": original_filename,
            "created_at": datetime.utcnow().isoformat(),
            "visibility": visibility,
            "priority": priority,
        }
        
        if metadata:
            job_data["metadata"] = json.dumps(metadata)
        
        if processing_config:
            job_data["processing_config"] = json.dumps(processing_config)
        
        if role_ids:
            job_data["role_ids"] = json.dumps(role_ids)
        
        if delegation_token:
            job_data["delegation_token"] = delegation_token
        
        try:
            message_id = await self.client.xadd(
                stream,
                job_data,
                maxlen=10000,
            )
            
            logger.info(
                "Job added to Redis Streams",
                stream=stream,
                file_id=file_id,
                message_id=message_id,
                priority=priority,
            )
            
            return message_id
        except Exception as e:
            logger.error(
                "Failed to add job to Redis",
                stream=stream,
                file_id=file_id,
                error=str(e),
            )
            raise
    
    async def ensure_consumer_group(self):
        """Ensure consumer groups exist on both priority streams (idempotent)."""
        if not self.client:
            await self.connect()

        for stream in (self.stream_high, self.stream_low):
            try:
                await self.client.xgroup_create(
                    stream,
                    self.consumer_group,
                    id="0",
                    mkstream=True,
                )
                logger.info(
                    "Consumer group created",
                    stream=stream,
                    group=self.consumer_group,
                )
            except ResponseError as e:
                if "BUSYGROUP" in str(e):
                    logger.debug(
                        "Consumer group already exists",
                        stream=stream,
                        group=self.consumer_group,
                    )
                else:
                    raise

