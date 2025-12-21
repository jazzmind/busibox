# AWS Bedrock Configuration Guide

**Created:** 2025-01-22  
**Status:** Active  
**Category:** Configuration

## Overview

This guide explains how to configure AWS Bedrock models in LiteLLM for the Busibox platform.

## Prerequisites

- AWS Bedrock API access
- Bedrock API key or IAM credentials
- Access to appropriate Bedrock models in us-east-1

## Understanding Bedrock Authentication

### Two Authentication Methods

1. **AWS IAM Credentials** (Standard - what LiteLLM uses)
   - Access Key ID (20 chars, e.g., `AKIAIOSFODNN7EXAMPLE`)
   - Secret Access Key (40 chars, e.g., `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`)
   - Used by AWS SDK (boto3) and LiteLLM
   
2. **Bedrock API Key** (Direct HTTP)
   - Bearer token for direct HTTP API calls
   - Works with Bedrock REST API
   - **Not directly compatible with LiteLLM**

### Inference Profiles

AWS Bedrock requires **inference profiles** for newer models (Claude 4.x+):

- **Direct model IDs** (old): `anthropic.claude-3-opus-20240229-v1:0`
- **Inference profiles** (new): `us.anthropic.claude-3-5-haiku-20241022-v1:0`

The `us.` prefix indicates a cross-region inference profile for US regions.

## Model Configuration

### Configured Models

Currently configured in `model_registry.yml`:

| Purpose | Model ID | Description |
|---------|----------|-------------|
| `frontier` | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` | Most capable Claude 3.5 |
| `frontier-fast` | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | Fast Claude 3.5 |
| `advanced` | `anthropic.claude-3-opus-20240229-v1:0` | High intelligence |
| `balanced` | `anthropic.claude-3-sonnet-20240229-v1:0` | Balanced performance |

### Model IDs That Work

✅ **Working models** (confirmed with your API key):
- `us.anthropic.claude-3-5-haiku-20241022-v1:0`
- `us.anthropic.claude-3-5-sonnet-20241022-v2:0`

❌ **Models requiring inference profiles** (may not work):
- `anthropic.claude-haiku-4-5-20251001-v1:0` - Requires inference profile

## Configuration Steps

### Step 1: Diagnose Your API Key

Run the diagnostic script to determine your authentication method:

```bash
cd /path/to/busibox
bash scripts/diagnose-bedrock-auth.sh
```

This will:
- Test if your API key works with bearer token auth
- Check if it's actually AWS IAM credentials
- Determine the correct format for vault configuration

### Step 2: Update Vault with Credentials

Edit the vault file:

```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

**If you have IAM credentials (ACCESS_KEY_ID:SECRET_ACCESS_KEY):**

```yaml
secrets:
  bedrock_api_key: "AKIAIOSFODNN7EXAMPLE:wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
  bedrock_region: "us-east-1"
```

**If you have a bearer token API key:**

Unfortunately, LiteLLM doesn't support Bedrock bearer tokens directly. Options:

1. **Get IAM credentials** (recommended)
   - Create IAM user with Bedrock permissions
   - Generate Access Key + Secret Key
   
2. **Use a proxy** (advanced)
   - Create custom proxy that converts bearer → AWS SDK
   - Point LiteLLM to proxy

### Step 3: Deploy Configuration

Deploy LiteLLM to test environment:

```bash
cd provision/ansible
make test-litellm
```

Or deploy all services:

```bash
make test
```

### Step 4: Test Configuration

Run the comprehensive test script:

```bash
cd /path/to/busibox
export LITELLM_MASTER_KEY="your-litellm-master-key"
bash scripts/test-bedrock-setup.sh test
```

This will test:
- ✓ LiteLLM connectivity
- ✓ Model availability
- ✓ Inference for each Bedrock model
- ✓ Streaming responses

## Troubleshooting

### Error: "on-demand throughput isn't supported"

**Cause:** Model requires inference profile, not direct model ID.

**Solution:** Use inference profile format (with `us.` prefix):
- ❌ `anthropic.claude-haiku-4-5-20251001-v1:0`
- ✅ `us.anthropic.claude-3-5-haiku-20241022-v1:0`

### Error: "The security token included in the request is invalid"

**Cause:** Incorrect AWS credentials or bearer token used with AWS SDK.

**Solution:**
1. Verify credentials format in vault
2. Check that you're using IAM credentials, not bearer token
3. Test with diagnostic script

### Error: "Model not found in LiteLLM"

**Cause:** Model not configured in `model_registry.yml` or deployment didn't run.

**Solution:**
1. Check `group_vars/all/model_registry.yml` has Bedrock models
2. Re-deploy LiteLLM: `make test-litellm`
3. Verify with: `curl http://10.96.201.207:4000/v1/models`

### Error: "LiteLLM not accessible"

**Cause:** Service not running or network issue.

**Solution:**
```bash
# Check if container is running
pct status 307  # test environment

# SSH into container
ssh root@10.96.201.207

# Check service status
systemctl status litellm

# View logs
journalctl -u litellm -n 50 --no-pager

# Check config
cat /etc/litellm/config.yaml
```

## Testing Direct Bedrock Access

Test Bedrock API directly (bypass LiteLLM):

```bash
# With bearer token (if that's what you have)
curl -X POST \
  https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "messages": [{
      "role": "user",
      "content": [{"text": "Hello!"}]
    }]
  }'
```

## IAM Permissions Required

If using IAM credentials, ensure the IAM user/role has these permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "bedrock:InvokeModel",
        "bedrock:InvokeModelWithResponseStream",
        "bedrock:ListFoundationModels",
        "bedrock:GetFoundationModel"
      ],
      "Resource": "*"
    }
  ]
}
```

## Adding New Models

To add new Bedrock models:

1. **Edit model registry:**
   ```bash
   vi provision/ansible/group_vars/all/model_registry.yml
   ```

2. **Add model entry:**
   ```yaml
   available_models:
     "claude-opus-3-5":
       provider: "bedrock"
       model: "us.anthropic.claude-3-5-opus-YYYYMMDD-v1:0"
       model_name: "us.anthropic.claude-3-5-opus-YYYYMMDD-v1:0"
       description: "Claude 3.5 Opus"
   ```

3. **Add purpose mapping:**
   ```yaml
   model_purposes:
     advanced-reasoning: "claude-opus-3-5"
   ```

4. **Deploy:**
   ```bash
   cd provision/ansible
   make test-litellm
   ```

## Reference

- **Diagnostic Script:** `scripts/diagnose-bedrock-auth.sh`
- **Test Script:** `scripts/test-bedrock-setup.sh`
- **Model Registry:** `provision/ansible/group_vars/all/model_registry.yml`
- **Vault:** `provision/ansible/roles/secrets/vars/vault.yml`
- **LiteLLM Config:** `provision/ansible/roles/litellm/`

## Related Documentation

- [LiteLLM Deployment](deployment/litellm.md)
- [Model Configuration](configuration/model-configuration.md)
- [Vault Secrets](configuration/vault-secrets.md)

