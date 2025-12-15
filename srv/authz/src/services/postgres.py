import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional, List

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
        self._issuer = config.get("issuer", "busibox-authz")
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
            # Ensure required tables exist (idempotent).
            await self.ensure_schema()

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

    async def ensure_schema(self) -> None:
        """
        Create authz tables if missing.

        We intentionally use CREATE TABLE IF NOT EXISTS to avoid a migration dependency
        for this service; Busibox infra can later formalize migrations if desired.
        """
        if not self.pool:
            # connect() will call ensure_schema() again; avoid recursion
            return
        async with self.pool.acquire() as conn:
            # Needed for gen_random_uuid() default. If extension install is restricted in an env,
            # infra should pre-provision it on the Busibox cluster DB.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  actor_id uuid NOT NULL,
                  action text NOT NULL,
                  resource_type text NOT NULL,
                  resource_id uuid NULL,
                  details jsonb NOT NULL DEFAULT '{}'::jsonb,
                  created_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_oauth_clients (
                  client_id text PRIMARY KEY,
                  client_secret_hash text NOT NULL,
                  allowed_audiences text[] NOT NULL DEFAULT '{}'::text[],
                  allowed_scopes text[] NOT NULL DEFAULT '{}'::text[],
                  is_active boolean NOT NULL DEFAULT true,
                  created_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_signing_keys (
                  kid text PRIMARY KEY,
                  alg text NOT NULL,
                  private_key_pem bytea NOT NULL,
                  public_jwk jsonb NOT NULL,
                  is_active boolean NOT NULL DEFAULT true,
                  created_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_roles (
                  id uuid PRIMARY KEY,
                  name text NOT NULL UNIQUE,
                  description text NULL,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  updated_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_users (
                  user_id uuid PRIMARY KEY,
                  email text NOT NULL,
                  status text NULL,
                  idp_provider text NULL,
                  idp_tenant_id text NULL,
                  idp_object_id text NULL,
                  idp_roles jsonb NOT NULL DEFAULT '[]'::jsonb,
                  idp_groups jsonb NOT NULL DEFAULT '[]'::jsonb,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  updated_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_user_roles (
                  user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
                  role_id uuid NOT NULL REFERENCES authz_roles(id) ON DELETE CASCADE,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  PRIMARY KEY (user_id, role_id)
                );
                """
            )

    # ---------------------------------------------------------------------
    # Audit
    # ---------------------------------------------------------------------

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

    # ---------------------------------------------------------------------
    # OAuth client registry
    # ---------------------------------------------------------------------

    async def upsert_oauth_client(
        self,
        *,
        client_id: str,
        client_secret_hash: str,
        allowed_audiences: List[str],
        allowed_scopes: List[str],
        is_active: bool = True,
    ) -> None:
        async with self.acquire(None, None) as conn:
            await conn.execute(
                """
                INSERT INTO authz_oauth_clients (client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (client_id) DO UPDATE
                  SET client_secret_hash = EXCLUDED.client_secret_hash,
                      allowed_audiences = EXCLUDED.allowed_audiences,
                      allowed_scopes = EXCLUDED.allowed_scopes,
                      is_active = EXCLUDED.is_active
                """,
                client_id,
                client_secret_hash,
                allowed_audiences,
                allowed_scopes,
                is_active,
            )

    async def get_oauth_client(self, client_id: str) -> Optional[dict]:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active
                FROM authz_oauth_clients
                WHERE client_id = $1
                """,
                client_id,
            )
            return dict(row) if row else None

    # ---------------------------------------------------------------------
    # Signing keys / JWKS
    # ---------------------------------------------------------------------

    async def insert_signing_key(self, *, kid: str, alg: str, private_key_pem: bytes, public_jwk: dict, is_active: bool = True) -> None:
        import json
        async with self.acquire(None, None) as conn:
            await conn.execute(
                """
                INSERT INTO authz_signing_keys (kid, alg, private_key_pem, public_jwk, is_active)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (kid) DO UPDATE
                  SET alg = EXCLUDED.alg,
                      private_key_pem = EXCLUDED.private_key_pem,
                      public_jwk = EXCLUDED.public_jwk,
                      is_active = EXCLUDED.is_active
                """,
                kid,
                alg,
                private_key_pem,
                json.dumps(public_jwk),  # JSONB columns need JSON string input
                is_active,
            )

    async def get_active_signing_key(self) -> Optional[dict]:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT kid, alg, private_key_pem, public_jwk
                FROM authz_signing_keys
                WHERE is_active = true
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            return dict(row) if row else None

    async def list_public_jwks(self) -> List[dict]:
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT public_jwk
                FROM authz_signing_keys
                WHERE is_active = true
                ORDER BY created_at DESC
                """
            )
            return [dict(r)["public_jwk"] for r in rows]

    # ---------------------------------------------------------------------
    # RBAC sync (initially driven by ai-portal)
    # ---------------------------------------------------------------------

    async def upsert_roles(self, roles: List[dict]) -> None:
        if not roles:
            return
        async with self.acquire(None, None) as conn:
            for r in roles:
                await conn.execute(
                    """
                    INSERT INTO authz_roles (id, name, description)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE
                      SET name = EXCLUDED.name,
                          description = EXCLUDED.description,
                          updated_at = now()
                    """,
                    uuid.UUID(r["id"]),
                    r["name"],
                    r.get("description"),
                )

    async def upsert_user_and_roles(
        self,
        *,
        user_id: str,
        email: str,
        status: str | None,
        idp_provider: str | None,
        idp_tenant_id: str | None,
        idp_object_id: str | None,
        idp_roles: List[str],
        idp_groups: List[str],
        user_role_ids: List[str],
    ) -> None:
        uid = uuid.UUID(user_id)
        async with self.acquire(user_id, user_role_ids) as conn:
            await conn.execute(
                """
                INSERT INTO authz_users (user_id, email, status, idp_provider, idp_tenant_id, idp_object_id, idp_roles, idp_groups)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (user_id) DO UPDATE
                  SET email = EXCLUDED.email,
                      status = EXCLUDED.status,
                      idp_provider = EXCLUDED.idp_provider,
                      idp_tenant_id = EXCLUDED.idp_tenant_id,
                      idp_object_id = EXCLUDED.idp_object_id,
                      idp_roles = EXCLUDED.idp_roles,
                      idp_groups = EXCLUDED.idp_groups,
                      updated_at = now()
                """,
                uid,
                email,
                status,
                idp_provider,
                idp_tenant_id,
                idp_object_id,
                idp_roles,
                idp_groups,
            )

            # Replace role assignments atomically (best-effort).
            await conn.execute("DELETE FROM authz_user_roles WHERE user_id = $1", uid)
            for rid in user_role_ids:
                await conn.execute(
                    "INSERT INTO authz_user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    uid,
                    uuid.UUID(rid),
                )

    async def get_user_roles(self, user_id: str) -> List[dict]:
        uid = uuid.UUID(user_id)
        async with self.acquire(user_id, None) as conn:
            rows = await conn.fetch(
                """
                SELECT r.id::text AS id, r.name AS name, r.description, r.created_at, r.updated_at
                FROM authz_user_roles ur
                JOIN authz_roles r ON r.id = ur.role_id
                WHERE ur.user_id = $1
                ORDER BY r.name
                """,
                uid,
            )
            return [dict(r) for r in rows]

    # ---------------------------------------------------------------------
    # RBAC admin operations
    # ---------------------------------------------------------------------

    async def create_role(self, *, name: str, description: str | None) -> dict:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_roles (id, name, description)
                VALUES (gen_random_uuid(), $1, $2)
                RETURNING id::text, name, description, created_at, updated_at
                """,
                name,
                description,
            )
            return dict(row)

    async def list_roles(self) -> List[dict]:
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, name, description, created_at, updated_at
                FROM authz_roles
                ORDER BY name
                """
            )
            return [dict(r) for r in rows]

    async def get_role(self, role_id: str) -> dict | None:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, name, description, created_at, updated_at
                FROM authz_roles
                WHERE id = $1
                """,
                uuid.UUID(role_id),
            )
            return dict(row) if row else None

    async def update_role(self, *, role_id: str, name: str | None, description: str | None) -> dict | None:
        async with self.acquire(None, None) as conn:
            # Build dynamic update query
            updates = []
            params = []
            param_idx = 1

            if name is not None:
                updates.append(f"name = ${param_idx}")
                params.append(name)
                param_idx += 1

            if description is not None:
                updates.append(f"description = ${param_idx}")
                params.append(description)
                param_idx += 1

            if not updates:
                return await self.get_role(role_id)

            updates.append("updated_at = now()")
            params.append(uuid.UUID(role_id))

            query = f"""
                UPDATE authz_roles
                SET {', '.join(updates)}
                WHERE id = ${param_idx}
                RETURNING id::text, name, description, created_at, updated_at
            """

            row = await conn.fetchrow(query, *params)
            return dict(row) if row else None

    async def delete_role(self, role_id: str) -> bool:
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_roles WHERE id = $1",
                uuid.UUID(role_id),
            )
            return result != "DELETE 0"

    async def add_user_role(self, *, user_id: str, role_id: str) -> dict:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, role_id) DO UPDATE
                  SET created_at = authz_user_roles.created_at
                RETURNING user_id::text, role_id::text, created_at
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
            return dict(row)

    async def remove_user_role(self, *, user_id: str, role_id: str) -> bool:
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
            return result != "DELETE 0"





