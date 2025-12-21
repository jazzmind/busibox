---
title: Bootstrap Test Credentials for Local Integration Testing
category: guides
created: 2024-12-14
updated: 2024-12-14
status: active
---

# Bootstrap Test Credentials for Local Integration Testing

## Overview

When developing and testing busibox-app libraries locally, you need valid JWT tokens from the authz service. The `bootstrap-test-credentials.sh` script automates the creation of test users, OAuth clients, and admin credentials.

## Quick Start

### From Busibox Directory

```bash
cd /path/to/busibox/provision/ansible

# For test environment
make bootstrap-test-creds INV=inventory/test

# For production environment
make bootstrap-test-creds INV=inventory/production
```

### Direct Script Usage

```bash
cd /path/to/busibox

# For test environment
bash scripts/bootstrap-test-credentials.sh test

# For production environment
bash scripts/bootstrap-test-credentials.sh production
```

## What It Does

The script:

1. **Checks authz service** - Verifies the authz service is running
2. **Generates credentials** - Creates:
   - Test OAuth client ID and secret
   - Admin token for RBAC operations
   - Test user with admin and user roles
3. **Outputs .env variables** - Prints ready-to-copy environment variables

## Example Output

```bash
========================================
Bootstrap Test Credentials for Authz
========================================

Environment: TEST
Checking authz service...
✓ Authz service is running

Getting bootstrap client info...
✓ Bootstrap client exists

Creating test OAuth client...
✓ Test OAuth client created

Creating test user...
✓ Test user created

========================================
Test Credentials Generated!
========================================

Copy these variables to your busibox-app/.env file:

# ============================================
# Busibox Test Credentials
# Generated: 2024-12-14 10:30:00
# Environment: test
# ============================================

# Authz Service
AUTHZ_BASE_URL=http://10.96.201.210:8010

# Test OAuth Client (for getting service tokens)
AUTHZ_TEST_CLIENT_ID=test-client-1702554600
AUTHZ_TEST_CLIENT_SECRET=a1b2c3d4e5f6...

# Bootstrap Client (fallback)
AUTHZ_BOOTSTRAP_CLIENT_ID=bootstrap-client
# AUTHZ_BOOTSTRAP_CLIENT_SECRET=<get-from-ansible-vault>

# Admin Token (for RBAC admin operations)
AUTHZ_ADMIN_TOKEN=f6e5d4c3b2a1...

# Test User
TEST_USER_ID=test-user-1702554600
TEST_USER_EMAIL=test@busibox.local

# Service URLs (test environment)
INGEST_API_HOST=10.96.201.206
INGEST_API_PORT=8002
AGENT_API_URL=http://10.96.201.207:4111
MILVUS_HOST=10.96.201.204
MILVUS_PORT=19530

# ============================================
```

## Usage in busibox-app

### 1. Copy Variables to .env

```bash
cd /path/to/busibox-app

# Create or update .env file
# Paste the output from bootstrap-test-credentials.sh
nano .env
```

### 2. Run Tests

```bash
npm test
```

The test helper (`tests/helpers/auth.ts`) will:
- Use the test client credentials to get real JWT tokens from authz
- Cache tokens to avoid repeated requests
- Use tokens for all service calls

### 3. Expected Results

With valid credentials, all tests should pass:

```
Test Suites: 6 passed, 6 total
Tests:       81 passed, 81 total
```

## How It Works

### Token Acquisition Flow

1. **Test starts** → Calls `getAuthzToken(userId, audience, scopes)`
2. **Helper checks cache** → Returns cached token if valid
3. **Helper requests token** → Calls authz `/oauth/token` with client credentials
4. **Authz validates client** → Checks client_id and client_secret
5. **Authz issues token** → Returns JWT signed with RS256
6. **Helper caches token** → Stores for reuse (expires in ~1 hour)
7. **Test uses token** → Includes in `Authorization: Bearer <token>` header
8. **Service validates token** → Verifies signature via authz JWKS

