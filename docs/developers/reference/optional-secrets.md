---
title: "Optional Secrets Reference"
category: "developer"
order: 128
description: "Required vs optional secrets configuration for applications"
published: true
---

# Optional Secrets Reference

## Overview

Applications can have both **required** and **optional** secrets. Required secrets must be present and non-empty in vault.yml, while optional secrets can be omitted or set to empty string.

## Configuration

### Application Definition

In `inventory/{env}/group_vars/all/00-main.yml`:

```yaml
applications:
  - name: ai-portal
    secrets:
      # Required secrets - must exist and be non-empty
      - database_url
      - better_auth_secret
      - sso_jwt_secret
    optional_secrets:
      # Optional secrets - can be empty or omitted
      - resend_api_key
      - stripe_api_key
```

### Vault Configuration

In `provision/ansible/roles/secrets/vars/vault.yml`:

```yaml
secrets:
  ai-portal:  # Note: hyphen becomes underscore in config
    # Required secrets
    database_url: "postgresql://..."
    better_auth_secret: "secret123"
    sso_jwt_secret: "jwt_secret"
    
    # Optional secrets - can be empty
    resend_api_key: ""  # Empty is OK for optional
    # stripe_api_key can be omitted entirely
```

## Behavior

### Required Secrets (`secrets`)

- **Must exist** in vault.yml
- **Must be non-empty** (length > 0)
- Deployment **fails** if missing or empty
- Error message shows which secret is missing

### Optional Secrets (`optional_secrets`)

- **Can be omitted** from vault.yml
- **Can be empty string** (`""`)
- Deployment **continues** if missing or empty
- If set and non-empty, included in `.env` file
- If missing or empty, commented out in `.env` file

## Generated .env File

When secrets are set:

```bash
# Required secrets from vault.yml
DATABASE_URL=postgresql://...
BETTER_AUTH_SECRET=secret123
SSO_JWT_SECRET=jwt_secret

# Optional secrets from vault.yml
RESEND_API_KEY=re_abc123  # Set if present

# Non-secret environment variables
NODE_ENV=production
```

When optional secrets are not set:

```bash
# Required secrets from vault.yml
DATABASE_URL=postgresql://...
BETTER_AUTH_SECRET=secret123
SSO_JWT_SECRET=jwt_secret

# Optional secrets from vault.yml
# RESEND_API_KEY=  # Optional - not set

# Non-secret environment variables
NODE_ENV=production
```

## Use Cases

### Email Services (Resend, SendGrid)

Make email API keys optional when email isn't configured:

```yaml
secrets:
  - database_url
optional_secrets:
  - resend_api_key
```

Application should check if `RESEND_API_KEY` is set before using email features.

### Payment Providers (Stripe, PayPal)

Make payment API keys optional for non-production environments:

```yaml
secrets:
  - database_url
optional_secrets:
  - stripe_api_key
  - stripe_webhook_secret
```

### Feature Flags

Optional API keys for optional features:

```yaml
secrets:
  - database_url
optional_secrets:
  - analytics_api_key
  - monitoring_api_key
  - search_api_key
```

### Multi-Environment

Different secrets for different environments:

**Production:**
```yaml
secrets:
  - database_url
  - stripe_api_key  # Required in production
optional_secrets:
  - resend_api_key
```

**Test:**
```yaml
secrets:
  - database_url
optional_secrets:
  - stripe_api_key  # Optional in test
  - resend_api_key
```

## Application Code

Handle optional secrets in your application:

```typescript
// Check if optional secret is set
const resendApiKey = process.env.RESEND_API_KEY;
const emailEnabled = resendApiKey && resendApiKey.length > 0;

if (emailEnabled) {
  // Use email service
  const resend = new Resend(resendApiKey);
  await resend.emails.send(...);
} else {
  // Skip email or use alternative
  console.log('Email service not configured');
}
```

## Validation

Ansible validates secrets during deployment:

**Required secrets:**
```
TASK [secrets : Validate all required secrets exist] ****
ok: [apps-lxc] => (item=ai-portal.database_url)
ok: [apps-lxc] => (item=ai-portal.better_auth_secret)
```

**Optional secrets:**
```
TASK [secrets : Validate optional secrets] ****
ok: [apps-lxc] => (item=ai-portal.resend_api_key) => {
    "msg": "Optional secret 'resend_api_key' for 'ai-portal': not set (optional)"
}
```

## Migration Guide

### Converting Required to Optional

1. **Move secret to optional_secrets:**

```yaml
# Before
secrets:
  - database_url
  - resend_api_key

# After
secrets:
  - database_url
optional_secrets:
  - resend_api_key
```

2. **Update vault.yml (can set to empty):**

```yaml
secrets:
  ai-portal:
    database_url: "postgresql://..."
    resend_api_key: ""  # Now can be empty
```

3. **Update application code** to handle missing secret.

4. **Redeploy:**

```bash
cd provision/ansible
make apps
```

### Adding New Optional Secret

1. **Add to application definition:**

```yaml
optional_secrets:
  - resend_api_key
  - new_optional_secret  # Add here
```

2. **Add to vault.yml (optional):**

```yaml
secrets:
  ai-portal:
    # Can omit entirely, or set to empty
    new_optional_secret: ""
```

3. **Deploy:**

```bash
cd provision/ansible
make apps
```

## Troubleshooting

### Error: "Missing or empty secret"

**Cause:** Secret is in `secrets` (required) but empty or missing.

**Solution:** Either:
1. Set the secret in vault.yml
2. Move to `optional_secrets` if not required

### Optional secret not in .env

**Expected:** Optional secrets only appear if set and non-empty.

**Check vault.yml:**
```yaml
secrets:
  ai-portal:
    resend_api_key: "actual_key_here"  # Must be non-empty
```

### Application fails without optional secret

**Cause:** Application doesn't handle missing optional secret.

**Solution:** Update application code:

```typescript
if (!process.env.OPTIONAL_SECRET) {
  console.warn('Optional feature disabled: secret not set');
  return;
}
```

## Best Practices

1. **Use optional_secrets for**:
   - External services that aren't always needed
   - Feature flags
   - Non-production environments
   - Third-party integrations

2. **Keep as required secrets**:
   - Database credentials
   - Authentication secrets
   - Core application secrets

3. **Document in code**:
   ```typescript
   // Optional: Email service API key
   // If not set, email features will be disabled
   const emailKey = process.env.RESEND_API_KEY;
   ```

4. **Test without optional secrets**:
   - Ensure application starts
   - Verify fallback behavior
   - Check error messages

5. **Environment-specific**:
   - Production may require more secrets
   - Test/dev can have more optional secrets

## Related Documentation

- [03-configure](../../administrators/03-configure.md) - Configuration and vault

