import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional, List

import asyncpg
import structlog
from cachetools import TTLCache

from busibox_common import AsyncPGPoolManager
from middleware.rls import set_rls_session_vars

logger = structlog.get_logger()

# Module-level lock to prevent concurrent schema initialization
_schema_lock = asyncio.Lock()
# Track schema initialization per database (by database name)
_schema_initialized: set[str] = set()


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


def safe_uuid(value: str | None) -> uuid.UUID | None:
    """
    Safely convert a string to UUID, returning None if invalid.
    
    Use this for optional fields where invalid UUIDs should be ignored
    rather than causing errors (e.g., resource_id in audit logs that
    might contain non-UUID identifiers).
    
    Args:
        value: String to convert to UUID, or None
        
    Returns:
        UUID object if valid, None otherwise
    """
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


class PostgresService:
    """
    Authz PostgreSQL service with domain-specific operations.
    
    Uses the shared AsyncPGPoolManager for connection pooling.
    """
    
    def __init__(self, config: dict):
        self._pool_manager = AsyncPGPoolManager.from_config(config)
        self._issuer = config.get("issuer", "busibox-authz")
        self._signing_key_cache = TTLCache(maxsize=1, ttl=300)
        self._jwks_cache = TTLCache(maxsize=1, ttl=300)

    @property
    def pool(self) -> Optional[asyncpg.Pool]:
        """Access the underlying pool (for compatibility with existing code)."""
        return self._pool_manager.pool

    async def connect(self):
        await self._pool_manager.connect()
        # Ensure required tables exist (idempotent).
        await self.ensure_schema()

    async def disconnect(self):
        await self._pool_manager.disconnect()

    @asynccontextmanager
    async def acquire(self, user_id: str | None, role_ids: List[str] | None):
        async with self._pool_manager.acquire() as conn:
            await set_rls_session_vars(conn, user_id, role_ids)
            yield conn

    async def ensure_schema(self) -> None:
        """
        Create authz tables if missing.

        Uses the shared SchemaManager pattern for idempotent schema creation.
        The schema is defined in schema.py and applied on every startup.
        
        Uses a lock to prevent concurrent schema migrations which can cause
        "tuple concurrently updated" errors in PostgreSQL.
        """
        global _schema_initialized
        
        if not self._pool_manager.is_connected:
            # connect() will call ensure_schema() again; avoid recursion
            return
        
        # Get database name for per-database tracking
        db_name = self._pool_manager.config.database or "default"
        
        # Fast path: schema already initialized for this database
        if db_name in _schema_initialized:
            return
        
        # Use lock to prevent concurrent schema initialization
        async with _schema_lock:
            # Double-check after acquiring lock
            if db_name in _schema_initialized:
                return
            
            from schema import get_authz_schema
            
            schema = get_authz_schema()
            async with self._pool_manager.acquire() as conn:
                await schema.apply(conn)
            
            _schema_initialized.add(db_name)
            logger.info("Authz schema initialization complete", database=db_name)

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
                safe_uuid(actor_id) or uuid.UUID(actor_id),  # actor_id should be valid UUID
                action,
                resource_type,
                safe_uuid(resource_id),  # resource_id may not always be a UUID
                json.dumps(details or {}),
            )

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
        self.invalidate_signing_key_cache()

    async def get_active_signing_key(self) -> Optional[dict]:
        cached = self._signing_key_cache.get("active")
        if cached is not None:
            return cached
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
            result = dict(row) if row else None
            self._signing_key_cache["active"] = result
            return result

    async def list_public_jwks(self) -> List[dict]:
        import json
        cached = self._jwks_cache.get("jwks")
        if cached is not None:
            return cached
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
            result = [json.loads(dict(r)["public_jwk"]) if isinstance(dict(r)["public_jwk"], str) else dict(r)["public_jwk"] for r in rows]
            self._jwks_cache["jwks"] = result
            return result

    def invalidate_signing_key_cache(self) -> None:
        self._signing_key_cache.clear()
        self._jwks_cache.clear()

    # ---------------------------------------------------------------------
    # RBAC sync (initially driven by busibox-portal)
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
        display_name: str | None,
        first_name: str | None,
        last_name: str | None,
        avatar_url: str | None,
        favorite_color: str | None,
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
                INSERT INTO authz_users (
                    user_id, email, status, display_name, first_name, last_name, avatar_url, favorite_color,
                    idp_provider, idp_tenant_id, idp_object_id, idp_roles, idp_groups
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                ON CONFLICT (user_id) DO UPDATE
                  SET email = EXCLUDED.email,
                      status = EXCLUDED.status,
                      display_name = EXCLUDED.display_name,
                      first_name = EXCLUDED.first_name,
                      last_name = EXCLUDED.last_name,
                      avatar_url = EXCLUDED.avatar_url,
                      favorite_color = EXCLUDED.favorite_color,
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
                display_name,
                first_name,
                last_name,
                avatar_url,
                favorite_color,
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

    async def get_user(self, user_id: str) -> dict | None:
        """Get a user by ID from authz_users."""
        try:
            uid = uuid.UUID(user_id)
        except (ValueError, AttributeError, TypeError):
            return None
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, email, status, display_name, first_name, last_name, avatar_url, favorite_color,
                       (github_pat_encrypted IS NOT NULL) AS has_github_pat, idp_provider, idp_tenant_id,
                       idp_object_id, idp_roles, idp_groups, created_at, updated_at
                FROM authz_users
                WHERE user_id = $1
                """,
                uid,
            )
            return dict(row) if row else None

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

    async def get_user_roles_batch(self, user_ids: List[str]) -> dict[str, List[dict]]:
        if not user_ids:
            return {}

        valid_ids: list[uuid.UUID] = []
        for user_id in user_ids:
            try:
                uid = uuid.UUID(user_id)
                valid_ids.append(uid)
            except (ValueError, AttributeError, TypeError):
                continue

        result: dict[str, List[dict]] = {user_id: [] for user_id in user_ids}
        if not valid_ids:
            return result

        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT ur.user_id::text AS user_id, r.id::text AS id, r.name AS name, r.description, r.scopes, r.created_at, r.updated_at
                FROM authz_user_roles ur
                JOIN authz_roles r ON r.id = ur.role_id
                WHERE ur.user_id = ANY($1::uuid[])
                ORDER BY r.name
                """,
                valid_ids,
            )

        for row in rows:
            role = dict(row)
            user_id = role.pop("user_id")
            result.setdefault(user_id, []).append(role)

        return result

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
                SELECT id::text, name, description, scopes, created_at, updated_at
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

    @staticmethod
    def _parse_owner_id(owner_id: str | None, owner_type: str = "") -> uuid.UUID | None:
        """Parse an owner_id string into a UUID.
        
        For role/user owner types, owner_id must be a valid UUID.
        For system owner types, owner_id can be a human-readable string
        (e.g., 'deploy-api') which is converted to a deterministic UUID5.
        """
        if owner_id is None:
            return None
        try:
            return uuid.UUID(owner_id)
        except ValueError:
            # Not a valid UUID -- generate a deterministic one from the string
            # This allows system owners like "deploy-api" to have stable UUIDs
            return uuid.uuid5(uuid.NAMESPACE_OID, f"busibox-system:{owner_id}")

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
            owner_id: UUID of the role/user, or string name for system keys
            encrypted_key: The KEK encrypted with the system master key
            key_algorithm: Algorithm used for encryption (default AES-256-GCM)
            
        Returns:
            Dict with kek_id and metadata
        """
        oid = self._parse_owner_id(owner_id, owner_type)
        
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
        oid = self._parse_owner_id(owner_id, owner_type)
        
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
        oid = self._parse_owner_id(owner_id, owner_type)
        
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

    async def get_wrapped_dek_for_system(
        self, file_id: str, system_owner_id: str
    ) -> Optional[dict]:
        """
        Get a wrapped DEK for a system owner (for service-level encryption).
        Used by internal services like deploy-api that encrypt data with a system KEK.
        """
        # Convert system_owner_id (e.g., "deploy-api") to its deterministic UUID
        system_oid = self._parse_owner_id(system_owner_id, "system")
        
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
                    AND kek.owner_type = 'system'
                    AND kek.owner_id = $2
                    AND kek.is_active = true
                LIMIT 1
                """,
                uuid.UUID(file_id),
                system_oid,
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

    # ---------------------------------------------------------------------
    # User Management (Full CRUD)
    # ---------------------------------------------------------------------

    async def create_user(
        self,
        *,
        email: str,
        status: str = "PENDING",
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar_url: str | None = None,
        favorite_color: str | None = None,
        role_ids: List[str] | None = None,
        assigned_by: str | None = None,
    ) -> dict:
        """Create a new user with optional role assignments."""
        user_id = uuid.uuid4()
        pending_expires_at = None
        if status == "PENDING":
            from datetime import datetime, timedelta
            pending_expires_at = datetime.now() + timedelta(days=7)
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_users (
                    user_id, email, status, display_name, first_name, last_name, avatar_url, favorite_color, pending_expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING user_id::text, email, status, display_name, first_name, last_name, avatar_url, favorite_color,
                          (github_pat_encrypted IS NOT NULL) AS has_github_pat, email_verified_at, last_login_at,
                          pending_expires_at, created_at, updated_at
                """,
                user_id,
                email.lower(),
                status,
                display_name,
                first_name,
                last_name,
                avatar_url,
                favorite_color,
                pending_expires_at,
            )
            
            # Assign roles if provided
            if role_ids:
                assigned_by_uuid = uuid.UUID(assigned_by) if assigned_by else None
                for rid in role_ids:
                    await conn.execute(
                        """
                        INSERT INTO authz_user_roles (user_id, role_id, assigned_by)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, role_id) DO NOTHING
                        """,
                        user_id,
                        uuid.UUID(rid),
                        assigned_by_uuid,
                    )
            
            user = dict(row)
            roles = await conn.fetch(
                """
                SELECT r.id::text AS id, r.name AS name, r.description, r.scopes, r.created_at, r.updated_at
                FROM authz_user_roles ur
                JOIN authz_roles r ON r.id = ur.role_id
                WHERE ur.user_id = $1
                ORDER BY r.name
                """,
                user_id,
            )
            user["roles"] = [dict(r) for r in roles]
            return user

    async def list_users(
        self,
        *,
        page: int = 1,
        limit: int = 20,
        status: str | None = None,
        search: str | None = None,
    ) -> dict:
        """List users with pagination and filtering."""
        offset = (page - 1) * limit
        
        async with self.acquire(None, None) as conn:
            # Build WHERE clause
            conditions = []
            params = []
            param_idx = 1
            
            if status:
                conditions.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1
            
            if search:
                conditions.append(
                    f"(email ILIKE ${param_idx}"
                    f" OR COALESCE(display_name, '') ILIKE ${param_idx}"
                    f" OR COALESCE(first_name, '') ILIKE ${param_idx}"
                    f" OR COALESCE(last_name, '') ILIKE ${param_idx}"
                    f" OR COALESCE(first_name, '') || ' ' || COALESCE(last_name, '') ILIKE ${param_idx})"
                )
                params.append(f"%{search}%")
                param_idx += 1
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM authz_users {where_clause}"
            total_count = await conn.fetchval(count_query, *params)
            
            # Get users with role data in a single query (no N+1)
            params.extend([limit, offset])
            query = f"""
                WITH selected_users AS (
                    SELECT user_id, email, status, display_name, first_name, last_name, avatar_url, favorite_color,
                           (github_pat_encrypted IS NOT NULL) AS has_github_pat, email_verified_at, last_login_at,
                           pending_expires_at, created_at, updated_at
                    FROM authz_users
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT ${param_idx} OFFSET ${param_idx + 1}
                )
                SELECT su.user_id::text AS user_id, su.email, su.status, su.display_name, su.first_name, su.last_name, su.avatar_url, su.favorite_color,
                       su.has_github_pat,
                       su.email_verified_at, su.last_login_at, su.pending_expires_at, su.created_at, su.updated_at,
                       r.id::text AS role_id, r.name AS role_name, r.description AS role_description, r.scopes AS role_scopes,
                       r.created_at AS role_created_at, r.updated_at AS role_updated_at
                FROM selected_users su
                LEFT JOIN authz_user_roles ur ON ur.user_id = su.user_id
                LEFT JOIN authz_roles r ON r.id = ur.role_id
                ORDER BY su.created_at DESC, r.name
            """
            rows = await conn.fetch(query, *params)

            users_by_id: dict[str, dict] = {}
            user_order: list[str] = []
            for row in rows:
                user_id = row["user_id"]
                if user_id not in users_by_id:
                    users_by_id[user_id] = {
                        "user_id": user_id,
                        "email": row["email"],
                        "status": row["status"],
                        "display_name": row["display_name"],
                        "first_name": row["first_name"],
                        "last_name": row["last_name"],
                        "avatar_url": row["avatar_url"],
                        "favorite_color": row["favorite_color"],
                        "has_github_pat": row["has_github_pat"],
                        "email_verified_at": row["email_verified_at"],
                        "last_login_at": row["last_login_at"],
                        "pending_expires_at": row["pending_expires_at"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "roles": [],
                    }
                    user_order.append(user_id)

                if row["role_id"] is not None:
                    users_by_id[user_id]["roles"].append(
                        {
                            "id": row["role_id"],
                            "name": row["role_name"],
                            "description": row["role_description"],
                            "scopes": row["role_scopes"],
                            "created_at": row["role_created_at"],
                            "updated_at": row["role_updated_at"],
                        }
                    )

            users = [users_by_id[user_id] for user_id in user_order]
            
            return {
                "users": users,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total_count": total_count,
                    "total_pages": (total_count + limit - 1) // limit,
                },
            }

    async def get_user_with_roles(self, user_id: str) -> dict | None:
        """Get a user by ID with their roles."""
        user = await self.get_user(user_id)
        if not user:
            return None
        user["roles"] = await self.get_user_roles(user_id)
        return user

    async def update_user(
        self,
        user_id: str,
        *,
        email: str | None = None,
        status: str | None = None,
        display_name: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
        avatar_url: str | None = None,
        favorite_color: str | None = None,
        email_verified_at: str | None = None,
        last_login_at: str | None = None,
        pending_expires_at: str | None = None,
    ) -> dict | None:
        """Update user fields."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            updates = []
            params = []
            param_idx = 1
            
            if email is not None:
                updates.append(f"email = ${param_idx}")
                params.append(email.lower())
                param_idx += 1
            
            if status is not None:
                updates.append(f"status = ${param_idx}")
                params.append(status)
                param_idx += 1

            if display_name is not None:
                updates.append(f"display_name = ${param_idx}")
                params.append(display_name)
                param_idx += 1

            if first_name is not None:
                updates.append(f"first_name = ${param_idx}")
                params.append(first_name)
                param_idx += 1

            if last_name is not None:
                updates.append(f"last_name = ${param_idx}")
                params.append(last_name)
                param_idx += 1

            if avatar_url is not None:
                updates.append(f"avatar_url = ${param_idx}")
                params.append(avatar_url)
                param_idx += 1

            if favorite_color is not None:
                updates.append(f"favorite_color = ${param_idx}")
                params.append(favorite_color)
                param_idx += 1
            
            if email_verified_at is not None:
                updates.append(f"email_verified_at = ${param_idx}")
                from datetime import datetime
                params.append(datetime.fromisoformat(email_verified_at.replace("Z", "+00:00")))
                param_idx += 1
            
            if last_login_at is not None:
                updates.append(f"last_login_at = ${param_idx}")
                from datetime import datetime
                params.append(datetime.fromisoformat(last_login_at.replace("Z", "+00:00")))
                param_idx += 1
            
            if pending_expires_at is not None:
                updates.append(f"pending_expires_at = ${param_idx}")
                from datetime import datetime
                params.append(datetime.fromisoformat(pending_expires_at.replace("Z", "+00:00")) if pending_expires_at else None)
                param_idx += 1
            
            if not updates:
                return await self.get_user_with_roles(user_id)
            
            updates.append("updated_at = now()")
            params.append(uid)
            
            query = f"""
                UPDATE authz_users
                SET {', '.join(updates)}
                WHERE user_id = ${param_idx}
                RETURNING user_id::text, email, status, display_name, first_name, last_name, avatar_url, favorite_color,
                          (github_pat_encrypted IS NOT NULL) AS has_github_pat, email_verified_at, last_login_at,
                          pending_expires_at, created_at, updated_at
            """
            
            row = await conn.fetchrow(query, *params)
            if not row:
                return None
            
            user = dict(row)
            roles = await conn.fetch(
                """
                SELECT r.id::text AS id, r.name AS name, r.description, r.scopes, r.created_at, r.updated_at
                FROM authz_user_roles ur
                JOIN authz_roles r ON r.id = ur.role_id
                WHERE ur.user_id = $1
                ORDER BY r.name
                """,
                uid,
            )
            user["roles"] = [dict(r) for r in roles]
            return user

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user (cascades to sessions, roles, etc.)."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_users WHERE user_id = $1",
                uid,
            )
            return result != "DELETE 0"

    async def activate_user(self, user_id: str) -> dict | None:
        """Activate a pending user."""
        return await self.update_user(
            user_id,
            status="ACTIVE",
            pending_expires_at=None,
        )

    async def deactivate_user(self, user_id: str) -> dict | None:
        """Deactivate an active user."""
        return await self.update_user(user_id, status="DEACTIVATED")

    async def reactivate_user(self, user_id: str) -> dict | None:
        """Reactivate a deactivated user."""
        return await self.update_user(user_id, status="ACTIVE")

    async def get_user_by_email(self, email: str) -> dict | None:
        """Get a user by email address."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id::text, email, status, display_name, first_name, last_name, avatar_url, favorite_color, email_verified_at, last_login_at,
                       (github_pat_encrypted IS NOT NULL) AS has_github_pat, pending_expires_at, created_at, updated_at
                FROM authz_users
                WHERE email = $1
                """,
                email.lower(),
            )
            if not row:
                return None
            user = dict(row)
            roles = await conn.fetch(
                """
                SELECT r.id::text AS id, r.name AS name, r.description, r.scopes, r.created_at, r.updated_at
                FROM authz_user_roles ur
                JOIN authz_roles r ON r.id = ur.role_id
                WHERE ur.user_id = $1
                ORDER BY r.name
                """,
                uuid.UUID(user["user_id"]),
            )
            user["roles"] = [dict(r) for r in roles]
            return user

    async def set_user_github_pat(self, user_id: str, github_pat_encrypted: bytes | None) -> bool:
        """Set or clear encrypted GitHub PAT for a user."""
        uid = validate_uuid(user_id, "user_id")
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                """
                UPDATE authz_users
                SET github_pat_encrypted = $1,
                    updated_at = now()
                WHERE user_id = $2
                """,
                github_pat_encrypted,
                uid,
            )
            return result != "UPDATE 0"

    async def get_user_github_pat_encrypted(self, user_id: str) -> bytes | None:
        """Fetch encrypted GitHub PAT bytes for a user."""
        uid = validate_uuid(user_id, "user_id")
        async with self.acquire(None, None) as conn:
            return await conn.fetchval(
                "SELECT github_pat_encrypted FROM authz_users WHERE user_id = $1",
                uid,
            )

    # ---------------------------------------------------------------------
    # Session Management
    # ---------------------------------------------------------------------

    async def create_session(
        self,
        *,
        user_id: str,
        token: str,
        expires_at: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        """Create or sync a session."""
        uid = validate_uuid(user_id, "user_id")
        from datetime import datetime
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_sessions (user_id, token, expires_at, ip_address, user_agent)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (token) DO UPDATE
                  SET expires_at = EXCLUDED.expires_at,
                      ip_address = EXCLUDED.ip_address,
                      user_agent = EXCLUDED.user_agent
                RETURNING id::text as session_id, user_id::text, token, expires_at, 
                          ip_address, user_agent, created_at
                """,
                uid,
                token,
                exp,
                ip_address,
                user_agent,
            )
            return dict(row)

    async def get_session(self, token: str) -> dict | None:
        """Get a session by token."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT s.id::text as session_id, s.user_id::text, s.token, s.expires_at,
                       s.ip_address, s.user_agent, s.created_at,
                       u.email, u.status, u.display_name, u.first_name, u.last_name, u.avatar_url, u.favorite_color
                FROM authz_sessions s
                JOIN authz_users u ON s.user_id = u.user_id
                WHERE s.token = $1 AND s.expires_at > now()
                """,
                token,
            )
            if not row:
                return None
            session = dict(row)
            session["user"] = {
                "user_id": session["user_id"],
                "email": session.pop("email"),
                "status": session.pop("status"),
                "display_name": session.pop("display_name", None),
                "first_name": session.pop("first_name", None),
                "last_name": session.pop("last_name", None),
                "avatar_url": session.pop("avatar_url", None),
                "favorite_color": session.pop("favorite_color", None),
            }
            return session

    async def get_session_by_id(self, session_id: str) -> dict | None:
        """Get a session by its ID (for JTI verification in JWT tokens)."""
        sid = validate_uuid(session_id, "session_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT s.id::text as session_id, s.user_id::text, s.token, s.expires_at,
                       s.ip_address, s.user_agent, s.created_at
                FROM authz_sessions s
                WHERE s.id = $1 AND s.expires_at > now()
                """,
                sid,
            )
            return dict(row) if row else None

    async def delete_session(self, token: str) -> bool:
        """Delete a session by token."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_sessions WHERE token = $1",
                token,
            )
            return result != "DELETE 0"

    async def delete_session_by_id(self, session_id: str) -> bool:
        """Delete a session by session_id (for self-service logout)."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_sessions WHERE id = $1",
                session_id,
            )
            return result != "DELETE 0"

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete all sessions for a user."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_sessions WHERE user_id = $1",
                uid,
            )
            return int(result.split()[-1]) if result else 0

    async def cleanup_expired_sessions(self) -> int:
        """Delete all expired sessions."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_sessions WHERE expires_at < now()"
            )
            return int(result.split()[-1]) if result else 0

    # ---------------------------------------------------------------------
    # Magic Links
    # ---------------------------------------------------------------------

    async def create_magic_link(
        self,
        *,
        user_id: str,
        email: str,
        expires_in_seconds: int = 900,  # 15 minutes
    ) -> dict:
        """Create a magic link for passwordless login."""
        import secrets
        uid = validate_uuid(user_id, "user_id")
        token = secrets.token_urlsafe(32)
        from datetime import datetime, timedelta
        expires_at = datetime.now() + timedelta(seconds=expires_in_seconds)
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_magic_links (user_id, token, email, expires_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text as magic_link_id, user_id::text, token, email, 
                          expires_at, created_at
                """,
                uid,
                token,
                email.lower(),
                expires_at,
            )
            return dict(row)

    async def get_magic_link(self, token: str) -> dict | None:
        """Get a magic link by token (without consuming it)."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text as magic_link_id, user_id::text, token, email,
                       expires_at, used_at, created_at
                FROM authz_magic_links
                WHERE token = $1
                """,
                token,
            )
            return dict(row) if row else None

    async def use_magic_link(self, token: str) -> dict | None:
        """
        Use (consume) a magic link and create a session.
        Returns the user and new session, or None if invalid/expired/used.
        
        NOTE: To handle email client link verification (e.g., Outlook Safe Links),
        we allow a 60-second grace period after first use. If the link was used
        within the last 60 seconds, we return the most recent session for that user
        instead of rejecting the request.
        """
        async with self.acquire(None, None) as conn:
            # First, check if link exists and is not expired
            row = await conn.fetchrow(
                """
                SELECT id, user_id, email, used_at
                FROM authz_magic_links
                WHERE token = $1 AND expires_at > now()
                """,
                token,
            )
            
            if not row:
                return None
            
            link_id = row["id"]
            user_id = row["user_id"]
            used_at = row["used_at"]
            
            # If already used, check if within grace period (60 seconds)
            if used_at is not None:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                # Ensure used_at is timezone-aware
                if used_at.tzinfo is None:
                    used_at = used_at.replace(tzinfo=timezone.utc)
                seconds_since_use = (now - used_at).total_seconds()
                
                if seconds_since_use > 60:
                    # Grace period expired - link is truly consumed
                    return None
                
                # Within grace period - return user's most recent active session
                user = await self.get_user_with_roles(str(user_id))
                session = await conn.fetchrow(
                    """
                    SELECT id::text as session_id, user_id::text, token, expires_at, ip_address, user_agent
                    FROM authz_sessions
                    WHERE user_id = $1 AND expires_at > now() AND revoked_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    user_id,
                )
                
                if session:
                    return {
                        "user": user,
                        "session": dict(session),
                    }
                # No active session found, but link was recently used - this shouldn't happen
                # Fall through to create a new session anyway
            
            # Mark as used (first use or creating new session after grace period lookup failed)
            if used_at is None:
                await conn.execute(
                    "UPDATE authz_magic_links SET used_at = now() WHERE id = $1",
                    link_id,
                )
            
            # Update user: activate if pending, set email verified, update last login
            await conn.execute(
                """
                UPDATE authz_users
                SET status = CASE WHEN status = 'PENDING' THEN 'ACTIVE' ELSE status END,
                    email_verified_at = COALESCE(email_verified_at, now()),
                    last_login_at = now(),
                    pending_expires_at = NULL,
                    updated_at = now()
                WHERE user_id = $1
                """,
                user_id,
            )
            
            # Get updated user
            user = await self.get_user_with_roles(str(user_id))
            
            # Create session
            import secrets
            from datetime import datetime, timedelta
            session_token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(hours=24)
            
            session = await self.create_session(
                user_id=str(user_id),
                token=session_token,
                expires_at=expires_at.isoformat(),
            )
            
            return {
                "user": user,
                "session": session,
            }

    async def cleanup_expired_magic_links(self) -> int:
        """Delete all expired magic links."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_magic_links WHERE expires_at < now()"
            )
            return int(result.split()[-1]) if result else 0

    # ---------------------------------------------------------------------
    # TOTP Codes
    # ---------------------------------------------------------------------

    async def create_totp_code(
        self,
        *,
        user_id: str,
        email: str,
        expires_in_seconds: int = 300,  # 5 minutes
    ) -> dict:
        """Create a TOTP code for multi-device login. Returns plaintext code."""
        import secrets
        import hashlib
        
        uid = validate_uuid(user_id, "user_id")
        
        # Generate 6-digit code
        code = str(secrets.randbelow(1000000)).zfill(6)
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        
        from datetime import datetime, timedelta
        expires_at = datetime.now() + timedelta(seconds=expires_in_seconds)
        
        async with self.acquire(None, None) as conn:
            await conn.execute(
                """
                INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uid,
                code_hash,
                email.lower(),
                expires_at,
            )
            
            return {
                "code": code,  # Plaintext - send via email
                "expires_at": expires_at.isoformat(),
            }

    async def verify_totp_code(self, email: str, code: str) -> dict | None:
        """
        Verify a TOTP code and create a session if valid.
        Returns user and session, or None if invalid.
        """
        import hashlib
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        
        async with self.acquire(None, None) as conn:
            # Find valid code
            row = await conn.fetchrow(
                """
                SELECT id, user_id
                FROM authz_totp_codes
                WHERE email = $1 AND code_hash = $2 AND expires_at > now() AND used_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                email.lower(),
                code_hash,
            )
            
            if not row:
                return None
            
            code_id = row["id"]
            user_id = row["user_id"]
            
            # Mark as used
            await conn.execute(
                "UPDATE authz_totp_codes SET used_at = now() WHERE id = $1",
                code_id,
            )
            
            # Update user last login
            await conn.execute(
                """
                UPDATE authz_users
                SET last_login_at = now(), updated_at = now()
                WHERE user_id = $1
                """,
                user_id,
            )
            
            # Get user
            user = await self.get_user_with_roles(str(user_id))
            
            # Create session
            import secrets
            from datetime import datetime, timedelta
            session_token = secrets.token_urlsafe(32)
            expires_at = datetime.now() + timedelta(hours=24)
            
            session = await self.create_session(
                user_id=str(user_id),
                token=session_token,
                expires_at=expires_at.isoformat(),
            )
            
            return {
                "user": user,
                "session": session,
            }

    async def cleanup_expired_totp_codes(self) -> int:
        """Delete all expired TOTP codes."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_totp_codes WHERE expires_at < now()"
            )
            return int(result.split()[-1]) if result else 0

    # ---------------------------------------------------------------------
    # Passkeys (WebAuthn)
    # ---------------------------------------------------------------------

    async def create_passkey_challenge(
        self,
        *,
        challenge_type: str,  # 'registration' or 'authentication'
        user_id: str | None = None,
        expires_in_seconds: int = 300,  # 5 minutes
    ) -> dict:
        """Create a passkey challenge for WebAuthn."""
        import secrets
        import base64
        
        # Generate random challenge
        challenge_bytes = secrets.token_bytes(32)
        challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode()
        
        uid = uuid.UUID(user_id) if user_id else None
        from datetime import datetime, timedelta
        expires_at = datetime.now() + timedelta(seconds=expires_in_seconds)
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_passkey_challenges (challenge, user_id, type, expires_at)
                VALUES ($1, $2, $3, $4)
                RETURNING id::text, challenge, user_id::text, type, expires_at, created_at
                """,
                challenge,
                uid,
                challenge_type,
                expires_at,
            )
            return dict(row)

    async def get_passkey_challenge(self, challenge: str) -> dict | None:
        """Get a passkey challenge."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, challenge, user_id::text, type, expires_at, created_at
                FROM authz_passkey_challenges
                WHERE challenge = $1 AND expires_at > now()
                """,
                challenge,
            )
            return dict(row) if row else None

    async def delete_passkey_challenge(self, challenge: str) -> bool:
        """Delete a passkey challenge (after use)."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_passkey_challenges WHERE challenge = $1",
                challenge,
            )
            return result != "DELETE 0"

    async def register_passkey(
        self,
        *,
        user_id: str,
        credential_id: str,
        credential_public_key: str,
        counter: int = 0,
        device_type: str,
        backed_up: bool = False,
        transports: List[str] | None = None,
        aaguid: str | None = None,
        name: str,
    ) -> dict:
        """Register a new passkey for a user."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_passkeys 
                    (user_id, credential_id, credential_public_key, counter, device_type,
                     backed_up, transports, aaguid, name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id::text as passkey_id, user_id::text, credential_id, name,
                          device_type, backed_up, transports, created_at
                """,
                uid,
                credential_id,
                credential_public_key,
                counter,
                device_type,
                backed_up,
                transports or [],
                aaguid,
                name,
            )
            return dict(row)

    async def get_passkey_by_credential_id(self, credential_id: str) -> dict | None:
        """Get a passkey by credential ID."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text as passkey_id, user_id::text, credential_id, 
                       credential_public_key, counter, device_type, backed_up,
                       transports, aaguid, name, last_used_at, created_at, updated_at
                FROM authz_passkeys
                WHERE credential_id = $1
                """,
                credential_id,
            )
            return dict(row) if row else None

    async def get_passkey(self, passkey_id: str) -> dict | None:
        """Get a passkey by passkey ID (for ownership checks)."""
        pid = validate_uuid(passkey_id, "passkey_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text as passkey_id, user_id::text, credential_id, 
                       credential_public_key, counter, device_type, backed_up,
                       transports, aaguid, name, last_used_at, created_at, updated_at
                FROM authz_passkeys
                WHERE id = $1
                """,
                pid,
            )
            return dict(row) if row else None

    async def list_user_passkeys(self, user_id: str) -> List[dict]:
        """List all passkeys for a user."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text as passkey_id, user_id::text, credential_id, name,
                       device_type, backed_up, transports, last_used_at, created_at
                FROM authz_passkeys
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                uid,
            )
            return [dict(row) for row in rows]

    async def update_passkey_counter(self, credential_id: str, new_counter: int) -> bool:
        """Update the passkey counter after authentication."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                """
                UPDATE authz_passkeys
                SET counter = $1, last_used_at = now(), updated_at = now()
                WHERE credential_id = $2
                """,
                new_counter,
                credential_id,
            )
            return result != "UPDATE 0"

    async def delete_passkey(self, passkey_id: str) -> bool:
        """Delete a passkey."""
        pid = validate_uuid(passkey_id, "passkey_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_passkeys WHERE id = $1",
                pid,
            )
            return result != "DELETE 0"

    async def authenticate_with_passkey(
        self,
        *,
        credential_id: str,
        new_counter: int,
    ) -> dict | None:
        """
        Authenticate with a passkey and create a session.
        Caller is responsible for verifying the signature before calling this.
        """
        passkey = await self.get_passkey_by_credential_id(credential_id)
        if not passkey:
            return None
        
        # Verify counter to prevent replay attacks
        # Note: Some authenticators (like iCloud Keychain) always return counter 0.
        # We only reject if the stored counter is non-zero and new_counter <= stored.
        stored_counter = passkey["counter"] or 0
        if stored_counter > 0 and new_counter <= stored_counter:
            logger.warning(
                "Passkey counter replay detected",
                credential_id=credential_id,
                expected_counter=stored_counter,
                received_counter=new_counter,
            )
            return None
        
        # Update counter
        await self.update_passkey_counter(credential_id, new_counter)
        
        user_id = passkey["user_id"]
        
        # Update user: activate if pending, update last login
        async with self.acquire(None, None) as conn:
            await conn.execute(
                """
                UPDATE authz_users
                SET status = CASE WHEN status = 'PENDING' THEN 'ACTIVE' ELSE status END,
                    last_login_at = now(),
                    pending_expires_at = NULL,
                    updated_at = now()
                WHERE user_id = $1
                """,
                uuid.UUID(user_id),
            )
        
        # Get user
        user = await self.get_user_with_roles(user_id)
        
        # Create session
        import secrets
        from datetime import datetime, timedelta
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)
        
        session = await self.create_session(
            user_id=user_id,
            token=session_token,
            expires_at=expires_at.isoformat(),
        )
        
        return {
            "user": user,
            "session": session,
        }

    async def cleanup_expired_passkey_challenges(self) -> int:
        """Delete all expired passkey challenges."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_passkey_challenges WHERE expires_at < now()"
            )
            return int(result.split()[-1]) if result else 0

    # ---------------------------------------------------------------------
    # Delegation Tokens
    # ---------------------------------------------------------------------

    async def create_delegation_token(
        self,
        *,
        user_id: str,
        scopes: List[str],
        name: str,
        expires_at: str,
    ) -> dict:
        """Create a delegation token for background tasks."""
        uid = validate_uuid(user_id, "user_id")
        from datetime import datetime
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_delegation_tokens (user_id, scopes, name, expires_at)
                VALUES ($1, $2, $3, $4)
                RETURNING jti::text, user_id::text, scopes, name, expires_at, created_at
                """,
                uid,
                scopes,
                name,
                exp,
            )
            return dict(row)

    async def get_delegation_token(self, jti: str) -> dict | None:
        """Get a delegation token by JTI."""
        token_id = validate_uuid(jti, "jti")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT jti::text, user_id::text, scopes, name, expires_at, created_at, revoked_at
                FROM authz_delegation_tokens
                WHERE jti = $1 AND expires_at > now() AND revoked_at IS NULL
                """,
                token_id,
            )
            return dict(row) if row else None

    async def list_user_delegation_tokens(self, user_id: str) -> List[dict]:
        """List all active delegation tokens for a user."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT jti::text, user_id::text, scopes, name, expires_at, created_at, revoked_at
                FROM authz_delegation_tokens
                WHERE user_id = $1 AND expires_at > now()
                ORDER BY created_at DESC
                """,
                uid,
            )
            return [dict(row) for row in rows]

    async def revoke_delegation_token(self, jti: str) -> bool:
        """Revoke a delegation token."""
        token_id = validate_uuid(jti, "jti")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                """
                UPDATE authz_delegation_tokens
                SET revoked_at = now()
                WHERE jti = $1 AND revoked_at IS NULL
                """,
                token_id,
            )
            return result != "UPDATE 0"

    async def cleanup_expired_delegation_tokens(self) -> int:
        """Delete all expired delegation tokens."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_delegation_tokens WHERE expires_at < now()"
            )
            return int(result.split()[-1]) if result else 0

    # ---------------------------------------------------------------------
    # User Channel Bindings
    # ---------------------------------------------------------------------

    async def create_user_channel_binding(
        self,
        *,
        user_id: str,
        channel_type: str,
        delegation_token: str,
        delegation_token_jti: str | None = None,
        ttl_minutes: int = 15,
    ) -> dict:
        """Create/update a pending channel binding and return link code."""
        uid = validate_uuid(user_id, "user_id")
        channel = channel_type.strip().lower()
        if not channel:
            raise ValueError("channel_type is required")

        import secrets
        from datetime import datetime, timedelta, timezone

        link_code = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:10].upper()
        link_expires_at = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        jti_uuid = validate_uuid(delegation_token_jti, "delegation_token_jti") if delegation_token_jti else None

        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_user_channel_bindings (
                    user_id, channel_type, external_id, link_code, link_expires_at,
                    delegation_token_jti, delegation_token, verified_at, updated_at
                )
                VALUES ($1, $2, NULL, $3, $4, $5, $6, NULL, now())
                ON CONFLICT (user_id, channel_type)
                DO UPDATE SET
                    external_id = NULL,
                    link_code = EXCLUDED.link_code,
                    link_expires_at = EXCLUDED.link_expires_at,
                    delegation_token_jti = EXCLUDED.delegation_token_jti,
                    delegation_token = EXCLUDED.delegation_token,
                    verified_at = NULL,
                    updated_at = now()
                RETURNING id::text, user_id::text, channel_type, link_code, link_expires_at, created_at, updated_at, verified_at
                """,
                uid,
                channel,
                link_code,
                link_expires_at,
                jti_uuid,
                delegation_token,
            )
            return dict(row)

    async def verify_user_channel_binding(
        self,
        *,
        channel_type: str,
        external_id: str,
        link_code: str,
    ) -> dict | None:
        """Verify a pending binding with link code and attach external identity."""
        channel = channel_type.strip().lower()
        external = external_id.strip().lower()
        code = link_code.strip().upper()
        if not channel or not external or not code:
            return None

        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                UPDATE authz_user_channel_bindings
                SET
                    external_id = $1,
                    verified_at = now(),
                    link_code = NULL,
                    link_expires_at = NULL,
                    updated_at = now()
                WHERE channel_type = $2
                  AND link_code = $3
                  AND link_expires_at > now()
                RETURNING id::text, user_id::text, channel_type, external_id, delegation_token, delegation_token_jti::text, verified_at
                """,
                external,
                channel,
                code,
            )
            return dict(row) if row else None

    async def lookup_user_channel_binding(
        self,
        *,
        channel_type: str,
        external_id: str,
    ) -> dict | None:
        """Lookup verified channel binding by external identity."""
        channel = channel_type.strip().lower()
        external = external_id.strip().lower()
        if not channel or not external:
            return None

        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, user_id::text, channel_type, external_id, delegation_token, delegation_token_jti::text, verified_at, created_at, updated_at
                FROM authz_user_channel_bindings
                WHERE channel_type = $1 AND external_id = $2 AND verified_at IS NOT NULL
                """,
                channel,
                external,
            )
            return dict(row) if row else None

    async def list_user_channel_bindings(self, user_id: str) -> List[dict]:
        """List all channel bindings for a user."""
        uid = validate_uuid(user_id, "user_id")
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, user_id::text, channel_type, external_id, verified_at, created_at, updated_at
                FROM authz_user_channel_bindings
                WHERE user_id = $1
                ORDER BY created_at DESC
                """,
                uid,
            )
            return [dict(row) for row in rows]

    async def update_channel_binding_token(
        self,
        *,
        channel_type: str,
        external_id: str,
        delegation_token: str,
    ) -> dict | None:
        """Update the delegation token JWT on a verified channel binding."""
        channel = channel_type.strip().lower()
        external = external_id.strip().lower()
        if not channel or not external:
            return None

        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                UPDATE authz_user_channel_bindings
                SET delegation_token = $1, updated_at = now()
                WHERE channel_type = $2 AND external_id = $3 AND verified_at IS NOT NULL
                RETURNING id::text, user_id::text, channel_type, external_id,
                          delegation_token, delegation_token_jti::text,
                          verified_at, created_at, updated_at
                """,
                delegation_token,
                channel,
                external,
            )
            return dict(row) if row else None

    async def delete_user_channel_binding(self, user_id: str, binding_id: str) -> bool:
        """Delete a user-owned channel binding."""
        uid = validate_uuid(user_id, "user_id")
        bid = validate_uuid(binding_id, "binding_id")
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_user_channel_bindings WHERE id = $1 AND user_id = $2",
                bid,
                uid,
            )
            return result != "DELETE 0"

    # ---------------------------------------------------------------------
    # Email Domain Configuration
    # ---------------------------------------------------------------------

    async def is_email_domain_allowed(self, email: str) -> bool:
        """Check if an email domain is allowed."""
        domain = email.lower().split("@")[-1] if "@" in email else ""
        if not domain:
            return False
        
        async with self.acquire(None, None) as conn:
            # Check if there are any domain rules
            count = await conn.fetchval("SELECT COUNT(*) FROM authz_email_domain_config")
            
            if count == 0:
                # No rules = allow all
                return True
            
            # Check if domain is explicitly allowed
            row = await conn.fetchrow(
                """
                SELECT is_allowed
                FROM authz_email_domain_config
                WHERE domain = $1
                """,
                domain,
            )
            
            if row:
                return row["is_allowed"]
            
            # Domain not in list - check if we're in allowlist or blocklist mode
            # If any domain is explicitly allowed, we're in allowlist mode (deny by default)
            # If any domain is explicitly blocked, we're in blocklist mode (allow by default)
            has_allowed = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM authz_email_domain_config WHERE is_allowed = true)"
            )
            
            # If we have allowed domains, we're in allowlist mode - deny unlisted
            # If we only have blocked domains, we're in blocklist mode - allow unlisted
            return not has_allowed

    async def add_email_domain_rule(self, domain: str, is_allowed: bool) -> dict:
        """Add or update an email domain rule."""
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_email_domain_config (domain, is_allowed)
                VALUES ($1, $2)
                ON CONFLICT (domain) DO UPDATE
                  SET is_allowed = EXCLUDED.is_allowed, updated_at = now()
                RETURNING id::text, domain, is_allowed, created_at, updated_at
                """,
                domain.lower(),
                is_allowed,
            )
            return dict(row)

    async def remove_email_domain_rule(self, domain: str) -> bool:
        """Remove an email domain rule."""
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_email_domain_config WHERE domain = $1",
                domain.lower(),
            )
            return result != "DELETE 0"

    async def list_email_domain_rules(self) -> List[dict]:
        """List all email domain rules."""
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, domain, is_allowed, created_at, updated_at
                FROM authz_email_domain_config
                ORDER BY domain
                """
            )
            return [dict(row) for row in rows]

    # ---------------------------------------------------------------------
    # Extended Audit Logging
    # ---------------------------------------------------------------------

    async def insert_audit_extended(
        self,
        *,
        actor_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        event_type: str | None = None,
        target_user_id: str | None = None,
        target_role_id: str | None = None,
        target_app_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        error_message: str | None = None,
        details: dict | None = None,
    ) -> dict:
        """Insert an audit log entry with extended fields."""
        # Handle "system" actor_id - use a well-known UUID for system events
        # This UUID represents the system actor: 00000000-0000-0000-0000-000000000001
        SYSTEM_ACTOR_UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")
        
        if actor_id.lower() == "system":
            actor_uuid = SYSTEM_ACTOR_UUID
        else:
            try:
                actor_uuid = uuid.UUID(actor_id)
            except ValueError:
                # If it's not a valid UUID and not "system", use system UUID as fallback
                actor_uuid = SYSTEM_ACTOR_UUID
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO audit_logs 
                    (actor_id, action, resource_type, resource_id, event_type,
                     target_user_id, target_role_id, target_app_id, ip_address,
                     user_agent, success, error_message, details)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13::jsonb)
                RETURNING id::text as audit_log_id, created_at
                """,
                actor_uuid,
                action,
                resource_type,
                safe_uuid(resource_id),  # resource_id may not always be a UUID
                event_type,
                safe_uuid(target_user_id),
                safe_uuid(target_role_id),
                safe_uuid(target_app_id),
                ip_address,
                user_agent,
                success,
                error_message,
                json.dumps(details or {}),
            )
            return dict(row)

    async def list_audit_logs(
        self,
        *,
        page: int = 1,
        limit: int = 50,
        actor_id: str | None = None,
        event_type: str | None = None,
        resource_type: str | None = None,
        target_user_id: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict:
        """List audit logs with pagination and filtering."""
        offset = (page - 1) * limit
        
        async with self.acquire(None, None) as conn:
            conditions = []
            params = []
            param_idx = 1
            
            if actor_id:
                conditions.append(f"actor_id = ${param_idx}")
                params.append(uuid.UUID(actor_id))
                param_idx += 1
            
            if event_type:
                conditions.append(f"event_type = ${param_idx}")
                params.append(event_type)
                param_idx += 1
            
            if resource_type:
                conditions.append(f"resource_type = ${param_idx}")
                params.append(resource_type)
                param_idx += 1
            
            if target_user_id:
                conditions.append(f"target_user_id = ${param_idx}")
                params.append(uuid.UUID(target_user_id))
                param_idx += 1
            
            if from_date:
                from datetime import datetime
                conditions.append(f"created_at >= ${param_idx}")
                params.append(datetime.fromisoformat(from_date.replace("Z", "+00:00")))
                param_idx += 1
            
            if to_date:
                from datetime import datetime
                conditions.append(f"created_at <= ${param_idx}")
                params.append(datetime.fromisoformat(to_date.replace("Z", "+00:00")))
                param_idx += 1
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            
            # Get total count
            count_query = f"SELECT COUNT(*) FROM audit_logs {where_clause}"
            total_count = await conn.fetchval(count_query, *params)
            
            # Get logs
            params.extend([limit, offset])
            query = f"""
                SELECT id::text, actor_id::text, action, resource_type, resource_id::text,
                       event_type, target_user_id::text, target_role_id::text, target_app_id::text,
                       ip_address, user_agent, success, error_message, details, created_at
                FROM audit_logs
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            rows = await conn.fetch(query, *params)
            
            return {
                "logs": [dict(row) for row in rows],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "total_count": total_count,
                    "total_pages": (total_count + limit - 1) // limit,
                },
            }

    async def get_user_audit_trail(self, user_id: str, limit: int = 100) -> List[dict]:
        """Get audit trail for a specific user."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, actor_id::text, action, resource_type, resource_id::text,
                       event_type, target_user_id::text, target_role_id::text, target_app_id::text,
                       ip_address, user_agent, success, error_message, details, created_at
                FROM audit_logs
                WHERE actor_id = $1 OR target_user_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                uid,
                limit,
            )
            return [dict(row) for row in rows]

    # ---------------------------------------------------------------------
    # Role-Resource Bindings
    # ---------------------------------------------------------------------

    async def create_role_binding(
        self,
        *,
        role_id: str,
        resource_type: str,
        resource_id: str,
        permissions: dict | None = None,
        created_by: str | None = None,
    ) -> dict:
        """Create a new role-resource binding."""
        rid = validate_uuid(role_id, "role_id")
        created_by_uuid = validate_uuid(created_by, "created_by") if created_by else None
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO authz_role_bindings (role_id, resource_type, resource_id, permissions, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                """,
                rid,
                resource_type,
                resource_id,
                json.dumps(permissions or {}),
                created_by_uuid,
            )
            return dict(row)

    async def get_role_binding(self, binding_id: str) -> dict | None:
        """Get a role binding by ID."""
        bid = validate_uuid(binding_id, "binding_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                FROM authz_role_bindings
                WHERE id = $1
                """,
                bid,
            )
            return dict(row) if row else None

    async def get_role_binding_by_unique(
        self,
        *,
        role_id: str,
        resource_type: str,
        resource_id: str,
    ) -> dict | None:
        """Get a role binding by its unique constraint (role_id, resource_type, resource_id)."""
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                FROM authz_role_bindings
                WHERE role_id = $1 AND resource_type = $2 AND resource_id = $3
                """,
                rid,
                resource_type,
                resource_id,
            )
            return dict(row) if row else None

    async def delete_role_binding(self, binding_id: str) -> bool:
        """Delete a role binding by ID. Returns True if deleted, False if not found."""
        bid = validate_uuid(binding_id, "binding_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                "DELETE FROM authz_role_bindings WHERE id = $1",
                bid,
            )
            return result == "DELETE 1"

    async def delete_role_binding_by_unique(
        self,
        *,
        role_id: str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """Delete a role binding by its unique constraint. Returns True if deleted."""
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            result = await conn.execute(
                """
                DELETE FROM authz_role_bindings
                WHERE role_id = $1 AND resource_type = $2 AND resource_id = $3
                """,
                rid,
                resource_type,
                resource_id,
            )
            return result == "DELETE 1"

    async def list_role_bindings(
        self,
        *,
        role_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[dict]:
        """List role bindings with optional filters."""
        async with self.acquire(None, None) as conn:
            conditions = []
            params: list = []
            param_idx = 1
            
            if role_id:
                rid = validate_uuid(role_id, "role_id")
                conditions.append(f"role_id = ${param_idx}")
                params.append(rid)
                param_idx += 1
            
            if resource_type:
                conditions.append(f"resource_type = ${param_idx}")
                params.append(resource_type)
                param_idx += 1
            
            if resource_id:
                conditions.append(f"resource_id = ${param_idx}")
                params.append(resource_id)
                param_idx += 1
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            
            params.extend([limit, offset])
            query = f"""
                SELECT id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                FROM authz_role_bindings
                {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
            """
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def get_roles_for_resource(
        self,
        resource_type: str,
        resource_id: str,
    ) -> List[dict]:
        """Get all roles that have access to a specific resource."""
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT r.id::text, r.name, r.description, r.scopes, r.created_at, r.updated_at,
                       b.id::text as binding_id, b.permissions, b.created_at as binding_created_at
                FROM authz_role_bindings b
                JOIN authz_roles r ON r.id = b.role_id
                WHERE b.resource_type = $1 AND b.resource_id = $2
                ORDER BY r.name
                """,
                resource_type,
                resource_id,
            )
            return [dict(row) for row in rows]

    async def get_resources_for_role(
        self,
        role_id: str,
        resource_type: str | None = None,
    ) -> List[dict]:
        """Get all resources that a role has access to, optionally filtered by type."""
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            if resource_type:
                rows = await conn.fetch(
                    """
                    SELECT id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                    FROM authz_role_bindings
                    WHERE role_id = $1 AND resource_type = $2
                    ORDER BY resource_type, resource_id
                    """,
                    rid,
                    resource_type,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT id::text, role_id::text, resource_type, resource_id, permissions, created_at, created_by::text
                    FROM authz_role_bindings
                    WHERE role_id = $1
                    ORDER BY resource_type, resource_id
                    """,
                    rid,
                )
            return [dict(row) for row in rows]

    async def check_role_has_resource(
        self,
        role_id: str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """Check if a role has access to a specific resource."""
        rid = validate_uuid(role_id, "role_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM authz_role_bindings
                WHERE role_id = $1 AND resource_type = $2 AND resource_id = $3
                """,
                rid,
                resource_type,
                resource_id,
            )
            return row is not None

    async def user_can_access_resource(
        self,
        user_id: str,
        resource_type: str,
        resource_id: str,
    ) -> bool:
        """Check if a user can access a resource via any of their roles."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 FROM authz_role_bindings b
                JOIN authz_user_roles ur ON ur.role_id = b.role_id
                WHERE ur.user_id = $1 AND b.resource_type = $2 AND b.resource_id = $3
                """,
                uid,
                resource_type,
                resource_id,
            )
            return row is not None

    async def get_user_accessible_resources(
        self,
        user_id: str,
        resource_type: str,
    ) -> List[str]:
        """Get all resource IDs of a given type that a user can access via their roles."""
        uid = validate_uuid(user_id, "user_id")
        
        async with self.acquire(None, None) as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT b.resource_id
                FROM authz_role_bindings b
                JOIN authz_user_roles ur ON ur.role_id = b.role_id
                WHERE ur.user_id = $1 AND b.resource_type = $2
                ORDER BY b.resource_id
                """,
                uid,
                resource_type,
            )
            return [row["resource_id"] for row in rows]
