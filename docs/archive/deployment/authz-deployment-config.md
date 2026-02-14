---
title: AuthZ Service Deployment Configuration
category: deployment
created: 2024-12-14
updated: 2024-12-14
status: active
---

# AuthZ Service Deployment Configuration

## Overview

This document describes the environment variables and configuration required to deploy the authz service and configure downstream services to use it.

## AuthZ Service Configuration

### Required Environment Variables

```bash
# PostgreSQL connection
POSTGRES_HOST=10.96.200.203
POSTGRES_PORT=5432
POSTGRES_DB=busibox
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=<secret>

# Token issuer (must match downstream service expectations)
AUTHZ_ISSUER=busibox-authz

# Access token TTL (seconds, default: 900 = 15 minutes)
AUTHZ_ACCESS_TOKEN_TTL=900

# Signing algorithm (default: RS256)
AUTHZ_SIGNING_ALG=RS256
AUTHZ_RSA_KEY_SIZE=2048

# Optional: encrypt stored private keys at rest
AUTHZ_KEY_ENCRYPTION_PASSPHRASE=<strong-passphrase>

# Bootstrap OAuth client (ai-portal)
AUTHZ_BOOTSTRAP_CLIENT_ID=ai-portal
AUTHZ_BOOTSTRAP_CLIENT_SECRET=<secret>
AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES=ingest-api,search-api,agent-api
AUTHZ_BOOTSTRAP_ALLOWED_SCOPES=ingest.read,ingest.write,search.read,agent.execute

# Optional: admin token for manual operations
AUTHZ_ADMIN_TOKEN=<secret>
```

### Ansible Vault Variables

Add to `provision/ansible/roles/secrets/vars/vault.yml`:

```yaml
# AuthZ service
authz_postgres_password: "{{ postgres_password }}"
authz_bootstrap_client_secret: "<generate-strong-secret>"
authz_key_encryption_passphrase: "<generate-strong-passphrase>"
authz_admin_token: "<generate-strong-token>"
```

### Systemd Service

The authz service runs as a systemd service on `authz-lxc` (CT 210):

```ini
[Unit]
Description=Busibox AuthZ Service
After=network.target postgresql.service

[Service]
Type=simple
User=busibox
WorkingDirectory=/srv/authz
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=/etc/busibox/authz.env
ExecStart=/srv/authz/venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8010
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## Downstream Service Configuration

### Ingest API

Update `provision/ansible/roles/ingest/templates/ingest.env.j2`:

```bash
# AuthZ configuration
AUTHZ_JWKS_URL=http://10.96.200.210:8010/.well-known/jwks.json
AUTHZ_ISSUER=busibox-authz
AUTHZ_AUDIENCE=ingest-api
JWT_ALGORITHMS=RS256
```

### Search API

Update `provision/ansible/roles/search/templates/search.env.j2`:

```bash
# AuthZ configuration
AUTHZ_JWKS_URL=http://10.96.200.210:8010/.well-known/jwks.json
AUTHZ_ISSUER=busibox-authz
AUTHZ_AUDIENCE=search-api
JWT_ALGORITHMS=RS256
```

### Agent API

Update `provision/ansible/roles/agent/templates/agent.env.j2`:

```bash
# AuthZ configuration
auth_jwks_url=http://10.96.200.210:8010/.well-known/jwks.json
auth_issuer=busibox-authz
auth_audience=agent-api
auth_token_url=http://10.96.200.210:8010/oauth/token
auth_client_id=agent-service
auth_client_secret={{ authz_agent_client_secret }}
```

### AI Portal

Update `provision/ansible/roles/apps/templates/ai-portal.env.j2`:

```bash
# AuthZ configuration
AUTHZ_BASE_URL=http://10.96.200.210:8010
AUTHZ_CLIENT_ID=ai-portal
AUTHZ_CLIENT_SECRET={{ authz_bootstrap_client_secret }}
```

## Container Network Configuration

### AuthZ Container (CT 210)

```bash
# In provision/pct/vars.env
CT_AUTHZ=210
IP_AUTHZ=10.96.200.210

# In provision/pct/create_lxc_base.sh
create_container "$CT_AUTHZ" "authz-lxc" "$IP_AUTHZ" "authz" "2" "2048"
```

### Firewall Rules

Add to container firewall configuration:

```bash
# Allow authz service
ufw allow from 10.96.200.0/21 to any port 8010 comment 'AuthZ service'
```

## Deployment Steps

### Initial Deployment

1. **Create authz container**:
   ```bash
   cd /root/busibox/provision/pct
   bash create_lxc_base.sh production
   ```

2. **Deploy authz service**:
   ```bash
   cd /root/busibox/provision/ansible
   make authz
   ```

3. **Verify deployment**:
   ```bash
   # Check service status
   ssh root@10.96.200.210
   systemctl status authz
   
   # Check JWKS endpoint
   curl http://10.96.200.210:8010/.well-known/jwks.json
   
   # Check health
   curl http://10.96.200.210:8010/health/ready
   ```

4. **Update downstream services**:
   ```bash
   cd /root/busibox/provision/ansible
   make ingest
   make search
   make agent
   make apps
   ```

### Updating Configuration

1. **Update vault variables**:
   ```bash
   cd /root/busibox/provision/ansible
   ansible-vault edit roles/secrets/vars/vault.yml
   ```

2. **Redeploy services**:
   ```bash
   make authz
   # Or for specific service
   make ingest
   ```

### Key Rotation

To rotate signing keys:

1. **Generate new key** (authz will auto-generate on startup if none exists)
2. **Mark old key inactive**:
   ```sql
   UPDATE authz_signing_keys SET is_active = false WHERE kid = '<old-kid>';
   ```
3. **Restart authz service**:
   ```bash
   ssh root@10.96.200.210
   systemctl restart authz
   ```

## Verification

### Test Token Exchange

```bash
# Get bootstrap client credentials from vault
CLIENT_ID="ai-portal"
CLIENT_SECRET="<from-vault>"

