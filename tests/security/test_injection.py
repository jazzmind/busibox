"""
Injection Attack Security Tests

OWASP API Security Top 10 Coverage:
- API8:2023 Security Misconfiguration
- API10:2023 Unsafe Consumption of APIs

Tests for SQL injection, NoSQL injection, Command injection, and more.
"""

import pytest
import httpx
import time

from utils.payloads import PayloadGenerator
from utils.fuzzer import Fuzzer, HttpMethod
from utils.assertions import SecurityAssertions


class TestSQLInjection:
    """Test SQL injection vulnerabilities."""
    
    @pytest.mark.injection
    def test_search_query_sql_injection(self, http_client, endpoints, auth_headers):
        """Test search query doesn't allow SQL injection."""
        payloads = PayloadGenerator.SQL_INJECTION_BASIC
        
        for payload in payloads:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": payload},
                headers=auth_headers,
            )
            
            # Should not return SQL error
            SecurityAssertions.assert_no_sql_errors(
                response.text, f"search_sql_injection:{payload[:30]}"
            )
            
            # Should handle gracefully
            assert response.status_code in [200, 400, 401, 403, 422], (
                f"Unexpected status for SQL injection: {response.status_code}"
            )
    
    @pytest.mark.injection
    def test_file_id_sql_injection(self, http_client, endpoints, auth_headers):
        """Test file ID parameter doesn't allow SQL injection."""
        payloads = PayloadGenerator.SQL_INJECTION_UUID
        
        for payload in payloads:
            url = f"{endpoints.data}/files/{payload}"
            response = http_client.get(url, headers=auth_headers)
            
            SecurityAssertions.assert_no_sql_errors(
                response.text, f"file_id_sql_injection:{payload[:30]}"
            )
    
    @pytest.mark.injection
    def test_conversation_id_sql_injection(self, http_client, endpoints, auth_headers):
        """Test conversation ID doesn't allow SQL injection."""
        payloads = PayloadGenerator.SQL_INJECTION_UUID
        
        for payload in payloads:
            url = f"{endpoints.agent}/conversations/{payload}"
            response = http_client.get(url, headers=auth_headers)
            
            SecurityAssertions.assert_no_sql_errors(
                response.text, f"conversation_sql_injection:{payload[:30]}"
            )
    
    @pytest.mark.injection
    @pytest.mark.slow
    def test_blind_sql_injection_timing(self, http_client, endpoints, auth_headers):
        """Test for time-based blind SQL injection."""
        # First, get baseline timing
        url = f"{endpoints.search}/search"
        
        start = time.time()
        response = http_client.post(
            url,
            json={"query": "normal search query"},
            headers=auth_headers,
            timeout=30.0,
        )
        baseline_ms = (time.time() - start) * 1000
        
        # Now test timing payloads
        timing_payloads = [
            "1'; SELECT SLEEP(5); --",
            "1'; SELECT pg_sleep(5); --",
            "1' AND (SELECT * FROM (SELECT(SLEEP(5)))a)--",
        ]
        
        for payload in timing_payloads:
            start = time.time()
            try:
                response = http_client.post(
                    url,
                    json={"query": payload},
                    headers=auth_headers,
                    timeout=30.0,
                )
                elapsed_ms = (time.time() - start) * 1000
                
                # If response took much longer than baseline, could be vulnerable
                # Allow 3x baseline as normal variance
                if elapsed_ms > 5000 and elapsed_ms > baseline_ms * 3:
                    pytest.fail(
                        f"Possible blind SQL injection: {payload[:30]}... "
                        f"took {elapsed_ms}ms vs baseline {baseline_ms}ms"
                    )
            except httpx.TimeoutException:
                pytest.fail(
                    f"Timeout on SQL injection payload - possible vulnerability: {payload[:30]}..."
                )


