# API Security Testing Guide

**Category**: guides  
**Created**: 2025-12-15  
**Updated**: 2025-12-15  
**Status**: active

## Overview

Busibox includes a comprehensive API security test suite that covers all core services (AuthZ, Agent, Ingest, Search). The tests identify common vulnerabilities including those in the OWASP API Security Top 10.

## Quick Start

### Running Security Tests

```bash
# From busibox repo root
make test-security

# Or via interactive menu
make test
# → Select "Service Tests" (option 5)
# → Select "API Security Tests" (option 7)

# Run against specific environment
cd provision/ansible
make test-security INV=inventory/test
make test-security INV=inventory/production
```

### Direct Execution

```bash
cd tests/security

# First time setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run tests
SECURITY_TEST_ENV=test python -m pytest -v

# Run specific test categories
python -m pytest -v -m auth          # Authentication tests
python -m pytest -v -m injection     # Injection tests
python -m pytest -v -m fuzz          # Fuzzing tests
python -m pytest -v -m rate_limit    # Rate limiting tests
```

## Test Categories

### 1. Authentication & Authorization (OWASP API2, API5)

Tests in `test_auth_security.py`:

| Test | Description |
|------|-------------|
| `test_agent_api_no_auth` | Verify endpoints reject unauthenticated requests |
| `test_ingest_api_no_auth` | Verify ingest API requires authentication |
| `test_search_api_no_auth` | Verify search API requires authentication |
| `test_authz_admin_no_auth` | Verify admin endpoints require authentication |
| `test_malformed_jwt_agent` | Verify malformed JWTs are rejected |
| `test_alg_none_attack` | Verify JWT alg:none attack is blocked |
| `test_expired_token` | Verify expired tokens are rejected |

### 2. Broken Object Level Authorization (OWASP API1)

Tests for IDOR (Insecure Direct Object Reference) vulnerabilities:

| Test | Description |
|------|-------------|
| `test_access_other_user_file` | Cannot access another user's files |
| `test_access_other_user_conversation` | Cannot access another user's conversations |
| `test_delete_other_user_resource` | Cannot delete another user's resources |
| `test_uuid_manipulation` | Malicious UUIDs are handled safely |

### 3. Injection Attacks (OWASP API8)

Tests in `test_injection.py`:

| Test | Description |
|------|-------------|
| `test_search_query_sql_injection` | SQL injection in search queries blocked |
| `test_file_id_sql_injection` | SQL injection in file IDs blocked |
| `test_nosql_query_injection` | NoSQL injection blocked |
| `test_filename_command_injection` | Command injection in filenames blocked |
| `test_file_path_traversal` | Path traversal attacks blocked |
| `test_download_path_traversal` | Path traversal in downloads blocked |
| `test_search_query_reflection` | XSS payloads sanitized |
| `test_header_injection_in_parameters` | Header injection blocked |
| `test_ssrf_in_url_parameters` | SSRF attempts blocked |
| `test_ldap_injection_in_search` | LDAP injection blocked |

### 4. Fuzzing Tests

Tests in `test_fuzzing.py`:

| Test | Description |
|------|-------------|
| `test_fuzz_search_query` | Malformed search queries handled |
| `test_fuzz_search_limit` | Invalid pagination parameters handled |
| `test_hypothesis_search_parameters` | Property-based fuzzing of search |
| `test_fuzz_file_id_parameter` | Malicious file IDs handled |
| `test_fuzz_agent_definition` | Malformed agent definitions handled |
| `test_fuzz_oauth_token_request` | Malformed OAuth requests handled |
| `test_fuzz_authorization_header` | Malformed auth headers handled |

### 5. Rate Limiting & Resource Exhaustion (OWASP API4)

Tests in `test_rate_limiting.py`:

| Test | Description |
|------|-------------|
| `test_large_query_rejection` | Very large queries rejected |
| `test_deep_json_nesting_rejection` | Deeply nested JSON rejected |
| `test_many_parameters_rejection` | Too many parameters rejected |
| `test_regex_dos_protection` | ReDoS patterns handled safely |
| `test_upload_size_limit` | Large file uploads handled |
| `test_upload_content_type_validation` | Dangerous file types blocked |

### 6. Endpoint Coverage

