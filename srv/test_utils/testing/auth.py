"""
AuthZ test client for managing test users, roles, and scopes.

Provides a clean interface for:
- Getting tokens via token exchange
- Adding/removing roles from test user
- Adding/removing scopes from test user
- Cleaning up test state

Usage:
    auth_client = AuthTestClient()
    
    # Get a token for the test user
    token = auth_client.get_token(audience="search-api")
    
    # Add a role to test role-based access
    auth_client.add_role_to_user("analyst")
    
    # Clean up after test
    auth_client.remove_role_from_user("analyst")
"""

import os
import uuid
from contextlib import contextmanager
from typing import Dict, List, Optional, Set

import httpx
import pytest


# Test mode header - tells the API to route to test database
TEST_MODE_HEADER = "X-Test-Mode"
TEST_MODE_VALUE = "true"


class AuthTestClient:
    """
    Client for managing test authentication state.
    
    Uses the authz admin API to manipulate roles and scopes for testing.
    All changes are tracked and can be cleaned up automatically.
    """
    
    def __init__(
        self,
        authz_url: Optional[str] = None,
        admin_token: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        test_user_id: Optional[str] = None,
    ):
        """
        Initialize the auth test client.
        
        Args:
            authz_url: Base URL for authz service (default from AUTHZ_JWKS_URL)
            admin_token: Admin token for authz API (default from AUTHZ_ADMIN_TOKEN)
            client_id: OAuth client ID (default from AUTHZ_BOOTSTRAP_CLIENT_ID)
            client_secret: OAuth client secret (default from AUTHZ_BOOTSTRAP_CLIENT_SECRET)
            test_user_id: Test user ID (default from TEST_USER_ID)
        """
        # Get authz URL from JWKS URL
        jwks_url = os.getenv("AUTHZ_JWKS_URL", "")
        default_url = jwks_url.replace("/.well-known/jwks.json", "") if jwks_url else ""
        
        self.authz_url = authz_url or default_url
        self.admin_token = admin_token or os.getenv("AUTHZ_ADMIN_TOKEN", "")
        self.client_id = client_id or os.getenv("AUTHZ_BOOTSTRAP_CLIENT_ID", "ai-portal")
        self.client_secret = client_secret or os.getenv("AUTHZ_BOOTSTRAP_CLIENT_SECRET", "")
        self.test_user_id = test_user_id or os.getenv("TEST_USER_ID", "")
        
        # Track changes for cleanup
        self._added_roles: Set[str] = set()
        self._added_scopes: Set[str] = set()
        self._created_roles: Set[str] = set()
        
        # Cache for role IDs
        self._role_cache: Dict[str, str] = {}
    
    def _require_config(self):
        """Verify required configuration is present."""
        if not self.authz_url:
            pytest.fail("AUTHZ_JWKS_URL not configured")
        if not self.admin_token:
            pytest.fail("AUTHZ_ADMIN_TOKEN not configured")
        if not self.client_secret:
            pytest.fail("AUTHZ_BOOTSTRAP_CLIENT_SECRET not configured")
        if not self.test_user_id:
            pytest.fail("TEST_USER_ID not configured")
    
    def _admin_headers(self) -> Dict[str, str]:
        """Get headers for admin API calls. Includes X-Test-Mode header."""
        return {
            "Authorization": f"Bearer {self.admin_token}",
            TEST_MODE_HEADER: TEST_MODE_VALUE,
        }
    
    # =========================================================================
    # Token Management
    # =========================================================================
    
    def get_token(self, audience: str = "search-api", scopes: str = "read write") -> str:
        """
        Get an access token for the test user via token exchange.
        
        Args:
            audience: Target audience for the token
            scopes: Space-separated scopes to request
            
        Returns:
            Access token string
        """
        self._require_config()
        
        with httpx.Client() as client:
            resp = client.post(
                f"{self.authz_url}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "requested_subject": self.test_user_id,
                    "audience": audience,
                    "scope": scopes,
                },
                headers={TEST_MODE_HEADER: TEST_MODE_VALUE},
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                pytest.fail(f"Failed to get token: {resp.status_code} - {resp.text}")
            
            data = resp.json()
            if "access_token" not in data:
                pytest.fail(f"No access_token in response: {data}")
            
            return data["access_token"]
    
    def get_auth_header(self, audience: str = "search-api") -> Dict[str, str]:
        """
        Get an Authorization header with a valid token.
        
        Includes X-Test-Mode header to route API requests to test database.
        
        Args:
            audience: Target audience for the token
            
        Returns:
            Dict with Authorization and X-Test-Mode headers
        """
        token = self.get_token(audience)
        return {
            "Authorization": f"Bearer {token}",
            TEST_MODE_HEADER: TEST_MODE_VALUE,
        }
    
    # =========================================================================
    # Role Management
    # =========================================================================
    
    def get_role_id(self, role_name: str) -> Optional[str]:
        """
        Get the ID of a role by name.
        
        Args:
            role_name: Name of the role
            
        Returns:
            Role ID or None if not found
        """
        if role_name in self._role_cache:
            return self._role_cache[role_name]
        
        self._require_config()
        
        with httpx.Client() as client:
            resp = client.get(
                f"{self.authz_url}/admin/roles",
                headers=self._admin_headers(),
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                return None
            
            roles = resp.json()
            for role in roles:
                self._role_cache[role["name"]] = role["id"]
                if role["name"] == role_name:
                    return role["id"]
            
            return None
    
    def create_role(self, role_name: str, scopes: Optional[List[str]] = None, description: str = "") -> str:
        """
        Create a new role for testing.
        
        Args:
            role_name: Name of the role
            scopes: Optional list of OAuth2 scopes to associate with the role
            description: Optional description
            
        Returns:
            Role ID
        """
        self._require_config()
        
        # Check if role already exists
        existing_id = self.get_role_id(role_name)
        if existing_id:
            return existing_id
        
        body = {
            "name": role_name,
            "description": description or f"Test role: {role_name}",
        }
        if scopes:
            body["scopes"] = scopes
        
        with httpx.Client() as client:
            resp = client.post(
                f"{self.authz_url}/admin/roles",
                headers=self._admin_headers(),
                json=body,
                timeout=10.0,
            )
            
            if resp.status_code not in [200, 201]:
                pytest.fail(f"Failed to create role: {resp.status_code} - {resp.text}")
            
            role = resp.json()
            role_id = role["id"]
            
            self._role_cache[role_name] = role_id
            self._created_roles.add(role_id)
            
            return role_id
    
    def delete_role(self, role_id: str) -> None:
        """
        Delete a role by ID.
        
        Args:
            role_id: ID of the role to delete
        """
        self._require_config()
        
        with httpx.Client() as client:
            resp = client.delete(
                f"{self.authz_url}/admin/roles/{role_id}",
                headers=self._admin_headers(),
                timeout=10.0,
            )
            
            # 200/204 = success, 404 = already deleted (also ok)
            if resp.status_code not in [200, 204, 404]:
                pytest.fail(f"Failed to delete role: {resp.status_code} - {resp.text}")
            
            # Remove from tracking
            self._created_roles.discard(role_id)
            # Remove from cache
            self._role_cache = {k: v for k, v in self._role_cache.items() if v != role_id}
    
    def add_role_to_user(self, role_name: str) -> str:
        """
        Add a role to the test user.
        
        Args:
            role_name: Name of the role to add
            
        Returns:
            Role ID
        """
        self._require_config()
        
        # Get or create the role
        role_id = self.get_role_id(role_name)
        if not role_id:
            role_id = self.create_role(role_name)
        
        with httpx.Client() as client:
            # Use POST /admin/user-roles with user_id and role_id in body
            resp = client.post(
                f"{self.authz_url}/admin/user-roles",
                headers=self._admin_headers(),
                json={"user_id": self.test_user_id, "role_id": role_id},
                timeout=10.0,
            )
            
            # 200/201 = success, 409 = already assigned (also ok)
            if resp.status_code not in [200, 201, 409]:
                pytest.fail(f"Failed to add role to user: {resp.status_code} - {resp.text}")
            
            self._added_roles.add(role_id)
            return role_id
    
    def remove_role_from_user(self, role_name: str) -> None:
        """
        Remove a role from the test user.
        
        Args:
            role_name: Name of the role to remove
        """
        self._require_config()
        
        role_id = self.get_role_id(role_name)
        if not role_id:
            return  # Role doesn't exist, nothing to remove
        
        with httpx.Client() as client:
            # Use DELETE /admin/user-roles with user_id and role_id in body
            resp = client.request(
                "DELETE",
                f"{self.authz_url}/admin/user-roles",
                headers=self._admin_headers(),
                json={"user_id": self.test_user_id, "role_id": role_id},
                timeout=10.0,
            )
            
            # 200/204 = success, 404 = not assigned (also ok)
            if resp.status_code not in [200, 204, 404]:
                pytest.fail(f"Failed to remove role from user: {resp.status_code} - {resp.text}")
            
            self._added_roles.discard(role_id)
    
    def get_user_roles(self) -> List[Dict]:
        """
        Get all roles assigned to the test user.
        
        Returns:
            List of role dicts with id and name
        """
        self._require_config()
        
        with httpx.Client() as client:
            resp = client.get(
                f"{self.authz_url}/admin/users/{self.test_user_id}/roles",
                headers=self._admin_headers(),
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                return []
            
            return resp.json()
    
    def clear_user_roles(self) -> None:
        """Remove all roles from the test user."""
        roles = self.get_user_roles()
        for role in roles:
            self.remove_role_from_user(role["name"])
    
    # =========================================================================
    # Scope Management (via OAuth client configuration)
    # =========================================================================
    
    # Note: Scopes in OAuth2 are typically granted per-client, not per-user.
    # The test user's scopes come from the token exchange request.
    # For testing scope enforcement, we control scopes in the token request.
    
    def get_token_with_scopes(self, scopes: List[str], audience: str = "search-api") -> str:
        """
        Get a token with specific scopes.
        
        Args:
            scopes: List of scopes to request
            audience: Target audience
            
        Returns:
            Access token
        """
        return self.get_token(audience=audience, scopes=" ".join(scopes))
    
    def get_token_without_scopes(self, audience: str = "search-api") -> str:
        """
        Get a token with no scopes (for testing scope enforcement).
        
        Args:
            audience: Target audience
            
        Returns:
            Access token with empty scope
        """
        return self.get_token(audience=audience, scopes="")
    
    # =========================================================================
    # Cleanup
    # =========================================================================
    
    def cleanup(self) -> None:
        """
        Clean up all changes made during testing.
        
        Removes roles added to user and deletes created roles.
        All cleanup operations use X-Test-Mode header to target test database.
        """
        # Remove roles from user
        for role_id in list(self._added_roles):
            try:
                with httpx.Client() as client:
                    # Note: _admin_headers() already includes X-Test-Mode
                    client.delete(
                        f"{self.authz_url}/admin/users/{self.test_user_id}/roles/{role_id}",
                        headers=self._admin_headers(),
                        timeout=10.0,
                    )
            except Exception:
                pass  # Best effort cleanup
        
        self._added_roles.clear()
        
        # Delete created roles
        for role_id in list(self._created_roles):
            try:
                with httpx.Client() as client:
                    client.delete(
                        f"{self.authz_url}/admin/roles/{role_id}",
                        headers=self._admin_headers(),
                        timeout=10.0,
                    )
            except Exception:
                pass  # Best effort cleanup
        
        self._created_roles.clear()
    
    # =========================================================================
    # Context Managers
    # =========================================================================
    
    @contextmanager
    def with_role(self, role_name: str):
        """
        Context manager to temporarily add a role to the test user.
        
        Usage:
            with auth_client.with_role("analyst"):
                # Test with analyst role
                response = client.get("/data", headers=auth_client.get_auth_header())
        """
        role_id = self.add_role_to_user(role_name)
        try:
            yield role_id
        finally:
            self.remove_role_from_user(role_name)
    
    @contextmanager
    def with_roles(self, role_names: List[str]):
        """
        Context manager to temporarily add multiple roles.
        
        Usage:
            with auth_client.with_roles(["analyst", "admin"]):
                # Test with both roles
                pass
        """
        role_ids = [self.add_role_to_user(name) for name in role_names]
        try:
            yield role_ids
        finally:
            for name in role_names:
                self.remove_role_from_user(name)
    
    @contextmanager
    def with_clean_user(self):
        """
        Context manager to ensure test user has no roles.
        
        Removes all roles before the test, restores after.
        
        Usage:
            with auth_client.with_clean_user():
                # Test user has no roles
                response = client.get("/data", headers=auth_client.get_auth_header())
                assert response.status_code == 403  # No access without roles
        """
        # Save current roles
        original_roles = self.get_user_roles()
        
        # Clear all roles
        self.clear_user_roles()
        
        try:
            yield
        finally:
            # Restore original roles
            for role in original_roles:
                self.add_role_to_user(role["name"])


# =============================================================================
# Pytest Fixtures
# =============================================================================

@pytest.fixture(scope="session")
def auth_client():
    """
    Pytest fixture for AuthTestClient (session-scoped).
    
    At session start, cleans up any stale test roles from previous runs.
    At session end, cleans up any roles created during this session.
    
    Usage:
        def test_something(auth_client):
            auth_client.add_role_to_user("analyst")
            token = auth_client.get_token()
            # ... test ...
            # Cleanup happens automatically at session end
    """
    client = AuthTestClient()
    
    # Clean up stale test roles from previous interrupted runs
    _cleanup_stale_test_roles(client)
    
    yield client
    client.cleanup()


def _cleanup_stale_test_roles(client: AuthTestClient) -> None:
    """
    Remove any test-* roles left over from previous test runs.
    
    This handles the case where tests were interrupted and cleanup didn't happen.
    Only removes roles that start with "test-" to avoid affecting real roles.
    """
    try:
        # Get all current roles for test user
        roles = client.get_user_roles()
        stale_roles = [r for r in roles if r.get("name", "").startswith("test-")]
        
        if stale_roles:
            print(f"[test_utils] Cleaning up {len(stale_roles)} stale test roles...")
            
            for role in stale_roles:
                try:
                    # Remove role from user
                    client.remove_role_from_user(role["name"])
                except Exception:
                    pass  # Best effort
                
                try:
                    # Delete the role itself
                    client.delete_role(role["id"])
                except Exception:
                    pass  # Best effort
            
            print("[test_utils] Stale role cleanup complete")
    except Exception as e:
        print(f"[test_utils] Warning: Could not clean up stale roles: {e}")


@pytest.fixture
def clean_test_user(auth_client):
    """
    Pytest fixture that ensures test user has no roles.
    
    Usage:
        def test_no_access_without_role(clean_test_user, auth_client):
            # Test user has no roles
            token = auth_client.get_token()
            # ... test that access is denied ...
    """
    with auth_client.with_clean_user():
        yield auth_client

