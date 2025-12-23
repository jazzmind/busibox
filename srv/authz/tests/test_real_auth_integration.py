"""
Real integration tests for authz authentication and user management endpoints.

These tests connect to the actual test PostgreSQL database and authz service.
NO MOCKS - all tests use real database operations and real authentication.

Run with: pytest tests/test_real_auth_integration.py -v

Required environment variables:
- TEST_DB_PASSWORD: Password for test database
- TEST_AUTHZ_URL: URL of test authz service (default: http://10.96.201.210:8010)
- AUTHZ_ADMIN_TOKEN: Admin token for authz service

These tests cover:
- User CRUD operations (create, list, get, update, delete)
- User status transitions (activate, deactivate, reactivate)
- User role management (add, remove roles)
- Session management (create, validate, delete)
- Magic link flow (create, validate, use)
- TOTP code flow (create, verify)
- Passkey operations (challenge, register, list, authenticate, delete)
- Audit log operations (create, list, user trail)
- Email domain configuration
"""

import os
import pytest
import asyncpg
import httpx
import uuid
import secrets
import hashlib
from datetime import datetime, timedelta

# Test database configuration
TEST_DB_HOST = os.getenv("TEST_DB_HOST", "10.96.201.203")
TEST_DB_PORT = int(os.getenv("TEST_DB_PORT", "5432"))
TEST_DB_NAME = os.getenv("TEST_DB_NAME", "busibox")
TEST_DB_USER = os.getenv("TEST_DB_USER", "busibox_user")
TEST_DB_PASSWORD = os.getenv("TEST_DB_PASSWORD", "")

# Test authz service
TEST_AUTHZ_URL = os.getenv("TEST_AUTHZ_URL", "http://10.96.201.210:8010")
ADMIN_TOKEN = os.getenv("AUTHZ_ADMIN_TOKEN", "")

# OAuth client credentials
BOOTSTRAP_CLIENT_ID = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
BOOTSTRAP_CLIENT_SECRET = os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")


def skip_if_no_credentials():
    """Skip tests if credentials are not set."""
    if not TEST_DB_PASSWORD:
        pytest.skip("TEST_DB_PASSWORD not set - cannot connect to test database")
    if not ADMIN_TOKEN:
        pytest.skip("AUTHZ_ADMIN_TOKEN not set - cannot authenticate to authz service")


def skip_if_no_oauth_credentials():
    """Skip tests if OAuth client credentials are not set."""
    skip_if_no_credentials()
    if not BOOTSTRAP_CLIENT_SECRET:
        pytest.skip("AUTHZ_BOOTSTRAP_CLIENT_SECRET not set - cannot test OAuth flows")


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
async def db_pool():
    """Create a connection pool to the test database."""
    skip_if_no_credentials()
    
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
def admin_headers():
    """Headers for admin-authenticated requests."""
    skip_if_no_credentials()
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


@pytest.fixture
async def test_role(db_pool):
    """Create a test role and clean it up after the test."""
    role_id = uuid.uuid4()
    role_name = f"TestRole_{role_id}"
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO authz_roles (id, name, description, scopes)
            VALUES ($1, $2, $3, $4)
            """,
            role_id,
            role_name,
            "Test role for integration tests",
            ["test.read", "test.write"],
        )
    
    yield {"id": str(role_id), "name": role_name}
    
    # Cleanup
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM authz_roles WHERE id = $1", role_id)


@pytest.fixture
async def test_user(db_pool):
    """Create a test user and clean it up after the test."""
    user_id = uuid.uuid4()
    email = f"test_{user_id}@test.example.com"
    
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO authz_users (user_id, email, status)
            VALUES ($1, $2, $3)
            """,
            user_id,
            email,
            "ACTIVE",
        )
    
    yield {"id": str(user_id), "email": email}
    
    # Cleanup
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM authz_users WHERE user_id = $1", user_id)


@pytest.fixture
async def clean_test_data(db_pool):
    """Clean up test data before and after tests."""
    async def cleanup():
        async with db_pool.acquire() as conn:
            # Clean up role bindings for test roles first (due to FK)
            await conn.execute(
                """
                DELETE FROM authz_role_bindings 
                WHERE role_id IN (SELECT id FROM authz_roles WHERE name LIKE 'TestRole_%')
                """
            )
            # Clean up test users (email pattern)
            await conn.execute(
                "DELETE FROM authz_users WHERE email LIKE '%@test.example.com'"
            )
            # Clean up test roles (name pattern)
            await conn.execute(
                "DELETE FROM authz_roles WHERE name LIKE 'TestRole_%'"
            )
            # Clean up test email domains
            await conn.execute(
                "DELETE FROM authz_email_domain_config WHERE domain LIKE 'test.%'"
            )
    
    await cleanup()
    yield
    await cleanup()


# ============================================================================
# User Management Tests
# ============================================================================


class TestUserCRUD:
    """Test user create, read, update, delete operations."""
    
    @pytest.mark.asyncio
    async def test_create_user(self, admin_headers, db_pool, clean_test_data):
        """Test creating a new user via API."""
        email = f"new_{uuid.uuid4()}@test.example.com"
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                json={
                    "email": email,
                    "status": "PENDING",
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 201, f"Failed to create user: {resp.text}"
            data = resp.json()
            assert data["email"] == email.lower()
            assert data["status"] == "PENDING"
            assert "user_id" in data
            assert "created_at" in data
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_users WHERE email = $1",
                    email.lower(),
                )
                assert row is not None
                assert row["status"] == "PENDING"
    
    @pytest.mark.asyncio
    async def test_list_users(self, admin_headers, test_user):
        """Test listing users via API."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to list users: {resp.text}"
            data = resp.json()
            assert "users" in data
            assert "pagination" in data
            assert isinstance(data["users"], list)
            assert data["pagination"]["page"] == 1
            
            # Our test user should be in the list
            user_ids = [u["user_id"] for u in data["users"]]
            assert test_user["id"] in user_ids
    
    @pytest.mark.asyncio
    async def test_list_users_with_filter(self, admin_headers, test_user):
        """Test listing users with status filter."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                params={"status": "ACTIVE"},
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            
            # All returned users should be ACTIVE
            for user in data["users"]:
                assert user["status"] == "ACTIVE"
    
    @pytest.mark.asyncio
    async def test_get_user(self, admin_headers, test_user):
        """Test getting a specific user via API."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to get user: {resp.text}"
            data = resp.json()
            # Note: admin.py endpoint returns "id" instead of "user_id"
            assert data["id"] == test_user["id"]
            assert data["email"] == test_user["email"]
            assert "roles" in data
            assert isinstance(data["roles"], list)
    
    @pytest.mark.asyncio
    async def test_get_nonexistent_user(self, admin_headers):
        """Test getting a user that doesn't exist."""
        fake_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{fake_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 404
    
    @pytest.mark.asyncio
    async def test_update_user(self, admin_headers, test_user, db_pool):
        """Test updating a user via API."""
        new_email = f"updated_{uuid.uuid4()}@test.example.com"
        
        async with httpx.AsyncClient() as client:
            resp = await client.patch(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}",
                headers=admin_headers,
                json={"email": new_email},
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to update user: {resp.text}"
            data = resp.json()
            assert data["email"] == new_email.lower()
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT email FROM authz_users WHERE user_id = $1",
                    uuid.UUID(test_user["id"]),
                )
                assert row["email"] == new_email.lower()
    
    @pytest.mark.asyncio
    async def test_delete_user(self, admin_headers, db_pool, clean_test_data):
        """Test deleting a user via API."""
        # Create a user to delete
        user_id = uuid.uuid4()
        email = f"delete_{user_id}@test.example.com"
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO authz_users (user_id, email, status) VALUES ($1, $2, $3)",
                user_id,
                email,
                "ACTIVE",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to delete user: {resp.text}"
            assert resp.json()["deleted"] is True
            
            # Verify deleted from database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_users WHERE user_id = $1",
                    user_id,
                )
                assert row is None


