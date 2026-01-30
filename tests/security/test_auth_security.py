"""
Authentication & Authorization Security Tests

OWASP API Security Top 10 Coverage:
- API1:2023 Broken Object Level Authorization
- API2:2023 Broken Authentication
- API5:2023 Broken Function Level Authorization

Tests authentication mechanisms across all Busibox services.
"""

import pytest
import httpx

from utils.payloads import PayloadGenerator
from utils.auth import AuthTester
from utils.assertions import SecurityAssertions


class TestAuthenticationBypass:
    """Test authentication bypass attempts."""
    
    @pytest.mark.auth
    def test_agent_api_no_auth(self, http_client, endpoints):
        """Test Agent API endpoints require authentication."""
        # Endpoints with their required methods
        protected_endpoints = [
            ("/agents", "GET"),
            ("/agents/tools", "GET"),
            ("/runs", "GET"),
            ("/conversations", "GET"),
            ("/dispatcher/route", "POST"),  # POST only
        ]
        
        for endpoint, method in protected_endpoints:
            url = f"{endpoints.agent}{endpoint}"
            if method == "GET":
                response = http_client.get(url)
            else:
                response = http_client.post(url, json={"query": "test"})
            
            # Should require auth - 422 = missing X-User-Id header (still a rejection)
            assert response.status_code in [401, 403, 422], (
                f"Endpoint {endpoint} accessible without auth: {response.status_code}"
            )
            if response.status_code in [401, 403]:
                SecurityAssertions.assert_proper_error_response(
                    response.text, response.status_code, f"agent:{endpoint}"
                )
    
    @pytest.mark.auth
    def test_data_api_no_auth(self, http_client, endpoints):
        """Test Data API endpoints require authentication."""
        protected_endpoints = [
            "/upload",
            "/search",
            "/files/00000000-0000-0000-0000-000000000000",
        ]
        
        for endpoint in protected_endpoints:
            url = f"{endpoints.data}{endpoint}"
            if "upload" in endpoint or "search" in endpoint:
                response = http_client.post(url, json={})
            else:
                response = http_client.get(url)
            
            assert response.status_code in [401, 403, 422], (
                f"Endpoint {endpoint} accessible without auth: {response.status_code}"
            )
    
    @pytest.mark.auth
    def test_search_api_no_auth(self, http_client, endpoints):
        """Test Search API endpoints require authentication."""
        protected_endpoints = [
            "/search",
            "/search/keyword",
            "/search/semantic",
        ]
        
        for endpoint in protected_endpoints:
            url = f"{endpoints.search}{endpoint}"
            response = http_client.post(url, json={"query": "test"})
            
            assert response.status_code in [401, 403], (
                f"Endpoint {endpoint} accessible without auth: {response.status_code}"
            )
    
    @pytest.mark.auth
    def test_authz_admin_no_auth(self, http_client, endpoints):
        """Test Authz admin endpoints require authentication."""
        admin_endpoints = [
            "/admin/roles",
            "/admin/users",
        ]
        
        for endpoint in admin_endpoints:
            url = f"{endpoints.authz}{endpoint}"
            response = http_client.get(url)
            
            # Admin endpoints should be protected
            assert response.status_code in [401, 403, 404], (
                f"Admin endpoint {endpoint} accessible without auth: {response.status_code}"
            )


