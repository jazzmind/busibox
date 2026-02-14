---
title: "LiteLLM Master Key"
category: "developer"
order: 132
description: "Admin UI password and API authentication for LiteLLM"
published: true
---

# LiteLLM Master Key Reference

**Created:** 2025-11-04  
**Status:** Active  
**Category:** Reference

## Overview

The LiteLLM Master Key serves two purposes:
1. **Admin UI Password** - Used to log into the LiteLLM admin interface
2. **API Authentication** - Used as a Bearer token for API requests

## Configuration

### Finding Your Master Key

#### Option 1: From Secrets File (Production)
```bash
# On your admin workstation
cd provision/ansible
ansible-vault view roles/secrets/vars/vault.yml | grep -A2 "litellm:"
```

#### Option 2: From Container (Test Environment)
```bash
# SSH to LiteLLM container
ssh root@10.96.201.208

# View the master key
cat /etc/default/litellm | grep LITELLM_MASTER_KEY
```

#### Option 3: From Ansible Inventory (Local Dev)
```bash
cat provision/ansible/inventory/local/group_vars/all.yml | grep litellm_master_key
```

### Default Value

If not set in secrets, the default is:
```
sk-litellm-master-key-change-me
```

**⚠️ WARNING:** Change this in production!

## Usage

### Admin UI Access

**URL (Test):** `http://10.96.201.208:4000` or `https://test.yourdomain.com/litellm`

**Login Credentials:**
- **Username:** `admin`
- **Password:** `<your-master-key>` (e.g., `sk-litellm-master-key-change-me`)

### API Access

#### Via curl
```bash
# List models
curl http://10.96.201.208:4000/v1/models \
  -H "Authorization: Bearer sk-litellm-master-key-change-me"

# Chat completion
curl http://10.96.201.208:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-litellm-master-key-change-me" \
  -d '{
    "model": "phi-4-multimodal",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

#### From AI Portal (already configured)
The AI Portal automatically uses the master key from environment variables:
```env
LITELLM_API_KEY=sk-litellm-master-key-change-me
LITELLM_BASE_URL=http://10.96.201.208:4000/v1
```

## Setting a Custom Master Key

### Option 1: Via Ansible Vault (Recommended)

1. **Decrypt the vault:**
   ```bash
   cd provision/ansible
   ansible-vault edit roles/secrets/vars/vault.yml
   ```

2. **Add/Update the master key:**
   ```yaml
   secrets:
     litellm:
       master_key: "sk-your-custom-key-here"
       database_url: "postgresql://..."
   ```

3. **Redeploy LiteLLM:**
   ```bash
   cd provision/ansible
   make llm-litellm INV=staging
   ```

### Option 2: Via Inventory Override (Staging Only)

Edit `provision/ansible/inventory/staging/group_vars/all/00-main.yml`:
```yaml
# Override the default master key (staging only)
litellm_master_key: "sk-test-custom-key-123"
```

Then redeploy.

## Security Best Practices

### Master Key Format
- Should start with `sk-` (convention)
- Use at least 32 random characters
- Mix of letters, numbers, and special characters

### Generate a Strong Key
```bash
# Option 1: Using openssl
echo "sk-$(openssl rand -hex 32)"

# Option 2: Using Python
python3 -c "import secrets; print(f'sk-{secrets.token_hex(32)}')"
```

### Key Rotation

1. Generate new key
2. Update in vault/inventory
3. Redeploy LiteLLM
4. Update AI Portal environment variables
5. Restart AI Portal

## Troubleshooting

### "Authentication Error" when calling API

**Cause:** Missing or incorrect Bearer token

**Fix:**
```bash
# Always include Authorization header
curl http://10.96.201.208:4000/v1/models \
  -H "Authorization: Bearer <your-master-key>"
```

### Can't login to admin UI

**Symptoms:**
- Username/password rejected
- Blank page after login

**Solutions:**

1. **Verify master key:**
   ```bash
   ssh root@10.96.201.208
   cat /etc/default/litellm | grep LITELLM_MASTER_KEY
   ```

2. **Check LiteLLM logs:**
   ```bash
   journalctl -u litellm -n 50
   ```

3. **Verify service is running:**
   ```bash
   systemctl status litellm
   curl http://localhost:4000/health
   ```

### Health endpoint returns 401

**This is normal!** The health endpoint is public and should **NOT** require authentication.

If it's requiring auth, check your LiteLLM config:
```yaml
general_settings:
  public_routes: ["/health", "/health/readiness", "/health/liveliness"]
```

## Related Documentation

- **LiteLLM Official Docs:** https://docs.litellm.ai/docs/proxy/virtual_keys
- **API Authentication:** https://docs.litellm.ai/docs/proxy/token_auth
- **Secrets Management:** See [03-configure](../../administrators/03-configure.md) for vault and configuration
- **LiteLLM Role README:** `provision/ansible/roles/litellm/README.md`

