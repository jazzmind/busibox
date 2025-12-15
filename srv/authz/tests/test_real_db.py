"""
Integration tests for authz service against real test database.

These tests connect to the actual test PostgreSQL database at 10.96.201.203
and verify the full OAuth2 token flow, RBAC operations, and database interactions.

Run with: pytest tests/test_real_db.py -v
"""

import pytest
import asyncpg
import httpx
import os
from datetime import datetime, timedelta

# Test database configuration
TEST_DB_HOST = os.getenv("TEST_DB_HOST", "10.96.201.203")
TEST_DB_PORT = int(os.getenv("TEST_DB_PORT", "5432"))
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "busibox")
TEST_DB_USER = os.getenv("TEST_DB_USER", "busibox_user")
TEST_DB_PASSWORD = os.getenv("TEST_DB_PASSWORD", "")  # Get from vault

# Test authz service
TEST_AUTHZ_URL = os.getenv("TEST_AUTHZ_URL", "http://10.96.201.210:8010")


@pytest.fixture
async def db_pool():
    """Create a connection pool to the test database."""
    if not TEST_DB_PASSWORD:
        pytest.skip("TEST_DB_PASSWORD not set - cannot connect to test database")
    
    pool = await asyncpg.create_pool(
        host=TEST_DB_HOST,
        port=TEST_DB_PORT,
        database=TEST_DB_NAME,
        user=TEST_DB_USER,
        password=TEST_DB_PASSWORD,
        min_size=1,
        max_size=5,
    )
    yield pool
    await pool.close()


@pytest.fixture
async def clean_test_data(db_pool):
    """Clean up test data before and after tests."""
    async with db_pool.acquire() as conn:
        # Clean up any existing test data - cast UUID columns to text for LIKE operator
        await conn.execute("DELETE FROM authz_user_roles WHERE user_id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_users WHERE user_id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_roles WHERE id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_oauth_clients WHERE client_id LIKE 'test-%'")
    
    yield
    
    # Clean up after test - cast UUID columns to text for LIKE operator
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM authz_user_roles WHERE user_id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_users WHERE user_id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_roles WHERE id::text LIKE 'test-%'")
        await conn.execute("DELETE FROM authz_oauth_clients WHERE client_id LIKE 'test-%'")


