---
title: "Model Registry Usage"
category: "developer"
order: 129
description: "Single source of truth for LLM model configurations"
published: true
---

# Model Registry Usage Guide

**Status:** Active  
**Created:** 2025-11-17  
**Updated:** 2025-11-17  
**Category:** Reference

## Overview

The Model Registry (`provision/ansible/group_vars/all/model_registry.yml`) is the single source of truth for all LLM model configurations in the Busibox platform. It defines which models are used for which purposes and their configurations.

## Purpose

- **Centralized Configuration**: One place to define all model mappings
- **Easy Model Swapping**: Change models without touching code
- **Environment Flexibility**: Different models for test vs production
- **Documentation**: Clear mapping between purposes and actual models

## Registry Structure

```yaml
model_purposes:
  <purpose_name>:
    model: "<short_name>"              # API-friendly name (e.g., "qwen3-embedding")
    model_name: "<hf_path>"            # Full HuggingFace path (e.g., "Qwen/Qwen3-Embedding-8B")
    description: "<description>"       # Human-readable description
    max_tokens: <integer>              # Maximum context length
    temperature: <float>               # (Optional) Default temperature
    provider: "<provider>"             # Which service provides it (litellm, colpali, etc.)
    endpoint: "<api_endpoint>"         # API endpoint path
```

### Example

```yaml
embedding:
  model: "qwen3-embedding"
  model_name: "Qwen/Qwen3-Embedding-8B"
  description: "Text embedding generation"
  max_tokens: 8192
  provider: "litellm"
  endpoint: "/embeddings"
```

## Defined Model Purposes

| Purpose | Model | Description | Provider |
|---------|-------|-------------|----------|
| `embedding` | qwen3-embedding | Text embeddings (4096 dims) | litellm/vLLM |
| `visual-embedding` | colpali-v1.3 | Visual document embeddings | colpali |
| `vision` | qwen3-vl-8b | Vision-language model | litellm/vLLM |
| `fast` | phi-4 | Fast chat model (6B params) | litellm/vLLM |
| `default` | qwen3-30b-instruct | General purpose chat (30B params) | litellm/vLLM |
| `cleanup` | phi-4 | Text cleanup and formatting | litellm/vLLM |
| `chat` | qwen3-30b-instruct | General chat and Q&A | litellm/vLLM |
| `classify` | phi-4 | Document classification | litellm/vLLM |
| `research` | qwen3-30b-instruct | Research and analysis | litellm/vLLM |
| `analysis` | qwen3-30b-instruct | Data analysis | litellm/vLLM |
| `parsing` | phi-4 | Document parsing | litellm/vLLM |

## How to Reference the Registry

### In Ansible Files

Use Jinja2 syntax to reference the registry:

```yaml
# Reference the short model name
embedding_model: "{{ model_purposes.embedding.model }}"

# Reference the full HuggingFace path
vllm_model: "{{ model_purposes.fast.model_name }}"

# Reference with fallback
embedding_dimension: "{{ model_purposes.embedding.max_tokens | default(32768) }}"

# Reference models with hyphens in the name
colpali_model: "{{ model_purposes['visual-embedding'].model_name }}"
```

### In Python Code

Read from environment variables (set by Ansible from the registry):

```python
# Good: Read from environment/config
embedding_model = config.get("embedding_model")
# Or with fallback
embedding_model = config.get("embedding_model", "qwen3-embedding")

# Bad: Hardcoded
embedding_model = "qwen3-embedding"  # DON'T DO THIS
```

### In Shell Scripts

Document the registry mapping in comments:

```bash
# Reference registry purposes in comments
MODELS=(
    "microsoft/Phi-4-multimodal-instruct"  # fast.model_name
    "Qwen/Qwen3-Embedding-8B"              # embedding.model_name  
    "vidore/colpali-v1.3"                  # visual-embedding.model_name
)

# Or use environment variables set by Ansible
EMBEDDING_MODEL="${EMBEDDING_MODEL:-qwen3-embedding}"  # model_purposes.embedding.model
```

## Common Use Cases

### Changing a Model for a Purpose

1. Update `model_registry.yml`:
   ```yaml
   embedding:
     model: "new-embedding-model"
     model_name: "VendorName/new-embedding-8B"
     description: "New embedding model"
     # ... other fields
   ```

2. Redeploy affected services:
   ```bash
   cd provision/ansible
   ansible-playbook -i inventory/production/hosts.yml site.yml --tags vllm_embedding,ingest
   ```

### Environment-Specific Models