class TestUserStatusTransitions:
    """Test user activation, deactivation, reactivation."""
    
    @pytest.mark.asyncio
    async def test_activate_pending_user(self, admin_headers, db_pool, clean_test_data):
        """Test activating a pending user."""
        user_id = uuid.uuid4()
        email = f"pending_{user_id}@test.example.com"
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO authz_users (user_id, email, status) VALUES ($1, $2, $3)",
                user_id,
                email,
                "PENDING",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/activate",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to activate user: {resp.text}"
            data = resp.json()
            assert data["status"] == "ACTIVE"
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT status FROM authz_users WHERE user_id = $1",
                    user_id,
                )
                assert row["status"] == "ACTIVE"
    
    @pytest.mark.asyncio
    async def test_deactivate_active_user(self, admin_headers, test_user, db_pool):
        """Test deactivating an active user."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}/deactivate",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to deactivate user: {resp.text}"
            data = resp.json()
            assert data["status"] == "DEACTIVATED"
    
    @pytest.mark.asyncio
    async def test_reactivate_deactivated_user(self, admin_headers, db_pool, clean_test_data):
        """Test reactivating a deactivated user."""
        user_id = uuid.uuid4()
        email = f"deactivated_{user_id}@test.example.com"
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO authz_users (user_id, email, status) VALUES ($1, $2, $3)",
                user_id,
                email,
                "DEACTIVATED",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/reactivate",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to reactivate user: {resp.text}"
            data = resp.json()
            assert data["status"] == "ACTIVE"
    
    @pytest.mark.asyncio
    async def test_cannot_activate_already_active_user(self, admin_headers, test_user):
        """Test that activating an already active user fails."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}/activate",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 400
            assert "already active" in resp.json()["detail"].lower()