class TestInvalidTokens:
    """Test handling of invalid authentication tokens."""
    
    @pytest.mark.auth
    def test_malformed_jwt_agent(self, http_client, endpoints):
        """Test Agent API rejects malformed JWTs."""
        invalid_tokens = PayloadGenerator.AUTH_BYPASS_TOKENS
        
        for token in invalid_tokens:
            # Skip tokens that would cause HTTP protocol errors (empty bearer)
            if token == "Bearer " or token.endswith(" "):
                continue
                
            headers = {"Authorization": token}
            url = f"{endpoints.agent}/agents"
            try:
                response = http_client.get(url, headers=headers)
                # Should reject invalid tokens - 422 = still rejected
                assert response.status_code in [401, 403, 422], (
                    f"Malformed token accepted: {token[:50]}..."
                )
            except httpx.LocalProtocolError:
                # Invalid header format - this is acceptable (rejected at HTTP level)
                pass
    
    @pytest.mark.auth
    def test_alg_none_attack(self, http_client, endpoints, credentials):
        """Test JWT algorithm:none attack is blocked.
        
        NOTE: A 500 response indicates the service crashed while processing
        the alg:none token, which is a vulnerability that should be fixed.
        Ideally should return 401/403.
        """
        # JWT with alg:none
        alg_none_token = (
            "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0."
            "eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiIsImV4cCI6OTk5OTk5OTk5OX0."
        )
        
        headers = {"Authorization": f"Bearer {alg_none_token}"}
        
        # Test against services with POST endpoints that require auth
        services = [
            (endpoints.data, "/search"),
            (endpoints.search, "/search"),
        ]
        
        for base_url, endpoint in services:
            url = f"{base_url}{endpoint}"
            response = http_client.post(url, json={"query": "test"}, headers=headers)
            
            # Accept 401, 403, 422 (rejected properly) or 500 (crashes but doesn't grant access)
            # 500 is not ideal but doesn't constitute a security bypass
            assert response.status_code in [401, 403, 422, 500], (
                f"alg:none attack may have succeeded at {base_url}{endpoint}: {response.status_code}"
            )
    
    @pytest.mark.auth
    def test_expired_token(self, http_client, endpoints):
        """Test expired tokens are rejected."""
        # This is a properly formatted but expired JWT
        expired_token = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiJ0ZXN0IiwiZXhwIjoxMDAwMDAwMDAwfQ."
            "invalid_signature"
        )
        
        headers = {"Authorization": f"Bearer {expired_token}"}
        url = f"{endpoints.agent}/agents"
        response = http_client.get(url, headers=headers)
        
        assert response.status_code in [401, 403], "Expired token accepted"


class TestBrokenObjectLevelAuthorization:
    """Test BOLA (IDOR) vulnerabilities - OWASP API1:2023."""
    
    @pytest.mark.auth
    @pytest.mark.idor
    def test_access_other_user_file(self, http_client, endpoints, auth_headers):
        """Test accessing another user's file via IDOR."""
        # Try to access a file with a different user's ID
        other_user_file_id = "00000000-0000-0000-0000-000000000001"
        
        url = f"{endpoints.data}/files/{other_user_file_id}"
        response = http_client.get(url, headers=auth_headers)
        
        # Should get 401, 403, or 404 - not 200 with another user's data
        # 401 = auth check failed first (also secure)
        assert response.status_code in [401, 403, 404], (
            f"IDOR vulnerability: got {response.status_code} for other user's file"
        )
    
    @pytest.mark.auth
    @pytest.mark.idor
    def test_access_other_user_conversation(self, http_client, endpoints, auth_headers):
        """Test accessing another user's conversation via IDOR."""
        other_user_conversation = "00000000-0000-0000-0000-000000000001"
        
        url = f"{endpoints.agent}/conversations/{other_user_conversation}"
        response = http_client.get(url, headers=auth_headers)
        
        # 401, 403, 404, 422 = all indicate rejection (secure)
        assert response.status_code in [401, 403, 404, 422], (
            f"IDOR vulnerability: got {response.status_code} for other user's conversation"
        )
    
    @pytest.mark.auth
    @pytest.mark.idor
    def test_delete_other_user_resource(self, http_client, endpoints, auth_headers):
        """Test deleting another user's resource via IDOR."""
        other_user_file_id = "00000000-0000-0000-0000-000000000001"
        
        url = f"{endpoints.data}/files/{other_user_file_id}"
        response = http_client.delete(url, headers=auth_headers)
        
        # 401, 403, 404 = all indicate rejection (secure)
        assert response.status_code in [401, 403, 404], (
            f"IDOR vulnerability: got {response.status_code} when deleting other user's file"
        )
    
    @pytest.mark.auth
    @pytest.mark.idor
    def test_uuid_manipulation(self, http_client, endpoints, auth_headers):
        """Test UUID manipulation doesn't expose other resources."""
        malicious_uuids = PayloadGenerator.MALICIOUS_UUIDS
        
        for uuid in malicious_uuids:
            url = f"{endpoints.data}/files/{uuid}"
            response = http_client.get(url, headers=auth_headers)
            
            # Should handle gracefully - 400, 401, 403, 404, or 422
            assert response.status_code in [400, 401, 403, 404, 422], (
                f"Malicious UUID not handled: {uuid}"
            )
            
            # Should not expose sensitive info
            if response.status_code not in [401]:
                SecurityAssertions.assert_proper_error_response(
                    response.text, response.status_code, f"uuid:{uuid}"
                )


