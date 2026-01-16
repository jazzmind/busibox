#!/usr/bin/env python3
"""
Bootstrap test databases for Docker integration testing.

This script initializes the test databases (test_authz, test_files, test_agent_server)
with the same schema and bootstrap data as the production databases.

Run after all services are healthy:
    python scripts/docker/bootstrap-test-databases.py

Environment variables:
    POSTGRES_HOST: PostgreSQL host (default: postgres)
    POSTGRES_PORT: PostgreSQL port (default: 5432)
    TEST_DB_USER: Test database user (default: busibox_test_user)
    TEST_DB_PASSWORD: Test database password (default: testpassword)
"""

import asyncio
import os
import sys
import hashlib
import secrets
import json
from datetime import datetime, timezone
from pathlib import Path

# Add authz src to path
authz_src = Path(__file__).parent.parent.parent / "srv" / "authz" / "src"
if authz_src.exists():
    sys.path.insert(0, str(authz_src))

try:
    import asyncpg
except ImportError:
    print("ERROR: asyncpg not installed. Run: pip install asyncpg")
    sys.exit(1)

# Configuration
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
TEST_DB_USER = os.getenv("TEST_DB_USER", "busibox_test_user")
TEST_DB_PASSWORD = os.getenv("TEST_DB_PASSWORD", "testpassword")

# Bootstrap OAuth client configuration (same as production)
BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "ai-portal-secret")
BOOTSTRAP_ALLOWED_AUDIENCES = ["ingest-api", "search-api", "agent-api"]
BOOTSTRAP_ALLOWED_SCOPES = ["read", "write", "search.read", "ingest.write", "ingest.read", "agent.execute"]

# Admin token for test access
ADMIN_TOKEN = os.getenv("AUTHZ_ADMIN_TOKEN", "local-admin-token")