class TestDatabaseSchema:
    """Test that all required database tables and schemas exist."""
    
    @pytest.mark.asyncio
    async def test_authz_tables_exist(self, db_pool):
        """Verify all authz tables exist."""
        async with db_pool.acquire() as conn:
            tables = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name LIKE 'authz_%'
                ORDER BY table_name
            """)
            
            table_names = [row['table_name'] for row in tables]
            
            # Check for required tables
            required_tables = [
                'audit_logs',  # Note: table is called audit_logs, not authz_audit_log
                'authz_oauth_clients',
                'authz_roles',
                'authz_signing_keys',
                'authz_user_roles',
                'authz_users',
            ]
            
            for table in required_tables:
                assert table in table_names, f"Missing required table: {table}"
    
    @pytest.mark.asyncio
    async def test_authz_roles_schema(self, db_pool):
        """Verify authz_roles table schema."""
        async with db_pool.acquire() as conn:
            columns = await conn.fetch("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'authz_roles'
                ORDER BY ordinal_position
            """)
            
            column_names = [row['column_name'] for row in columns]
            
            assert 'id' in column_names
            assert 'name' in column_names
            assert 'description' in column_names  # Note: no permissions column in current schema
            assert 'created_at' in column_names
            assert 'updated_at' in column_names
    
    @pytest.mark.asyncio
    async def test_authz_oauth_clients_schema(self, db_pool):
        """Verify authz_oauth_clients table schema."""
        async with db_pool.acquire() as conn:
            columns = await conn.fetch("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = 'authz_oauth_clients'
                ORDER BY ordinal_position
            """)
            
            column_names = [row['column_name'] for row in columns]
            
            assert 'client_id' in column_names
            assert 'client_secret_hash' in column_names
            assert 'allowed_audiences' in column_names
            assert 'allowed_scopes' in column_names


class TestOAuthClientOperations:
    """Test OAuth client CRUD operations."""
    
    @pytest.mark.asyncio
    async def test_create_oauth_client(self, db_pool, clean_test_data):
        """Test creating an OAuth client."""
        async with db_pool.acquire() as conn:
            client_id = f"test-client-{datetime.now().timestamp()}"
            client_secret_hash = "pbkdf2:sha256:200000$test$hash"
            
            await conn.execute("""
                INSERT INTO authz_oauth_clients 
                (client_id, client_secret_hash, allowed_audiences, allowed_scopes)
                VALUES ($1, $2, $3, $4)
            """, client_id, client_secret_hash, 
                ["ingest-api", "agent-api"],  # Pass as array, not JSON string
                ["ingest.read", "agent.execute"])
            
            # Verify it was created
            row = await conn.fetchrow(
                "SELECT * FROM authz_oauth_clients WHERE client_id = $1",
                client_id
            )
            
            assert row is not None
            assert row['client_id'] == client_id
            assert row['allowed_audiences'] == ["ingest-api", "agent-api"]
    
    @pytest.mark.asyncio
    async def test_list_oauth_clients(self, db_pool, clean_test_data):
        """Test listing OAuth clients."""
        async with db_pool.acquire() as conn:
            # Create a test client
            client_id = f"test-client-{datetime.now().timestamp()}"
            await conn.execute("""
                INSERT INTO authz_oauth_clients 
                (client_id, client_secret_hash, allowed_audiences, allowed_scopes)
                VALUES ($1, $2, $3, $4)
            """, client_id, "hash", [], [])
            
            # List all test clients
            rows = await conn.fetch("""
                SELECT client_id FROM authz_oauth_clients 
                WHERE client_id LIKE 'test-%'
            """)
            
            assert len(rows) >= 1
            client_ids = [row['client_id'] for row in rows]
            assert client_id in client_ids


class TestRBACOperations:
    """Test RBAC (roles and users) operations."""
    
    @pytest.mark.asyncio
    async def test_create_role(self, db_pool, clean_test_data):
        """Test creating a role."""
        async with db_pool.acquire() as conn:
            role_id = f"test-role-{datetime.now().timestamp()}"
            
            await conn.execute("""
                INSERT INTO authz_roles (id, name, description)
                VALUES ($1, $2, $3)
            """, role_id, "Test Role", "Test role description")
            
            # Verify it was created
            row = await conn.fetchrow(
                "SELECT * FROM authz_roles WHERE id = $1",
                role_id
            )
            
            assert row is not None
            assert row['name'] == "Test Role"
            assert row['permissions'] == '["read", "write"]'
    
    @pytest.mark.asyncio
    async def test_create_user_and_assign_role(self, db_pool, clean_test_data):
        """Test creating a user and assigning a role."""
        async with db_pool.acquire() as conn:
            user_id = f"test-user-{datetime.now().timestamp()}"
            role_id = f"test-role-{datetime.now().timestamp()}"
            
            # Create role
            await conn.execute("""
                INSERT INTO authz_roles (id, name, description)
                VALUES ($1, $2, $3)
            """, role_id, "Test Role", "Test role")
            
            # Create user
            await conn.execute("""
                INSERT INTO authz_users (user_id, email, status)
                VALUES ($1, $2, $3)
            """, user_id, "test@example.com", "active")
            
            # Assign role to user
            await conn.execute("""
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
            """, user_id, role_id)
            
            # Verify assignment
            rows = await conn.fetch("""
                SELECT r.name 
                FROM authz_user_roles ur
                JOIN authz_roles r ON ur.role_id = r.id
                WHERE ur.user_id = $1
            """, user_id)
            
            assert len(rows) == 1
            assert rows[0]['name'] == "Test Role"
    
    @pytest.mark.asyncio
    async def test_get_user_roles(self, db_pool, clean_test_data):
        """Test retrieving user roles."""
        async with db_pool.acquire() as conn:
            user_id = f"test-user-{datetime.now().timestamp()}"
            role1_id = f"test-role-1-{datetime.now().timestamp()}"
            role2_id = f"test-role-2-{datetime.now().timestamp()}"
            
            # Create roles
            await conn.execute("""
                INSERT INTO authz_roles (id, name, description)
                VALUES ($1, $2, $3), ($4, $5, $6)
            """, role1_id, "Role 1", "Role 1 desc",
                 role2_id, "Role 2", "Role 2 desc")
            
            # Create user
            await conn.execute("""
                INSERT INTO authz_users (user_id, email, status)
                VALUES ($1, $2, $3)
            """, user_id, "test@example.com", "active")
            
            # Assign both roles
            await conn.execute("""
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2), ($1, $3)
            """, user_id, role1_id, role2_id)
            
            # Get user roles
            rows = await conn.fetch("""
                SELECT r.id, r.name, r.permissions
                FROM authz_user_roles ur
                JOIN authz_roles r ON ur.role_id = r.id
                WHERE ur.user_id = $1
                ORDER BY r.name
            """, user_id)
            
            assert len(rows) == 2
            assert rows[0]['name'] == "Role 1"
            assert rows[1]['name'] == "Role 2"


class TestSigningKeys:
    """Test signing key operations."""
    
    @pytest.mark.asyncio
    async def test_signing_keys_exist(self, db_pool):
        """Test that signing keys exist in the database."""
        async with db_pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT kid, alg, is_active 
                FROM authz_signing_keys 
                ORDER BY created_at DESC
                LIMIT 5
            """)
            
            # Should have at least one signing key
            assert len(rows) >= 1, "No signing keys found - authz may not be initialized"
            
            # At least one should be active
            active_keys = [row for row in rows if row['is_active']]
            assert len(active_keys) >= 1, "No active signing keys found"
    
    @pytest.mark.asyncio
    async def test_active_signing_key_has_jwk(self, db_pool):
        """Test that active signing key has a valid JWK."""
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT kid, alg, public_jwk 
                FROM authz_signing_keys 
                WHERE is_active = true
                ORDER BY created_at DESC
                LIMIT 1
            """)
            
            assert row is not None, "No active signing key found"
            assert row['kid'] is not None
            assert row['alg'] == 'RS256'
            assert row['public_jwk'] is not None
            
            # Verify JWK has required fields
            import json
            jwk = json.loads(row['public_jwk'])
            assert 'kty' in jwk
            assert 'kid' in jwk
            assert 'use' in jwk
            assert jwk['kty'] == 'RSA'


class TestAuditLog:
    """Test audit logging."""
    
    @pytest.mark.asyncio
    async def test_audit_log_table_exists(self, db_pool):
        """Test that audit log table exists and has correct schema."""
        async with db_pool.acquire() as conn:
            columns = await conn.fetch("""
                SELECT column_name 
                FROM information_schema.columns
                WHERE table_name = 'audit_logs'
            """)
            
            column_names = [row['column_name'] for row in columns]
            
            assert 'id' in column_names
            assert 'created_at' in column_names  # Note: column is called created_at, not timestamp
            assert 'actor_id' in column_names
            assert 'action' in column_names
            assert 'resource_type' in column_names
    
    @pytest.mark.asyncio
    async def test_write_audit_log(self, db_pool, clean_test_data):
        """Test writing to audit log."""
        async with db_pool.acquire() as conn:
            import uuid
            test_actor_id = uuid.uuid4()
            test_resource_id = uuid.uuid4()
            
            await conn.execute("""
                INSERT INTO audit_logs 
                (actor_id, action, resource_type, resource_id, details)
                VALUES ($1, $2, $3, $4, $5)
            """, test_actor_id, "test_action", "test_resource", 
                 test_resource_id, '{"test": true}')
            
            # Verify it was written
            rows = await conn.fetch("""
                SELECT * FROM audit_logs 
                WHERE actor_id = $1
                AND action = 'test_action'
            """, test_actor_id)
            
            assert len(rows) >= 1


class TestRLSSessionVariables:
    """Test Row Level Security session variables."""
    
    @pytest.mark.asyncio
    async def test_set_rls_variables(self, db_pool):
        """Test setting RLS session variables."""
        async with db_pool.acquire() as conn:
            user_id = "test-user-123"
            role_ids = '["admin", "user"]'
            
            # Set RLS variables (using the fixed syntax)
            await conn.execute(f"SET app.user_id = '{user_id}'")
            await conn.execute(f"SET app.user_role_ids_read = '{role_ids}'")
            await conn.execute(f"SET app.user_role_ids_write = '{role_ids}'")
            
            # Verify they were set
            user_id_result = await conn.fetchval("SHOW app.user_id")
            assert user_id_result == user_id
            
            role_ids_result = await conn.fetchval("SHOW app.user_role_ids_read")
            assert role_ids_result == role_ids


@pytest.mark.skipif(
    not os.getenv("TEST_AUTHZ_URL"),
    reason="TEST_AUTHZ_URL not set - skipping live service tests"
)
class TestAuthzServiceEndpoints:
    """Test live authz service endpoints."""
    
    @pytest.mark.asyncio
    async def test_health_endpoint(self):
        """Test health endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{TEST_AUTHZ_URL}/health/live")
            assert resp.status_code == 200
            data = resp.json()
            assert data['status'] == 'ok'
    
    @pytest.mark.asyncio
    async def test_jwks_endpoint(self):
        """Test JWKS endpoint."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{TEST_AUTHZ_URL}/.well-known/jwks.json")
            assert resp.status_code == 200
            data = resp.json()
            assert 'keys' in data
            assert len(data['keys']) >= 1
            
            # Verify JWK structure
            jwk = data['keys'][0]
            assert 'kty' in jwk
            assert 'kid' in jwk
            assert 'use' in jwk
            assert jwk['kty'] == 'RSA'

