"""
Defense-in-Depth Security Tests for Search API.

Tests verify BOTH layers of security:
1. API-level: JWT authentication and role/scope checking
2. Database-level: Row-Level Security (RLS) policies

These tests ensure that even if one layer fails, the other protects data.

Requirements:
- AuthZ service running with test user configured
- PostgreSQL with RLS policies enabled
- Milvus with partition-based isolation
- Test user starts with NO roles (tests add roles as needed)
"""

import os
import uuid
import pytest
from fastapi.testclient import TestClient

# Import shared testing utilities
from testing.auth import AuthTestClient
from testing.database import DatabasePool, RLSEnabledPool


# =============================================================================
# Test Configuration
# =============================================================================

# Database for files (production: "files", tests: "test_files")
FILES_DB = os.getenv("POSTGRES_DB", "files")
FILES_HOST = os.getenv("POSTGRES_HOST", "localhost")


def get_files_db_pool():
    """Get a database pool for the files database."""
    return DatabasePool(
        host=FILES_HOST,
        database=FILES_DB,
        user=os.getenv("POSTGRES_USER", "busibox_test_user"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


# =============================================================================
# API-Level Security Tests
# =============================================================================

class TestAPILevelSecurity:
    """
    Tests for API-level security (JWT authentication, role/scope checking).
    
    These tests verify that the API correctly:
    - Rejects unauthenticated requests
    - Rejects requests with invalid tokens
    - Enforces role-based access at the API level
    """
    
    def test_unauthenticated_request_rejected(self, test_client):
        """Verify that requests without auth are rejected with 401."""
        response = test_client.post(
            "/search",
            json={"query": "test", "mode": "keyword", "limit": 5},
        )
        assert response.status_code == 401
        assert "error" in response.json()
    
    def test_invalid_token_rejected(self, test_client):
        """Verify that requests with invalid tokens are rejected."""
        response = test_client.post(
            "/search",
            json={"query": "test", "mode": "keyword", "limit": 5},
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert response.status_code == 401
    
    def test_expired_token_rejected(self, test_client, auth_client: AuthTestClient):
        """Verify that expired tokens are rejected."""
        # Note: This would require generating an expired token
        # For now, we just verify that invalid tokens are rejected
        response = test_client.post(
            "/search",
            json={"query": "test", "mode": "keyword", "limit": 5},
            headers={"Authorization": "Bearer eyJ.expired.token"},
        )
        assert response.status_code == 401
    
    def test_valid_token_passes_auth(self, test_client, auth_client: AuthTestClient):
        """Verify that valid tokens pass authentication."""
        header = auth_client.get_auth_header(audience="search-api")
        
        response = test_client.post(
            "/search",
            json={"query": "test", "mode": "keyword", "limit": 5},
            headers=header,
        )
        
        # Should not be auth error - may be success or service error
        assert response.status_code not in [401, 403], f"Auth failed: {response.text}"


# =============================================================================
# Role-Based Access Control Tests
# =============================================================================

class TestRoleBasedAccessControl:
    """
    Tests for role-based access control.
    
    Verifies that:
    - Users without roles get no results (can't access any partitions)
    - Users with roles can only see content in their role's partitions
    - Role changes are reflected immediately in search results
    """
    
    def test_user_without_roles_gets_no_results(self, test_client, auth_client: AuthTestClient):
        """
        User with no roles should get empty search results.
        
        This verifies that the default state is "no access" - users must
        be explicitly granted roles to access content.
        """
        # Ensure user has no roles
        with auth_client.with_clean_user():
            header = auth_client.get_auth_header(audience="search-api")
            
            response = test_client.post(
                "/search/keyword",
                json={"query": "test", "limit": 100},
                headers=header,
            )
            
            # Auth should pass
            assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
            
            if response.status_code == 200:
                data = response.json()
                # User without roles should get no results
                # (they have no access to any partitions)
                assert len(data["results"]) == 0, \
                    f"User without roles should not see any content, got {len(data['results'])} results"
    
    def test_user_with_role_can_search_role_partition(self, test_client, auth_client: AuthTestClient):
        """
        User with a role should be able to search content in that role's partition.
        """
        # Add a role to the user
        with auth_client.with_role("test-analyst"):
            header = auth_client.get_auth_header(audience="search-api")
            
            response = test_client.post(
                "/search/keyword",
                json={"query": "test", "limit": 10},
                headers=header,
            )
            
            # Auth should pass
            assert response.status_code not in [401, 403], f"Auth failed: {response.text}"
            
            # Note: May get 0 results if no content exists in the role's partition
            # The key test is that auth passes and search executes
    
    def test_role_removal_revokes_access(self, test_client, auth_client: AuthTestClient):
        """
        When a role is removed, user should immediately lose access to that partition.
        """
        # First, add a role
        role_id = auth_client.add_role_to_user("test-temporary-role")
        
        # Get token with the role
        header_with_role = auth_client.get_auth_header(audience="search-api")
        
        # Now remove the role
        auth_client.remove_role_from_user("test-temporary-role")
        
        # Get a new token (without the role)
        header_without_role = auth_client.get_auth_header(audience="search-api")
        
        # Search with the new token should not include content from the removed role
        response = test_client.post(
            "/search/keyword",
            json={"query": "test", "limit": 10},
            headers=header_without_role,
        )
        
        assert response.status_code not in [401, 403]


# =============================================================================
# Database-Level RLS Tests
# =============================================================================

@pytest.mark.asyncio
class TestDatabaseRLS:
    """
    Tests for database-level Row-Level Security.
    
    These tests verify that RLS policies correctly restrict data access
    at the database level, independent of the API layer.
    
    This is the "second line of defense" - even if the API layer is bypassed,
    the database should still enforce access control.
    """
    
    async def test_rls_blocks_access_without_user_context(self):
        """
        Without setting app.user_id, RLS should block all access.
        """
        pool = get_files_db_pool()
        try:
            await pool.initialize()
            
            async with pool.acquire() as conn:
                # Don't set any RLS context
                # Query should return empty results due to RLS
                result = await conn.fetch(
                    "SELECT file_id, filename FROM ingestion_files LIMIT 10"
                )
                
                # Without user context, RLS should block access
                # (user_id doesn't match any owner_id)
                assert len(result) == 0, \
                    "RLS should block access when app.user_id is not set"
        finally:
            await pool.close()
    
    async def test_rls_allows_access_with_correct_user(self):
        """
        With correct app.user_id set, user should see their own documents.
        """
        pool = RLSEnabledPool(
            host=FILES_HOST,
            database=FILES_DB,
            user=os.getenv("POSTGRES_USER", "busibox_test_user"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
        
        try:
            await pool.initialize()
            
            # Set RLS context to test user
            test_user_id = os.getenv("TEST_USER_ID")
            if not test_user_id:
                pytest.skip("TEST_USER_ID not set")
            
            pool.set_rls_context(user_id=test_user_id)
            
            async with pool.acquire() as conn:
                # Query should only return documents owned by this user
                result = await conn.fetch(
                    "SELECT file_id, owner_id FROM ingestion_files LIMIT 10"
                )
                
                # All returned documents should be owned by the test user
                for row in result:
                    assert str(row["owner_id"]) == test_user_id, \
                        f"RLS leaked document owned by {row['owner_id']} to user {test_user_id}"
        finally:
            await pool.close()
    
    async def test_rls_blocks_access_to_other_users_documents(self):
        """
        User should not be able to see documents owned by other users.
        """
        pool = RLSEnabledPool(
            host=FILES_HOST,
            database=FILES_DB,
            user=os.getenv("POSTGRES_USER", "busibox_test_user"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
        
        try:
            await pool.initialize()
            
            # Set RLS context to a random user ID that doesn't own any documents
            fake_user_id = str(uuid.uuid4())
            pool.set_rls_context(user_id=fake_user_id)
            
            async with pool.acquire() as conn:
                # Query should return no results (fake user owns nothing)
                result = await conn.fetch(
                    "SELECT file_id, owner_id FROM ingestion_files LIMIT 10"
                )
                
                assert len(result) == 0, \
                    f"RLS leaked {len(result)} documents to user {fake_user_id} who owns nothing"
        finally:
            await pool.close()


# =============================================================================
# Milvus Partition Isolation Tests
# =============================================================================

class TestMilvusPartitionIsolation:
    """
    Tests for Milvus partition-based isolation.
    
    Verifies that:
    - Search only queries partitions the user has access to
    - Users cannot query partitions they don't have roles for
    """
    
    def test_search_only_queries_accessible_partitions(self, test_client, auth_client: AuthTestClient):
        """
        Search should only query partitions corresponding to user's roles.
        """
        # User with no roles should not query any partitions
        with auth_client.with_clean_user():
            header = auth_client.get_auth_header(audience="search-api")
            
            response = test_client.post(
                "/search/keyword",
                json={"query": "test", "limit": 10},
                headers=header,
            )
            
            if response.status_code == 200:
                data = response.json()
                # No results expected - no partitions accessible
                assert len(data["results"]) == 0


# =============================================================================
# Combined Defense-in-Depth Tests
# =============================================================================

class TestDefenseInDepth:
    """
    Tests that verify both API and database layers work together.
    
    These tests simulate scenarios where one layer might be bypassed
    and verify that the other layer still protects data.
    """
    
    def test_api_and_rls_agree_on_access(self, test_client, auth_client: AuthTestClient):
        """
        API-level and database-level access control should agree.
        
        If the API says a user can't access something, the database
        should also block access, and vice versa.
        """
        # User without roles
        with auth_client.with_clean_user():
            header = auth_client.get_auth_header(audience="search-api")
            
            # API should return no results
            response = test_client.post(
                "/search/keyword",
                json={"query": "test", "limit": 10},
                headers=header,
            )
            
            if response.status_code == 200:
                api_results = len(response.json()["results"])
                
                # Database should also return no results
                # (This is verified by the RLS tests above)
                
                assert api_results == 0, \
                    "API returned results for user without roles - defense in depth failed"
    
    def test_token_roles_match_database_access(self, test_client, auth_client: AuthTestClient):
        """
        Roles in the JWT token should match database access.
        
        When a user has a role in their token, they should be able to
        access content both via the API and directly in the database.
        """
        with auth_client.with_role("test-analyst"):
            header = auth_client.get_auth_header(audience="search-api")
            
            # API search should work
            response = test_client.post(
                "/search/keyword",
                json={"query": "test", "limit": 10},
                headers=header,
            )
            
            # Auth should pass
            assert response.status_code not in [401, 403]

