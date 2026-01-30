# Config API Reference

> **Created**: 2026-01-29  
> **Status**: Active  
> **Category**: Reference  

## Overview

The Config API provides runtime configuration management via database storage. This replaces runtime secrets in Ansible vault with database-stored configuration that can be changed after installation without redeploying.

The API is part of the Deploy Service and runs on port 8011 in the authz container.

## Architecture

### What Goes in the Database (Runtime, Changeable)

These are configurations that may need to change after installation:

| Category | Keys | Description |
|----------|------|-------------|
| `smtp` | `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password`, `smtp_from_email` | Email server settings |
| `api_keys` | `openai_api_key`, `huggingface_token`, `resend_api_key`, `bedrock_api_key` | External API keys |
| `oauth` | `microsoft_client_id`, `microsoft_client_secret`, `github_oauth_client_id`, `github_oauth_client_secret` | OAuth provider credentials |
| `email` | `admin_email`, `allowed_email_domains` | Email settings |
| `feature_flags` | Various | Feature toggles |

### What Stays in Ansible Vault (Infrastructure)

These are bootstrap secrets needed before the database is available:

- Network configuration (octets, domain)
- Database password (can't store DB password in DB!)
- MinIO credentials (needed before object storage available)
- JWT/auth secrets (needed at service startup)
- SSH keys (Ansible connectivity)
- SSL certificates
- GitHub personal access token (for private repos during install)

## API Endpoints

Base URL: `http://{deploy-api-host}:8011/api/v1/config`

### Authentication

All endpoints require admin authentication via JWT bearer token:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8011/api/v1/config
```

### List All Configs

```http
GET /api/v1/config
GET /api/v1/config?category=smtp
```

**Response:**
```json
{
  "configs": [
    {
      "key": "smtp_host",
      "value": "smtp.example.com",
      "encrypted": false,
      "category": "smtp",
      "description": "SMTP server hostname"
    }
  ],
  "total": 1
}
```

### List Categories

```http
GET /api/v1/config/categories
```

**Response:**
```json
{
  "categories": [
    {
      "category": "smtp",
      "keys": ["smtp_host", "smtp_port", "smtp_user", "smtp_password"],
      "count": 4
    }
  ]
}
```

### Get Config Value

```http
GET /api/v1/config/{key}
```

**Response:**
```json
{
  "key": "smtp_host",
  "value": "smtp.example.com",
  "encrypted": false,
  "category": "smtp",
  "description": "SMTP server hostname"
}
```

Note: Encrypted values are returned as `"********"` for security.

### Get Raw Config Value

```http
GET /api/v1/config/{key}/raw
```

**Response:**
```json
{
  "key": "smtp_password",
  "value": "actual_password_here",
  "encrypted": true
}
```

**Warning:** This returns the actual secret value. Use only when needed for service configuration.

### Set Config Value

```http
PUT /api/v1/config/{key}
Content-Type: application/json

{
  "value": "smtp.example.com",
  "encrypted": false,
  "category": "smtp",
  "description": "SMTP server hostname"
}
```

**Response:**
```json
{
  "key": "smtp_host",
  "value": "smtp.example.com",
  "encrypted": false,
  "category": "smtp",
  "description": "SMTP server hostname"
}
```

### Delete Config Value

```http
DELETE /api/v1/config/{key}
```

**Response:**
```json
{
  "deleted": true,
  "key": "smtp_host"
}
```

### Bulk Set Configs

```http
POST /api/v1/config/bulk
Content-Type: application/json

{
  "configs": {
    "smtp_host": {
      "value": "smtp.example.com",
      "category": "smtp"
    },
    "smtp_port": {
      "value": "587",
      "category": "smtp"
    }
  }
}
```

### Export All Configs

```http
GET /api/v1/config/export/all
```

**Response:**
```json
{
  "configs": {
    "smtp_host": {
      "value": "smtp.example.com",
      "encrypted": false,
      "category": "smtp",
      "description": "SMTP server hostname"
    }
  },
  "total": 1
}
```

**Warning:** This returns all values including encrypted ones. Use for backup/migration only.

## Usage Examples

### Setting SMTP Configuration

```bash
# Set SMTP host
curl -X PUT http://localhost:8011/api/v1/config/smtp_host \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "smtp.gmail.com", "category": "smtp", "description": "SMTP server"}'

# Set SMTP password (encrypted)
curl -X PUT http://localhost:8011/api/v1/config/smtp_password \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "my_secret_password", "encrypted": true, "category": "smtp"}'
```

### Setting API Keys

```bash
# Set OpenAI API key
curl -X PUT http://localhost:8011/api/v1/config/openai_api_key \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"value": "sk-...", "encrypted": true, "category": "api_keys"}'
```

### Service Integration

Services can read configuration at startup or runtime:

```python
import httpx

async def get_config(key: str, token: str) -> str:
    """Get configuration value from Config API."""
    response = await httpx.get(
        f"http://deploy-api:8011/api/v1/config/{key}/raw",
        headers={"Authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    return response.json()["value"]

# Usage
openai_key = await get_config("openai_api_key", admin_token)
```

## Database Schema

The config table is created in the `busibox` database:

```sql
CREATE TABLE config (
    key VARCHAR(255) PRIMARY KEY,
    value TEXT NOT NULL,
    encrypted BOOLEAN DEFAULT false,
    category VARCHAR(50),
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
```

## Migration Guide

### From Vault to Config API

1. **Identify runtime configs** in your vault (SMTP, API keys, OAuth)
2. **Deploy the config table migration**:
   ```bash
   make install SERVICE=postgres
   ```
3. **Migrate values** using the bulk set endpoint or admin UI
4. **Update services** to read from Config API instead of environment variables
5. **Remove migrated values** from vault (optional, for clarity)

### Recommended Categories

| Category | Keys |
|----------|------|
| `smtp` | smtp_host, smtp_port, smtp_user, smtp_password, smtp_from_email, smtp_secure |
| `api_keys` | openai_api_key, huggingface_token, resend_api_key, bedrock_api_key, bedrock_region |
| `oauth` | microsoft_client_id, microsoft_client_secret, microsoft_tenant_id, github_oauth_client_id, github_oauth_client_secret |
| `email` | admin_email, allowed_email_domains |
| `feature_flags` | enable_feature_x, enable_feature_y |

## Security Considerations

1. **Encrypted flag**: Set `encrypted: true` for sensitive values. Currently this masks the value in API responses. Future: actual encryption at rest.

2. **Admin only**: All Config API endpoints require admin authentication.

3. **Audit logging**: Consider logging config changes for audit trail.

4. **Raw endpoint**: The `/raw` endpoint returns actual secret values. Use sparingly.

## Related Documentation

- [Vault Architecture](../configuration/vault-architecture.md)
- [Service Secrets](../configuration/service-secrets.md)
- [Deploy API](./deploy-api.md)