Tests in `test_endpoint_coverage.py` verify ALL endpoints are tested:

- **AuthZ API**: 11 endpoints (OAuth, admin, health)
- **Agent API**: 26 endpoints (agents, tools, workflows, runs, conversations)
- **Ingest API**: 16 endpoints (upload, files, search, export)
- **Search API**: 7 endpoints (search modes, health)

## Test Structure

```
tests/security/
├── conftest.py              # Pytest fixtures and configuration
├── pytest.ini               # Pytest settings
├── requirements.txt         # Python dependencies
├── run_tests.sh            # Test runner script
├── README.md               # Test suite documentation
├── utils/
│   ├── __init__.py
│   ├── payloads.py         # Malicious payload generators
│   ├── fuzzer.py           # Fuzzing utilities
│   ├── auth.py             # Authentication helpers
│   └── assertions.py       # Security assertion helpers
├── test_auth_security.py   # Authentication/authorization tests
├── test_injection.py       # Injection attack tests
├── test_fuzzing.py         # Fuzzing tests
├── test_rate_limiting.py   # Rate limiting tests
└── test_endpoint_coverage.py # Comprehensive endpoint tests
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECURITY_TEST_ENV` | Target environment (`local`, `test`, `production`) | `test` |
| `TEST_JWT_TOKEN` | Valid JWT for authenticated tests | None |
| `AUTHZ_ADMIN_TOKEN` | Admin token for admin endpoint tests | None |
| `TEST_CLIENT_ID` | OAuth client ID for token tests | None |
| `TEST_CLIENT_SECRET` | OAuth client secret for token tests | None |
| `TEST_USER_ID` | User ID for X-User-Id header | `test-security-user` |

### Service Endpoints

The test suite automatically configures endpoints based on environment:

**Local Development**:
```python
agent  = "http://localhost:8000"
ingest = "http://localhost:8002"
search = "http://localhost:8003"
authz  = "http://localhost:8010"
files  = "http://localhost:9000"
```

**Test Environment** (10.96.201.x):
```python
agent  = "http://10.96.201.202:8000"
ingest = "http://10.96.201.206:8002"
search = "http://10.96.201.204:8003"
authz  = "http://10.96.201.210:8010"
files  = "http://10.96.201.205:9000"
```

**Production Environment** (10.96.200.x):
```python
agent  = "http://10.96.200.202:8000"
ingest = "http://10.96.200.206:8002"
search = "http://10.96.200.204:8003"
authz  = "http://10.96.200.210:8010"
files  = "http://10.96.200.205:9000"
```

## Adding New Tests

### 1. Create Test File

Create a new `test_*.py` file in `tests/security/`:

```python
"""
Description of security tests.
"""
import pytest
from utils.payloads import PayloadGenerator
from utils.assertions import SecurityAssertions


class TestMySecurityTests:
    """Description of test class."""
    
    @pytest.mark.auth  # Use appropriate marker
    def test_my_security_check(self, http_client, endpoints, auth_headers):
        """Test description."""
        url = f"{endpoints.ingest}/my-endpoint"
        response = http_client.get(url, headers=auth_headers)
        
        # Assertions
        assert response.status_code in [401, 403, 404]
        SecurityAssertions.assert_no_sql_errors(response.text, "context")
```

### 2. Available Fixtures

| Fixture | Scope | Description |
|---------|-------|-------------|
| `http_client` | session | Synchronous HTTP client |
| `async_http_client` | session | Async HTTP client |
| `endpoints` | session | Service endpoint URLs |
| `credentials` | session | Test credentials |
| `auth_headers` | function | Authentication headers |
| `admin_headers` | function | Admin authentication headers |

### 3. Test Markers

| Marker | Description |
|--------|-------------|
| `@pytest.mark.auth` | Authentication/authorization tests |
| `@pytest.mark.injection` | Injection attack tests |
| `@pytest.mark.fuzz` | Fuzzing tests |
| `@pytest.mark.rate_limit` | Rate limiting tests |
| `@pytest.mark.idor` | IDOR vulnerability tests |
| `@pytest.mark.slow` | Slow tests (skipped by default) |
| `@pytest.mark.destructive` | Tests that modify data |

### 4. Payload Generators

Use `PayloadGenerator` for malicious inputs:

```python
from utils.payloads import PayloadGenerator

# SQL injection payloads
PayloadGenerator.SQL_INJECTION_BASIC
PayloadGenerator.SQL_INJECTION_UUID

# XSS payloads
PayloadGenerator.XSS_BASIC

# Command injection
PayloadGenerator.COMMAND_INJECTION

# Path traversal
PayloadGenerator.PATH_TRAVERSAL

# Invalid UUIDs
PayloadGenerator.MALICIOUS_UUIDS

# Auth bypass tokens
PayloadGenerator.AUTH_BYPASS_TOKENS
```

### 5. Security Assertions

Use `SecurityAssertions` for security-specific checks:

```python
from utils.assertions import SecurityAssertions

# Check for SQL errors in response
SecurityAssertions.assert_no_sql_errors(response.text, "context")

# Check for sensitive data exposure
SecurityAssertions.assert_no_sensitive_data(response.text, "context")

# Check for proper error response
SecurityAssertions.assert_proper_error_response(
    response.text, response.status_code, "context"
)
```

## OWASP API Security Top 10 Coverage

| Risk | Coverage | Tests |
|------|----------|-------|
| API1: Broken Object Level Authorization | ✅ | IDOR tests |
| API2: Broken Authentication | ✅ | Auth bypass, token tests |
| API3: Broken Object Property Level Authorization | ✅ | Mass assignment tests |
| API4: Unrestricted Resource Consumption | ✅ | Rate limiting, DoS tests |
| API5: Broken Function Level Authorization | ✅ | Admin endpoint tests |
| API6: Unrestricted Access to Sensitive Business Flows | ⚠️ | Partial (needs business logic) |
| API7: Server Side Request Forgery | ✅ | SSRF tests |
| API8: Security Misconfiguration | ✅ | Health endpoint info leak tests |
| API9: Improper Inventory Management | ✅ | Endpoint coverage tests |
| API10: Unsafe Consumption of APIs | ⚠️ | Partial (internal APIs) |

## CI/CD Integration

### GitHub Actions

```yaml
security-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    
    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
    
    - name: Install dependencies
      run: |
        cd tests/security
        python -m venv venv
        source venv/bin/activate
        pip install -r requirements.txt
    
    - name: Run security tests
      env:
        SECURITY_TEST_ENV: test
      run: |
        cd tests/security
        source venv/bin/activate
        python -m pytest -v --tb=short
```

### Pre-deployment Check

Add to your deployment pipeline:

```bash
# Run before deploying to production
make test-security INV=inventory/test

# Fail deployment if security tests fail
if [ $? -ne 0 ]; then
    echo "Security tests failed - aborting deployment"
    exit 1
fi
```

## Troubleshooting

### Tests Fail with Connection Refused

**Cause**: Target services not running or wrong environment.

**Solution**:
```bash
# Check service health
curl http://10.96.201.210:8010/health/live  # AuthZ
curl http://10.96.201.206:8002/health       # Ingest
curl http://10.96.201.204:8003/health       # Search
curl http://10.96.201.202:8000/health       # Agent

# Deploy services if needed
cd provision/ansible
make all INV=inventory/test
```

### Tests Fail with 401 Unauthorized

**Cause**: Missing or invalid test credentials.

**Solution**:
```bash
# Bootstrap test credentials
cd provision/ansible
make bootstrap-test-creds INV=inventory/test

# Export credentials
export TEST_JWT_TOKEN="..."
export AUTHZ_ADMIN_TOKEN="..."
```

### Slow Tests Not Running

**Cause**: Slow tests are skipped by default.

**Solution**:
```bash
# Run with slow tests
python -m pytest -v --runslow
```

## Related Documentation

- [OAuth2 Token Exchange](oauth2-token-exchange-implementation.md) - Authentication architecture
- [Bootstrap Test Credentials](bootstrap-test-credentials.md) - Setting up test auth
- [Agent Server Testing](agent-server-testing.md) - Agent API testing
- [Architecture](../architecture/architecture.md) - System overview

## Security Contact

If you discover a security vulnerability, please report it responsibly:

1. Do not open a public issue
2. Contact the security team directly
3. Provide detailed reproduction steps
4. Allow time for a fix before disclosure