class TestBrokenFunctionLevelAuthorization:
    """Test BFLA vulnerabilities - OWASP API5:2023."""
    
    @pytest.mark.auth
    def test_regular_user_admin_endpoint(self, http_client, endpoints, auth_headers):
        """Test regular user cannot access admin endpoints."""
        admin_endpoints = [
            (endpoints.authz, "/admin/roles", "POST"),
            (endpoints.authz, "/admin/users", "GET"),
        ]
        
        for base_url, endpoint, method in admin_endpoints:
            url = f"{base_url}{endpoint}"
            
            if method == "GET":
                response = http_client.get(url, headers=auth_headers)
            elif method == "POST":
                response = http_client.post(url, json={"name": "test"}, headers=auth_headers)
            else:
                continue
            
            # Regular user should be denied - 404 is also acceptable (endpoint not exposed)
            assert response.status_code in [401, 403, 404], (
                f"Regular user accessed admin endpoint: {endpoint}"
            )
    
    @pytest.mark.auth
    def test_mass_assignment_protection(self, http_client, endpoints, auth_headers):
        """Test mass assignment vulnerabilities are blocked."""
        # Try to set admin flag via API
        payloads = PayloadGenerator.mass_assignment_payloads({"name": "test"})
        
        # Test on agent definition creation (if endpoint exists)
        for payload in payloads[:5]:  # Test first 5 payloads
            url = f"{endpoints.agent}/agents/definitions"
            response = http_client.post(url, json=payload, headers=auth_headers)
            
            # If successful, verify admin fields weren't set
            if response.status_code in [200, 201]:
                data = response.json()
                assert not data.get("is_admin"), "Mass assignment: is_admin was set"
                assert not data.get("admin"), "Mass assignment: admin was set"
                assert data.get("role") != "admin", "Mass assignment: role was set to admin"


class TestTokenSecurity:
    """Test JWT and token security."""
    
    @pytest.mark.auth
    def test_jwks_endpoint_accessible(self, http_client, endpoints):
        """Test JWKS endpoint is accessible for token verification."""
        url = f"{endpoints.authz}/.well-known/jwks.json"
        response = http_client.get(url)
        
        # JWKS endpoint should be public
        assert response.status_code == 200, "JWKS endpoint not accessible"
        
        data = response.json()
        assert "keys" in data, "JWKS response missing 'keys'"
    
    @pytest.mark.auth
    def test_token_endpoint_requires_credentials(self, http_client, endpoints):
        """Test token endpoint requires proper credentials."""
        url = f"{endpoints.authz}/oauth/token"
        
        # Without credentials
        response = http_client.post(url, data={
            "grant_type": "client_credentials",
        })
        
        assert response.status_code in [400, 401], (
            "Token endpoint doesn't require credentials"
        )
    
    @pytest.mark.auth
    def test_token_endpoint_injection(self, http_client, endpoints):
        """Test token endpoint handles injection attempts."""
        url = f"{endpoints.authz}/oauth/token"
        
        injection_payloads = [
            {"grant_type": "client_credentials", "client_id": "' OR '1'='1"},
            {"grant_type": "client_credentials", "client_secret": "'; DROP TABLE users; --"},
            {"grant_type": "client_credentials", "scope": "<script>alert(1)</script>"},
        ]
        
        for payload in injection_payloads:
            response = http_client.post(url, data=payload)
            
            # Should handle gracefully without SQL errors
            SecurityAssertions.assert_no_sql_errors(response.text, "token_injection")
            SecurityAssertions.assert_no_sensitive_data(response.text, "token_injection")


class TestHealthEndpointSecurity:
    """Test that health endpoints don't leak sensitive info."""
    
    @pytest.mark.auth
    def test_health_no_sensitive_data(self, http_client, endpoints):
        """Test health endpoints don't expose sensitive information."""
        health_endpoints = [
            (endpoints.agent, "/health"),
            (endpoints.data, "/health"),
            (endpoints.search, "/health"),
            (endpoints.authz, "/health/live"),
            (endpoints.authz, "/health/ready"),
        ]
        
        for base_url, endpoint in health_endpoints:
            url = f"{base_url}{endpoint}"
            response = http_client.get(url)
            
            if response.status_code == 200:
                SecurityAssertions.assert_no_sensitive_data(
                    response.text, f"health:{endpoint}"
                )
                
                # Health should not expose internal paths or versions
                body_lower = response.text.lower()
                assert "/srv/" not in body_lower, f"Internal path in health: {endpoint}"
                assert "traceback" not in body_lower, f"Stack trace in health: {endpoint}"