### Client Credentials Grant

The script creates an OAuth client with:

```json
{
  "client_id": "test-client-<timestamp>",
  "client_secret": "<random-32-byte-hex>",
  "allowed_audiences": [
    "ingest-api",
    "agent-api", 
    "search-api",
    "authz"
  ],
  "allowed_scopes": [
    "ingest.read",
    "ingest.write",
    "agent.execute",
    "search.read",
    "audit.write",
    "rbac.read"
  ]
}
```

This allows the test client to request tokens for any service with appropriate scopes.

## Troubleshooting

### Authz Service Not Running

**Error**: `Cannot connect to authz service at http://10.96.201.210:8010`

**Solution**:
```bash
cd provision/ansible
make authz INV=inventory/test
```

### Client Creation Failed

**Error**: `Could not create client via API`

**Cause**: Admin token not accepted or authz admin endpoint not accessible

**Solution**: The script will still output credentials. You can:

1. Create the client manually via authz admin API
2. Use the bootstrap client credentials (get secret from ansible vault)

### Bootstrap Secret Not Available

**Warning**: `Bootstrap client secret needed from ansible vault`

**Solution**:
```bash
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.yml | grep authz_bootstrap
```

Copy the bootstrap secret to your `.env`:
```bash
AUTHZ_BOOTSTRAP_CLIENT_SECRET=<secret-from-vault>
```

### Tests Still Failing with 401

**Possible causes**:

1. **Credentials not in .env** - Verify `.env` file exists and has correct values
2. **Wrong environment** - Test environment uses different IPs than production
3. **Token expired** - Clear token cache and try again
4. **Client not created** - Manually create via authz admin API

**Debug steps**:
```bash
# Check .env file
cat /path/to/busibox-app/.env | grep AUTHZ

# Test token acquisition manually
curl -X POST http://10.96.201.210:8010/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "client_credentials",
    "client_id": "test-client-xxx",
    "client_secret": "xxx",
    "audience": "ingest-api",
    "scope": "ingest.read"
  }'
```

## Security Notes

### Test vs Production

- **Test environment**: Safe to use generated credentials
- **Production environment**: Use with caution, consider separate test users

### Credential Rotation

Test credentials are timestamped and can be regenerated:

```bash
# Generate new credentials
make bootstrap-test-creds INV=inventory/test

# Old credentials will still work until manually revoked
```

### Revoking Credentials

To revoke test credentials:

```bash
# Via authz admin API
curl -X DELETE http://10.96.201.210:8010/admin/oauth/clients/test-client-xxx \
  -H "Authorization: Bearer <admin-token>"
```

Or delete from database:

```bash
psql -h 10.96.201.203 -U busibox_user -d busibox
DELETE FROM authz_oauth_clients WHERE client_id = 'test-client-xxx';
```

## Integration with CI/CD

### GitHub Actions

```yaml
name: Test Busibox-App

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v3
      
      - name: Setup Node.js
        uses: actions/setup-node@v3
        with:
          node-version: '20'
      
      - name: Bootstrap test credentials
        run: |
          cd busibox/provision/ansible
          make bootstrap-test-creds INV=inventory/test > /tmp/test-creds.env
      
      - name: Setup .env
        run: |
          cd busibox-app
          cat /tmp/test-creds.env | grep -E "^[A-Z]" > .env
      
      - name: Install dependencies
        run: |
          cd busibox-app
          npm install
      
      - name: Run tests
        run: |
          cd busibox-app
          npm test
```

## Related Documentation

- [Busibox-App Testing Guide](../../busibox-app/tests/README.md)
- [OAuth2 Token Exchange Implementation](./oauth2-token-exchange-implementation.md)
- [AuthZ Deployment Config](../deployment/authz-deployment-config.md)

## Summary

✅ **One command** to generate all test credentials
✅ **Copy/paste** .env variables
✅ **100% test pass rate** with valid credentials
✅ **Works for both** test and production environments
✅ **Safe to regenerate** credentials anytime