class TestUserRoleManagement:
    """Test adding and removing roles from users."""
    
    @pytest.mark.asyncio
    async def test_add_role_to_user(self, admin_headers, test_user, test_role, db_pool):
        """Test adding a role to a user."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}/roles/{test_role['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to add role: {resp.text}"
            data = resp.json()
            assert data["user_id"] == test_user["id"]
            assert data["role_id"] == test_role["id"]
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM authz_user_roles 
                    WHERE user_id = $1 AND role_id = $2
                    """,
                    uuid.UUID(test_user["id"]),
                    uuid.UUID(test_role["id"]),
                )
                assert row is not None
    
    @pytest.mark.asyncio
    async def test_remove_role_from_user(self, admin_headers, test_user, test_role, db_pool):
        """Test removing a role from a user."""
        # First add the role
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                uuid.UUID(test_user["id"]),
                uuid.UUID(test_role["id"]),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}/roles/{test_role['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to remove role: {resp.text}"
            assert resp.json()["deleted"] is True
            
            # Verify removed from database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT * FROM authz_user_roles 
                    WHERE user_id = $1 AND role_id = $2
                    """,
                    uuid.UUID(test_user["id"]),
                    uuid.UUID(test_role["id"]),
                )
                assert row is None
    
    @pytest.mark.asyncio
    async def test_user_roles_appear_in_get_user(self, admin_headers, test_user, test_role, db_pool):
        """Test that assigned roles appear when getting a user."""
        # Add role to user
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                uuid.UUID(test_user["id"]),
                uuid.UUID(test_role["id"]),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{test_user['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["roles"]) >= 1
            role_ids = [r["id"] for r in data["roles"]]
            assert test_role["id"] in role_ids


# ============================================================================
# Session Management Tests
# ============================================================================


class TestSessionManagement:
    """Test session create, validate, delete operations."""
    
    @pytest.mark.asyncio
    async def test_create_session(self, admin_headers, test_user, db_pool):
        """Test creating a session."""
        session_token = secrets.token_urlsafe(32)
        expires_at = (datetime.now() + timedelta(hours=24)).isoformat()
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/sessions",
                headers=admin_headers,
                json={
                    "user_id": test_user["id"],
                    "token": session_token,
                    "expires_at": expires_at,
                    "ip_address": "192.168.1.100",
                    "user_agent": "Mozilla/5.0 Test",
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to create session: {resp.text}"
            data = resp.json()
            assert data["user_id"] == test_user["id"]
            assert data["token"] == session_token
            assert "session_id" in data
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_sessions WHERE token = $1",
                    session_token,
                )
                assert row is not None
                assert str(row["user_id"]) == test_user["id"]
    
    @pytest.mark.asyncio
    async def test_validate_session(self, admin_headers, test_user, db_pool):
        """Test validating a session."""
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)
        
        # Create session directly in DB
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_sessions (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(test_user["id"]),
                session_token,
                expires_at,
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/sessions/{session_token}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to validate session: {resp.text}"
            data = resp.json()
            assert data["user_id"] == test_user["id"]
            assert data["token"] == session_token
            assert "user" in data
    
    @pytest.mark.asyncio
    async def test_validate_expired_session(self, admin_headers, test_user, db_pool):
        """Test that expired sessions are not valid."""
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() - timedelta(hours=1)  # Expired
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_sessions (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(test_user["id"]),
                session_token,
                expires_at,
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/sessions/{session_token}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 404
    
    @pytest.mark.asyncio
    async def test_delete_session(self, admin_headers, test_user, db_pool):
        """Test deleting a session."""
        session_token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(hours=24)
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_sessions (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(test_user["id"]),
                session_token,
                expires_at,
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/auth/sessions/{session_token}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to delete session: {resp.text}"
            assert resp.json()["deleted"] is True
            
            # Verify deleted
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_sessions WHERE token = $1",
                    session_token,
                )
                assert row is None
    
    @pytest.mark.asyncio
    async def test_delete_all_user_sessions(self, admin_headers, test_user, db_pool):
        """Test deleting all sessions for a user."""
        # Create multiple sessions
        tokens = []
        async with db_pool.acquire() as conn:
            for i in range(3):
                token = secrets.token_urlsafe(32)
                tokens.append(token)
                await conn.execute(
                    """
                    INSERT INTO authz_sessions (user_id, token, expires_at)
                    VALUES ($1, $2, $3)
                    """,
                    uuid.UUID(test_user["id"]),
                    token,
                    datetime.now() + timedelta(hours=24),
                )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/auth/sessions/user/{test_user['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to delete user sessions: {resp.text}"
            assert resp.json()["deleted_count"] >= 3
            
            # Verify all deleted
            async with db_pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM authz_sessions WHERE user_id = $1",
                    uuid.UUID(test_user["id"]),
                )
                assert count == 0


# ============================================================================
# Magic Link Tests
# ============================================================================


class TestMagicLinks:
    """Test magic link create, validate, use operations."""
    
    @pytest.mark.asyncio
    async def test_create_magic_link(self, admin_headers, test_user, db_pool):
        """Test creating a magic link."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/magic-links",
                headers=admin_headers,
                json={
                    "user_id": test_user["id"],
                    "email": test_user["email"],
                    "expires_in_seconds": 900,
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to create magic link: {resp.text}"
            data = resp.json()
            assert "token" in data
            assert "magic_link_id" in data
            assert data["email"] == test_user["email"]
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_magic_links WHERE token = $1",
                    data["token"],
                )
                assert row is not None
                assert str(row["user_id"]) == test_user["id"]
    
    @pytest.mark.asyncio
    async def test_validate_magic_link(self, admin_headers, test_user, db_pool):
        """Test validating a magic link."""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=15)
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_magic_links (user_id, token, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uuid.UUID(test_user["id"]),
                token,
                test_user["email"],
                expires_at,
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/magic-links/{token}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to validate magic link: {resp.text}"
            data = resp.json()
            assert data["user_id"] == test_user["id"]
            assert data["email"] == test_user["email"]
    
    @pytest.mark.asyncio
    async def test_use_magic_link(self, admin_headers, db_pool, clean_test_data):
        """Test using (consuming) a magic link to create a session."""
        # Create a PENDING user
        user_id = uuid.uuid4()
        email = f"magic_{user_id}@test.example.com"
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO authz_users (user_id, email, status) VALUES ($1, $2, $3)",
                user_id,
                email,
                "PENDING",
            )
            
            # Create magic link
            token = secrets.token_urlsafe(32)
            await conn.execute(
                """
                INSERT INTO authz_magic_links (user_id, token, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                user_id,
                token,
                email,
                datetime.now() + timedelta(minutes=15),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/magic-links/{token}/use",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to use magic link: {resp.text}"
            data = resp.json()
            
            # User should be activated
            assert data["user"]["status"] == "ACTIVE"
            assert data["user"]["email_verified_at"] is not None
            
            # Session should be created
            assert "session" in data
            assert "token" in data["session"]
            
            # Verify magic link marked as used
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT used_at FROM authz_magic_links WHERE token = $1",
                    token,
                )
                assert row["used_at"] is not None
    
    @pytest.mark.asyncio
    async def test_cannot_reuse_magic_link(self, admin_headers, test_user, db_pool):
        """Test that a used magic link cannot be used again."""
        token = secrets.token_urlsafe(32)
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_magic_links (user_id, token, email, expires_at, used_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                uuid.UUID(test_user["id"]),
                token,
                test_user["email"],
                datetime.now() + timedelta(minutes=15),
                datetime.now(),  # Already used
            )
        
        async with httpx.AsyncClient() as client:
            # Validate should fail
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/magic-links/{token}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 410  # Gone


# ============================================================================
# TOTP Tests
# ============================================================================


class TestTOTP:
    """Test TOTP code create and verify operations."""
    
    @pytest.mark.asyncio
    async def test_create_totp_code(self, admin_headers, test_user, db_pool):
        """Test creating a TOTP code."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/totp",
                headers=admin_headers,
                json={
                    "user_id": test_user["id"],
                    "email": test_user["email"],
                    "expires_in_seconds": 300,
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to create TOTP code: {resp.text}"
            data = resp.json()
            assert "code" in data
            assert len(data["code"]) == 6  # 6-digit code
            assert "expires_at" in data
            
            # Verify in database (hash, not plaintext)
            code_hash = hashlib.sha256(data["code"].encode()).hexdigest()
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_totp_codes WHERE code_hash = $1",
                    code_hash,
                )
                assert row is not None
    
    @pytest.mark.asyncio
    async def test_verify_totp_code(self, admin_headers, test_user, db_pool):
        """Test verifying a TOTP code creates a session."""
        # Create a TOTP code directly
        code = str(secrets.randbelow(1000000)).zfill(6)
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uuid.UUID(test_user["id"]),
                code_hash,
                test_user["email"],
                datetime.now() + timedelta(minutes=5),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/totp/verify",
                headers=admin_headers,
                json={
                    "email": test_user["email"],
                    "code": code,
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to verify TOTP: {resp.text}"
            data = resp.json()
            
            # User info returned
            assert data["user"]["user_id"] == test_user["id"]
            
            # Session created
            assert "session" in data
            assert "token" in data["session"]
    
    @pytest.mark.asyncio
    async def test_invalid_totp_code_rejected(self, admin_headers, test_user, db_pool):
        """Test that an invalid TOTP code is rejected."""
        # Create a valid code
        valid_code = str(secrets.randbelow(1000000)).zfill(6)
        code_hash = hashlib.sha256(valid_code.encode()).hexdigest()
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uuid.UUID(test_user["id"]),
                code_hash,
                test_user["email"],
                datetime.now() + timedelta(minutes=5),
            )
        
        async with httpx.AsyncClient() as client:
            # Try wrong code
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/totp/verify",
                headers=admin_headers,
                json={
                    "email": test_user["email"],
                    "code": "000000",  # Wrong code
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 401


# ============================================================================
# Passkey Tests
# ============================================================================


class TestPasskeys:
    """Test passkey (WebAuthn) operations."""
    
    @pytest.mark.asyncio
    async def test_create_passkey_challenge(self, admin_headers, test_user):
        """Test creating a passkey challenge."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/passkeys/challenge",
                headers=admin_headers,
                json={
                    "type": "registration",
                    "user_id": test_user["id"],
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to create challenge: {resp.text}"
            data = resp.json()
            assert "challenge" in data
            assert "expires_at" in data
    
    @pytest.mark.asyncio
    async def test_register_passkey(self, admin_headers, test_user, db_pool):
        """Test registering a passkey."""
        credential_id = secrets.token_urlsafe(32)
        credential_public_key = secrets.token_urlsafe(64)
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/passkeys",
                headers=admin_headers,
                json={
                    "user_id": test_user["id"],
                    "credential_id": credential_id,
                    "credential_public_key": credential_public_key,
                    "counter": 0,
                    "device_type": "singleDevice",
                    "backed_up": False,
                    "transports": ["internal"],
                    "name": "Test Passkey",
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to register passkey: {resp.text}"
            data = resp.json()
            assert "passkey_id" in data
            assert data["name"] == "Test Passkey"
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_passkeys WHERE credential_id = $1",
                    credential_id,
                )
                assert row is not None
    
    @pytest.mark.asyncio
    async def test_list_user_passkeys(self, admin_headers, test_user, db_pool):
        """Test listing passkeys for a user."""
        # Create passkeys directly
        for i in range(2):
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO authz_passkeys 
                        (user_id, credential_id, credential_public_key, counter, 
                         device_type, backed_up, transports, name)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    uuid.UUID(test_user["id"]),
                    secrets.token_urlsafe(32),
                    secrets.token_urlsafe(64),
                    0,
                    "singleDevice",
                    False,
                    ["internal"],
                    f"Test Passkey {i}",
                )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/passkeys/user/{test_user['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to list passkeys: {resp.text}"
            data = resp.json()
            assert "passkeys" in data
            assert len(data["passkeys"]) >= 2
    
    @pytest.mark.asyncio
    async def test_delete_passkey(self, admin_headers, test_user, db_pool):
        """Test deleting a passkey."""
        passkey_id = uuid.uuid4()
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_passkeys 
                    (id, user_id, credential_id, credential_public_key, counter, 
                     device_type, backed_up, transports, name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                passkey_id,
                uuid.UUID(test_user["id"]),
                secrets.token_urlsafe(32),
                secrets.token_urlsafe(64),
                0,
                "singleDevice",
                False,
                ["internal"],
                "Passkey to Delete",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/auth/passkeys/{passkey_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to delete passkey: {resp.text}"
            
            # Verify deleted
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_passkeys WHERE id = $1",
                    passkey_id,
                )
                assert row is None
    
    @pytest.mark.asyncio
    async def test_authenticate_with_passkey(self, admin_headers, test_user, db_pool):
        """Test authenticating with a passkey."""
        credential_id = secrets.token_urlsafe(32)
        
        # Create passkey with counter 0
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_passkeys 
                    (user_id, credential_id, credential_public_key, counter, 
                     device_type, backed_up, transports, name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                uuid.UUID(test_user["id"]),
                credential_id,
                secrets.token_urlsafe(64),
                0,
                "singleDevice",
                False,
                ["internal"],
                "Auth Test Passkey",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/passkeys/authenticate",
                headers=admin_headers,
                json={
                    "credential_id": credential_id,
                    "new_counter": 1,  # Must be > stored counter
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to authenticate: {resp.text}"
            data = resp.json()
            
            assert data["user"]["user_id"] == test_user["id"]
            assert "session" in data
            assert "token" in data["session"]
            
            # Verify counter updated
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT counter FROM authz_passkeys WHERE credential_id = $1",
                    credential_id,
                )
                assert row["counter"] == 1
    
    @pytest.mark.asyncio
    async def test_passkey_counter_replay_rejected(self, admin_headers, test_user, db_pool):
        """Test that counter replay is rejected."""
        credential_id = secrets.token_urlsafe(32)
        
        # Create passkey with counter 5
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_passkeys 
                    (user_id, credential_id, credential_public_key, counter, 
                     device_type, backed_up, transports, name)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                uuid.UUID(test_user["id"]),
                credential_id,
                secrets.token_urlsafe(64),
                5,
                "singleDevice",
                False,
                ["internal"],
                "Replay Test Passkey",
            )
        
        async with httpx.AsyncClient() as client:
            # Try with counter <= stored (replay attack)
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/passkeys/authenticate",
                headers=admin_headers,
                json={
                    "credential_id": credential_id,
                    "new_counter": 3,  # Less than stored counter
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 401


# ============================================================================
# Audit Log Tests
# ============================================================================


class TestAuditLogs:
    """Test audit log operations."""
    
    @pytest.mark.asyncio
    async def test_create_audit_log(self, admin_headers, test_user, db_pool):
        """Test creating an audit log entry."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/audit/log",
                headers=admin_headers,
                json={
                    "actor_id": test_user["id"],
                    "action": "TEST_ACTION",
                    "resource_type": "test_resource",
                    "resource_id": str(uuid.uuid4()),
                    "event_type": "test",
                    "success": True,
                    "details": {"test_key": "test_value"},
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to create audit log: {resp.text}"
            data = resp.json()
            assert data["status"] == "ok"
            assert "audit_log_id" in data
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM audit_logs WHERE id = $1",
                    uuid.UUID(data["audit_log_id"]),
                )
                assert row is not None
                assert row["action"] == "TEST_ACTION"
    
    @pytest.mark.asyncio
    async def test_list_audit_logs(self, admin_headers, test_user, db_pool):
        """Test listing audit logs."""
        # Create some audit entries
        async with db_pool.acquire() as conn:
            for i in range(5):
                await conn.execute(
                    """
                    INSERT INTO audit_logs 
                        (actor_id, action, resource_type, event_type, success, details)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    """,
                    uuid.UUID(test_user["id"]),
                    f"LIST_TEST_{i}",
                    "test_resource",
                    "test",
                    True,
                    "{}",
                )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/audit/logs",
                headers=admin_headers,
                params={"limit": 10},
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to list audit logs: {resp.text}"
            data = resp.json()
            assert "logs" in data
            assert "pagination" in data
            assert len(data["logs"]) >= 5
    
    @pytest.mark.asyncio
    async def test_list_audit_logs_with_filter(self, admin_headers, test_user, db_pool):
        """Test listing audit logs with filters."""
        # Create specific audit entries
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_logs 
                    (actor_id, action, resource_type, event_type, success, details)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.UUID(test_user["id"]),
                "FILTERED_ACTION",
                "filtered_resource",
                "filtered_event",
                True,
                "{}",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/audit/logs",
                headers=admin_headers,
                params={
                    "actor_id": test_user["id"],
                    "event_type": "filtered_event",
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            
            # All returned logs should match filter
            for log in data["logs"]:
                assert log["actor_id"] == test_user["id"]
                assert log["event_type"] == "filtered_event"
    
    @pytest.mark.asyncio
    async def test_get_user_audit_trail(self, admin_headers, test_user, db_pool):
        """Test getting audit trail for a specific user."""
        # Create audit entries for this user
        async with db_pool.acquire() as conn:
            # As actor
            await conn.execute(
                """
                INSERT INTO audit_logs 
                    (actor_id, action, resource_type, success, details)
                VALUES ($1, $2, $3, $4, $5)
                """,
                uuid.UUID(test_user["id"]),
                "USER_ACTOR_ACTION",
                "test",
                True,
                "{}",
            )
            # As target
            await conn.execute(
                """
                INSERT INTO audit_logs 
                    (actor_id, action, resource_type, target_user_id, success, details)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                uuid.uuid4(),  # Different actor
                "USER_TARGET_ACTION",
                "test",
                uuid.UUID(test_user["id"]),  # This user is target
                True,
                "{}",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/audit/logs/user/{test_user['id']}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to get user audit trail: {resp.text}"
            data = resp.json()
            assert data["user_id"] == test_user["id"]
            assert len(data["logs"]) >= 2


# ============================================================================
# Email Domain Tests
# ============================================================================


class TestEmailDomains:
    """Test email domain configuration."""
    
    @pytest.mark.asyncio
    async def test_add_email_domain(self, admin_headers, db_pool, clean_test_data):
        """Test adding an email domain rule."""
        domain = f"test.{uuid.uuid4().hex[:8]}.com"
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/email-domains",
                headers=admin_headers,
                json={
                    "domain": domain,
                    "is_allowed": True,
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to add domain: {resp.text}"
            data = resp.json()
            assert data["domain"] == domain.lower()
            assert data["is_allowed"] is True
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_email_domain_config WHERE domain = $1",
                    domain.lower(),
                )
                assert row is not None
    
    @pytest.mark.asyncio
    async def test_list_email_domains(self, admin_headers, db_pool, clean_test_data):
        """Test listing email domain rules."""
        # Add some domains
        domains = []
        async with db_pool.acquire() as conn:
            for i in range(3):
                domain = f"test.list{i}.example.com"
                domains.append(domain)
                await conn.execute(
                    """
                    INSERT INTO authz_email_domain_config (domain, is_allowed)
                    VALUES ($1, $2)
                    """,
                    domain,
                    True,
                )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/email-domains",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to list domains: {resp.text}"
            data = resp.json()
            assert "domains" in data
            
            listed_domains = [d["domain"] for d in data["domains"]]
            for domain in domains:
                assert domain in listed_domains
    
    @pytest.mark.asyncio
    async def test_remove_email_domain(self, admin_headers, db_pool, clean_test_data):
        """Test removing an email domain rule."""
        domain = "test.remove.example.com"
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_email_domain_config (domain, is_allowed)
                VALUES ($1, $2)
                """,
                domain,
                True,
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/email-domains/{domain}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to remove domain: {resp.text}"
            assert resp.json()["deleted"] is True
            
            # Verify removed
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_email_domain_config WHERE domain = $1",
                    domain,
                )
                assert row is None


# ============================================================================
# Cleanup Tests
# ============================================================================


class TestCleanup:
    """Test cleanup operations."""
    
    @pytest.mark.asyncio
    async def test_cleanup_expired_items(self, admin_headers, test_user, db_pool):
        """Test cleanup of expired sessions, magic links, etc."""
        # Create expired items
        async with db_pool.acquire() as conn:
            # Expired session
            await conn.execute(
                """
                INSERT INTO authz_sessions (user_id, token, expires_at)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(test_user["id"]),
                f"expired_session_{uuid.uuid4()}",
                datetime.now() - timedelta(hours=1),
            )
            
            # Expired magic link
            await conn.execute(
                """
                INSERT INTO authz_magic_links (user_id, token, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uuid.UUID(test_user["id"]),
                f"expired_magic_{uuid.uuid4()}",
                test_user["email"],
                datetime.now() - timedelta(hours=1),
            )
            
            # Expired TOTP code
            await conn.execute(
                """
                INSERT INTO authz_totp_codes (user_id, code_hash, email, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                uuid.UUID(test_user["id"]),
                hashlib.sha256(b"123456").hexdigest(),
                test_user["email"],
                datetime.now() - timedelta(hours=1),
            )
            
            # Expired passkey challenge
            await conn.execute(
                """
                INSERT INTO authz_passkey_challenges (challenge, type, expires_at)
                VALUES ($1, $2, $3)
                """,
                f"expired_challenge_{uuid.uuid4()}",
                "registration",
                datetime.now() - timedelta(hours=1),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/cleanup",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Failed to cleanup: {resp.text}"
            data = resp.json()
            assert data["status"] == "ok"
            assert "cleaned" in data
            
            # At least some items should have been cleaned
            total_cleaned = (
                data["cleaned"]["sessions"] +
                data["cleaned"]["magic_links"] +
                data["cleaned"]["totp_codes"] +
                data["cleaned"]["passkey_challenges"]
            )
            assert total_cleaned >= 4


# ============================================================================
# Authentication Tests
# ============================================================================


class TestAuthentication:
    """Test authentication requirements."""
    
    @pytest.mark.asyncio
    async def test_unauthorized_without_token(self):
        """Test that endpoints require authentication."""
        async with httpx.AsyncClient() as client:
            # No auth header
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users",
                timeout=30.0,
            )
            
            assert resp.status_code == 401
    
    @pytest.mark.asyncio
    async def test_unauthorized_with_invalid_token(self):
        """Test that invalid tokens are rejected."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers={"Authorization": "Bearer invalid-token-12345"},
                timeout=30.0,
            )
            
            assert resp.status_code == 401
    
    @pytest.mark.asyncio
    async def test_authorized_with_valid_token(self, admin_headers):
        """Test that valid admin token works."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200


# ============================================================================
# Integration Flow Tests
# ============================================================================


class TestIntegrationFlows:
    """Test complete integration flows."""
    
    @pytest.mark.asyncio
    async def test_full_user_lifecycle(self, admin_headers, db_pool, clean_test_data):
        """Test complete user lifecycle: create -> activate -> add role -> deactivate -> delete."""
        email = f"lifecycle_{uuid.uuid4()}@test.example.com"
        
        async with httpx.AsyncClient() as client:
            # 1. Create user (PENDING)
            create_resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                json={"email": email, "status": "PENDING"},
                timeout=30.0,
            )
            assert create_resp.status_code == 201
            user = create_resp.json()
            user_id = user["user_id"]
            assert user["status"] == "PENDING"
            
            # 2. Create a role for the user
            role_id = str(uuid.uuid4())
            role_name = f"TestRole_{role_id}"
            async with db_pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO authz_roles (id, name, description)
                    VALUES ($1, $2, $3)
                    """,
                    uuid.UUID(role_id),
                    role_name,
                    "Test role",
                )
            
            # 3. Activate user
            activate_resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/activate",
                headers=admin_headers,
                timeout=30.0,
            )
            assert activate_resp.status_code == 200
            assert activate_resp.json()["status"] == "ACTIVE"
            
            # 4. Add role
            add_role_resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/roles/{role_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert add_role_resp.status_code == 200
            
            # 5. Verify user has role
            get_resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert get_resp.status_code == 200
            user_data = get_resp.json()
            assert len(user_data["roles"]) == 1
            assert user_data["roles"][0]["id"] == role_id
            
            # 6. Deactivate user
            deactivate_resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/deactivate",
                headers=admin_headers,
                timeout=30.0,
            )
            assert deactivate_resp.status_code == 200
            assert deactivate_resp.json()["status"] == "DEACTIVATED"
            
            # 7. Delete user
            delete_resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert delete_resp.status_code == 200
            
            # Verify user is gone
            get_deleted_resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert get_deleted_resp.status_code == 404
    
    @pytest.mark.asyncio
    async def test_magic_link_login_flow(self, admin_headers, db_pool, clean_test_data):
        """Test complete magic link login flow."""
        email = f"magic_flow_{uuid.uuid4()}@test.example.com"
        
        async with httpx.AsyncClient() as client:
            # 1. Create pending user
            create_resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/users",
                headers=admin_headers,
                json={"email": email, "status": "PENDING"},
                timeout=30.0,
            )
            assert create_resp.status_code == 201
            user_id = create_resp.json()["user_id"]
            
            # 2. Create magic link
            magic_resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/magic-links",
                headers=admin_headers,
                json={
                    "user_id": user_id,
                    "email": email,
                },
                timeout=30.0,
            )
            assert magic_resp.status_code == 200
            magic_token = magic_resp.json()["token"]
            
            # 3. Use magic link
            use_resp = await client.post(
                f"{TEST_AUTHZ_URL}/auth/magic-links/{magic_token}/use",
                headers=admin_headers,
                timeout=30.0,
            )
            assert use_resp.status_code == 200
            result = use_resp.json()
            
            # User should now be ACTIVE and email verified
            assert result["user"]["status"] == "ACTIVE"
            assert result["user"]["email_verified_at"] is not None
            
            # Session should be created
            session_token = result["session"]["token"]
            
            # 4. Validate the session
            validate_resp = await client.get(
                f"{TEST_AUTHZ_URL}/auth/sessions/{session_token}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert validate_resp.status_code == 200
            assert validate_resp.json()["user_id"] == user_id


# ============================================================================
# OAuth Token Exchange Tests (Real Database)
# ============================================================================


class TestOAuthTokenExchange:
    """Test OAuth2 token exchange flow with real database."""
    
    @pytest.mark.asyncio
    async def test_token_exchange_with_user_and_roles(self, admin_headers, db_pool, clean_test_data):
        """
        Test complete OAuth2 token exchange flow:
        1. Create user and role with scopes in real database
        2. Sync user via internal API
        3. Exchange for access token
        4. Verify token contains correct scopes and roles
        """
        import jwt
        import json
        
        user_id = uuid.uuid4()
        role_id = uuid.uuid4()
        email = f"token_test_{user_id}@test.example.com"
        role_name = f"TestRole_{role_id}"
        
        # Create role with scopes directly in database
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, description, scopes)
                VALUES ($1, $2, $3, $4)
                """,
                role_id,
                role_name,
                "Test role for token exchange",
                ["ingest.read", "ingest.write", "search.read"],
            )
            
            # Create user
            await conn.execute(
                """
                INSERT INTO authz_users (user_id, email, status)
                VALUES ($1, $2, $3)
                """,
                user_id,
                email,
                "ACTIVE",
            )
            
            # Assign role to user
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                """,
                user_id,
                role_id,
            )
        
        async with httpx.AsyncClient() as client:
            # Get JWKS for token verification
            jwks_resp = await client.get(
                f"{TEST_AUTHZ_URL}/.well-known/jwks.json",
                timeout=30.0,
            )
            assert jwks_resp.status_code == 200
            jwks = jwks_resp.json()
            assert len(jwks["keys"]) >= 1
            jwk = jwks["keys"][0]
            
            # Exchange for access token
            skip_if_no_oauth_credentials()
            exchange_resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": BOOTSTRAP_CLIENT_ID,
                    "client_secret": BOOTSTRAP_CLIENT_SECRET,
                    "audience": "ingest-api",
                    "requested_subject": str(user_id),
                    "requested_purpose": "integration-test",
                },
                timeout=30.0,
            )
            
            assert exchange_resp.status_code == 200, f"Token exchange failed: {exchange_resp.text}"
            token_data = exchange_resp.json()
            assert "access_token" in token_data
            assert token_data["token_type"] == "Bearer"
            assert token_data["expires_in"] > 0
            
            # Decode and verify token
            access_token = token_data["access_token"]
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            decoded = jwt.decode(
                access_token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},  # Audience may vary by environment
            )
            
            # Verify token claims
            assert decoded["sub"] == str(user_id)
            assert decoded["typ"] == "access"
            
            # Verify scopes are aggregated from role
            assert "ingest.read" in decoded["scope"]
            assert "ingest.write" in decoded["scope"]
            assert "search.read" in decoded["scope"]
            
            # Verify role is present
            assert len(decoded["roles"]) == 1
            assert decoded["roles"][0]["id"] == str(role_id)
            assert decoded["roles"][0]["name"] == role_name
    
    @pytest.mark.asyncio
    async def test_token_exchange_with_multiple_roles(self, admin_headers, db_pool, clean_test_data):
        """Test token exchange aggregates scopes from multiple roles."""
        import jwt
        import json
        
        user_id = uuid.uuid4()
        role1_id = uuid.uuid4()
        role2_id = uuid.uuid4()
        email = f"multi_role_{user_id}@test.example.com"
        
        # Create roles with different scopes
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, description, scopes)
                VALUES ($1, $2, $3, $4)
                """,
                role1_id,
                f"Engineering_{role1_id}",
                "Engineering role",
                ["ingest.read", "search.read"],
            )
            
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, description, scopes)
                VALUES ($1, $2, $3, $4)
                """,
                role2_id,
                f"Finance_{role2_id}",
                "Finance role",
                ["search.read", "search.write"],
            )
            
            # Create user
            await conn.execute(
                """
                INSERT INTO authz_users (user_id, email, status)
                VALUES ($1, $2, $3)
                """,
                user_id,
                email,
                "ACTIVE",
            )
            
            # Assign both roles
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2), ($1, $3)
                """,
                user_id,
                role1_id,
                role2_id,
            )
        
        async with httpx.AsyncClient() as client:
            # Get JWKS
            jwks_resp = await client.get(
                f"{TEST_AUTHZ_URL}/.well-known/jwks.json",
                timeout=30.0,
            )
            jwks = jwks_resp.json()
            jwk = jwks["keys"][0]
            
            # Exchange for token
            exchange_resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                headers=admin_headers,
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": BOOTSTRAP_CLIENT_ID,
                    "client_secret": BOOTSTRAP_CLIENT_SECRET,
                    "audience": "search-api",
                    "requested_subject": str(user_id),
                },
                timeout=30.0,
            )
            
            assert exchange_resp.status_code == 200, f"Token exchange failed: {exchange_resp.text}"
            token_data = exchange_resp.json()
            
            # Decode token
            access_token = token_data["access_token"]
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            decoded = jwt.decode(
                access_token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            
            # Verify both roles present
            assert len(decoded["roles"]) == 2
            role_ids = [r["id"] for r in decoded["roles"]]
            assert str(role1_id) in role_ids
            assert str(role2_id) in role_ids
            
            # Verify scopes are aggregated (union of both roles)
            assert "ingest.read" in decoded["scope"]
            assert "search.read" in decoded["scope"]
            assert "search.write" in decoded["scope"]
    
    @pytest.mark.asyncio
    async def test_token_exchange_fails_for_unknown_user(self, admin_headers):
        """Test token exchange fails for user not in database."""
        fake_user_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            exchange_resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                headers=admin_headers,
                json={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": BOOTSTRAP_CLIENT_ID,
                    "client_secret": BOOTSTRAP_CLIENT_SECRET,
                    "audience": "ingest-api",
                    "requested_subject": fake_user_id,
                },
                timeout=30.0,
            )
            
            # Should fail - user doesn't exist
            assert exchange_resp.status_code == 400
            assert "unknown_subject" in exchange_resp.json().get("detail", "")
    
    @pytest.mark.asyncio
    async def test_client_credentials_flow(self, admin_headers):
        """Test OAuth2 client_credentials grant (service-to-service)."""
        import jwt
        import json
        
        skip_if_no_oauth_credentials()
        
        async with httpx.AsyncClient() as client:
            # Get JWKS
            jwks_resp = await client.get(
                f"{TEST_AUTHZ_URL}/.well-known/jwks.json",
                timeout=30.0,
            )
            jwks = jwks_resp.json()
            jwk = jwks["keys"][0]
            
            # Request token with client_credentials (no scope - client_credentials
            # only gets scopes from client's allowed_scopes which is empty by default)
            token_resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": BOOTSTRAP_CLIENT_ID,
                    "client_secret": BOOTSTRAP_CLIENT_SECRET,
                    "audience": "agent-api",
                },
                timeout=30.0,
            )
            
            assert token_resp.status_code == 200, f"Client credentials failed: {token_resp.text}"
            token_data = token_resp.json()
            assert "access_token" in token_data
            
            # Decode token
            access_token = token_data["access_token"]
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            decoded = jwt.decode(
                access_token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            
            # For client_credentials, sub is the client_id
            assert decoded["sub"] == BOOTSTRAP_CLIENT_ID
            assert decoded["roles"] == []  # No user roles for service tokens


class TestJWKSEndpoint:
    """Tests for JWKS and bootstrap functionality."""
    
    @pytest.mark.asyncio
    async def test_jwks_endpoint_returns_public_key(self):
        """Test that JWKS endpoint returns valid public keys."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/.well-known/jwks.json",
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert "keys" in data
            assert len(data["keys"]) >= 1
            
            # Verify JWK structure
            jwk = data["keys"][0]
            assert jwk["kty"] == "RSA"
            assert "kid" in jwk
            assert "n" in jwk  # modulus
            assert "e" in jwk  # exponent
            assert jwk["use"] == "sig"
    
    @pytest.mark.asyncio
    async def test_jwks_can_verify_tokens(self, admin_headers):
        """Test that JWKS keys can verify issued tokens."""
        import jwt
        import json
        
        skip_if_no_oauth_credentials()
        
        async with httpx.AsyncClient() as client:
            # Get JWKS
            jwks_resp = await client.get(
                f"{TEST_AUTHZ_URL}/.well-known/jwks.json",
                timeout=30.0,
            )
            jwks = jwks_resp.json()
            jwk = jwks["keys"][0]
            
            # Get a token (no scope - use allowed audience)
            token_resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": BOOTSTRAP_CLIENT_ID,
                    "client_secret": BOOTSTRAP_CLIENT_SECRET,
                    "audience": "agent-api",
                },
                timeout=30.0,
            )
            
            assert token_resp.status_code == 200, f"Token request failed: {token_resp.text}"
            access_token = token_resp.json()["access_token"]
            
            # Verify token using JWKS
            public_key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            decoded = jwt.decode(
                access_token,
                public_key,
                algorithms=["RS256"],
                options={"verify_aud": False},
            )
            
            assert decoded["sub"] == BOOTSTRAP_CLIENT_ID
            assert "exp" in decoded
            assert "iat" in decoded
            assert "jti" in decoded


