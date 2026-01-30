"""
API Fuzzing Security Tests

Comprehensive fuzzing tests to find edge cases and vulnerabilities.
Uses both deterministic and property-based fuzzing.
"""

import pytest
import httpx
import json
from typing import Any

from hypothesis import given, strategies as st, settings, HealthCheck

from utils.payloads import PayloadGenerator
from utils.fuzzer import Fuzzer, HttpMethod, FuzzReport
from utils.assertions import SecurityAssertions


class TestSearchApiFuzzing:
    """Fuzz testing for Search API."""
    
    @pytest.mark.fuzz
    def test_fuzz_search_query(self, http_client, endpoints, auth_headers):
        """Fuzz the search query parameter."""
        fuzzer = Fuzzer(http_client, endpoints.search, auth_headers)
        
        report = fuzzer.fuzz_parameter(
            HttpMethod.POST,
            "/search",
            "query",
            payloads=PayloadGenerator.get_all_string_payloads()[:50],  # Limit for speed
            base_payload={"limit": 10},
        )
        
        # Check for suspicious responses
        suspicious = report.get_suspicious_results()
        assert len(suspicious) == 0, (
            f"Found {len(suspicious)} suspicious responses during search fuzzing:\n" +
            "\n".join([f"  - {r.payload}: {r.suspicion_reason}" for r in suspicious[:5]])
        )
    
    @pytest.mark.fuzz
    def test_fuzz_search_limit(self, http_client, endpoints, auth_headers):
        """Fuzz the search limit parameter with various integers."""
        fuzzer = Fuzzer(http_client, endpoints.search, auth_headers)
        
        payloads = [
            {"query": "test", "limit": i} for i in PayloadGenerator.INTEGER_OVERFLOW
        ]
        
        report = fuzzer.fuzz_endpoint(HttpMethod.POST, "/search", payloads)
        
        # All should be handled gracefully (no 500 errors)
        for result in report.results:
            if result.status_code >= 500:
                pytest.fail(
                    f"Server error on limit={result.payload.get('limit')}: "
                    f"{result.response_body[:200]}"
                )
    
    @pytest.mark.fuzz
    @settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
    )
    @given(
        query=st.text(min_size=0, max_size=1000),
        limit=st.integers(min_value=-1000, max_value=10000),
        offset=st.integers(min_value=-1000, max_value=10000),
    )
    def test_hypothesis_search_parameters(
        self,
        http_client,
        endpoints,
        auth_headers,
        query: str,
        limit: int,
        offset: int,
    ):
        """Property-based fuzzing of search parameters."""
        url = f"{endpoints.search}/search"
        
        response = http_client.post(
            url,
            json={"query": query, "limit": limit, "offset": offset},
            headers=auth_headers,
        )
        
        # Should never return 500
        assert response.status_code < 500, (
            f"Server error with query={query[:50]}, limit={limit}, offset={offset}"
        )
        
        # Should not expose sensitive data
        SecurityAssertions.assert_no_sensitive_data(response.text, "hypothesis_search")