class TestNoSQLInjection:
    """Test NoSQL injection vulnerabilities."""
    
    @pytest.mark.injection
    def test_nosql_query_injection(self, http_client, endpoints, auth_headers):
        """Test search doesn't allow NoSQL injection."""
        payloads = PayloadGenerator.NOSQL_INJECTION_STRINGS
        
        for payload in payloads:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": payload},
                headers=auth_headers,
            )
            
            # Should not expose NoSQL errors
            body_lower = response.text.lower()
            assert "mongodb" not in body_lower, f"MongoDB error exposed: {payload[:30]}"
            assert "bson" not in body_lower, f"BSON error exposed: {payload[:30]}"
    
    @pytest.mark.injection
    def test_nosql_object_injection(self, http_client, endpoints, auth_headers):
        """Test JSON body doesn't allow NoSQL operator injection."""
        # Try to inject NoSQL operators in JSON body
        malicious_payloads = [
            {"query": {"$gt": ""}, "limit": 10},
            {"query": {"$ne": None}, "limit": 10},
            {"query": {"$regex": ".*"}, "limit": 10},
        ]
        
        url = f"{endpoints.search}/search"
        
        for payload in malicious_payloads:
            response = http_client.post(url, json=payload, headers=auth_headers)
            
            # Should reject or handle safely
            assert response.status_code in [200, 400, 422], (
                f"Unexpected response to NoSQL injection: {response.status_code}"
            )


class TestCommandInjection:
    """Test command injection vulnerabilities."""
    
    @pytest.mark.injection
    def test_filename_command_injection(self, http_client, endpoints, auth_headers):
        """Test filename handling doesn't allow command injection."""
        payloads = PayloadGenerator.COMMAND_INJECTION
        
        # Test via file metadata endpoint
        for payload in payloads:
            # URL-encode the payload for path
            import urllib.parse
            encoded = urllib.parse.quote(payload, safe="")
            
            url = f"{endpoints.data}/files/{encoded}"
            response = http_client.get(url, headers=auth_headers)
            
            # Should handle gracefully without command execution
            body_lower = response.text.lower()
            assert "uid=" not in body_lower, f"Command executed: {payload[:30]}"
            assert "root:" not in body_lower, f"Command executed: {payload[:30]}"
            assert "bin/bash" not in body_lower, f"Command executed: {payload[:30]}"
    
    @pytest.mark.injection
    def test_export_format_command_injection(self, http_client, endpoints, auth_headers):
        """Test export format parameter doesn't allow command injection."""
        payloads = PayloadGenerator.COMMAND_INJECTION[:5]  # Test subset
        
        file_id = "00000000-0000-0000-0000-000000000000"
        
        for payload in payloads:
            url = f"{endpoints.data}/files/{file_id}/export"
            response = http_client.get(
                url,
                params={"format": payload},
                headers=auth_headers,
            )
            
            # Should reject - 401 = auth check first (still secure)
            assert response.status_code in [400, 401, 403, 404, 422], (
                f"Invalid format not rejected: {payload[:30]}"
            )


class TestPathTraversal:
    """Test path traversal vulnerabilities."""
    
    @pytest.mark.injection
    def test_file_path_traversal(self, http_client, endpoints, auth_headers):
        """Test file endpoints don't allow path traversal."""
        payloads = PayloadGenerator.PATH_TRAVERSAL
        
        for payload in payloads:
            import urllib.parse
            encoded = urllib.parse.quote(payload, safe="")
            
            url = f"{endpoints.data}/files/{encoded}"
            response = http_client.get(url, headers=auth_headers)
            
            # Should not expose system files
            body_lower = response.text.lower()
            assert "root:" not in body_lower, f"Path traversal exposed /etc/passwd: {payload}"
            assert "daemon:" not in body_lower, f"Path traversal exposed /etc/passwd: {payload}"
            
            # Should not expose internal file content
            assert "/srv/" not in body_lower, f"Internal path exposed: {payload}"
    
    @pytest.mark.injection
    def test_download_path_traversal(self, http_client, endpoints, auth_headers):
        """Test download endpoint doesn't allow path traversal."""
        payloads = PayloadGenerator.PATH_TRAVERSAL
        
        for payload in payloads:
            import urllib.parse
            encoded = urllib.parse.quote(payload, safe="")
            
            url = f"{endpoints.data}/files/{encoded}/download"
            response = http_client.get(url, headers=auth_headers)
            
            # Should return error, not file content - 401 = auth check first (still secure)
            assert response.status_code in [400, 401, 403, 404, 422], (
                f"Path traversal not blocked: {payload}"
            )