class TestAdminRoleEndpoints:
    """Tests for admin role management endpoints (using real database)."""
    
    @pytest.mark.asyncio
    async def test_create_role(self, admin_headers, db_pool, clean_test_data):
        """Test creating a role via admin endpoint."""
        role_name = f"test-role-{uuid.uuid4().hex[:8]}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/roles",
                headers=admin_headers,
                json={
                    "name": role_name,
                    "description": "Test role description",
                    "scopes": ["read.documents", "write.documents"],
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Create role failed: {resp.text}"
            data = resp.json()
            assert data["name"] == role_name
            assert data["description"] == "Test role description"
            assert "id" in data
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_roles WHERE id = $1",
                    uuid.UUID(data["id"]),
                )
                assert row is not None
                assert row["name"] == role_name
    
    @pytest.mark.asyncio
    async def test_list_roles(self, admin_headers, test_role):
        """Test listing all roles."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/roles",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            
            # Our test role should be in the list
            role_ids = [r["id"] for r in data]
            assert test_role["id"] in role_ids
    
    @pytest.mark.asyncio
    async def test_get_role(self, admin_headers, test_role, db_pool):
        """Test getting a specific role by ID."""
        role_id = test_role["id"]
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/roles/{role_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert data["id"] == role_id
            assert data["name"] == test_role["name"]
    
    @pytest.mark.asyncio
    async def test_update_role(self, admin_headers, db_pool, clean_test_data):
        """Test updating a role."""
        # Create a role first
        role_id = str(uuid.uuid4())
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, description)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(role_id),
                f"TestRole_original_{role_id[:8]}",
                "Original description",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{TEST_AUTHZ_URL}/admin/roles/{role_id}",
                headers=admin_headers,
                json={
                    "name": f"TestRole_updated_{role_id[:8]}",
                    "description": "Updated description",
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Update role failed: {resp.text}"
            data = resp.json()
            assert data["name"] == f"TestRole_updated_{role_id[:8]}"
            assert data["description"] == "Updated description"
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT name, description FROM authz_roles WHERE id = $1",
                    uuid.UUID(role_id),
                )
                assert row["name"] == f"TestRole_updated_{role_id[:8]}"
                assert row["description"] == "Updated description"
    
    @pytest.mark.asyncio
    async def test_delete_role(self, admin_headers, db_pool, clean_test_data):
        """Test deleting a role."""
        # Create a role first
        role_id = str(uuid.uuid4())
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, description)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(role_id),
                "role-to-delete",
                "Will be deleted",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/roles/{role_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True
            
            # Verify deleted in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_roles WHERE id = $1",
                    uuid.UUID(role_id),
                )
                assert row is None
    
    @pytest.mark.asyncio
    async def test_create_role_requires_auth(self):
        """Test that creating a role requires authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/roles",
                json={"name": "unauthorized-role"},
                timeout=30.0,
            )
            
            assert resp.status_code == 401