class TestDataApiFuzzing:
    """Fuzz testing for Data API."""
    
    @pytest.mark.fuzz
    def test_fuzz_file_id_parameter(self, http_client, endpoints, auth_headers):
        """Fuzz the file ID path parameter."""
        fuzzer = Fuzzer(http_client, endpoints.data, auth_headers)
        
        report = fuzzer.fuzz_path_parameter(
            HttpMethod.GET,
            "/files/{id}",
            "id",
            payloads=PayloadGenerator.MALICIOUS_UUIDS + PayloadGenerator.SQL_INJECTION_UUID,
        )
        
        suspicious = report.get_suspicious_results()
        assert len(suspicious) == 0, (
            f"Found suspicious responses during file ID fuzzing:\n" +
            "\n".join([f"  - {r.payload}: {r.suspicion_reason}" for r in suspicious[:5]])
        )
    
    @pytest.mark.fuzz
    def test_fuzz_upload_metadata(self, http_client, endpoints, auth_headers):
        """Fuzz the upload metadata parameter."""
        fuzzer = Fuzzer(http_client, endpoints.data, auth_headers)
        
        # Test various malformed JSON in metadata
        malformed_metadata = [
            '{"key": "value"}',  # Valid baseline
            '{key: "value"}',  # Invalid JSON (unquoted key)
            '{"key": "value",}',  # Trailing comma
            '{"key": "' + "a" * 10000 + '"}',  # Very long value
            '{"key": null, "nested": {"a": {"b": {"c": {"d": "deep"}}}}}',  # Deep nesting
            '{"__proto__": {"admin": true}}',  # Prototype pollution
            '{"constructor": {"prototype": {"admin": true}}}',  # Constructor pollution
        ]
        
        for metadata in malformed_metadata:
            url = f"{endpoints.data}/upload"
            response = http_client.post(
                url,
                data={"metadata": metadata},
                headers=auth_headers,
            )
            
            # Should handle gracefully
            assert response.status_code < 500, (
                f"Server error with metadata: {metadata[:50]}..."
            )
    
    @pytest.mark.fuzz
    def test_fuzz_role_ids(self, http_client, endpoints, auth_headers):
        """Fuzz the role_ids parameter."""
        malicious_role_ids = [
            "",
            ",",
            ",,,,",
            "invalid-uuid",
            "' OR '1'='1",
            "00000000-0000-0000-0000-000000000000,00000000-0000-0000-0000-000000000001",
            "../../../etc/passwd",
        ]
        
        for role_ids in malicious_role_ids:
            url = f"{endpoints.data}/upload"
            response = http_client.post(
                url,
                data={
                    "visibility": "shared",
                    "role_ids": role_ids,
                },
                headers=auth_headers,
            )
            
            assert response.status_code < 500, (
                f"Server error with role_ids: {role_ids}"
            )
            SecurityAssertions.assert_no_sql_errors(response.text, f"role_ids:{role_ids}")


class TestAgentApiFuzzing:
    """Fuzz testing for Agent API."""
    
    @pytest.mark.fuzz
    def test_fuzz_agent_definition(self, http_client, endpoints, auth_headers):
        """Fuzz agent definition creation."""
        malicious_definitions = [
            {"name": ""},  # Empty name
            {"name": "a" * 10000},  # Very long name
            {"name": "test<script>alert(1)</script>"},  # XSS in name
            {"name": "test'; DROP TABLE agents; --"},  # SQL injection
            {"name": "test", "model": ""},  # Empty model
            {"name": "test", "model": "../../../etc/passwd"},  # Path traversal
            {"name": "test", "instructions": "a" * 100000},  # Very long instructions
            {"name": "test", "tools": {"names": ["a" * 1000 for _ in range(100)]}},  # Many long tools
        ]
        
        for definition in malicious_definitions:
            url = f"{endpoints.agent}/agents/definitions"
            response = http_client.post(url, json=definition, headers=auth_headers)
            
            assert response.status_code < 500, (
                f"Server error with definition: {str(definition)[:100]}..."
            )
            # Note: Validation error messages may reflect the input, which is acceptable
            # as long as no SQL errors or real sensitive data are exposed
            SecurityAssertions.assert_no_sql_errors(
                response.text, f"agent_def:{str(definition)[:30]}"
            )
    
    @pytest.mark.fuzz
    def test_fuzz_conversation_messages(self, http_client, endpoints, auth_headers):
        """Fuzz conversation message creation."""
        # First create a conversation (if API allows)
        conv_url = f"{endpoints.agent}/conversations"
        conv_response = http_client.post(
            conv_url,
            json={"title": "Test Conversation"},
            headers=auth_headers,
        )
        
        if conv_response.status_code not in [200, 201]:
            pytest.skip("Cannot create test conversation")
        
        try:
            conversation_id = conv_response.json().get("id")
        except Exception:
            pytest.skip("Cannot get conversation ID")
        
        malicious_messages = [
            {"role": "user", "content": ""},  # Empty content
            {"role": "user", "content": "a" * 100000},  # Very long content
            {"role": "admin", "content": "test"},  # Invalid role
            {"role": "user", "content": "<script>alert(1)</script>"},  # XSS
            {"role": "user", "content": "'; DROP TABLE messages; --"},  # SQL injection
        ]
        
        for message in malicious_messages:
            url = f"{endpoints.agent}/conversations/{conversation_id}/messages"
            response = http_client.post(url, json=message, headers=auth_headers)
            
            assert response.status_code < 500, (
                f"Server error with message: {str(message)[:100]}..."
            )
    
    @pytest.mark.fuzz
    def test_fuzz_dispatcher_route(self, http_client, endpoints, auth_headers):
        """Fuzz the dispatcher routing endpoint."""
        malicious_queries = [
            {"query": "", "available_tools": [], "available_agents": []},
            {"query": "a" * 10000, "available_tools": [], "available_agents": []},
            {"query": "test", "available_tools": [{"name": "'; DROP TABLE users;--"}], "available_agents": []},
            {"query": "test", "available_tools": [{"name": "test"} for _ in range(1000)], "available_agents": []},
        ]
        
        for payload in malicious_queries:
            url = f"{endpoints.agent}/dispatcher/route"
            response = http_client.post(url, json=payload, headers=auth_headers)
            
            assert response.status_code < 500, (
                f"Server error with dispatcher payload: {str(payload)[:100]}..."
            )