async def check_schema_exists(conn):
    """Check if the authz schema tables exist."""
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'authz_signing_keys'
        )
    """)
    return result


async def apply_authz_schema(conn):
    """Check if authz schema exists (applied by init-databases.sql)."""
    print("  Checking authz schema...")
    
    if await check_schema_exists(conn):
        print("  ✓ Schema already exists (from init-databases.sql)")
        return True
    else:
        print("  ✗ Schema not found - this should be applied by init-databases.sql")
        print("    Make sure postgres container initialized correctly")
        return False


async def create_bootstrap_data(conn):
    """Create bootstrap data (signing keys, OAuth clients, test domains)."""
    print("  Creating bootstrap data...")
    
    # Check if signing key exists
    existing_key = await conn.fetchval("SELECT kid FROM authz_signing_keys WHERE is_active = true LIMIT 1")
    if not existing_key:
        # Generate RSA key pair
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.backends import default_backend
            import base64
            
            # Generate key
            private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
                backend=default_backend()
            )
            
            # Get private key PEM
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ).decode('utf-8')
            
            # Get public key numbers for JWK
            public_key = private_key.public_key()
            public_numbers = public_key.public_numbers()
            
            # Convert to base64url
            def int_to_base64url(n, length):
                data = n.to_bytes(length, byteorder='big')
                return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')
            
            kid = secrets.token_urlsafe(16)
            jwk = {
                "kty": "RSA",
                "kid": kid,
                "use": "sig",
                "alg": "RS256",
                "n": int_to_base64url(public_numbers.n, 256),
                "e": int_to_base64url(public_numbers.e, 3),
            }
            
            # private_key_pem is bytea in the schema
            private_pem_bytes = private_pem.encode('utf-8')
            
            await conn.execute("""
                INSERT INTO authz_signing_keys (kid, alg, public_jwk, private_key_pem, is_active)
                VALUES ($1, $2, $3, $4, true)
                ON CONFLICT (kid) DO NOTHING
            """, kid, "RS256", json.dumps(jwk), private_pem_bytes)
            
            print(f"    ✓ Created signing key: {kid}")
        except ImportError:
            print("    ⚠ cryptography not installed, skipping signing key creation")
    else:
        print(f"    ✓ Signing key exists: {existing_key}")
    
    # Create bootstrap OAuth client
    existing_client = await conn.fetchval(
        "SELECT client_id FROM authz_oauth_clients WHERE client_id = $1",
        BOOTSTRAP_CLIENT_ID
    )
    if not existing_client:
        # Hash the client secret
        secret_hash = hashlib.sha256(BOOTSTRAP_CLIENT_SECRET.encode()).hexdigest()
        
        await conn.execute("""
            INSERT INTO authz_oauth_clients (client_id, client_secret_hash, allowed_audiences, allowed_scopes, is_active)
            VALUES ($1, $2, $3, $4, true)
            ON CONFLICT (client_id) DO NOTHING
        """, BOOTSTRAP_CLIENT_ID, secret_hash, BOOTSTRAP_ALLOWED_AUDIENCES, BOOTSTRAP_ALLOWED_SCOPES)
        
        print(f"    ✓ Created OAuth client: {BOOTSTRAP_CLIENT_ID}")
    else:
        print(f"    ✓ OAuth client exists: {existing_client}")
    
    # Allow test.example.com domain for tests
    # Use separate INSERT statements to ensure both domains are added
    test_domains = ["test.example.com", "busibox.local"]
    for domain in test_domains:
        existing_domain = await conn.fetchval(
            "SELECT domain FROM authz_email_domain_config WHERE domain = $1",
            domain
        )
        if not existing_domain:
            await conn.execute("""
                INSERT INTO authz_email_domain_config (domain, is_allowed)
                VALUES ($1, true)
                ON CONFLICT (domain) DO NOTHING
            """, domain)
            print(f"    ✓ Added test email domain: {domain}")
        else:
            print(f"    ✓ Test email domain exists: {domain}")
    
    print("  ✓ Bootstrap data created")


async def bootstrap_test_authz():
    """Bootstrap the test_authz database."""
    print("\nBootstrapping test_authz database...")
    
    try:
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database="test_authz"
        )
    except Exception as e:
        print(f"  ERROR: Cannot connect to test_authz: {e}")
        return False
    
    try:
        schema_ok = await apply_authz_schema(conn)
        if not schema_ok:
            return False
        await create_bootstrap_data(conn)
        print("✓ test_authz bootstrapped successfully")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await conn.close()


async def add_test_domains_to_production():
    """Add test email domains to the production authz database.
    
    This is needed because integration tests call the live authz-api,
    which uses the production 'authz' database. The tests create users
    with @test.example.com emails, so the domain must be allowed.
    """
    print("\nAdding test domains to production authz database...")
    
    try:
        # Connect as busibox_user to production authz database
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user="busibox_user",
            password=os.getenv("POSTGRES_PASSWORD", "devpassword"),
            database="authz"
        )
    except Exception as e:
        print(f"  WARNING: Cannot connect to authz database: {e}")
        print("  (This is OK if running outside Docker or if DB is not ready)")
        return True  # Don't fail the bootstrap
    
    try:
        await conn.execute("""
            INSERT INTO authz_email_domain_config (domain, is_allowed)
            VALUES ('test.example.com', true), ('busibox.local', true)
            ON CONFLICT (domain) DO NOTHING
        """)
        print("  ✓ Test domains added to production authz")
        return True
    except Exception as e:
        print(f"  WARNING: Could not add test domains: {e}")
        return True  # Don't fail the bootstrap
    finally:
        await conn.close()


async def check_files_schema_exists(conn):
    """Check if the ingest/files schema tables exist."""
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'ingestion_files'
        )
    """)
    return result


