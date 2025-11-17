# Model Registry Rule

**ALWAYS** reference the model registry when configuring LLM models.

## Purpose

The model registry (`provision/ansible/group_vars/all/model_registry.yml`) is the single source of truth for:
- Which models are used for which purposes (embedding, vision, chat, etc.)
- Model names (HuggingFace paths)
- Model configurations (max_tokens, temperature, endpoints)

## Rule: Reference, Don't Repeat

### ❌ **NEVER** hardcode model names

```yaml
# BAD: Hardcoded model name
embedding_model: "qwen3-embedding"
vllm_default_model: "microsoft/Phi-4-multimodal-instruct"
```

```python
# BAD: Hardcoded in Python
self.primary_model = "qwen3-embedding"
```

```bash
# BAD: Hardcoded in bash script
MODEL="Qwen/Qwen3-Embedding-8B"
```

### ✅ **ALWAYS** reference the registry

#### In Ansible Files (.yml, .j2)

Use Jinja2 references:

```yaml
# GOOD: Reference registry
embedding_model: "{{ model_purposes.embedding.model }}"
vllm_default_model: "{{ model_purposes.fast.model_name }}"

# GOOD: In litellm config
litellm_models:
  - model_name: "{{ model_purposes.embedding.model }}"
    litellm_params:
      model: "openai/{{ model_purposes.embedding.model }}"
```

#### In Python Code

Read from config/environment variables (set by Ansible from registry):

```python
# GOOD: Read from environment
self.primary_model = config.get("embedding_model", "qwen3-embedding")  # default as fallback
```

#### In Shell Scripts

Add comments mapping to registry purposes:

```bash
# GOOD: Document registry mapping
MODELS=(
    "microsoft/Phi-4-multimodal-instruct"  # fast.model_name
    "Qwen/Qwen3-Embedding-8B"              # embedding.model_name  
    "vidore/colpali-v1.3"                  # visual-embedding.model_name
)
```

Or read from environment variables set by Ansible.

## Model Registry Structure

```yaml
model_purposes:
  embedding:
    model: "qwen3-embedding"              # Short name for API calls
    model_name: "Qwen/Qwen3-Embedding-8B" # Full HuggingFace path
    description: "Text embedding generation"
    max_tokens: 8192
    provider: "litellm"
    endpoint: "/embeddings"
```

### Key Fields

- `model`: Short name used in API calls (e.g., "qwen3-embedding")
- `model_name`: Full HuggingFace model path (e.g., "Qwen/Qwen3-Embedding-8B")
- `description`: Human-readable purpose
- `max_tokens`: Maximum context length
- `provider`: Which service provides the model (litellm, colpali, etc.)
- `endpoint`: API endpoint path
- `temperature`: (optional) Default temperature for generation

## Why This Matters

1. **Single Source of Truth**: Change a model in one place, updates everywhere
2. **Environment Flexibility**: Different models for test vs production
3. **Documentation**: Clear mapping between purposes and models
4. **Maintainability**: No hunting for hardcoded model names across codebase

## Common Model Purposes

- `embedding` - Dense text embeddings (e.g., for semantic search)
- `visual-embedding` - Visual document embeddings (e.g., ColPali for PDFs)
- `vision` - Vision-language models (e.g., for image understanding)
- `fast` - Fast, lightweight chat models (e.g., Phi-4)
- `default` - General purpose chat (e.g., Qwen3-30B)
- `cleanup` - Text cleanup and formatting
- `chat` - General chat and Q&A
- `classify` - Document classification
- `research` - Research and analysis
- `analysis` - Data analysis
- `parsing` - Document parsing

## Files That Reference Registry

### Ansible
- `inventory/*/group_vars/all/00-main.yml` - Environment-specific overrides
- `roles/litellm/defaults/main.yml` - LiteLLM model routing
- `roles/vllm_embedding/defaults/main.yml` - vLLM embedding service
- `roles/ingest/templates/*.env.j2` - Ingest service configuration

### Scripts
- `provision/pct/host/setup-llm-models.sh` - Pre-download models
- `scripts/test-vllm-embedding.sh` - Test embedding service

### Application Code
- `srv/ingest/src/processors/embedder.py` - Embedding generation
- Any service that calls LLM APIs

## Deployment Flow

1. **Define**: Update `model_registry.yml` to change model mappings
2. **Deploy**: Ansible reads registry and:
   - Configures vLLM to serve models
   - Configures liteLLM to route requests
   - Sets environment variables for services
   - Deploys JSON registry to `/etc/ingest/model_registry.json`
3. **Use**: Services read from:
   - Environment variables (set by Ansible)
   - JSON registry file (for runtime lookups)
   - Never hardcoded values

## Checking Your Work

Before committing, verify:

```bash
# Check for hardcoded model names in Ansible
cd provision/ansible
grep -r "qwen3-embedding\|Phi-4\|Qwen3-VL" --include="*.yml" --include="*.j2" .

# Should only find:
# - model_registry.yml (source of truth)
# - Comments/documentation
# - Default fallbacks in code

# Check for Jinja2 references
grep -r "model_purposes\." --include="*.yml" --include="*.j2" .

# Should find multiple references
```

## When to Update Registry

Update `model_registry.yml` when:
- Switching to a different model for a purpose
- Adding a new model purpose
- Changing model parameters (max_tokens, temperature)
- Deploying to a new environment with different models

## Related Files

- `provision/ansible/group_vars/all/model_registry.yml` - Master registry
- `provision/ansible/roles/ingest/templates/model_registry.json.j2` - JSON template for services
- `provision/ansible/roles/ingest/tasks/model_registry.yml` - Deployment tasks

