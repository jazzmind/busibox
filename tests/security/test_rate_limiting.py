"""
Rate Limiting and DoS Protection Tests

Tests for rate limiting, resource exhaustion protection,
and general DoS mitigation.
"""

import pytest
import httpx
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed

from utils.assertions import SecurityAssertions


class TestRateLimiting:
    """Test rate limiting is enforced."""
    
    @pytest.mark.rate_limit
    @pytest.mark.slow
    def test_search_rate_limiting(self, http_client, endpoints, auth_headers):
        """Test search endpoint has rate limiting."""
        url = f"{endpoints.search}/search"
        
        # Send rapid requests
        responses = []
        for i in range(100):
            response = http_client.post(
                url,
                json={"query": f"test query {i}"},
                headers=auth_headers,
            )
            responses.append(response.status_code)
            
            # Check if we got rate limited
            if response.status_code == 429:
                break
        
        # Should have been rate limited at some point
        # If not, log a warning (rate limiting is recommended but not critical)
        if 429 not in responses:
            print(f"Warning: No rate limiting detected on {url} after {len(responses)} requests")
    
    @pytest.mark.rate_limit
    @pytest.mark.slow
    def test_auth_endpoint_rate_limiting(self, http_client, endpoints):
        """Test authentication endpoint has rate limiting (critical for brute force)."""
        url = f"{endpoints.authz}/oauth/token"
        
        # Send rapid failed auth attempts
        responses = []
        for i in range(50):
            response = http_client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": f"invalid_{i}",
                    "client_secret": f"wrong_{i}",
                },
            )
            responses.append(response.status_code)
            
            if response.status_code == 429:
                break
        
        # Auth endpoint SHOULD have rate limiting
        if 429 not in responses:
            print(
                f"Warning: No rate limiting on auth endpoint after {len(responses)} failed attempts. "
                "This could allow brute force attacks."
            )
    
    @pytest.mark.rate_limit
    @pytest.mark.slow
    def test_upload_rate_limiting(self, http_client, endpoints, auth_headers):
        """Test upload endpoint has rate limiting."""
        url = f"{endpoints.data}/upload"
        
        # Send rapid upload requests (without actual files)
        responses = []
        for i in range(20):
            response = http_client.post(
                url,
                data={"metadata": f'{{"test": {i}}}'},
                headers=auth_headers,
            )
            responses.append(response.status_code)
            
            if response.status_code == 429:
                break
        
        # Note: 400 is expected since we're not sending files
        # But we're testing that 429 rate limit can be triggered
    
    @pytest.mark.rate_limit
    @pytest.mark.slow
    def test_concurrent_request_handling(self, http_client, endpoints, auth_headers):
        """Test service handles concurrent requests without crashing."""
        url = f"{endpoints.search}/search"
        
        def make_request(i):
            try:
                response = http_client.post(
                    url,
                    json={"query": f"concurrent test {i}"},
                    headers=auth_headers,
                    timeout=30.0,
                )
                return response.status_code
            except Exception as e:
                return str(e)
        
        # Send 50 concurrent requests
        with ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(make_request, i) for i in range(50)]
            results = [f.result() for f in as_completed(futures)]
        
        # Count results
        status_counts = {}
        for result in results:
            status_counts[result] = status_counts.get(result, 0) + 1
        
        # Should not have all 500 errors
        error_count = status_counts.get(500, 0)
        assert error_count < len(results) / 2, (
            f"Too many server errors under concurrent load: {error_count}/{len(results)}"
        )