Override in inventory files:

```yaml
# inventory/test/group_vars/all/00-main.yml
model_purposes:
  default:
    model: "phi-4"  # Use smaller model for testing
    model_name: "microsoft/Phi-4-multimodal-instruct"
```

### Adding a New Model Purpose

1. Add to `model_registry.yml`:
   ```yaml
   model_purposes:
     summarization:
       model: "phi-4-summary"
       model_name: "microsoft/Phi-4-multimodal-instruct"
       description: "Document summarization"
       max_tokens: 8192
       temperature: 0.3
       provider: "litellm"
       endpoint: "/chat/completions"
   ```

2. Configure liteLLM to serve it (if needed)
3. Update services to use the new purpose

## Files That Reference the Registry

### Ansible Configuration
- `inventory/*/group_vars/all/00-main.yml` - Environment-specific values
- `roles/litellm/defaults/main.yml` - LiteLLM model routing
- `roles/vllm_embedding/defaults/main.yml` - vLLM embedding service
- `roles/colpali/defaults/main.yml` - ColPali visual embeddings
- `roles/ingest/templates/*.env.j2` - Ingest service environment

### Scripts
- `provision/pct/host/setup-llm-models.sh` - Pre-download models
- `scripts/test-vllm-embedding.sh` - Test embedding service

### Application Code
- `srv/ingest/src/processors/embedder.py` - Embedding generation
- Any service calling LLM APIs

## Deployment Flow

1. **Update Registry**: Edit `model_registry.yml`
2. **Ansible Reads Registry**: Variables loaded into Ansible context
3. **Templates Generated**: Ansible renders templates with model values
4. **Services Configured**: Environment variables set from registry
5. **JSON Deployed**: Registry deployed to `/etc/ingest/model_registry.json`
6. **Services Use Models**: Applications read from environment or JSON

## Validation

### Check for Hardcoded Models

```bash
cd provision/ansible

# Find hardcoded model names (should only be in registry and comments)
grep -r "qwen3-embedding\|Phi-4\|Qwen3-VL" --include="*.yml" --include="*.j2" .

# Check for proper registry references
grep -r "model_purposes\." --include="*.yml" --include="*.j2" .
```

### Test Registry Changes

```bash
# 1. Update model_registry.yml
# 2. Validate syntax
ansible-playbook provision/ansible/site.yml --syntax-check

# 3. Deploy to test environment
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml --tags ingest,vllm

# 4. Test the changes
bash scripts/test-vllm-embedding.sh
```

## Troubleshooting

### Model Not Found

**Symptom**: Service fails to load model  
**Solution**: Verify model is pre-downloaded:
```bash
ssh root@proxmox
ls /var/lib/llm-models/huggingface/hub/models--*
```

### Wrong Model Being Used

**Symptom**: Service uses unexpected model  
**Solution**: Check Ansible variable precedence:
1. Inventory-specific overrides (`inventory/*/group_vars/`)
2. Role defaults (`roles/*/defaults/main.yml`)
3. Master registry (`group_vars/all/model_registry.yml`)

### Registry Not Deployed

**Symptom**: Services can't find `/etc/ingest/model_registry.json`  
**Solution**: Redeploy with model_registry tag:
```bash
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags model_registry
```

## Best Practices

1. **Always Reference, Never Hardcode**: Use Jinja2 references in Ansible
2. **Document Script Mappings**: Add comments mapping to registry purposes
3. **Use Defaults**: Provide fallback values for robustness
4. **Test Before Production**: Validate registry changes in test environment
5. **Keep Registry Updated**: Update registry when changing models
6. **Version Control**: Track registry changes in git

## Related Documentation

- `.cursor/rules/003-model-registry.md` - Development rules for referencing registry
- `provision/ansible/group_vars/all/model_registry.yml` - Master registry
- `provision/ansible/roles/ingest/tasks/model_registry.yml` - Deployment tasks
- `docs/configuration/llm-models.md` - LLM model configuration guide

## Migration Notes

Prior to 2025-11-17, model names were hardcoded across multiple files. This refactoring:
- Centralized all model definitions to the registry
- Updated all Ansible roles to reference the registry
- Updated scripts to document registry mappings
- Created developer documentation for proper usage

## Support

For questions or issues:
1. Check registry syntax: `ansible-playbook --syntax-check`
2. Verify variable values: `ansible-inventory --list`
3. Review deployment logs: `journalctl -u <service>`
4. Consult developer guide: `.cursor/rules/003-model-registry.md`