# Sync a test user (requires user to exist in ai-portal DB)
curl -X POST http://10.96.200.210:8010/internal/sync/user \
  -H "Content-Type: application/json" \
  -d '{
    "client_id": "'$CLIENT_ID'",
    "client_secret": "'$CLIENT_SECRET'",
    "user_id": "test-user-uuid",
    "email": "test@example.com",
    "roles": [{"id": "test-role-uuid", "name": "TestRole"}],
    "user_role_ids": ["test-role-uuid"]
  }'

# Exchange for access token
curl -X POST http://10.96.200.210:8010/oauth/token \
  -H "Content-Type: application/json" \
  -d '{
    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
    "client_id": "'$CLIENT_ID'",
    "client_secret": "'$CLIENT_SECRET'",
    "audience": "ingest-api",
    "scope": "ingest.write",
    "requested_subject": "test-user-uuid"
  }'

# Should return:
# {
#   "access_token": "eyJ...",
#   "token_type": "bearer",
#   "expires_in": 900,
#   "scope": "ingest.write",
#   "issued_token_type": "urn:ietf:params:oauth:token-type:access_token"
# }
```

### Test Service Authentication

```bash
# Get token from above
TOKEN="<access-token>"

# Call ingest-api with token
curl -X POST http://10.96.200.206:8001/files/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@test.pdf"

# Should succeed with 200 OK
```

### Verify JWKS Validation

```bash
# Check downstream service logs
ssh root@10.96.200.206
journalctl -u ingest -n 50 --no-pager | grep -i "jwt\|authz"

# Should see successful token validation
```

## Troubleshooting

### AuthZ Service Won't Start

**Check logs**:
```bash
ssh root@10.96.200.210
journalctl -u authz -n 100 --no-pager
```

**Common issues**:
- PostgreSQL connection failed: Check `POSTGRES_*` vars
- Key generation failed: Check `AUTHZ_KEY_ENCRYPTION_PASSPHRASE`
- Bootstrap client failed: Check `AUTHZ_BOOTSTRAP_CLIENT_SECRET`

### Token Exchange Fails

**Error**: `invalid_client`

**Fix**: Verify client credentials in vault match `AUTHZ_BOOTSTRAP_CLIENT_SECRET`

**Error**: `unknown_subject`

**Fix**: User not synced to authz. Call `/internal/sync/user` first.

**Error**: `unauthorized_client_audience`

**Fix**: Requested audience not in `AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES`

### Service Rejects Token

**Error**: `Invalid or expired JWT token`

**Fix**: Check service `AUTHZ_JWKS_URL` points to authz JWKS endpoint

**Error**: `issuer mismatch`

**Fix**: Ensure service `AUTHZ_ISSUER` matches authz `AUTHZ_ISSUER`

**Error**: `audience mismatch`

**Fix**: Ensure service `AUTHZ_AUDIENCE` matches requested audience in token exchange

## Monitoring

### Health Checks

```bash
# Liveness (always returns 200)
curl http://10.96.200.210:8010/health/live

# Readiness (checks DB connection)
curl http://10.96.200.210:8010/health/ready
```

### Metrics

Key metrics to monitor:
- Token exchange request rate
- Token validation failures
- JWKS fetch rate
- Database query latency
- Active signing keys count

### Audit Logs

Query audit logs:
```sql
SELECT * FROM audit_logs 
WHERE action = 'oauth.token.issued' 
ORDER BY created_at DESC 
LIMIT 100;
```

## Security Considerations

1. **Secrets Management**:
   - Store all secrets in Ansible vault
   - Rotate `AUTHZ_BOOTSTRAP_CLIENT_SECRET` regularly
   - Use strong `AUTHZ_KEY_ENCRYPTION_PASSPHRASE`

2. **Network Security**:
   - AuthZ service only accessible from internal network
   - Use firewall rules to restrict access
   - Consider TLS for production

3. **Token Security**:
   - Short TTL (15 minutes) reduces exposure
   - Tokens are audience-bound (can't be reused)
   - Asymmetric signing prevents forgery

4. **Key Rotation**:
   - Rotate signing keys quarterly
   - Keep old keys active for grace period
   - Monitor for expired token errors

## Related Documentation

- [AuthZ Service Architecture](../architecture/authz-service.md)
- [Token Exchange Flow](../guides/token-exchange-flow.md)
- [RBAC Management](../guides/rbac-management.md)
- [AI Portal Migration Guide](../../ai-portal/docs/AUTHZ_MIGRATION_GUIDE.md)

