import uuid
from contextlib import asynccontextmanager
from typing import Optional, List

import asyncpg
import structlog

from middleware.rls import set_rls_session_vars

logger = structlog.get_logger()


class PostgresService:
    def __init__(self, config: dict):
        self.host = config.get("postgres_host", "10.96.200.203")
        self.port = config.get("postgres_port", 5432)
        self.database = config.get("postgres_db", "busibox")
        self.user = config.get("postgres_user", "busibox_user")
        self.password = config.get("postgres_password", "")
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
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
            logger.info("Authz PostgreSQL pool created")

    async def disconnect(self):
        if self.pool:
            await self.pool.close()
            self.pool = None
            logger.info("Authz PostgreSQL pool closed")

    @asynccontextmanager
    async def acquire(self, user_id: str | None, role_ids: List[str] | None):
        if not self.pool:
            await self.connect()
        async with self.pool.acquire() as conn:
            await set_rls_session_vars(conn, user_id, role_ids)
            yield conn

    async def insert_audit(self, actor_id: str, action: str, resource_type: str, resource_id: str | None, details: dict, user_id: str | None, role_ids: List[str] | None):
        async with self.acquire(user_id, role_ids) as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs (actor_id, action, resource_type, resource_id, details)
                VALUES ($1, $2, $3, $4, $5)
                """,
                uuid.UUID(actor_id),
                action,
                resource_type,
                uuid.UUID(resource_id) if resource_id else None,
                details or {},
            )