class TestXSSVulnerabilities:
    """Test Cross-Site Scripting vulnerabilities in API responses."""
    
    @pytest.mark.injection
    def test_search_query_reflection(self, http_client, endpoints, auth_headers):
        """Test search query isn't reflected unsafely in response."""
        payloads = PayloadGenerator.XSS_BASIC
        
        for payload in payloads:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": payload},
                headers=auth_headers,
            )
            
            # XSS payloads should be escaped or not reflected
            if response.status_code == 200:
                # Check for direct reflection
                if "<script>" in payload:
                    assert "<script>" not in response.text, (
                        f"XSS payload reflected: {payload[:30]}"
                    )
    
    @pytest.mark.injection
    def test_error_message_reflection(self, http_client, endpoints, auth_headers):
        """Test error messages don't reflect user input unsafely."""
        xss_payload = "<script>alert('XSS')</script>"
        
        # Try to trigger error with XSS in various parameters
        endpoints_to_test = [
            (f"{endpoints.agent}/agents/{xss_payload}", "GET"),
            (f"{endpoints.data}/files/{xss_payload}", "GET"),
        ]
        
        for url, method in endpoints_to_test:
            if method == "GET":
                response = http_client.get(url, headers=auth_headers)
            
            # XSS should not be reflected in error message
            SecurityAssertions.assert_no_reflection(
                response.text, xss_payload, f"error_reflection:{url}"
            )


class TestHeaderInjection:
    """Test HTTP header injection vulnerabilities."""
    
    @pytest.mark.injection
    def test_header_injection_in_parameters(self, http_client, endpoints, auth_headers):
        """Test parameters don't allow header injection."""
        payloads = PayloadGenerator.HEADER_INJECTION
        
        for payload in payloads:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": payload},
                headers=auth_headers,
            )
            
            # Check response headers for injected content
            for header_name in response.headers:
                assert "injected" not in header_name.lower(), (
                    f"Header injection succeeded: {payload[:30]}"
                )


class TestSSRFVulnerabilities:
    """Test Server-Side Request Forgery vulnerabilities."""
    
    @pytest.mark.injection
    def test_ssrf_in_url_parameters(self, http_client, endpoints, auth_headers):
        """Test URL parameters don't allow SSRF."""
        payloads = PayloadGenerator.SSRF_PAYLOADS[:5]  # Test subset
        
        # If there's a URL parameter anywhere, test it
        # For now, test via metadata that might be processed
        for payload in payloads:
            url = f"{endpoints.data}/upload"
            
            # Try to inject SSRF via metadata
            response = http_client.post(
                url,
                data={
                    "metadata": f'{{"url": "{payload}"}}',
                },
                headers=auth_headers,
            )
            
            # Should not make requests to internal URLs
            # This is hard to detect from response, but at least shouldn't error with internal info
            if response.status_code == 500:
                SecurityAssertions.assert_no_sensitive_data(
                    response.text, f"ssrf:{payload[:30]}"
                )


class TestLDAPInjection:
    """Test LDAP injection vulnerabilities (if LDAP is used)."""
    
    @pytest.mark.injection
    def test_ldap_injection_in_search(self, http_client, endpoints, auth_headers):
        """Test search doesn't allow LDAP injection."""
        payloads = PayloadGenerator.LDAP_INJECTION
        
        for payload in payloads:
            url = f"{endpoints.search}/search"
            response = http_client.post(
                url,
                json={"query": payload},
                headers=auth_headers,
            )
            
            # Should handle gracefully without LDAP errors
            body_lower = response.text.lower()
            assert "ldap" not in body_lower, f"LDAP error exposed: {payload}"

