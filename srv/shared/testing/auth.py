"""
AuthZ test client for managing test users, roles, and scopes.

Provides a clean interface for:
- Getting tokens via Zero Trust token exchange (magic link login + subject_token)
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

# Well-known test user email (must match oauth.py bootstrap)
TEST_USER_EMAIL = "test@test.example.com"


class AuthTestClient:
    """
    Client for managing test authentication state.
    
    Uses the authz login flow (magic link) to obtain session JWTs,
    then exchanges them for service-scoped tokens via Zero Trust token exchange.
    
    No client credentials (API_SERVICE_CLIENT_SECRET) are required.
    All changes are tracked and can be cleaned up automatically.
    """
    
    def __init__(
        self,
        authz_url: Optional[str] = None,
        admin_token: Optional[str] = None,
        test_user_id: Optional[str] = None,
        test_user_email: Optional[str] = None,
    ):
        """
        Initialize the auth test client.
        
        Args:
            authz_url: Base URL for authz service (default from AUTHZ_JWKS_URL or AUTH_JWKS_URL)
            admin_token: Admin token for role management (optional)
            test_user_id: Test user ID (default from TEST_USER_ID)
            test_user_email: Test user email (default: test@test.example.com)
        """
        # Get authz URL from JWKS URL (try both env var names)
        jwks_url = os.getenv("AUTHZ_JWKS_URL", "") or os.getenv("AUTH_JWKS_URL", "")
        default_url = jwks_url.replace("/.well-known/jwks.json", "") if jwks_url else ""
        
        self.authz_url = authz_url or default_url
        # Default to the well-known test user ID if not provided
        # This ID is created by _ensure_bootstrap_test_user() in authz
        self.test_user_id = test_user_id or os.getenv("TEST_USER_ID", "00000000-0000-0000-0000-000000000001")
        self.test_user_email = test_user_email or os.getenv("TEST_USER_EMAIL", TEST_USER_EMAIL)
        
        # Admin token for role management (optional)
        self.admin_token = admin_token or os.getenv("AUTHZ_ADMIN_TOKEN", "")
        
        # Track changes for cleanup
        self._added_roles: Set[str] = set()
        self._added_scopes: Set[str] = set()
        self._created_roles: Set[str] = set()
        
        # Cache for role IDs
        self._role_cache: Dict[str, str] = {}
        
        # Cache for session JWT (avoids repeated login)
        self._session_jwt: Optional[str] = None
    
    def _require_config(self, require_admin: bool = False):
        """
        Verify required configuration is present.
        
        Args:
            require_admin: If True, also require AUTHZ_ADMIN_TOKEN (for user management operations)
        """
        if not self.authz_url:
            pytest.fail("AUTHZ_JWKS_URL not configured")
        if not self.test_user_id:
            pytest.fail("TEST_USER_ID not configured")
    
    def _admin_headers(self) -> Dict[str, str]:
        """Get headers for admin API calls. Includes X-Test-Mode header."""
        return {
            "Authorization": f"Bearer {self.admin_token}",
            TEST_MODE_HEADER: TEST_MODE_VALUE,
        }
    
    # =========================================================================
    # User Management
    # =========================================================================
    
    def ensure_test_user_exists(self) -> None:
        """
        Ensure the test user exists in the database.
        
        In Zero Trust architecture, the test user is bootstrapped automatically
        by authz on startup (_ensure_bootstrap_test_user). This method verifies
        the user can be logged in by attempting a magic link login.
        
        If admin token is available, also checks via admin API as fallback.
        """
        self._require_config()
        
        # Try to verify the test user exists by initiating a login
        # This will create the user if it doesn't exist (as PENDING)
        with httpx.Client() as client:
            resp = client.post(
                f"{self.authz_url}/auth/login/initiate",
                json={"email": self.test_user_email},
                headers={TEST_MODE_HEADER: TEST_MODE_VALUE},
                timeout=10.0,
            )
            
            if resp.status_code == 200:
                # Login initiated successfully - user exists (or was created)
                return
        
        # Fallback: If admin token is available, try admin API
        if self.admin_token:
            with httpx.Client() as client:
                resp = client.get(
                    f"{self.authz_url}/admin/users/{self.test_user_id}",
                    headers=self._admin_headers(),
                    timeout=10.0,
                )
                
                if resp.status_code == 200:
                    return
                
                # Try to create user via admin API
                resp = client.post(
                    f"{self.authz_url}/admin/users",
                    headers=self._admin_headers(),
                    json={
                        "email": self.test_user_email,
                        "status": "ACTIVE",
                    },
                    timeout=10.0,
                )
                
                if resp.status_code in [200, 201, 409]:
                    return
                
                pytest.fail(f"Failed to create test user: {resp.status_code} - {resp.text}")
    
    # =========================================================================
    # Token Management (Zero Trust)
    # =========================================================================
    
    def _get_session_jwt(self) -> str:
        """
        Get a session JWT for the test user via magic link login.
        
        This is the Zero Trust flow:
        1. POST /auth/login/initiate with test user email
        2. POST /auth/magic-links/{token}/use to get session JWT
        
        The session JWT is cached for the lifetime of this client.
        
        Returns:
            Session JWT string
        """
        if self._session_jwt:
            return self._session_jwt
        
        self._require_config()
        
        with httpx.Client() as client:
            # Step 1: Initiate login for the test user
            resp = client.post(
                f"{self.authz_url}/auth/login/initiate",
                json={"email": self.test_user_email},
                headers={TEST_MODE_HEADER: TEST_MODE_VALUE},
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                pytest.fail(
                    f"Failed to initiate login for test user ({self.test_user_email}): "
                    f"{resp.status_code} - {resp.text}"
                )
            
            data = resp.json()
            magic_link_token = data.get("magic_link_token")
            if not magic_link_token:
                pytest.fail(f"No magic_link_token in login response: {data}")
            
            # Step 2: Use the magic link to get a session JWT
            resp = client.post(
                f"{self.authz_url}/auth/magic-links/{magic_link_token}/use",
                headers={TEST_MODE_HEADER: TEST_MODE_VALUE},
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                pytest.fail(
                    f"Failed to use magic link for test user: "
                    f"{resp.status_code} - {resp.text}. "
                    f"Check that test.example.com is in ALLOWED_EMAIL_DOMAINS "
                    f"or ALLOWED_EMAIL_DOMAINS is empty."
                )
            
            data = resp.json()
            session = data.get("session", {})
            session_jwt = session.get("token")
            
            if not session_jwt:
                pytest.fail(f"No session JWT in magic link response: {data}")
            
            self._session_jwt = session_jwt
            return session_jwt
    
    def get_token(self, audience: str = "search-api", scopes: str = "read write") -> str:
        """
        Get an access token for the test user via Zero Trust token exchange.
        
        Uses the magic link login flow to get a session JWT, then exchanges
        it for a service-scoped access token. No client credentials required.
        
        Args:
            audience: Target audience for the token (e.g., "data-api", "search-api", "agent-api")
            scopes: Space-separated scopes to request (scopes come from RBAC, this is advisory)
            
        Returns:
            Access token string
        """
        self._require_config()
        
        # Get a session JWT for the test user
        session_jwt = self._get_session_jwt()
        
        with httpx.Client() as client:
            # Exchange session JWT for service-scoped access token
            resp = client.post(
                f"{self.authz_url}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "subject_token": session_jwt,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                    "audience": audience,
                    "scope": scopes,
                },
                headers={TEST_MODE_HEADER: TEST_MODE_VALUE},
                timeout=10.0,
            )
            
            if resp.status_code != 200:
                pytest.fail(f"Failed to exchange token: {resp.status_code} - {resp.text}")
            
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
            
        NOTE: Requires AUTHZ_ADMIN_TOKEN to be configured.
        """
        if role_name in self._role_cache:
            return self._role_cache[role_name]
        
        self._require_config(require_admin=True)
        
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
            
        NOTE: Requires AUTHZ_ADMIN_TOKEN to be configured.
        """
        self._require_config(require_admin=True)
        
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
            
        NOTE: Requires AUTHZ_ADMIN_TOKEN to be configured.
        """
        self._require_config(require_admin=True)
        
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
            
        NOTE: Requires AUTHZ_ADMIN_TOKEN to be configured.
        """
        self._require_config(require_admin=True)
        
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
            
        NOTE: Requires AUTHZ_ADMIN_TOKEN to be configured.
        """
        self._require_config(require_admin=True)
        
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
        Clears cached session JWT.
        """
        # Clear cached session
        self._session_jwt = None
        
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
    
    At session start, ensures test user exists and cleans up any stale test roles.
    At session end, cleans up any roles created during this session.
    
    Usage:
        def test_something(auth_client):
            auth_client.add_role_to_user("analyst")
            token = auth_client.get_token()
            # ... test ...
            # Cleanup happens automatically at session end
    """
    client = AuthTestClient()
    
    # Ensure test user exists in the database
    client.ensure_test_user_exists()
    
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

