import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional, List

import asyncpg
import structlog

from middleware.rls import set_rls_session_vars

logger = structlog.get_logger()


def validate_uuid(value: str, field_name: str = "id") -> uuid.UUID:
    """
    Validate and convert a string to UUID.
    
    Args:
        value: String to convert to UUID
        field_name: Name of the field for error messages
        
    Returns:
        UUID object
        
    Raises:
        ValueError: If the string is not a valid UUID
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as e:
        raise ValueError(f"Invalid UUID format for {field_name}: {value}") from e


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
                  scopes text[] NOT NULL DEFAULT '{}'::text[],
                  created_at timestamptz NOT NULL DEFAULT now(),
                  updated_at timestamptz NOT NULL DEFAULT now()
                );
                """
            )
            
            # Migration: Add scopes column if missing (for existing deployments)
            await conn.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'authz_roles' AND column_name = 'scopes'
                    ) THEN
                        ALTER TABLE authz_roles ADD COLUMN scopes text[] NOT NULL DEFAULT '{}'::text[];
                    END IF;
                END $$;
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
            
            # ----------------------------------------------------------------
            # Envelope Encryption Keystore Tables
            # ----------------------------------------------------------------
            
            # Master Key Encryption Keys (KEKs) - one per role or user
            # The KEK itself is encrypted with the system master key (from env)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_key_encryption_keys (
                  kek_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  owner_type text NOT NULL CHECK (owner_type IN ('role', 'user', 'system')),
                  owner_id uuid NULL,  -- NULL for system-level keys
                  encrypted_key bytea NOT NULL,  -- KEK encrypted with master key
                  key_algorithm text NOT NULL DEFAULT 'AES-256-GCM',
                  key_version integer NOT NULL DEFAULT 1,
                  is_active boolean NOT NULL DEFAULT true,
                  created_at timestamptz NOT NULL DEFAULT now(),
                  rotated_at timestamptz NULL,
                  UNIQUE (owner_type, owner_id, key_version)
                );
                """
            )
            
            # Data Encryption Key registry - tracks DEKs wrapped with KEKs
            # Each file's DEK can be wrapped with multiple KEKs (one per authorized role)
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS authz_wrapped_data_keys (
                  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                  file_id uuid NOT NULL,  -- Reference to the encrypted file
                  kek_id uuid NOT NULL REFERENCES authz_key_encryption_keys(kek_id) ON DELETE CASCADE,
                  wrapped_dek bytea NOT NULL,  -- DEK encrypted with the KEK
                  dek_algorithm text NOT NULL DEFAULT 'AES-256-GCM',
                  created_at timestamptz NOT NULL DEFAULT now(),
                  UNIQUE (file_id, kek_id)
                );
                """
            )
            
            # Index for fast lookups by file_id
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_wrapped_data_keys_file_id 
                ON authz_wrapped_data_keys(file_id);
                """
            )

    # ---------------------------------------------------------------------
    # Audit
    # ---------------------------------------------------------------------

    async def insert_audit(self, actor_id: str, action: str, resource_type: str, resource_id: str | None, details: dict, user_id: str | None, role_ids: List[str] | None):
        async with self.acquire(user_id, role_ids) as conn:
            import json
            await conn.execute(
                """
                INSERT INTO audit_logs (actor_id, action, resource_type, resource_id, details)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                uuid.UUID(actor_id),
                action,
                resource_type,
                uuid.UUID(resource_id) if resource_id else None,
                json.dumps(details or {}),
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
                SELECT client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active, created_at
                FROM authz_oauth_clients
                WHERE client_id = $1
                """,
                client_id,
            )
            return dict(row) if row else None

    async def create_oauth_client(
        self,
        *,
        client_id: str,
        client_secret_hash: str,
        allowed_audiences: List[str],
        allowed_scopes: List[str],
        is_active: bool = True,
    ) -> None:
        """Create a new OAuth client (alias for upsert for clarity in admin endpoints)."""
        await self.upsert_oauth_client(
            client_id=client_id,
            client_secret_hash=client_secret_hash,
            allowed_audiences=allowed_audiences,
            allowed_scopes=allowed_scopes,
            is_active=is_active,
        )

    async def list_oauth_clients(self) -> List[dict]:
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT client_id, allowed_audiences, allowed_scopes, is_active, created_at
                FROM authz_oauth_clients
                ORDER BY created_at DESC
                """
            )
            return [dict(r) for r in rows]

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
        import json
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT public_jwk
                FROM authz_signing_keys
                WHERE is_active = true
                ORDER BY created_at DESC
                """
            )
            # JSONB columns are returned as strings, need to parse them
            return [json.loads(dict(r)["public_jwk"]) if isinstance(dict(r)["public_jwk"], str) else dict(r)["public_jwk"] for r in rows]

    # ---------------------------------------------------------------------
    # RBAC sync (initially driven by ai-portal)
    # ---------------------------------------------------------------------

    async def upsert_roles(self, roles: List[dict]) -> dict[str, str]:
        """
        Upsert roles and return a mapping of role names to their IDs.
        Returns: dict mapping role name -> role ID (as string)
        """
        if not roles:
            return {}
        name_to_id: dict[str, str] = {}
        async with self.acquire(None, None) as conn:
            for r in roles:
                scopes = r.get("scopes", [])
                # First, check if a role with this name already exists
                existing = await conn.fetchrow(
                    "SELECT id FROM authz_roles WHERE name = $1",
                    r["name"]
                )
                if existing:
                    # Role with this name exists, use its ID and update description/scopes if provided
                    role_id = existing["id"]
                    await conn.execute(
                        """
                        UPDATE authz_roles
                        SET description = COALESCE($1, description),
                            scopes = CASE WHEN $2::text[] = '{}'::text[] THEN scopes ELSE $2 END,
                            updated_at = now()
                        WHERE id = $3
                        """,
                        r.get("description"),
                        scopes,
                        role_id,
                    )
                    name_to_id[r["name"]] = str(role_id)
                else:
                    # Role doesn't exist, insert with provided ID
                    role_id = uuid.UUID(r["id"])
                    await conn.execute(
                        """
                        INSERT INTO authz_roles (id, name, description, scopes)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (id) DO UPDATE
                          SET name = EXCLUDED.name,
                              description = EXCLUDED.description,
                              scopes = EXCLUDED.scopes,
                              updated_at = now()
                        """,
                        role_id,
                        r["name"],
                        r.get("description"),
                        scopes,
                    )
                    name_to_id[r["name"]] = str(role_id)
        return name_to_id

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
                json.dumps(idp_roles) if idp_roles else '[]',  # Convert list to JSON string for JSONB column
                json.dumps(idp_groups) if idp_groups else '[]',  # Convert list to JSON string for JSONB column
            )

            # Replace role assignments atomically (best-effort).
            await conn.execute("DELETE FROM authz_user_roles WHERE user_id = $1", uid)
            for rid in user_role_ids:
                await conn.execute(
                    "INSERT INTO authz_user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    uid,
                    uuid.UUID(rid),
                )

    async def user_exists(self, user_id: str) -> bool:
        """Check if a user exists in authz_users."""
        try:
            uid = uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            # Invalid UUID format
            return False
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM authz_users WHERE user_id = $1",
                uid,
            )
            return row is not None

    async def get_user_roles(self, user_id: str) -> List[dict]:
        try:
            uid = uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            # Invalid UUID format - return empty list
            return []
        
        async with self.acquire(user_id, None) as conn:
            rows = await conn.fetch(
                """
                SELECT r.id::text AS id, r.name AS name, r.description, r.scopes, r.created_at, r.updated_at
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

    async def create_role(self, *, name: str, description: str | None, scopes: List[str] | None = None) -> dict:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_roles (id, name, description, scopes)
                VALUES (gen_random_uuid(), $1, $2, $3)
                RETURNING id::text, name, description, scopes, created_at, updated_at
                """,
                name,
                description,
                scopes or [],
            )
            return dict(row)

    async def list_roles(self) -> List[dict]:
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, name, description, scopes, created_at, updated_at
                FROM authz_roles
                ORDER BY name
                """
            )
            return [dict(r) for r in rows]

    async def get_role(self, role_id: str) -> dict | None:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, name, description, scopes, created_at, updated_at
                FROM authz_roles
                WHERE id = $1
                """,
                uuid.UUID(role_id),
            )
            return dict(row) if row else None

    async def get_role_by_id(self, role_id: str) -> dict | None:
        """Alias for get_role for consistency."""
        return await self.get_role(role_id)

    async def get_role_by_name(self, name: str) -> dict | None:
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, name, description, created_at, updated_at
                FROM authz_roles
                WHERE name = $1
                """,
                name,
            )
            return dict(row) if row else None

    async def update_role(self, *, role_id: str, name: str | None, description: str | None, scopes: List[str] | None = None) -> dict | None:
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

            if scopes is not None:
                updates.append(f"scopes = ${param_idx}")
                params.append(scopes)
                param_idx += 1

            if not updates:
                return await self.get_role(role_id)

            updates.append("updated_at = now()")
            params.append(uuid.UUID(role_id))

            query = f"""
                UPDATE authz_roles
                SET {', '.join(updates)}
                WHERE id = ${param_idx}
                RETURNING id::text, name, description, scopes, created_at, updated_at
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
        # Validate UUIDs before database operation
        uid = validate_uuid(user_id, "user_id")
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT (user_id, role_id) DO UPDATE
                  SET created_at = authz_user_roles.created_at
                RETURNING user_id::text, role_id::text, created_at
                """,
                uid,
                rid,
            )
            return dict(row)

    async def remove_user_role(self, *, user_id: str, role_id: str) -> bool:
        # Validate UUIDs before database operation
        uid = validate_uuid(user_id, "user_id")
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                uid,
                rid,
            )
            return result != "DELETE 0"

    # ---------------------------------------------------------------------
    # Envelope Encryption - Key Encryption Keys (KEKs)
    # ---------------------------------------------------------------------

    async def create_kek(
        self,
        *,
        owner_type: str,
        owner_id: str | None,
        encrypted_key: bytes,
        key_algorithm: str = "AES-256-GCM",
    ) -> dict:
        """
        Create a new Key Encryption Key for a role, user, or system.
        
        Args:
            owner_type: 'role', 'user', or 'system'
            owner_id: UUID of the role/user, or None for system keys
            encrypted_key: The KEK encrypted with the system master key
            key_algorithm: Algorithm used for encryption (default AES-256-GCM)
            
        Returns:
            Dict with kek_id and metadata
        """
        oid = uuid.UUID(owner_id) if owner_id else None
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_key_encryption_keys 
                    (owner_type, owner_id, encrypted_key, key_algorithm)
                VALUES ($1, $2, $3, $4)
                RETURNING kek_id::text, owner_type, owner_id::text, key_algorithm, 
                          key_version, is_active, created_at
                """,
                owner_type,
                oid,
                encrypted_key,
                key_algorithm,
            )
            return dict(row)

    async def get_kek(self, kek_id: str) -> Optional[dict]:
        """Get a KEK by ID."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT kek_id::text, owner_type, owner_id::text, encrypted_key, 
                       key_algorithm, key_version, is_active, created_at, rotated_at
                FROM authz_key_encryption_keys
                WHERE kek_id = $1 AND is_active = true
                """,
                uuid.UUID(kek_id),
            )
            return dict(row) if row else None

    async def get_kek_for_owner(
        self, owner_type: str, owner_id: str | None
    ) -> Optional[dict]:
        """
        Get the active KEK for a specific owner (role, user, or system).
        Returns the most recent active key version.
        """
        oid = uuid.UUID(owner_id) if owner_id else None
        
        async with self.acquire(None, None) as conn:
            if oid:
                row = await conn.fetchrow(
                    """
                    SELECT kek_id::text, owner_type, owner_id::text, encrypted_key, 
                           key_algorithm, key_version, is_active, created_at, rotated_at
                    FROM authz_key_encryption_keys
                    WHERE owner_type = $1 AND owner_id = $2 AND is_active = true
                    ORDER BY key_version DESC
                    LIMIT 1
                    """,
                    owner_type,
                    oid,
                )
            else:
                row = await conn.fetchrow(
                    """
                    SELECT kek_id::text, owner_type, owner_id::text, encrypted_key, 
                           key_algorithm, key_version, is_active, created_at, rotated_at
                    FROM authz_key_encryption_keys
                    WHERE owner_type = $1 AND owner_id IS NULL AND is_active = true
                    ORDER BY key_version DESC
                    LIMIT 1
                    """,
                    owner_type,
                )
            return dict(row) if row else None

    async def get_keks_for_roles(self, role_ids: List[str]) -> List[dict]:
        """Get active KEKs for multiple roles."""
        if not role_ids:
            return []
        
        rids = [uuid.UUID(rid) for rid in role_ids]
        
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT ON (owner_id) 
                    kek_id::text, owner_type, owner_id::text, encrypted_key, 
                    key_algorithm, key_version, is_active, created_at, rotated_at
                FROM authz_key_encryption_keys
                WHERE owner_type = 'role' AND owner_id = ANY($1) AND is_active = true
                ORDER BY owner_id, key_version DESC
                """,
                rids,
            )
            return [dict(row) for row in rows]

    async def rotate_kek(
        self,
        *,
        owner_type: str,
        owner_id: str | None,
        new_encrypted_key: bytes,
    ) -> dict:
        """
        Rotate a KEK by creating a new version and marking old ones inactive.
        
        Returns the new KEK record.
        """
        oid = uuid.UUID(owner_id) if owner_id else None
        
        async with self.acquire(None, None) as conn:
            async with conn.transaction():
                # Get current max version
                if oid:
                    current = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(key_version), 0)
                        FROM authz_key_encryption_keys
                        WHERE owner_type = $1 AND owner_id = $2
                        """,
                        owner_type,
                        oid,
                    )
                else:
                    current = await conn.fetchval(
                        """
                        SELECT COALESCE(MAX(key_version), 0)
                        FROM authz_key_encryption_keys
                        WHERE owner_type = $1 AND owner_id IS NULL
                        """,
                        owner_type,
                    )
                
                new_version = current + 1
                
                # Mark old keys as inactive
                if oid:
                    await conn.execute(
                        """
                        UPDATE authz_key_encryption_keys
                        SET is_active = false, rotated_at = now()
                        WHERE owner_type = $1 AND owner_id = $2 AND is_active = true
                        """,
                        owner_type,
                        oid,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE authz_key_encryption_keys
                        SET is_active = false, rotated_at = now()
                        WHERE owner_type = $1 AND owner_id IS NULL AND is_active = true
                        """,
                        owner_type,
                    )
                
                # Insert new key
                row = await conn.fetchrow(
                    """
                    INSERT INTO authz_key_encryption_keys 
                        (owner_type, owner_id, encrypted_key, key_version)
                    VALUES ($1, $2, $3, $4)
                    RETURNING kek_id::text, owner_type, owner_id::text, key_algorithm, 
                              key_version, is_active, created_at
                    """,
                    owner_type,
                    oid,
                    new_encrypted_key,
                    new_version,
                )
                return dict(row)

    # ---------------------------------------------------------------------
    # Envelope Encryption - Wrapped Data Keys (DEKs)
    # ---------------------------------------------------------------------

    async def store_wrapped_dek(
        self,
        *,
        file_id: str,
        kek_id: str,
        wrapped_dek: bytes,
        dek_algorithm: str = "AES-256-GCM",
    ) -> dict:
        """
        Store a wrapped (encrypted) Data Encryption Key for a file.
        
        A file can have multiple wrapped DEKs - one per authorized KEK (role/user).
        """
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_wrapped_data_keys (file_id, kek_id, wrapped_dek, dek_algorithm)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (file_id, kek_id) DO UPDATE
                    SET wrapped_dek = EXCLUDED.wrapped_dek,
                        dek_algorithm = EXCLUDED.dek_algorithm
                RETURNING id::text, file_id::text, kek_id::text, dek_algorithm, created_at
                """,
                uuid.UUID(file_id),
                uuid.UUID(kek_id),
                wrapped_dek,
                dek_algorithm,
            )
            return dict(row)

    async def get_wrapped_deks_for_file(self, file_id: str) -> List[dict]:
        """
        Get all wrapped DEKs for a file.
        Returns wrapped DEKs with their associated KEK info.
        """
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT 
                    wdk.id::text,
                    wdk.file_id::text,
                    wdk.kek_id::text,
                    wdk.wrapped_dek,
                    wdk.dek_algorithm,
                    wdk.created_at,
                    kek.owner_type,
                    kek.owner_id::text as kek_owner_id,
                    kek.encrypted_key as kek_encrypted_key,
                    kek.key_algorithm as kek_algorithm
                FROM authz_wrapped_data_keys wdk
                JOIN authz_key_encryption_keys kek ON wdk.kek_id = kek.kek_id
                WHERE wdk.file_id = $1 AND kek.is_active = true
                """,
                uuid.UUID(file_id),
            )
            return [dict(row) for row in rows]

    async def get_wrapped_dek_for_roles(
        self, file_id: str, role_ids: List[str]
    ) -> Optional[dict]:
        """
        Get a wrapped DEK that the user can unwrap based on their roles.
        Returns the first matching wrapped DEK.
        """
        if not role_ids:
            return None
        
        rids = [uuid.UUID(rid) for rid in role_ids]
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    wdk.id::text,
                    wdk.file_id::text,
                    wdk.kek_id::text,
                    wdk.wrapped_dek,
                    wdk.dek_algorithm,
                    wdk.created_at,
                    kek.owner_type,
                    kek.owner_id::text as kek_owner_id,
                    kek.encrypted_key as kek_encrypted_key,
                    kek.key_algorithm as kek_algorithm
                FROM authz_wrapped_data_keys wdk
                JOIN authz_key_encryption_keys kek ON wdk.kek_id = kek.kek_id
                WHERE wdk.file_id = $1 
                    AND kek.owner_type = 'role'
                    AND kek.owner_id = ANY($2)
                    AND kek.is_active = true
                LIMIT 1
                """,
                uuid.UUID(file_id),
                rids,
            )
            return dict(row) if row else None

    async def get_wrapped_dek_for_user(
        self, file_id: str, user_id: str
    ) -> Optional[dict]:
        """
        Get a wrapped DEK for a specific user (for personal files).
        """
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT 
                    wdk.id::text,
                    wdk.file_id::text,
                    wdk.kek_id::text,
                    wdk.wrapped_dek,
                    wdk.dek_algorithm,
                    wdk.created_at,
                    kek.owner_type,
                    kek.owner_id::text as kek_owner_id,
                    kek.encrypted_key as kek_encrypted_key,
                    kek.key_algorithm as kek_algorithm
                FROM authz_wrapped_data_keys wdk
                JOIN authz_key_encryption_keys kek ON wdk.kek_id = kek.kek_id
                WHERE wdk.file_id = $1 
                    AND kek.owner_type = 'user'
                    AND kek.owner_id = $2
                    AND kek.is_active = true
                LIMIT 1
                """,
                uuid.UUID(file_id),
                uuid.UUID(user_id),
            )
            return dict(row) if row else None

    async def delete_wrapped_deks_for_file(self, file_id: str) -> int:
        """Delete all wrapped DEKs for a file (used when deleting a file)."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_wrapped_data_keys WHERE file_id = $1",
                uuid.UUID(file_id),
            )
            # Extract count from "DELETE N"
            return int(result.split()[-1]) if result else 0

    async def add_wrapped_dek_for_role(
        self,
        *,
        file_id: str,
        role_id: str,
        wrapped_dek: bytes,
        dek_algorithm: str = "AES-256-GCM",
    ) -> Optional[dict]:
        """
        Add a wrapped DEK for a role (used when sharing a file with a new role).
        Gets the role's KEK and stores the wrapped DEK.
        """
        # Get the role's KEK
        kek = await self.get_kek_for_owner("role", role_id)
        if not kek:
            logger.warning(
                "No KEK found for role, cannot add wrapped DEK",
                role_id=role_id,
                file_id=file_id,
            )
            return None
        
        return await self.store_wrapped_dek(
            file_id=file_id,
            kek_id=kek["kek_id"],
            wrapped_dek=wrapped_dek,
            dek_algorithm=dek_algorithm,
        )

    async def remove_wrapped_dek_for_role(self, *, file_id: str, role_id: str) -> bool:
        """Remove the wrapped DEK for a specific role (used when unsharing)."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                """
                DELETE FROM authz_wrapped_data_keys wdk
                USING authz_key_encryption_keys kek
                WHERE wdk.kek_id = kek.kek_id
                    AND wdk.file_id = $1
                    AND kek.owner_type = 'role'
                    AND kek.owner_id = $2
                """,
                uuid.UUID(file_id),
                uuid.UUID(role_id),
            )
            return result != "DELETE 0"