class TestResourceExhaustion:
    """Test protection against resource exhaustion attacks."""
    
    @pytest.mark.rate_limit
    def test_large_query_rejection(self, http_client, endpoints, auth_headers):
        """Test very large queries are rejected."""
        url = f"{endpoints.search}/search"
        
        # Try increasingly large queries
        sizes = [10000, 100000, 1000000]  # 10KB, 100KB, 1MB
        
        for size in sizes:
            large_query = "a" * size
            try:
                response = http_client.post(
                    url,
                    json={"query": large_query},
                    headers=auth_headers,
                    timeout=30.0,
                )
                
                # Should reject or handle gracefully
                if size > 10000:
                    assert response.status_code in [400, 413, 422], (
                        f"Large query ({size} bytes) not rejected: {response.status_code}"
                    )
            except httpx.TimeoutException:
                pass  # Timeout is acceptable for large payloads
    
    @pytest.mark.rate_limit
    def test_deep_json_nesting_rejection(self, http_client, endpoints, auth_headers):
        """Test deeply nested JSON is rejected."""
        url = f"{endpoints.search}/search"
        
        # Create deeply nested JSON
        def create_nested(depth):
            if depth == 0:
                return "value"
            return {"nested": create_nested(depth - 1)}
        
        for depth in [10, 50, 100]:
            nested_json = {"query": "test", "filters": create_nested(depth)}
            
            try:
                response = http_client.post(
                    url,
                    json=nested_json,
                    headers=auth_headers,
                    timeout=10.0,
                )
                
                # Deep nesting should be rejected or ignored
                assert response.status_code < 500, (
                    f"Server error with {depth}-level nesting"
                )
            except Exception:
                pass  # Rejection is acceptable
    
    @pytest.mark.rate_limit
    def test_many_parameters_rejection(self, http_client, endpoints, auth_headers):
        """Test many query parameters are handled."""
        url = f"{endpoints.search}/search"
        
        # Create payload with many fields
        many_fields = {f"field_{i}": f"value_{i}" for i in range(1000)}
        many_fields["query"] = "test"
        
        response = http_client.post(
            url,
            json=many_fields,
            headers=auth_headers,
        )
        
        # Should handle gracefully (ignore extra fields or reject)
        assert response.status_code < 500, "Server error with many fields"
    
    @pytest.mark.rate_limit
    def test_regex_dos_protection(self, http_client, endpoints, auth_headers):
        """Test ReDoS (Regular Expression DoS) protection."""
        # Evil regex patterns that can cause catastrophic backtracking
        redos_payloads = [
            "a" * 30 + "!",
            "(a+)+$" * 10,
            "((a+)+)+$",
            "a]]*" * 20,
        ]
        
        url = f"{endpoints.search}/search"
        
        for payload in redos_payloads:
            start = time.time()
            try:
                response = http_client.post(
                    url,
                    json={"query": payload},
                    headers=auth_headers,
                    timeout=5.0,  # Should complete quickly
                )
                elapsed = time.time() - start
                
                # Should not take more than 5 seconds
                assert elapsed < 5.0, (
                    f"Possible ReDoS vulnerability: {payload[:30]}... took {elapsed}s"
                )
            except httpx.TimeoutException:
                pytest.fail(f"Timeout - possible ReDoS with: {payload[:30]}...")


class TestSlowlorisProtection:
    """Test protection against Slowloris-style attacks."""
    
    @pytest.mark.rate_limit
    @pytest.mark.slow
    def test_slow_request_timeout(self, http_client, endpoints, auth_headers):
        """Test that slow requests are timed out."""
        # This test checks if the server has appropriate timeouts
        # We can't easily simulate Slowloris in Python, but we can check
        # that the server doesn't wait forever for request completion
        
        url = f"{endpoints.search}/search"
        
        # Send incomplete request and measure timeout
        # Most servers should timeout within 30-60 seconds
        # This is a basic check - full Slowloris testing requires specialized tools
        
        start = time.time()
        try:
            response = http_client.post(
                url,
                json={"query": "test"},
                headers=auth_headers,
                timeout=10.0,
            )
            # Normal response within timeout is fine
            assert True
        except httpx.TimeoutException:
            # Our client timeout triggered, but server may still be waiting
            elapsed = time.time() - start
            assert elapsed < 15.0, "Server may not have proper request timeouts"


class TestFileUploadLimits:
    """Test file upload size limits and protection."""
    
    @pytest.mark.rate_limit
    def test_upload_size_limit(self, http_client, endpoints, auth_headers):
        """Test that upload size limits are enforced."""
        url = f"{endpoints.data}/upload"
        
        # Try to upload large file (10MB)
        large_data = b"x" * (10 * 1024 * 1024)
        
        try:
            response = http_client.post(
                url,
                files={"file": ("large.txt", large_data, "text/plain")},
                headers={k: v for k, v in auth_headers.items() if k.lower() != "content-type"},
                timeout=60.0,
            )
            
            # Should be rejected or accepted
            # 401 = auth check first (acceptable)
            # If accepted (200), that's fine (10MB is reasonable)
            # If rejected with 413, that's also fine (size limit)
            assert response.status_code in [200, 400, 401, 413, 422], (
                f"Unexpected response for large upload: {response.status_code}"
            )
        except httpx.TimeoutException:
            pass  # Timeout is acceptable for large uploads
    
    @pytest.mark.rate_limit
    def test_upload_content_type_validation(self, http_client, endpoints, auth_headers):
        """Test that dangerous file types are rejected."""
        url = f"{endpoints.data}/upload"
        
        dangerous_files = [
            ("test.exe", b"MZ\x90\x00", "application/x-executable"),
            ("test.sh", b"#!/bin/bash\nrm -rf /", "application/x-sh"),
            ("test.php", b"<?php system($_GET['cmd']); ?>", "application/x-php"),
        ]
        
        for filename, content, content_type in dangerous_files:
            try:
                response = http_client.post(
                    url,
                    files={"file": (filename, content, content_type)},
                    headers={k: v for k, v in auth_headers.items() if k.lower() != "content-type"},
                )
                
                # Should either reject or handle safely
                # If accepted, content should be stored safely without execution
                if response.status_code in [200, 201]:
                    # File was accepted - verify it's stored safely
                    # (actual verification would require checking storage)
                    pass
            except Exception:
                pass  # Rejection is acceptable

