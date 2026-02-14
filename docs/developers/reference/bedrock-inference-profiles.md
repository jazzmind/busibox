---
title: "AWS Bedrock Inference Profiles"
category: "developer"
order: 138
description: "Cross-region routing and model access for AWS Bedrock"
published: true
---

# AWS Bedrock Inference Profiles Explained

**Created:** 2025-01-22  
**Category:** Reference  
**Status:** Active

## What Are Inference Profiles?

Inference profiles are AWS Bedrock's way of routing model requests across regions and managing model access.

### Key Concepts

**1. Cross-Region Routing**
- Automatically route requests to regions with available capacity
- Increase throughput and reduce latency
- Required for Claude 3.5 models

**2. Model ID Format**

```
# Without inference profile (legacy Claude 3)
anthropic.claude-3-sonnet-20240229-v1:0

# With inference profile (Claude 3.5)
us.anthropic.claude-3-5-sonnet-20241022-v2:0
    └─ Region prefix indicates cross-region profile
```

**3. Regional Prefixes**
- `us.` - US cross-region inference profile (routes within US regions)
- `eu.` - Europe cross-region inference profile
- No prefix - Direct model ID (may require provisioned throughput)

## Why Inference Profiles?

### Problem Without Profiles

```
POST /model/anthropic.claude-3-5-sonnet-20241022-v2:0/converse
❌ Error: "on-demand throughput isn't supported"
```

### Solution With Profiles

```
POST /model/us.anthropic.claude-3-5-sonnet-20241022-v2:0/converse
✅ Success: Routes to available region automatically
```

## Claude Model Versions

### Current Models (2025)

| Model | Inference Profile Required | Model ID |
|-------|---------------------------|----------|
| **Claude 3.5 Sonnet v2** | ✅ Yes | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` |
| **Claude 3.5 Haiku** | ✅ Yes | `us.anthropic.claude-3-5-haiku-20241022-v1:0` |
| **Claude 3.5 Sonnet v1** | ✅ Yes | `us.anthropic.claude-3-5-sonnet-20240620-v1:0` |
| **Claude 3 Sonnet** | ❌ No | `anthropic.claude-3-sonnet-20240229-v1:0` |
| **Claude 3 Haiku** | ❌ No | `anthropic.claude-3-haiku-20240307-v1:0` |
| **Claude 3 Opus** | ❌ No | `anthropic.claude-3-opus-20240229-v1:0` |

### ⚠️ Important Notes

1. **Claude 4.x doesn't exist yet** (as of Jan 2025)
2. **Claude 3.5 Sonnet v2** is the most capable model available
3. **Claude 3 Opus** may require higher access tier
4. Inference profiles provide **better availability** than direct model IDs

## Configuration in Busibox

### Model Registry Format

```yaml
available_models:
  "claude-sonnet-3-5-v2":
    provider: "bedrock"
    model: "us.anthropic.claude-3-5-sonnet-20241022-v2:0"  # Full inference profile ID
    model_name: "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    description: "Claude 3.5 Sonnet v2 - Most capable"
```

### LiteLLM Configuration

LiteLLM automatically handles inference profiles:

```python
# Your code
response = litellm.completion(
    model="frontier",  # Maps to claude-sonnet-3-5-v2
    messages=[{"role": "user", "content": "Hello"}]
)

# LiteLLM calls Bedrock with
model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
```

## Benefits of Inference Profiles

### 1. Higher Availability
- Automatic failover between regions
- Better uptime during regional issues
- Reduced rate limiting

### 2. Better Performance
- Lower latency (routes to nearest available region)
- Higher throughput
- Automatic load balancing

### 3. Usage Tracking
- Tag inference profiles for cost allocation
- Track usage by department/project
- Better billing insights

### 4. Required for Latest Models
- All Claude 3.5 models require inference profiles
- Future models will likely require them
- Legacy models (Claude 3) work without them

## Common Errors

### Error: "on-demand throughput isn't supported"

**Cause:** Trying to use direct model ID instead of inference profile

**Solution:**
```diff
- model: "anthropic.claude-3-5-sonnet-20241022-v2:0"
+ model: "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
```

### Error: "The provided model identifier is invalid"

**Cause:** Model doesn't exist or you don't have access

**Solutions:**
1. Check model ID spelling
2. Verify model is available in your region
3. Confirm you have access to the model
4. Use `scripts/list-bedrock-profiles.sh` to test available models

### Error: "Model ID anthropic.claude-4-5-haiku not found"

**Cause:** Claude 4.x series doesn't exist yet!

**Solution:** Use Claude 3.5 models:
- `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (best)
- `us.anthropic.claude-3-5-haiku-20241022-v1:0` (fast)

## Testing Inference Profiles

### Check Available Models

```bash
cd /path/to/busibox
export BEDROCK_API_KEY="your-key"
bash scripts/list-bedrock-profiles.sh
```

### Test Specific Model

```bash
curl -k -X POST \
  "https://bedrock-runtime.us-east-1.amazonaws.com/model/us.anthropic.claude-3-5-haiku-20241022-v1:0/converse" \
  -H "Authorization: Bearer $BEDROCK_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":[{"text":"Hello"}]}]}'
```

## Best Practices

### 1. Always Use Inference Profiles for Claude 3.5

✅ **Good:**
```yaml
model: "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
```

❌ **Bad:**
```yaml
model: "anthropic.claude-3-5-sonnet-20241022-v2:0"
```

### 2. Use Latest Model Versions

- Claude 3.5 Sonnet **v2** (Oct 2024) is better than v1 (June 2024)
- Always check for new versions

### 3. Choose Right Regional Profile

- `us.` for US-based applications
- `eu.` for Europe-based applications
- Consider data residency requirements

### 4. Test Before Deploying

Always test new model IDs:
```bash
bash scripts/list-bedrock-profiles.sh
```

## Reference

### Useful Scripts
- `scripts/list-bedrock-profiles.sh` - Find available models
- `scripts/diagnose-bedrock-auth.sh` - Test API key
- `scripts/test-bedrock-setup.sh` - Test LiteLLM configuration

### Documentation
- [AWS Bedrock Inference Profiles](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles.html)
- [Anthropic Claude Models](https://www.anthropic.com/claude)
- Busibox: [bedrock-quickstart](bedrock-quickstart.md)

### Model Registry
- Location: `provision/ansible/group_vars/all/model_registry.yml`
- Purpose mappings: `model_purposes` section
- Available models: `available_models` section