async def apply_files_schema(conn):
    """Apply the ingest/files schema to test_files database."""
    print("  Applying ingest/files schema...")
    
    # Check if schema already exists
    if await check_files_schema_exists(conn):
        print("  ✓ Schema already exists")
        return True
    
    # Create extension
    await conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    
    # Create tables in order
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            name VARCHAR(255) NOT NULL,
            description TEXT,
            created_by UUID NOT NULL,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_files (
            file_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL,
            owner_id UUID,
            filename VARCHAR(255) NOT NULL,
            original_filename VARCHAR(255) NOT NULL,
            mime_type VARCHAR(100) NOT NULL,
            size_bytes BIGINT NOT NULL,
            storage_path TEXT NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            document_type VARCHAR(50),
            primary_language VARCHAR(10),
            detected_languages VARCHAR(10)[],
            classification_confidence REAL CHECK (classification_confidence >= 0 AND classification_confidence <= 1),
            chunk_count INTEGER DEFAULT 0,
            vector_count INTEGER DEFAULT 0,
            processing_duration_seconds INTEGER,
            extracted_title VARCHAR(500),
            extracted_author VARCHAR(255),
            extracted_date DATE,
            extracted_keywords TEXT[],
            metadata JSONB DEFAULT '{}',
            permissions JSONB NOT NULL DEFAULT '{"visibility": "private"}',
            visibility VARCHAR(20) DEFAULT 'personal',
            has_markdown BOOLEAN DEFAULT false,
            markdown_path VARCHAR(512),
            images_path VARCHAR(512),
            image_count INTEGER DEFAULT 0,
            processing_strategies JSONB DEFAULT '[]'::jsonb,
            group_id UUID REFERENCES groups(id) ON DELETE SET NULL,
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_status (
            file_id UUID PRIMARY KEY REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
            chunks_processed INTEGER,
            total_chunks INTEGER,
            pages_processed INTEGER,
            total_pages INTEGER,
            error_message TEXT,
            retry_count INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_chunks (
            chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            char_offset INTEGER,
            token_count INTEGER,
            page_number INTEGER,
            section_heading VARCHAR(500),
            processing_strategy VARCHAR(50) DEFAULT 'simple',
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMP NOT NULL DEFAULT NOW(),
            UNIQUE (file_id, chunk_index)
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS document_roles (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            role_id UUID NOT NULL,
            role_name VARCHAR(100) NOT NULL,
            added_at TIMESTAMP DEFAULT NOW(),
            added_by UUID,
            UNIQUE(file_id, role_id)
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS group_memberships (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            group_id UUID NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
            user_id UUID NOT NULL,
            role VARCHAR(50) DEFAULT 'member',
            joined_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(group_id, user_id)
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_history (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            stage VARCHAR(50) NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'started',
            started_at TIMESTAMP NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMP,
            duration_ms INTEGER,
            details JSONB DEFAULT '{}',
            error_message TEXT
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_strategy_results (
            result_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            file_id UUID NOT NULL REFERENCES ingestion_files(file_id) ON DELETE CASCADE,
            processing_strategy VARCHAR(50) NOT NULL,
            success BOOLEAN NOT NULL DEFAULT false,
            text_length INTEGER,
            chunk_count INTEGER,
            embedding_count INTEGER,
            visual_embedding_count INTEGER,
            processing_time_seconds NUMERIC(10,3),
            error_message TEXT,
            metadata JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(file_id, processing_strategy)
        )
    """)
    
    # Create indexes
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_user_id ON ingestion_files(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_owner ON ingestion_files(owner_id)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_content_hash ON ingestion_files(content_hash)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_document_type ON ingestion_files(document_type)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_created_at ON ingestion_files(created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_visibility ON ingestion_files(visibility)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_files_group ON ingestion_files(group_id)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_status_stage ON ingestion_status(stage)",
        "CREATE INDEX IF NOT EXISTS idx_ingestion_chunks_file_id ON ingestion_chunks(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_document_roles_file ON document_roles(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_document_roles_role ON document_roles(role_id)",
        "CREATE INDEX IF NOT EXISTS idx_document_roles_name ON document_roles(role_name)",
        "CREATE INDEX IF NOT EXISTS idx_groups_created_by ON groups(created_by)",
        "CREATE INDEX IF NOT EXISTS idx_group_memberships_user ON group_memberships(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_group_memberships_group ON group_memberships(group_id)",
        "CREATE INDEX IF NOT EXISTS idx_processing_history_file_id ON processing_history(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_processing_history_stage ON processing_history(stage)",
        "CREATE INDEX IF NOT EXISTS idx_strategy_results_file ON processing_strategy_results(file_id)",
        "CREATE INDEX IF NOT EXISTS idx_strategy_results_strategy ON processing_strategy_results(processing_strategy)",
    ]
    for idx in indexes:
        await conn.execute(idx)
    
    print("  ✓ Schema applied")
    return True


async def bootstrap_test_files():
    """Bootstrap the test_files database (for ingest/search)."""
    print("\nBootstrapping test_files database...")
    
    try:
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database="test_files"
        )
    except Exception as e:
        print(f"  ERROR: Cannot connect to test_files: {e}")
        return False
    
    try:
        schema_ok = await apply_files_schema(conn)
        if not schema_ok:
            return False
        print("✓ test_files bootstrapped successfully")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await conn.close()


async def check_agent_schema_exists(conn):
    """Check if the agent schema tables exist."""
    result = await conn.fetchval("""
        SELECT EXISTS (
            SELECT FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_name = 'agent_definitions'
        )
    """)
    return result


async def apply_agent_schema(conn):
    """Apply the agent schema to test_agent_server database."""
    print("  Applying agent schema...")
    
    # Check if schema already exists
    if await check_agent_schema_exists(conn):
        print("  ✓ Schema already exists")
        return True
    
    # Create extension
    await conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    
    # Create tables (from agent/app/db/schema.sql)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) UNIQUE NOT NULL,
            display_name VARCHAR(255),
            description TEXT,
            model VARCHAR(255) NOT NULL,
            instructions TEXT NOT NULL,
            tools JSONB DEFAULT '{}'::jsonb,
            workflows JSONB,
            scopes JSONB DEFAULT '[]'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            is_builtin BOOLEAN DEFAULT FALSE,
            created_by VARCHAR(255),
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS tool_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) UNIQUE NOT NULL,
            description TEXT,
            schema JSONB DEFAULT '{}'::jsonb,
            entrypoint VARCHAR(255) NOT NULL,
            scopes JSONB DEFAULT '[]'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS workflow_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) UNIQUE NOT NULL,
            description TEXT,
            steps JSONB DEFAULT '[]'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_definitions (
            id UUID PRIMARY KEY,
            name VARCHAR(120) UNIQUE NOT NULL,
            description TEXT,
            config JSONB DEFAULT '{}'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_databases (
            id UUID PRIMARY KEY,
            name VARCHAR(120) UNIQUE NOT NULL,
            description TEXT,
            config JSONB DEFAULT '{}'::jsonb,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS rag_documents (
            id UUID PRIMARY KEY,
            rag_database_id UUID REFERENCES rag_databases(id) ON DELETE CASCADE,
            path VARCHAR(255) NOT NULL,
            metadata JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS run_records (
            id UUID PRIMARY KEY,
            agent_id UUID NOT NULL,
            workflow_id UUID,
            status VARCHAR(50) DEFAULT 'pending',
            input JSONB DEFAULT '{}'::jsonb,
            output JSONB,
            events JSONB DEFAULT '[]'::jsonb,
            created_by VARCHAR(255),
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS token_grants (
            id UUID PRIMARY KEY,
            subject VARCHAR(255) NOT NULL,
            scopes JSONB DEFAULT '[]'::jsonb,
            token TEXT NOT NULL,
            expires_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
        )
    """)
    
    # Create indexes
    await conn.execute("CREATE INDEX IF NOT EXISTS ix_agent_definitions_name ON agent_definitions(name)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_rag_documents_db ON rag_documents(rag_database_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_token_grants_subject ON token_grants(subject)")
    
    print("  ✓ Schema applied")
    return True


async def bootstrap_test_agent_server():
    """Bootstrap the test_agent_server database."""
    print("\nBootstrapping test_agent_server database...")
    
    try:
        conn = await asyncpg.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=TEST_DB_USER,
            password=TEST_DB_PASSWORD,
            database="test_agent_server"
        )
    except Exception as e:
        print(f"  ERROR: Cannot connect to test_agent_server: {e}")
        return False
    
    try:
        schema_ok = await apply_agent_schema(conn)
        if not schema_ok:
            return False
        print("✓ test_agent_server bootstrapped successfully")
        return True
    except Exception as e:
        print(f"  ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await conn.close()


async def main():
    print("=" * 60)
    print("Busibox Test Database Bootstrap")
    print("=" * 60)
    print(f"PostgreSQL: {POSTGRES_HOST}:{POSTGRES_PORT}")
    print(f"Test User: {TEST_DB_USER}")
    print()
    
    all_success = True
    
    # Bootstrap test_authz
    if not await bootstrap_test_authz():
        all_success = False
    
    # Bootstrap test_files (for ingest/search)
    if not await bootstrap_test_files():
        all_success = False
    
    # Bootstrap test_agent_server
    if not await bootstrap_test_agent_server():
        all_success = False
    
    # Also add test domains to production authz for integration tests
    await add_test_domains_to_production()
    
    print()
    if all_success:
        print("=" * 60)
        print("✓ All test databases bootstrapped successfully")
        print("=" * 60)
        return 0
    else:
        print("=" * 60)
        print("✗ Some databases failed to bootstrap")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
