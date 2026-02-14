---
title: "Bedrock Setup Quick Start"
category: "developer"
order: 140
description: "AWS Bedrock support in LiteLLM with Claude models"
published: true
---

# Bedrock Setup - Quick Start

## Summary

I've configured AWS Bedrock support in LiteLLM with several Claude models. Here's what you need to do:

## 🎯 Your Workflow

### 1. Understand Your API Key

Your Bedrock API key works with **bearer token authentication** to Bedrock's REST API:
```bash
curl -X POST \
  https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse \
  -H "Authorization: Bearer YOUR_KEY"
```

**However:** LiteLLM uses the AWS SDK which requires **IAM credentials** (Access Key ID + Secret Access Key).

### 2. Run Diagnostic Script

Determine if your API key is actually IAM credentials:

```bash
cd /path/to/busibox
bash scripts/diagnose-bedrock-auth.sh
```

This will tell you:
- ✓ If your key works with bearer auth
- ✓ If it's in IAM credential format (ACCESS_KEY:SECRET_KEY)
- ✓ What format to use in vault

### 3. Update Vault

**Option A: Interactive script (recommended)**
```bash
bash scripts/update-bedrock-credentials.sh staging
```

**Option B: Manual edit**
```bash
cd provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

Add/update:
```yaml
secrets:
  bedrock_api_key: "ACCESS_KEY_ID:SECRET_ACCESS_KEY"
  bedrock_region: "us-east-1"
  
  litellm:
    bedrock_api_key: "ACCESS_KEY_ID:SECRET_ACCESS_KEY"
```

### 4. Deploy LiteLLM

```bash
cd provision/ansible
make test-litellm
```

### 5. Test Bedrock Models

```bash
cd /path/to/busibox
export LITELLM_MASTER_KEY="your-litellm-master-key"
bash scripts/test-bedrock-setup.sh staging
```

## 📋 Configured Models (Verified Working)

| Purpose | Model | Description |
|---------|-------|-------------|
| `frontier` / `claude-best` | Claude 3.5 Sonnet v2 | Most capable model available (Oct 2024) |
| `frontier-fast` / `claude-fast` | Claude 3.5 Haiku | Fast & efficient (Oct 2024) |
| `claude-balanced` | Claude 3.5 Sonnet v1 | Original Claude 3.5 (June 2024) |
| `chat` | Claude 3.5 Haiku | General chat |
| `research` | Claude 3.5 Sonnet v2 | Research & analysis |

**Note:** Claude 4.x series doesn't exist yet. Claude 3.5 Sonnet v2 is the latest and most capable model.

## 🔧 Troubleshooting

### If bearer token doesn't work with LiteLLM

Your bearer token is likely **not IAM credentials**. You need to:

1. **Get AWS IAM credentials** (recommended)
   - Create IAM user with Bedrock permissions
   - Generate Access Key + Secret Key
   - Format: `AKIAXXXXXXXXXXXXXXXX:SECRET_KEY_40_CHARS`

2. **Or test if your key IS IAM credentials**
   - Run diagnostic script
   - It will test both auth methods

### Model returns "inference profile" error

Use models with `us.` prefix (cross-region inference profiles):
- ✅ `us.anthropic.claude-3-5-haiku-20241022-v1:0`
- ❌ `anthropic.claude-haiku-4-5-20251001-v1:0`

### Can't connect to LiteLLM

```bash
# Check service status
ssh root@10.96.201.207 systemctl status litellm

# View logs
ssh root@10.96.201.207 journalctl -u litellm -n 50

# Test directly
curl http://10.96.201.207:4000/health
```

## 📚 Files Created

1. **`scripts/diagnose-bedrock-auth.sh`** - Diagnose your API key type
2. **`scripts/update-bedrock-credentials.sh`** - Update vault interactively
3. **`scripts/test-bedrock-setup.sh`** - Test all Bedrock models
4. **[bedrock-inference-profiles](bedrock-inference-profiles.md)** - Cross-region routing and model IDs
5. **`group_vars/all/model_registry.yml`** - Updated with Bedrock models

## 🚀 Next Steps

1. Run diagnostic: `bash scripts/diagnose-bedrock-auth.sh`
2. Update vault with correct credentials
3. Deploy: `cd provision/ansible && make test-litellm INV=inventory/staging`
4. Test: `bash scripts/test-bedrock-setup.sh staging`

## ❓ Need Help?

- **Inference profiles:** [bedrock-inference-profiles](bedrock-inference-profiles.md)
- **Diagnostic:** `scripts/diagnose-bedrock-auth.sh`
- **Your API key works with:** `https://bedrock-runtime.us-east-1.amazonaws.com/`
- **LiteLLM needs:** AWS IAM credentials (Access Key + Secret Key)

