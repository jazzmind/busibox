# Embedding Configuration Rules

## Overview

Busibox supports configurable embedding models with dynamic dimensions. **The model registry is the single source of truth** for embedding configuration.

## Single Source of Truth

**ALL embedding configuration must come from `model_registry.yml`.**

DO NOT hardcode embedding models or dimensions anywhere. Use Jinja2 lookups in Ansible templates.

## Configuration Flow

```
model_registry.yml (Ansible - SINGLE SOURCE OF TRUTH)
    ↓
Ansible templates derive values via Jinja2 lookups
    ↓
Environment variables set in services
    ↓
model_registry.json (deployed to /etc/ingest/)
    ↓
Runtime: ModelRegistry class (busibox_common.llm)
```

## Model Registry Schema

In `provision/ansible/group_vars/all/model_registry.yml`:

```yaml
available_models:
  "bge-large":
    provider: "fastembed"
    model: "bge-large-en-v1.5"
    model_name: "BAAI/bge-large-en-v1.5"
    description: "Text embedding generation (1024-d, production)"
    dimension: 1024  # REQUIRED for embedding models
    
  "nomic-embed":
    provider: "fastembed"
    model: "nomic-embed-text-v1.5"
    model_name: "nomic-ai/nomic-embed-text-v1.5"
    description: "Matryoshka embedding"
    dimension: 768
    matryoshka: true  # Indicates Matryoshka support
    matryoshka_dimensions: [64, 128, 256, 512, 768]  # Available truncation dims

model_purposes:
  embedding: "bge-large"  # Default embedding model - CHANGE THIS TO SWITCH MODELS
```

## Ansible Template Pattern

**ALWAYS** use this Jinja2 pattern in `.env.j2` templates to derive embedding config:

```jinja2
# Embedding configuration (derived from model_registry.yml)
{% set embedding_model_key = model_purposes.embedding | default('bge-large') %}
{% set embedding_model_config = available_models[embedding_model_key] | default({}) %}
FASTEMBED_MODEL={{ embedding_model_config.model_name | default('BAAI/bge-large-en-v1.5') }}
EMBEDDING_DIMENSION={{ embedding_model_config.dimension | default(1024) }}
```

This pattern is used in:
- `roles/ingest/templates/ingest.env.j2`
- `roles/agent_api/templates/agent-api.env.j2`
- `roles/search_api/templates/search-api.env.j2`
- `roles/milvus/tasks/main.yml` (for schema init)

## Getting Embedding Config in Code

### Python (busibox_common)

```python
from busibox_common.llm import get_registry

registry = get_registry()

# Get full config
config = registry.get_embedding_config("embedding")
# Returns: {"model": "bge-large-en-v1.5", "dimension": 1024, ...}

# Get just the dimension
dim = registry.get_embedding_dimension("embedding")
# Returns: 1024
```

### Environment Variables (Set by Ansible)

These are set automatically by Ansible from model_registry:
- `FASTEMBED_MODEL`: FastEmbed model name (e.g., "BAAI/bge-large-en-v1.5")
- `EMBEDDING_MODEL`: Model name 
- `EMBEDDING_DIMENSION`: Dimension (e.g., "1024")

## Milvus Collection Setup

### Dynamic Dimensions

Collections are created with dimensions from the model registry:

```python
from agent.services.insights_service import get_embedding_dimension

dim = get_embedding_dimension()  # From registry
# Use dim when creating collection schema
```

### Model Tracking

All embeddings should track which model generated them:

```python
class ChatInsight:
    model_name: str = "bge-large-en-v1.5"  # Track the model
```

This enables:
- Querying only compatible embeddings
- Model migration tracking
- Future: Automatic re-embedding on model change

## Changing Embedding Models

### Step 1: Update Model Registry

```yaml
model_purposes:
  embedding: "bge-small"  # Change from "bge-large" to new model
```

### Step 2: Check If Migration is Needed

Use the interactive menu or make commands:

```bash
# Interactive menu
make menu  # Select "Migration" -> "Check Embedding Model Migration"

# Or direct command
cd provision/ansible
make check-embeddings
```

### Step 3: Migrate (If Needed)

If dimensions don't match, migrate:

```bash
# Interactive (prompts for confirmation)
make migrate-embeddings

# Or force (no confirmation)
make migrate-embeddings-force
```

This will:
1. Drop the existing Milvus 'documents' collection
2. Recreate it with the new embedding dimension
3. You then need to re-ingest all documents

### Step 4: Re-ingest Documents

After migration, re-ingest all documents:
- **Admin UI**: Documents > Re-index All
- **API**: `curl -X POST http://ingest-lxc:8002/api/reindex`
- **Manual**: Re-upload documents through normal flow

## Matryoshka Embeddings (Future)

Matryoshka embeddings allow dimension truncation without re-embedding:

```python
# Native 768-d embedding
embedding = await get_embedding(text)  # [768 floats]

# Truncate to 256-d for faster search
embedding_256 = embedding[:256]
```

### Benefits

1. **Flexible storage**: Store native dim, query at any lower dim
2. **Performance tuning**: Use lower dims for speed, higher for accuracy
3. **No re-embedding**: Change effective dimension without regenerating

### Supported Models

- `nomic-ai/nomic-embed-text-v1.5` (768-d, truncatable to 64/128/256/512)
- Future: Check model registry `matryoshka: true` flag

### Usage Pattern (Future)

```python
config = registry.get_embedding_config("embedding")
if config.get("matryoshka"):
    # Can truncate embeddings for different use cases
    available_dims = config.get("matryoshka_dimensions", [])
```

## Common Issues

### Dimension Mismatch

**Error**: `the length(X) of float data should divide the dim(Y)`

**Cause**: Embedding dimension doesn't match collection schema

**Fix**:
1. Verify `FASTEMBED_MODEL` env var matches model registry
2. Check collection was created with correct dimension
3. If collection exists with wrong dimension, recreate it

### Zero Vectors

**Symptom**: Search returns no results or poor results

**Cause**: Embedding service returning zero vectors (fallback)

**Fix**:
1. Check embedding service authentication
2. Verify embedding service URL is correct
3. Check embedding service logs for errors

## Related Files

- `provision/ansible/group_vars/all/model_registry.yml` - Model configuration
- `srv/shared/busibox_common/llm.py` - ModelRegistry class
- `srv/agent/app/services/insights_service.py` - Milvus collection setup
- `srv/ingest/src/processors/embedder.py` - FastEmbed wrapper
