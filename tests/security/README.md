# Busibox API Security Test Suite

Comprehensive security testing for all Busibox API endpoints, covering OWASP API Security Top 10 and common attack vectors.

## Quick Start

```bash
# Run all security tests against test environment
make test-security

# Or run directly
./tests/security/run_tests.sh --env=test

# Run against local development
./tests/security/run_tests.sh --env=local

# Run specific test categories
./tests/security/run_tests.sh --marker=auth        # Authentication tests
./tests/security/run_tests.sh --marker=injection   # Injection tests
./tests/security/run_tests.sh --marker=fuzz        # Fuzzing tests
./tests/security/run_tests.sh --marker=rate_limit  # Rate limiting tests
```

## Test Categories

### 1. Authentication & Authorization (`test_auth_security.py`)
- **Authentication Bypass**: Tests all endpoints require proper authentication
- **Invalid Tokens**: Tests handling of malformed, expired, and manipulated JWTs
- **BOLA (IDOR)**: Tests object-level authorization (accessing other users' resources)
- **BFLA**: Tests function-level authorization (admin endpoints)
- **Token Security**: Tests JWT handling, JWKS endpoint, key confusion attacks

OWASP Coverage:
- API1:2023 Broken Object Level Authorization
- API2:2023 Broken Authentication  
- API5:2023 Broken Function Level Authorization

### 2. Injection Attacks (`test_injection.py`)
- **SQL Injection**: Tests query parameters, path parameters, and body fields
- **NoSQL Injection**: Tests MongoDB-style injection in JSON bodies
- **Command Injection**: Tests filename and parameter handling
- **Path Traversal**: Tests file path handling for directory escape
- **XSS**: Tests input reflection in error messages and responses
- **Header Injection**: Tests CRLF injection in parameters
- **SSRF**: Tests URL parameters for internal network access

OWASP Coverage:
- API8:2023 Security Misconfiguration
- API10:2023 Unsafe Consumption of APIs

### 3. Fuzzing (`test_fuzzing.py`)
- **Parameter Fuzzing**: Tests all API parameters with malicious inputs
- **Boundary Testing**: Tests edge cases (empty, very long, special chars)
- **Unicode Handling**: Tests international characters and special unicode
- **Header Fuzzing**: Tests various header manipulations
- **Property-Based Testing**: Uses Hypothesis for automated test generation

### 4. Rate Limiting (`test_rate_limiting.py`)
- **Rate Limit Enforcement**: Tests that rate limits exist
- **Concurrent Requests**: Tests handling of parallel requests
- **Resource Exhaustion**: Tests large payloads, deep nesting
- **ReDoS Protection**: Tests regex denial of service
- **Upload Limits**: Tests file upload size restrictions

## Test Markers

Use pytest markers to run specific test categories:

```bash
# Run only authentication tests
pytest tests/security/ -m auth

# Run only injection tests
pytest tests/security/ -m injection

# Run fuzzing tests
pytest tests/security/ -m fuzz

# Run rate limiting tests (slow)
pytest tests/security/ -m rate_limit

# Run IDOR tests
pytest tests/security/ -m idor

# Exclude slow tests
pytest tests/security/ -m "not slow"
```

## Environment Configuration

Set environment variables before running tests:

```bash
# Target environment (local, test, production)
export SECURITY_TEST_ENV=test

# Authentication credentials (optional, for authenticated endpoint tests)
export TEST_JWT_TOKEN="your-jwt-token"
export AUTHZ_ADMIN_TOKEN="admin-token"
export TEST_CLIENT_ID="client-id"
export TEST_CLIENT_SECRET="client-secret"
```

Or create a `.env` file in `tests/security/`:

```env
SECURITY_TEST_ENV=test
TEST_JWT_TOKEN=...
AUTHZ_ADMIN_TOKEN=...
```

## Endpoints Tested

### Agent API (port 8000)
- `/agents` - Agent definitions
- `/agents/tools` - Tool definitions
- `/agents/workflows` - Workflow definitions
- `/runs` - Agent execution
- `/conversations` - Conversation management
- `/dispatcher/route` - Query routing
- `/health` - Health check

### Ingest API (port 8002)
- `/upload` - File upload
- `/search` - Document search
- `/files/{id}` - File operations
- `/files/{id}/roles` - Role management
- `/health` - Health check

### Search API (port 8001)
- `/search` - Hybrid search
- `/search/keyword` - Keyword search
- `/search/semantic` - Semantic search
- `/search/mmr` - Diversity search
- `/search/explain` - Search explanation
- `/health` - Health check

### Authz API (port 8010)
- `/oauth/token` - Token issuance
- `/.well-known/jwks.json` - Public keys
- `/admin/roles` - Role management
- `/admin/users` - User management
- `/health/live` - Liveness check
- `/health/ready` - Readiness check

## Test Utilities

### PayloadGenerator (`utils/payloads.py`)
Pre-built attack payloads for:
- SQL injection (basic, blind, UUID-specific)
- NoSQL injection
- Command injection
- XSS (basic and encoded)
- Path traversal
- Header injection
- SSRF
- Mass assignment
- Boundary values

### Fuzzer (`utils/fuzzer.py`)
Automated fuzzing with:
- Parameter fuzzing
- Path parameter fuzzing
- Header fuzzing
- Rate limit testing
- Async concurrent testing
- Suspicious response detection

### AuthTester (`utils/auth.py`)
Authentication testing:
- No-auth testing
- Invalid token testing
- Token manipulation
- IDOR testing
- Privilege escalation testing
- JWT key confusion

### SecurityAssertions (`utils/assertions.py`)
Custom assertions for:
- Sensitive data exposure
- SQL error detection
- Stack trace detection
- Proper error responses
- CORS configuration
- Security headers

## Adding New Tests

1. Create a new test file in `tests/security/`
2. Import utilities from `utils/`
3. Use appropriate markers (`@pytest.mark.auth`, etc.)
4. Use fixtures from `conftest.py`

Example:

```python
import pytest
from utils.payloads import PayloadGenerator
from utils.assertions import SecurityAssertions

class TestMyNewFeature:
    @pytest.mark.auth
    def test_new_endpoint_requires_auth(self, http_client, endpoints):
        response = http_client.get(f"{endpoints.agent}/new-endpoint")
        SecurityAssertions.assert_auth_required(response.status_code)
    
    @pytest.mark.injection
    def test_new_endpoint_sql_injection(self, http_client, endpoints, auth_headers):
        for payload in PayloadGenerator.SQL_INJECTION_BASIC:
            response = http_client.post(
                f"{endpoints.agent}/new-endpoint",
                json={"field": payload},
                headers=auth_headers,
            )
            SecurityAssertions.assert_no_sql_errors(response.text)
```

## CI/CD Integration

Add to your CI pipeline:

```yaml
security-tests:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Setup Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
    - name: Install dependencies
      run: pip install -r tests/security/requirements.txt
    - name: Run security tests
      env:
        SECURITY_TEST_ENV: test
      run: pytest tests/security/ -v --tb=short -m "not slow"
```

## Troubleshooting

### Tests failing with connection errors
- Ensure target services are running
- Check firewall rules allow connections
- Verify endpoint IPs in `conftest.py`

### Tests failing with 401 errors
- Set up authentication credentials in environment
- Run `make bootstrap-test-creds` to generate test tokens

### Slow tests timing out
- Increase timeout in `pytest.ini`
- Run with `--timeout=120` for slow tests
- Use `--runslow` to include slow tests

## Security Test Philosophy

1. **Non-destructive by default**: Tests don't modify production data
2. **Defense in depth**: Tests multiple layers of security
3. **Real payloads**: Uses actual attack patterns, not simplified versions
4. **Clear assertions**: Each test has clear pass/fail criteria
5. **Actionable results**: Failed tests provide remediation guidance