class TestUserRoleAdminEndpoints:
    """Tests for user-role binding admin endpoints."""
    
    @pytest.mark.asyncio
    async def test_add_user_role_via_admin_endpoint(self, admin_headers, test_user, test_role, db_pool):
        """Test adding a role to a user via admin endpoint."""
        user_id = test_user["id"]
        role_id = test_role["id"]
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/user-roles",
                headers=admin_headers,
                json={
                    "user_id": user_id,
                    "role_id": role_id,
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Add user role failed: {resp.text}"
            data = resp.json()
            assert data["user_id"] == user_id
            assert data["role_id"] == role_id
            
            # Verify in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                    uuid.UUID(user_id),
                    uuid.UUID(role_id),
                )
                assert row is not None
    
    @pytest.mark.asyncio
    async def test_remove_user_role_via_admin_endpoint(self, admin_headers, test_user, test_role, db_pool):
        """Test removing a role from a user via admin endpoint."""
        user_id = test_user["id"]
        role_id = test_role["id"]
        
        # First add the role
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
        
        import json as json_lib
        async with httpx.AsyncClient() as client:
            resp = await client.request(
                "DELETE",
                f"{TEST_AUTHZ_URL}/admin/user-roles",
                headers={**admin_headers, "Content-Type": "application/json"},
                content=json_lib.dumps({
                    "user_id": user_id,
                    "role_id": role_id,
                }),
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            assert resp.json()["deleted"] is True
            
            # Verify removed in database
            async with db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT * FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                    uuid.UUID(user_id),
                    uuid.UUID(role_id),
                )
                assert row is None
    
    @pytest.mark.asyncio
    async def test_get_user_roles_via_admin_endpoint(self, admin_headers, test_user, test_role, db_pool):
        """Test getting user roles via admin endpoint."""
        user_id = test_user["id"]
        role_id = test_role["id"]
        
        # Add role to user
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/users/{user_id}/roles",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            
            # Our test role should be in the list
            role_ids = [r["id"] for r in data]
            assert role_id in role_ids


