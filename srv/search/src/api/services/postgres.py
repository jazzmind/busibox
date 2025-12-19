"""
PostgreSQL connection pool service for Search API.
"""

import asyncpg
from typing import Optional
import structlog

logger = structlog.get_logger()


class PostgresService:
    """PostgreSQL connection pool service."""
    
    def __init__(self, config: dict):
        """Initialize PostgreSQL connection pool."""
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", 5432)
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "app_user")
        self.password = config.get("postgres_password", "")
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create connection pool."""
        if not self.pool:
            self.pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                min_size=2,
                max_size=10,
            )
            logger.info("Search API PostgreSQL pool created")
    
    async def disconnect(self):
        """Close connection pool."""
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Search API PostgreSQL pool closed")
    
    async def acquire(self):
        """Get a connection from the pool."""
        if not self.pool:
            await self.connect()
        return self.pool.acquire()





