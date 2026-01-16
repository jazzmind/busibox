"""
Authz Service Database Schema Definition.

This module defines the database schema for the authz service using the
shared SchemaManager pattern. The schema is applied idempotently on every
service startup.

Usage:
    from schema import get_authz_schema
    
    schema = get_authz_schema()
    async with pool.acquire() as conn:
        await schema.apply(conn)
"""

import sys
from pathlib import Path

# Add shared library to path (when deployed: /srv/shared)
_shared_paths = [
    Path(__file__).parent.parent.parent.parent / "shared",  # Local dev: srv/shared
    Path("/srv/shared"),  # Deployed
]
for _path in _shared_paths:
    if _path.exists() and str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

try:
    from busibox_common import SchemaManager
except ImportError:
    # Fallback: define minimal SchemaManager inline if shared lib not available
    class SchemaManager:
        def __init__(self):
            self._sql_statements = []
        
        def add_extension(self, name: str) -> "SchemaManager":
            self._sql_statements.append(f'CREATE EXTENSION IF NOT EXISTS "{name}";')
            return self
        
        def add_table(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        def add_index(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        def add_migration(self, sql: str) -> "SchemaManager":
            self._sql_statements.append(sql.strip())
            return self
        
        async def apply(self, conn) -> None:
            for sql in self._sql_statements:
                await conn.execute(sql)


def get_authz_schema() -> SchemaManager:
    """
    Build and return the authz service schema definition.
    
    Returns:
        SchemaManager configured with all authz tables and indexes.
    """
    schema = SchemaManager()
    
    # ==========================================================================
    # Extensions
    # ==========================================================================
    schema.add_extension("pgcrypto")
    
    # ==========================================================================
    # Core Tables
    # ==========================================================================
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            actor_id uuid NOT NULL,
            action text NOT NULL,
            resource_type text NOT NULL,
            resource_id uuid NULL,
            details jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_oauth_clients (
            client_id text PRIMARY KEY,
            client_secret_hash text NOT NULL,
            allowed_audiences text[] NOT NULL DEFAULT '{}'::text[],
            allowed_scopes text[] NOT NULL DEFAULT '{}'::text[],
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_signing_keys (
            kid text PRIMARY KEY,
            alg text NOT NULL,
            private_key_pem bytea NOT NULL,
            public_jwk jsonb NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_roles (
            id uuid PRIMARY KEY,
            name text NOT NULL UNIQUE,
            description text NULL,
            scopes text[] NOT NULL DEFAULT '{}'::text[],
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
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
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_user_roles (
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            role_id uuid NOT NULL REFERENCES authz_roles(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (user_id, role_id)
        )
    """)
    
    # ==========================================================================
    # Envelope Encryption Tables
    # ==========================================================================
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_key_encryption_keys (
            kek_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            owner_type text NOT NULL CHECK (owner_type IN ('role', 'user', 'system')),
            owner_id uuid NULL,
            encrypted_key bytea NOT NULL,
            key_algorithm text NOT NULL DEFAULT 'AES-256-GCM',
            key_version integer NOT NULL DEFAULT 1,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            rotated_at timestamptz NULL,
            UNIQUE (owner_type, owner_id, key_version)
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_wrapped_data_keys (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id uuid NOT NULL,
            kek_id uuid NOT NULL REFERENCES authz_key_encryption_keys(kek_id) ON DELETE CASCADE,
            wrapped_dek bytea NOT NULL,
            dek_algorithm text NOT NULL DEFAULT 'AES-256-GCM',
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (file_id, kek_id)
        )
    """)
    
    # ==========================================================================
    # Session Management Tables
    # ==========================================================================
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_sessions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            token text NOT NULL UNIQUE,
            expires_at timestamptz NOT NULL,
            ip_address text NULL,
            user_agent text NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_magic_links (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            token text NOT NULL UNIQUE,
            email text NOT NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_passkeys (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            credential_id text NOT NULL UNIQUE,
            credential_public_key text NOT NULL,
            counter bigint NOT NULL DEFAULT 0,
            device_type text NOT NULL,
            backed_up boolean NOT NULL DEFAULT false,
            transports text[] NOT NULL DEFAULT '{}'::text[],
            aaguid text NULL,
            name text NOT NULL,
            last_used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_passkey_challenges (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            challenge text NOT NULL UNIQUE,
            user_id uuid NULL,
            type text NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_totp_codes (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            code_hash text NOT NULL,
            email text NOT NULL,
            expires_at timestamptz NOT NULL,
            used_at timestamptz NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_delegation_tokens (
            jti uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
            scopes text[] NOT NULL DEFAULT '{}',
            name text NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            revoked_at timestamptz NULL
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_email_domain_config (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            domain text NOT NULL UNIQUE,
            is_allowed boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    
    schema.add_table("""
        CREATE TABLE IF NOT EXISTS authz_role_bindings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            role_id uuid NOT NULL REFERENCES authz_roles(id) ON DELETE CASCADE,
            resource_type text NOT NULL,
            resource_id text NOT NULL,
            permissions jsonb DEFAULT '{}'::jsonb,
            created_at timestamptz DEFAULT now(),
            created_by uuid NULL,
            UNIQUE(role_id, resource_type, resource_id)
        )
    """)
    
    # ==========================================================================
    # Indexes
    # ==========================================================================
    
    # Wrapped data keys
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_wrapped_data_keys_file_id ON authz_wrapped_data_keys(file_id)")
    
    # Sessions
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_sessions_token ON authz_sessions(token)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_sessions_user_id ON authz_sessions(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_sessions_expires_at ON authz_sessions(expires_at)")
    
    # Magic links
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_magic_links_token ON authz_magic_links(token)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_magic_links_user_id ON authz_magic_links(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_magic_links_email ON authz_magic_links(email)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_magic_links_expires_at ON authz_magic_links(expires_at)")
    
    # TOTP codes
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_totp_codes_user_id ON authz_totp_codes(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_totp_codes_email_code ON authz_totp_codes(email, code_hash)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_totp_codes_expires_at ON authz_totp_codes(expires_at)")
    
    # Passkeys
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_passkeys_user_id ON authz_passkeys(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_passkeys_credential_id ON authz_passkeys(credential_id)")
    
    # Passkey challenges
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_passkey_challenges_challenge ON authz_passkey_challenges(challenge)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_passkey_challenges_expires_at ON authz_passkey_challenges(expires_at)")
    
    # Delegation tokens
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_delegation_tokens_user_id ON authz_delegation_tokens(user_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_delegation_tokens_expires_at ON authz_delegation_tokens(expires_at)")
    
    # Email domain config
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_authz_email_domain_config_domain ON authz_email_domain_config(domain)")
    
    # Role bindings
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_role_bindings_resource ON authz_role_bindings(resource_type, resource_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_role_bindings_role ON authz_role_bindings(role_id)")
    
    # Audit logs
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_id ON audit_logs(actor_id)")
    schema.add_index("CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at)")
    
    # ==========================================================================
    # Migrations (Inline ALTER TABLE patterns for existing deployments)
    # ==========================================================================
    
    # Add scopes column to authz_roles if missing
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_roles' AND column_name = 'scopes'
            ) THEN
                ALTER TABLE authz_roles ADD COLUMN scopes text[] NOT NULL DEFAULT '{}'::text[];
            END IF;
        END $$
    """)
    
    # Add extended user columns if missing
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_users' AND column_name = 'email_verified_at'
            ) THEN
                ALTER TABLE authz_users ADD COLUMN email_verified_at timestamptz NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_users' AND column_name = 'last_login_at'
            ) THEN
                ALTER TABLE authz_users ADD COLUMN last_login_at timestamptz NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_users' AND column_name = 'pending_expires_at'
            ) THEN
                ALTER TABLE authz_users ADD COLUMN pending_expires_at timestamptz NULL;
            END IF;
        END $$
    """)
    
    # Add is_system column to authz_roles if missing
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_roles' AND column_name = 'is_system'
            ) THEN
                ALTER TABLE authz_roles ADD COLUMN is_system boolean NOT NULL DEFAULT false;
            END IF;
        END $$
    """)
    
    # Add assigned_by column to authz_user_roles if missing
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'authz_user_roles' AND column_name = 'assigned_by'
            ) THEN
                ALTER TABLE authz_user_roles ADD COLUMN assigned_by uuid NULL;
            END IF;
        END $$
    """)
    
    # Add extended audit_logs columns if missing
    schema.add_migration("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'event_type'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN event_type text NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'target_user_id'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN target_user_id uuid NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'target_role_id'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN target_role_id uuid NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'target_app_id'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN target_app_id uuid NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'ip_address'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN ip_address text NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'user_agent'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN user_agent text NULL;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'success'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN success boolean NOT NULL DEFAULT true;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'audit_logs' AND column_name = 'error_message'
            ) THEN
                ALTER TABLE audit_logs ADD COLUMN error_message text NULL;
            END IF;
        END $$
    """)
    
    return schema