class TestOAuthFormEncoding:
    """Tests for OAuth2 form-encoded request support."""
    
    @pytest.mark.asyncio
    async def test_token_endpoint_with_form_encoding(self, admin_headers):
        """Test that token endpoint accepts application/x-www-form-urlencoded."""
        skip_if_no_oauth_credentials()
        
        async with httpx.AsyncClient() as client:
            # Test form-encoded request (standard OAuth2 format)
            # Note: no scope - client_credentials only gets client's allowed_scopes
            form_data = {
                "grant_type": "client_credentials",
                "client_id": BOOTSTRAP_CLIENT_ID,
                "client_secret": BOOTSTRAP_CLIENT_SECRET,
                "audience": "ingest-api",
            }
            
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                data=form_data,  # Sends as application/x-www-form-urlencoded
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Form-encoded request failed: {resp.text}"
            token_data = resp.json()
            assert "access_token" in token_data
            assert token_data["token_type"] == "Bearer"
    
    @pytest.mark.asyncio
    async def test_token_endpoint_with_json_body(self, admin_headers):
        """Test that token endpoint accepts JSON (our extension)."""
        skip_if_no_oauth_credentials()
        
        async with httpx.AsyncClient() as client:
            json_data = {
                "grant_type": "client_credentials",
                "client_id": BOOTSTRAP_CLIENT_ID,
                "client_secret": BOOTSTRAP_CLIENT_SECRET,
                "audience": "search-api",
            }
            
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                json=json_data,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"JSON request failed: {resp.text}"
            token_data = resp.json()
            assert "access_token" in token_data
            assert token_data["token_type"] == "Bearer"
    
    @pytest.mark.asyncio
    async def test_token_exchange_with_form_encoding(self, admin_headers, db_pool, clean_test_data):
        """Test token exchange with form-encoded request."""
        skip_if_no_oauth_credentials()
        
        # Create a user with roles first
        user_id = str(uuid.uuid4())
        role_id = str(uuid.uuid4())
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_users (user_id, email, status)
                VALUES ($1, $2, 'ACTIVE')
                """,
                uuid.UUID(user_id),
                f"form-test-{user_id[:8]}@test.example.com",
            )
            await conn.execute(
                """
                INSERT INTO authz_roles (id, name, scopes)
                VALUES ($1, $2, $3)
                """,
                uuid.UUID(role_id),
                f"TestRole_form-{role_id[:8]}",
                ["search.read", "ingest.read"],
            )
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
        
        async with httpx.AsyncClient() as client:
            # Test token exchange with form encoding
            # Note: scope is taken from user's roles, not specified in request
            form_data = {
                "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                "client_id": BOOTSTRAP_CLIENT_ID,
                "client_secret": BOOTSTRAP_CLIENT_SECRET,
                "audience": "agent-api",
                "requested_subject": user_id,
            }
            
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/oauth/token",
                data=form_data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30.0,
            )
            
            assert resp.status_code == 200, f"Form-encoded exchange failed: {resp.text}"
            token_data = resp.json()
            assert "access_token" in token_data


# ============================================================================
# Role Bindings Tests
# ============================================================================


class TestRoleBindingsCRUD:
    """Test role-resource binding create, read, update, delete operations."""
    
    @pytest.mark.asyncio
    async def test_create_binding(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test creating a role-resource binding."""
        role_id = test_role["id"]
        resource_type = "app"
        resource_id = str(uuid.uuid4())
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{TEST_AUTHZ_URL}/admin/bindings",
                headers=admin_headers,
                json={
                    "role_id": role_id,
                    "resource_type": resource_type,
                    "resource_id": resource_id,
                    "permissions": {"read": True, "write": False},
                },
                timeout=30.0,
            )
            
            assert resp.status_code == 201, f"Failed to create binding: {resp.text}"
            data = resp.json()
            assert data["role_id"] == role_id
            assert data["resource_type"] == resource_type
            assert data["resource_id"] == resource_id
            assert data["permissions"] == {"read": True, "write": False}
            assert "id" in data
            
            # Clean up
            binding_id = data["id"]
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_role_bindings WHERE id = $1",
                    uuid.UUID(binding_id)
                )
    
    @pytest.mark.asyncio
    async def test_create_binding_duplicate(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test that creating a duplicate binding returns 409."""
        role_id = test_role["id"]
        resource_type = "app"
        resource_id = str(uuid.uuid4())
        
        # Create first binding directly in DB
        binding_id = uuid.uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                VALUES ($1, $2, $3, $4)
                """,
                binding_id,
                uuid.UUID(role_id),
                resource_type,
                resource_id,
            )
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{TEST_AUTHZ_URL}/admin/bindings",
                    headers=admin_headers,
                    json={
                        "role_id": role_id,
                        "resource_type": resource_type,
                        "resource_id": resource_id,
                    },
                    timeout=30.0,
                )
                
                assert resp.status_code == 409, f"Expected conflict: {resp.text}"
        finally:
            # Clean up
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_role_bindings WHERE id = $1",
                    binding_id
                )
    
    @pytest.mark.asyncio
    async def test_list_bindings(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test listing bindings with filters."""
        role_id = test_role["id"]
        
        # Create test bindings
        binding_ids = []
        async with db_pool.acquire() as conn:
            for i, rtype in enumerate(["app", "library", "app"]):
                binding_id = uuid.uuid4()
                binding_ids.append(binding_id)
                await conn.execute(
                    """
                    INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                    VALUES ($1, $2, $3, $4)
                    """,
                    binding_id,
                    uuid.UUID(role_id),
                    rtype,
                    f"resource_{i}",
                )
        
        try:
            async with httpx.AsyncClient() as client:
                # List all bindings for the role
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/admin/bindings",
                    headers=admin_headers,
                    params={"role_id": role_id},
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to list bindings: {resp.text}"
                data = resp.json()
                assert len(data) == 3
                
                # Filter by resource type
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/admin/bindings",
                    headers=admin_headers,
                    params={"role_id": role_id, "resource_type": "app"},
                    timeout=30.0,
                )
                
                assert resp.status_code == 200
                data = resp.json()
                assert len(data) == 2
                assert all(b["resource_type"] == "app" for b in data)
        finally:
            # Clean up
            async with db_pool.acquire() as conn:
                for binding_id in binding_ids:
                    await conn.execute(
                        "DELETE FROM authz_role_bindings WHERE id = $1",
                        binding_id
                    )
    
    @pytest.mark.asyncio
    async def test_get_binding(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test getting a specific binding by ID."""
        role_id = test_role["id"]
        binding_id = uuid.uuid4()
        resource_id = str(uuid.uuid4())
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id, permissions)
                VALUES ($1, $2, $3, $4, $5)
                """,
                binding_id,
                uuid.UUID(role_id),
                "library",
                resource_id,
                '{"access": "full"}',
            )
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/admin/bindings/{binding_id}",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to get binding: {resp.text}"
                data = resp.json()
                assert data["id"] == str(binding_id)
                assert data["role_id"] == role_id
                assert data["resource_type"] == "library"
                assert data["resource_id"] == resource_id
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_role_bindings WHERE id = $1",
                    binding_id
                )
    
    @pytest.mark.asyncio
    async def test_delete_binding(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test deleting a binding."""
        role_id = test_role["id"]
        binding_id = uuid.uuid4()
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                VALUES ($1, $2, $3, $4)
                """,
                binding_id,
                uuid.UUID(role_id),
                "app",
                "app_to_delete",
            )
        
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{TEST_AUTHZ_URL}/admin/bindings/{binding_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            
            assert resp.status_code == 204, f"Failed to delete binding: {resp.text}"
            
            # Verify it's gone
            resp = await client.get(
                f"{TEST_AUTHZ_URL}/admin/bindings/{binding_id}",
                headers=admin_headers,
                timeout=30.0,
            )
            assert resp.status_code == 404


class TestRoleBindingsQueries:
    """Test role binding query endpoints."""
    
    @pytest.mark.asyncio
    async def test_get_role_bindings(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test getting all bindings for a role."""
        role_id = test_role["id"]
        
        # Create test bindings
        binding_ids = []
        async with db_pool.acquire() as conn:
            for i in range(3):
                binding_id = uuid.uuid4()
                binding_ids.append(binding_id)
                await conn.execute(
                    """
                    INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                    VALUES ($1, $2, $3, $4)
                    """,
                    binding_id,
                    uuid.UUID(role_id),
                    "app",
                    f"app_{i}",
                )
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/roles/{role_id}/bindings",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to get role bindings: {resp.text}"
                data = resp.json()
                assert len(data) == 3
        finally:
            async with db_pool.acquire() as conn:
                for binding_id in binding_ids:
                    await conn.execute(
                        "DELETE FROM authz_role_bindings WHERE id = $1",
                        binding_id
                    )
    
    @pytest.mark.asyncio
    async def test_get_resource_roles(self, admin_headers, test_role, db_pool, clean_test_data):
        """Test getting all roles for a resource."""
        role_id = test_role["id"]
        resource_id = str(uuid.uuid4())
        
        # Create test binding
        binding_id = uuid.uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                VALUES ($1, $2, $3, $4)
                """,
                binding_id,
                uuid.UUID(role_id),
                "library",
                resource_id,
            )
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/resources/library/{resource_id}/roles",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to get resource roles: {resp.text}"
                data = resp.json()
                assert len(data) == 1
                assert data[0]["id"] == role_id
                assert data[0]["name"] == test_role["name"]
                assert "binding_id" in data[0]
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_role_bindings WHERE id = $1",
                    binding_id
                )
    
    @pytest.mark.asyncio
    async def test_user_can_access_resource(self, admin_headers, test_user, test_role, db_pool, clean_test_data):
        """Test checking if a user can access a resource."""
        user_id = test_user["id"]
        role_id = test_role["id"]
        resource_id = str(uuid.uuid4())
        
        # Assign role to user and create binding
        binding_id = uuid.uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
            await conn.execute(
                """
                INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                VALUES ($1, $2, $3, $4)
                """,
                binding_id,
                uuid.UUID(role_id),
                "app",
                resource_id,
            )
        
        try:
            async with httpx.AsyncClient() as client:
                # User should have access
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/users/{user_id}/can-access/app/{resource_id}",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to check access: {resp.text}"
                data = resp.json()
                assert data["has_access"] is True
                
                # User should not have access to a different resource
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/users/{user_id}/can-access/app/{uuid.uuid4()}",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200
                data = resp.json()
                assert data["has_access"] is False
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                    uuid.UUID(user_id),
                    uuid.UUID(role_id),
                )
                await conn.execute(
                    "DELETE FROM authz_role_bindings WHERE id = $1",
                    binding_id
                )
    
    @pytest.mark.asyncio
    async def test_get_user_resources(self, admin_headers, test_user, test_role, db_pool, clean_test_data):
        """Test getting all resources a user can access."""
        user_id = test_user["id"]
        role_id = test_role["id"]
        
        # Assign role to user and create bindings
        binding_ids = []
        resource_ids = [str(uuid.uuid4()) for _ in range(3)]
        
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO authz_user_roles (user_id, role_id)
                VALUES ($1, $2)
                """,
                uuid.UUID(user_id),
                uuid.UUID(role_id),
            )
            for res_id in resource_ids:
                binding_id = uuid.uuid4()
                binding_ids.append(binding_id)
                await conn.execute(
                    """
                    INSERT INTO authz_role_bindings (id, role_id, resource_type, resource_id)
                    VALUES ($1, $2, $3, $4)
                    """,
                    binding_id,
                    uuid.UUID(role_id),
                    "library",
                    res_id,
                )
        
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{TEST_AUTHZ_URL}/users/{user_id}/resources/library",
                    headers=admin_headers,
                    timeout=30.0,
                )
                
                assert resp.status_code == 200, f"Failed to get user resources: {resp.text}"
                data = resp.json()
                assert "resource_ids" in data
                assert len(data["resource_ids"]) == 3
                for res_id in resource_ids:
                    assert res_id in data["resource_ids"]
        finally:
            async with db_pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM authz_user_roles WHERE user_id = $1 AND role_id = $2",
                    uuid.UUID(user_id),
                    uuid.UUID(role_id),
                )
                for binding_id in binding_ids:
                    await conn.execute(
                        "DELETE FROM authz_role_bindings WHERE id = $1",
                        binding_id
                    )
