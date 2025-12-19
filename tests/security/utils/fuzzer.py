"""
API Fuzzer

Automated fuzzing utilities for API security testing.
"""

import asyncio
import json
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from enum import Enum
import time

import httpx
from hypothesis import given, strategies as st, settings

from .payloads import PayloadGenerator


class HttpMethod(Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


@dataclass
class FuzzResult:
    """Result of a single fuzz test."""
    endpoint: str
    method: str
    payload: Any
    status_code: int
    response_time_ms: float
    response_body: str
    is_error: bool = False
    error_message: str = ""
    is_suspicious: bool = False
    suspicion_reason: str = ""


@dataclass
class FuzzReport:
    """Summary report for fuzzing session."""
    total_requests: int = 0
    successful_requests: int = 0
    error_requests: int = 0
    suspicious_responses: int = 0
    results: List[FuzzResult] = field(default_factory=list)
    
    def add_result(self, result: FuzzResult):
        self.results.append(result)
        self.total_requests += 1
        if result.is_error:
            self.error_requests += 1
        else:
            self.successful_requests += 1
        if result.is_suspicious:
            self.suspicious_responses += 1
    
    def get_suspicious_results(self) -> List[FuzzResult]:
        return [r for r in self.results if r.is_suspicious]


class Fuzzer:
    """API Fuzzer for security testing."""
    
    # Suspicious patterns in responses that may indicate vulnerabilities
    SUSPICIOUS_PATTERNS = [
        # SQL errors
        "sql syntax",
        "mysql",
        "postgresql",
        "sqlite",
        "ora-",
        "syntax error",
        "unclosed quotation",
        "quoted string not properly terminated",
        
        # Stack traces / debug info
        "traceback",
        "stack trace",
        "at line",
        "exception",
        "internal server error",
        "debug",
        
        # Path disclosure
        "/usr/",
        "/var/",
        "/etc/",
        "/home/",
        "c:\\",
        "windows\\",
        
        # Command execution
        "uid=",
        "gid=",
        "root:",
        "bin/bash",
        "command not found",
        
        # Information disclosure
        "password",
        "secret",
        "token",
        "api_key",
        "private_key",
        "aws_secret",
        
        # NoSQL errors
        "mongodb",
        "bson",
        "objectid",
    ]
    
    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.payloads = PayloadGenerator()
    
    def _is_suspicious_response(self, response_body: str) -> Tuple[bool, str]:
        """Check if response contains suspicious patterns."""
        body_lower = response_body.lower()
        for pattern in self.SUSPICIOUS_PATTERNS:
            if pattern.lower() in body_lower:
                return True, f"Found suspicious pattern: {pattern}"
        return False, ""
    
    def _make_request(
        self,
        method: HttpMethod,
        endpoint: str,
        payload: Any = None,
        params: Optional[Dict[str, str]] = None,
    ) -> FuzzResult:
        """Make HTTP request and return result."""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        try:
            if method == HttpMethod.GET:
                response = self.client.get(
                    url, params=params, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.POST:
                response = self.client.post(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.PUT:
                response = self.client.put(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.PATCH:
                response = self.client.patch(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.DELETE:
                response = self.client.delete(
                    url, headers=self.headers, timeout=self.timeout
                )
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response_time = (time.time() - start_time) * 1000
            response_body = response.text
            
            is_suspicious, reason = self._is_suspicious_response(response_body)
            
            # Also flag unexpected 500 errors as suspicious
            if response.status_code >= 500:
                is_suspicious = True
                reason = f"Server error: {response.status_code}"
            
            return FuzzResult(
                endpoint=endpoint,
                method=method.value,
                payload=payload,
                status_code=response.status_code,
                response_time_ms=response_time,
                response_body=response_body[:2000],  # Truncate long responses
                is_suspicious=is_suspicious,
                suspicion_reason=reason,
            )
            
        except Exception as e:
            return FuzzResult(
                endpoint=endpoint,
                method=method.value,
                payload=payload,
                status_code=0,
                response_time_ms=(time.time() - start_time) * 1000,
                response_body="",
                is_error=True,
                error_message=str(e),
            )
    
    def fuzz_parameter(
        self,
        method: HttpMethod,
        endpoint: str,
        param_name: str,
        payloads: Optional[List[Any]] = None,
        base_payload: Optional[Dict[str, Any]] = None,
    ) -> FuzzReport:
        """Fuzz a single parameter with various payloads."""
        report = FuzzReport()
        
        if payloads is None:
            payloads = PayloadGenerator.get_all_string_payloads()
        
        base = base_payload or {}
        
        for payload_value in payloads:
            test_payload = base.copy()
            test_payload[param_name] = payload_value
            
            result = self._make_request(method, endpoint, test_payload)
            report.add_result(result)
        
        return report
    
    def fuzz_endpoint(
        self,
        method: HttpMethod,
        endpoint: str,
        payloads: List[Dict[str, Any]],
    ) -> FuzzReport:
        """Fuzz an endpoint with multiple payloads."""
        report = FuzzReport()
        
        for payload in payloads:
            result = self._make_request(method, endpoint, payload)
            report.add_result(result)
        
        return report
    
    def fuzz_path_parameter(
        self,
        method: HttpMethod,
        endpoint_template: str,
        param_name: str,
        payloads: Optional[List[str]] = None,
    ) -> FuzzReport:
        """Fuzz a path parameter in an endpoint."""
        report = FuzzReport()
        
        if payloads is None:
            payloads = (
                PayloadGenerator.PATH_TRAVERSAL +
                PayloadGenerator.SQL_INJECTION_UUID +
                PayloadGenerator.MALICIOUS_UUIDS
            )
        
        for payload_value in payloads:
            endpoint = endpoint_template.replace(f"{{{param_name}}}", str(payload_value))
            result = self._make_request(method, endpoint)
            report.add_result(result)
        
        return report
    
    def fuzz_headers(
        self,
        method: HttpMethod,
        endpoint: str,
        header_payloads: Optional[Dict[str, List[str]]] = None,
    ) -> FuzzReport:
        """Fuzz request headers."""
        report = FuzzReport()
        
        if header_payloads is None:
            header_payloads = {
                "Authorization": PayloadGenerator.AUTH_BYPASS_TOKENS,
                "X-Forwarded-For": ["127.0.0.1", "localhost", "::1", "0.0.0.0"],
                "X-Real-IP": ["127.0.0.1", "localhost"],
                "X-User-Id": PayloadGenerator.MALICIOUS_UUIDS,
                "Content-Type": [
                    "application/json",
                    "application/xml",
                    "text/html",
                    "../../../etc/passwd",
                ],
            }
        
        for header_name, values in header_payloads.items():
            for value in values:
                modified_headers = self.headers.copy()
                modified_headers[header_name] = value
                
                old_headers = self.headers
                self.headers = modified_headers
                
                result = self._make_request(method, endpoint)
                result.payload = {header_name: value}
                report.add_result(result)
                
                self.headers = old_headers
        
        return report
    
    def test_rate_limiting(
        self,
        method: HttpMethod,
        endpoint: str,
        num_requests: int = 100,
        delay_ms: float = 10,
    ) -> FuzzReport:
        """Test rate limiting by sending rapid requests."""
        report = FuzzReport()
        
        for i in range(num_requests):
            result = self._make_request(method, endpoint)
            report.add_result(result)
            
            # Check if we got rate limited
            if result.status_code == 429:
                result.is_suspicious = False  # Rate limiting is good
                break
            
            time.sleep(delay_ms / 1000)
        
        # If we made all requests without 429, that's suspicious
        if report.total_requests == num_requests:
            report.results[-1].is_suspicious = True
            report.results[-1].suspicion_reason = "No rate limiting detected"
            report.suspicious_responses += 1
        
        return report
    
    def test_method_override(
        self,
        endpoint: str,
        expected_methods: List[HttpMethod],
    ) -> FuzzReport:
        """Test HTTP method override vulnerabilities."""
        report = FuzzReport()
        
        override_headers = [
            ("X-HTTP-Method-Override", "DELETE"),
            ("X-HTTP-Method", "DELETE"),
            ("X-Method-Override", "DELETE"),
        ]
        
        for header_name, header_value in override_headers:
            modified_headers = self.headers.copy()
            modified_headers[header_name] = header_value
            
            old_headers = self.headers
            self.headers = modified_headers
            
            # Send GET but try to override to DELETE
            result = self._make_request(HttpMethod.GET, endpoint)
            result.payload = {header_name: header_value}
            
            # If we got 204 (successful delete) or similar, that's suspicious
            if result.status_code in [200, 204]:
                result.is_suspicious = True
                result.suspicion_reason = f"Method override may have worked via {header_name}"
            
            report.add_result(result)
            self.headers = old_headers
        
        return report


class AsyncFuzzer(Fuzzer):
    """Async version of the API Fuzzer for concurrent testing."""
    
    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
        concurrency: int = 10,
    ):
        self.async_client = client
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}
        self.timeout = timeout
        self.concurrency = concurrency
        self.payloads = PayloadGenerator()
    
    async def _make_request_async(
        self,
        method: HttpMethod,
        endpoint: str,
        payload: Any = None,
        params: Optional[Dict[str, str]] = None,
    ) -> FuzzResult:
        """Make async HTTP request."""
        url = f"{self.base_url}{endpoint}"
        start_time = time.time()
        
        try:
            if method == HttpMethod.GET:
                response = await self.async_client.get(
                    url, params=params, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.POST:
                response = await self.async_client.post(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.PUT:
                response = await self.async_client.put(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.PATCH:
                response = await self.async_client.patch(
                    url, json=payload, headers=self.headers, timeout=self.timeout
                )
            elif method == HttpMethod.DELETE:
                response = await self.async_client.delete(
                    url, headers=self.headers, timeout=self.timeout
                )
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            response_time = (time.time() - start_time) * 1000
            response_body = response.text
            
            is_suspicious, reason = self._is_suspicious_response(response_body)
            
            if response.status_code >= 500:
                is_suspicious = True
                reason = f"Server error: {response.status_code}"
            
            return FuzzResult(
                endpoint=endpoint,
                method=method.value,
                payload=payload,
                status_code=response.status_code,
                response_time_ms=response_time,
                response_body=response_body[:2000],
                is_suspicious=is_suspicious,
                suspicion_reason=reason,
            )
            
        except Exception as e:
            return FuzzResult(
                endpoint=endpoint,
                method=method.value,
                payload=payload,
                status_code=0,
                response_time_ms=(time.time() - start_time) * 1000,
                response_body="",
                is_error=True,
                error_message=str(e),
            )
    
    async def fuzz_endpoint_concurrent(
        self,
        method: HttpMethod,
        endpoint: str,
        payloads: List[Dict[str, Any]],
    ) -> FuzzReport:
        """Fuzz endpoint concurrently."""
        report = FuzzReport()
        semaphore = asyncio.Semaphore(self.concurrency)
        
        async def bounded_request(payload):
            async with semaphore:
                return await self._make_request_async(method, endpoint, payload)
        
        tasks = [bounded_request(p) for p in payloads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                report.add_result(FuzzResult(
                    endpoint=endpoint,
                    method=method.value,
                    payload=None,
                    status_code=0,
                    response_time_ms=0,
                    response_body="",
                    is_error=True,
                    error_message=str(result),
                ))
            else:
                report.add_result(result)
        
        return report




