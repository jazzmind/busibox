"""
Security Test Assertions

Custom assertions for security testing.
"""

from typing import Any, Dict, List, Optional
import re
import json


class SecurityAssertions:
    """Security-focused test assertions."""
    
    # Patterns that should never appear in responses
    SENSITIVE_PATTERNS = [
        # Credentials
        r"password[\"']?\s*[:=]\s*[\"']?[^\"'\s]+",
        r"api[_-]?key[\"']?\s*[:=]\s*[\"']?[^\"'\s]+",
        r"secret[_-]?key[\"']?\s*[:=]\s*[\"']?[^\"'\s]+",
        r"access[_-]?token[\"']?\s*[:=]\s*[\"']?[^\"'\s]+",
        r"private[_-]?key[\"']?\s*[:=]\s*[\"']?[^\"'\s]+",
        
        # AWS credentials
        r"AKIA[0-9A-Z]{16}",  # AWS Access Key
        r"aws[_-]?secret[_-]?access[_-]?key",
        
        # Connection strings
        r"postgres://[^\s]+",
        r"mysql://[^\s]+",
        r"mongodb://[^\s]+",
        r"redis://[^\s]+",
        
        # Internal paths
        r"/srv/[^\s]+",
        r"/opt/[^\s]+",
        r"/home/[a-z]+/",
        r"/var/log/[^\s]+",
        r"/etc/passwd",
        r"/etc/shadow",
        
        # Stack traces
        r"File \"[^\"]+\", line \d+",
        r"Traceback \(most recent call last\)",
        r"at [a-zA-Z0-9_]+\.[a-zA-Z0-9_]+\([^\)]*\)",
    ]
    
    # SQL error patterns
    SQL_ERROR_PATTERNS = [
        r"SQL syntax.*MySQL",
        r"Warning.*mysql_",
        r"PostgreSQL.*ERROR",
        r"ORA-\d{5}",
        r"Microsoft SQL Server",
        r"SQLite3::",
        r"syntax error at or near",
        r"unclosed quotation mark",
        r"quoted string not properly terminated",
    ]
    
    @classmethod
    def assert_no_sensitive_data(cls, response_body: str, context: str = "") -> None:
        """Assert that response doesn't contain sensitive data patterns."""
        for pattern in cls.SENSITIVE_PATTERNS:
            matches = re.findall(pattern, response_body, re.IGNORECASE)
            assert not matches, (
                f"Sensitive data found in response{' (' + context + ')' if context else ''}: "
                f"Pattern '{pattern}' matched: {matches[:3]}"
            )
    
    @classmethod
    def assert_no_sql_errors(cls, response_body: str, context: str = "") -> None:
        """Assert that response doesn't contain SQL error messages."""
        for pattern in cls.SQL_ERROR_PATTERNS:
            matches = re.findall(pattern, response_body, re.IGNORECASE)
            assert not matches, (
                f"SQL error found in response{' (' + context + ')' if context else ''}: "
                f"Pattern '{pattern}' matched"
            )
    
    @classmethod
    def assert_no_stack_trace(cls, response_body: str, context: str = "") -> None:
        """Assert that response doesn't contain stack traces."""
        stack_patterns = [
            r"Traceback \(most recent call last\)",
            r"File \"[^\"]+\", line \d+",
            r"at [a-zA-Z0-9_.]+\([^\)]*:\d+:\d+\)",  # JavaScript stack
            r"Exception in thread",
            r"java\.[a-zA-Z.]+Exception",
        ]
        for pattern in stack_patterns:
            matches = re.findall(pattern, response_body, re.IGNORECASE)
            assert not matches, (
                f"Stack trace found in response{' (' + context + ')' if context else ''}: "
                f"Pattern '{pattern}' matched"
            )
    
    @classmethod
    def assert_proper_error_response(
        cls,
        response_body: str,
        status_code: int,
        context: str = "",
    ) -> None:
        """Assert that error responses are properly formatted and don't leak info."""
        if status_code >= 400:
            # Should not contain sensitive info
            cls.assert_no_sensitive_data(response_body, context)
            cls.assert_no_sql_errors(response_body, context)
            cls.assert_no_stack_trace(response_body, context)
    
    @classmethod
    def assert_no_verbose_errors(
        cls,
        response_body: str,
        status_code: int,
        context: str = "",
    ) -> None:
        """Assert that errors don't contain excessive detail."""
        if status_code >= 500:
            # 500 errors should be generic
            try:
                error_data = json.loads(response_body)
                # Check if error contains internal details
                error_str = json.dumps(error_data).lower()
                
                internal_indicators = [
                    "line ",
                    "file ",
                    "column ",
                    "query",
                    "table",
                    "column",
                    "database",
                    "internal",
                ]
                
                found_indicators = [
                    ind for ind in internal_indicators
                    if ind in error_str
                ]
                
                assert len(found_indicators) < 3, (
                    f"Verbose error response{' (' + context + ')' if context else ''}: "
                    f"Contains internal indicators: {found_indicators}"
                )
            except json.JSONDecodeError:
                pass  # Not JSON, can't check structure
    
    @classmethod
    def assert_auth_required(cls, status_code: int, context: str = "") -> None:
        """Assert that authentication is required (401)."""
        assert status_code == 401, (
            f"Expected 401 Unauthorized{' (' + context + ')' if context else ''}, "
            f"got {status_code}"
        )
    
    @classmethod
    def assert_forbidden(cls, status_code: int, context: str = "") -> None:
        """Assert that access is forbidden (403)."""
        assert status_code == 403, (
            f"Expected 403 Forbidden{' (' + context + ')' if context else ''}, "
            f"got {status_code}"
        )
    
    @classmethod
    def assert_not_found_or_forbidden(
        cls,
        status_code: int,
        context: str = "",
    ) -> None:
        """Assert that resource is either not found or forbidden (IDOR protection)."""
        assert status_code in [403, 404], (
            f"Expected 403 or 404 for IDOR protection{' (' + context + ')' if context else ''}, "
            f"got {status_code}"
        )
    
    @classmethod
    def assert_rate_limited(cls, status_code: int, context: str = "") -> None:
        """Assert that rate limiting is in effect (429)."""
        assert status_code == 429, (
            f"Expected 429 Too Many Requests{' (' + context + ')' if context else ''}, "
            f"got {status_code}"
        )
    
    @classmethod
    def assert_bad_request(cls, status_code: int, context: str = "") -> None:
        """Assert that request is rejected as bad (400)."""
        assert status_code == 400, (
            f"Expected 400 Bad Request{' (' + context + ')' if context else ''}, "
            f"got {status_code}"
        )
    
    @classmethod
    def assert_no_cors_wildcard(cls, headers: Dict[str, str], context: str = "") -> None:
        """Assert that CORS doesn't use wildcard for authenticated endpoints."""
        cors_origin = headers.get("access-control-allow-origin", "")
        cors_credentials = headers.get("access-control-allow-credentials", "")
        
        # Wildcard with credentials is dangerous
        if cors_credentials.lower() == "true":
            assert cors_origin != "*", (
                f"Dangerous CORS configuration{' (' + context + ')' if context else ''}: "
                "Wildcard origin with credentials allowed"
            )
    
    @classmethod
    def assert_secure_headers(cls, headers: Dict[str, str], context: str = "") -> None:
        """Assert that security headers are present."""
        recommended_headers = {
            "x-content-type-options": "nosniff",
            "x-frame-options": ["DENY", "SAMEORIGIN"],
        }
        
        # Note: These are recommendations, not failures
        missing = []
        for header, expected in recommended_headers.items():
            value = headers.get(header, "")
            if isinstance(expected, list):
                if value.upper() not in [e.upper() for e in expected]:
                    missing.append(header)
            elif value.lower() != expected.lower():
                missing.append(header)
        
        if missing:
            # Log warning but don't fail (security headers are good practice but not critical)
            print(f"Warning: Missing security headers{' (' + context + ')' if context else ''}: {missing}")
    
    @classmethod
    def assert_no_reflection(
        cls,
        response_body: str,
        injected_value: str,
        context: str = "",
    ) -> None:
        """Assert that injected value is not reflected in response (XSS check)."""
        # Check for direct reflection
        assert injected_value not in response_body, (
            f"Input reflected in response{' (' + context + ')' if context else ''}: "
            f"'{injected_value}' found in response"
        )
        
        # Check for HTML-encoded reflection that could still be dangerous
        if "<" in injected_value or ">" in injected_value:
            # If script tags were injected, they shouldn't appear even encoded
            # in a way that could execute
            dangerous_patterns = [
                injected_value,
                injected_value.replace("<", "&lt;").replace(">", "&gt;"),
            ]
            for pattern in dangerous_patterns:
                if pattern in response_body and "script" in pattern.lower():
                    assert False, (
                        f"Potentially dangerous reflection{' (' + context + ')' if context else ''}"
                    )
    
    @classmethod
    def assert_timing_safe(
        cls,
        timing_ms: float,
        baseline_ms: float,
        threshold_factor: float = 5.0,
        context: str = "",
    ) -> None:
        """
        Assert that timing difference isn't suspicious (timing attack check).
        
        A very large timing difference could indicate information leakage
        (e.g., early return on valid username vs invalid username).
        """
        if baseline_ms > 0:
            ratio = timing_ms / baseline_ms
            assert ratio < threshold_factor, (
                f"Suspicious timing difference{' (' + context + ')' if context else ''}: "
                f"{timing_ms}ms vs baseline {baseline_ms}ms (ratio: {ratio:.2f})"
            )