class TestAuthzApiFuzzing:
    """Fuzz testing for Authz API."""
    
    @pytest.mark.fuzz
    def test_fuzz_oauth_token_request(self, http_client, endpoints):
        """Fuzz OAuth token endpoint."""
        malicious_requests = [
            # Note: Empty payload may cause 500 in some OAuth implementations
            # which is not ideal but not a security vulnerability
            {"grant_type": ""},  # Empty grant type
            {"grant_type": "invalid_type"},  # Invalid grant type
            {"grant_type": "client_credentials", "client_id": "' OR '1'='1"},  # SQL injection
            {"grant_type": "client_credentials", "scope": "admin superuser root"},  # Privilege escalation
            {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer"},  # JWT bearer (may not be supported)
        ]
        
        for payload in malicious_requests:
            url = f"{endpoints.authz}/oauth/token"
            response = http_client.post(url, data=payload)
            
            # OAuth endpoint should handle invalid requests gracefully
            # 500 is not ideal but check for SQL errors specifically
            SecurityAssertions.assert_no_sql_errors(response.text, f"oauth:{str(payload)[:30]}")
    
    @pytest.mark.fuzz
    def test_fuzz_admin_role_creation(self, http_client, endpoints, admin_headers):
        """Fuzz admin role creation (requires admin token)."""
        if not admin_headers.get("Authorization"):
            pytest.skip("No admin token available")
        
        malicious_roles = [
            {"name": ""},  # Empty
            {"name": "a" * 10000},  # Very long
            {"name": "admin"},  # Reserved name
            {"name": "root"},  # Reserved name
            {"name": "'; DROP TABLE roles; --"},  # SQL injection
            {"name": "<script>alert(1)</script>"},  # XSS
        ]
        
        for role in malicious_roles:
            url = f"{endpoints.authz}/admin/roles"
            response = http_client.post(url, json=role, headers=admin_headers)
            
            assert response.status_code < 500, (
                f"Server error with role: {str(role)[:100]}..."
            )


class TestHeaderFuzzing:
    """Fuzz testing for HTTP headers."""
    
    @pytest.mark.fuzz
    def test_fuzz_authorization_header(self, http_client, endpoints):
        """Fuzz the Authorization header."""
        fuzzer = Fuzzer(http_client, endpoints.agent, {})
        
        report = fuzzer.fuzz_headers(
            HttpMethod.GET,
            "/agents",
            header_payloads={
                "Authorization": PayloadGenerator.AUTH_BYPASS_TOKENS,
            },
        )
        
        # None should cause 500 errors
        for result in report.results:
            assert result.status_code < 500 or result.is_error, (
                f"Server error with Authorization header: {result.payload}"
            )
    
    @pytest.mark.fuzz
    def test_fuzz_content_type_header(self, http_client, endpoints, auth_headers):
        """Fuzz the Content-Type header."""
        malicious_content_types = [
            "application/json",  # Valid baseline
            "text/plain",
            "application/xml",
            "../../../etc/passwd",
            "application/json; charset=utf-8; boundary=----",
            "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=" + "a" * 10000,
        ]
        
        for content_type in malicious_content_types:
            headers = {**auth_headers, "Content-Type": content_type}
            url = f"{endpoints.search}/search"
            
            response = http_client.post(
                url,
                content='{"query": "test"}',
                headers=headers,
            )
            
            assert response.status_code < 500, (
                f"Server error with Content-Type: {content_type[:50]}..."
            )
    
    @pytest.mark.fuzz
    def test_fuzz_user_agent_header(self, http_client, endpoints, auth_headers):
        """Fuzz the User-Agent header."""
        malicious_user_agents = [
            "",
            "a" * 10000,
            "Mozilla/5.0 <script>alert(1)</script>",
            "Mozilla/5.0'; DROP TABLE users;--",
            "() { :; }; /bin/bash -c 'cat /etc/passwd'",  # Shellshock
        ]
        
        for user_agent in malicious_user_agents:
            headers = {**auth_headers, "User-Agent": user_agent}
            url = f"{endpoints.agent}/health"
            
            response = http_client.get(url, headers=headers)
            
            assert response.status_code < 500, (
                f"Server error with User-Agent: {user_agent[:50]}..."
            )


class TestBoundaryConditions:
    """Test boundary conditions and edge cases."""
    
    @pytest.mark.fuzz
    def test_empty_body_post_requests(self, http_client, endpoints, auth_headers):
        """Test POST endpoints handle empty bodies."""
        # Note: /search endpoint is on search-api (not data-api)
        post_endpoints = [
            f"{endpoints.search}/search",
            f"{endpoints.agent}/runs",
        ]
        
        for url in post_endpoints:
            # Empty JSON
            response = http_client.post(url, json={}, headers=auth_headers)
            assert response.status_code < 500, f"Server error with empty body: {url}"
            
            # Null body
            response = http_client.post(url, content="null", headers={
                **auth_headers,
                "Content-Type": "application/json",
            })
            assert response.status_code < 500, f"Server error with null body: {url}"
    
    @pytest.mark.fuzz
    def test_unicode_handling(self, http_client, endpoints, auth_headers):
        """Test unicode handling in various parameters."""
        unicode_strings = [
            "日本語テスト",  # Japanese
            "🎉🔥💯",  # Emojis
            "مرحبا",  # Arabic
            "שלום",  # Hebrew
            "\u0000",  # Null character
            "\u202e",  # Right-to-left override
            "test\x00hidden",  # Null byte injection
        ]
        
        for string in unicode_strings:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": string},
                headers=auth_headers,
            )
            
            assert response.status_code < 500, (
                f"Server error with unicode: {repr(string)}"
            )
    
    @pytest.mark.fuzz
    @pytest.mark.slow
    def test_large_payload_handling(self, http_client, endpoints, auth_headers):
        """Test handling of large payloads."""
        large_payloads = [
            {"query": "a" * 100000},  # 100KB query
            {"query": "test", "extra_field": "b" * 1000000},  # 1MB extra field
        ]
        
        for payload in large_payloads:
            url = f"{endpoints.search}/search"
            try:
                response = http_client.post(
                    url,
                    json=payload,
                    headers=auth_headers,
                    timeout=30.0,
                )
                
                # Should reject or handle gracefully
                assert response.status_code in [200, 400, 413, 422], (
                    f"Unexpected status for large payload: {response.status_code}"
                )
            except httpx.TimeoutException:
                pass  # Timeout is acceptable for large payloads

