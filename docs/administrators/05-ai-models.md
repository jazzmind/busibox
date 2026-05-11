---
title: "AI Models & Services"
category: "administrator"
order: 5
description: "Managing AI model providers, local inference, and the LLM gateway"
published: true
---

# AI Models & Services

Busibox uses a LiteLLM gateway to provide a unified interface to multiple AI model providers. You can run local models on your own hardware, connect to cloud providers, or use both simultaneously.

## Architecture

```
Agent API  ─┐
Data Worker ─┼──▶  LiteLLM Gateway  ──▶  vLLM (local GPU)
Search API  ─┘         │                  MLX (Apple Silicon)
                       │                  OpenAI
                       └──────────────▶  Anthropic
                                         AWS Bedrock
```

All services call the LiteLLM gateway using an OpenAI-compatible API. The gateway routes requests to the appropriate provider based on the model name.

## LiteLLM Gateway

LiteLLM is the central model router. It's deployed as part of the standard Busibox installation.

### Deploying LiteLLM

```bash
make install SERVICE=litellm
```

### Configuration

LiteLLM configuration is managed through Ansible variables:

| Variable | Purpose |
|----------|---------|
| `LITELLM_API_KEY` | API key for gateway access (from vault) |
| `LITELLM_BASE_URL` | Gateway URL (auto-configured) |
| `LITELLM_SALT_KEY` | Encryption key for credentials stored in LiteLLM's database — **must never change** after initial setup |

Model definitions are configured in the LiteLLM config file, which maps model names to providers and endpoints.

For AWS Bedrock setup, see [Bedrock Quick Start](../developers/reference/bedrock-quickstart.md) and [Bedrock Inference Profiles](../developers/reference/bedrock-inference-profiles.md).

### Adding a Cloud Provider

To add a cloud AI provider (e.g., OpenAI, Anthropic, AWS Bedrock), use the **Settings > AI Models** screen in the Admin UI. This is the only supported way to save provider credentials:

1. Navigate to **Admin > Settings > AI Models**
2. Select your cloud provider
3. Enter the API key or credentials
4. Click **Save**

The admin UI routes all credential saves through agent-api (`POST /llm/keys`), which:
1. Stores the key in LiteLLM via `/credentials` and `/config/update`
2. Persists an encrypted copy to config-api (the durable backup) so keys survive LiteLLM restarts

> **Important**: Do not bypass the Admin UI by calling config-api or LiteLLM directly. Only credentials saved through the Admin UI are properly persisted and will survive restarts.

### Credential Persistence and Restart Recovery

Cloud provider credentials (Bedrock, OpenAI, Anthropic) are stored in three places:

| Location | Durability | Purpose |
|----------|-----------|---------|
| LiteLLM PostgreSQL DB | Survives LiteLLM restarts (unless salt key changes) | Active routing |
| agent-api `os.environ` | Lost on agent-api restart | Fast in-process access |
| config-api `llm-keys` (encrypted) | Permanent until explicitly deleted | Durable backup |

**Automatic restore after restart**: On the first authenticated request after a LiteLLM restart, agent-api detects that LiteLLM has no provider credentials and automatically restores them from config-api. No manual action is required.

**If credentials appear to be missing** after a restart (e.g., LiteLLM OOM kill, container recreate, or salt key mismatch), force an immediate restore:

```
POST /llm/keys/verify-restore
Authorization: Bearer <admin-token>
```

This resets the restore flag and pushes credentials from config-api back to LiteLLM. In the Admin UI this is exposed via **Settings > AI Models > Verify Restore**.

### The LITELLM_SALT_KEY Warning

LiteLLM encrypts all credentials stored in its database using `LITELLM_SALT_KEY`. If this key changes between restarts (e.g., from a container recreate that picks up a different value), **all stored credentials become permanently unreadable from LiteLLM's DB**.

Busibox mitigates this automatically: the durable copy in config-api is used to restore credentials after any restart. However, if config-api also lost the keys (e.g., a fresh install), you will need to re-enter credentials via the Admin UI.

To check for salt key issues look for `Unable to decrypt` errors in LiteLLM logs:

```bash
make manage SERVICE=litellm ACTION=logs
```

If you see decrypt errors, use the **Clean Stale Data** button in Admin > Settings > AI Models (or `POST /llm/keys/clean-stale`), then re-enter your credentials.

## Local Model Runtimes

### vLLM (NVIDIA GPUs)

vLLM is the primary local inference engine for systems with NVIDIA GPUs.

```bash
# Deploy vLLM
make install SERVICE=vllm

# Check status
make manage SERVICE=vllm ACTION=status

# View logs
make manage SERVICE=vllm ACTION=logs
```

vLLM supports:
- High-throughput serving with continuous batching
- Quantized models (GPTQ, AWQ) for efficient memory usage
- Multiple concurrent requests
- OpenAI-compatible API

#### GPU Configuration

Key variables for GPU inference:

| Variable | Purpose |
|----------|---------|
| `VLLM_MODEL` | Model to serve (e.g., `meta-llama/Llama-3.1-8B-Instruct`) |
| `VLLM_GPU_MEMORY_UTILIZATION` | Fraction of GPU memory to use |
| `VLLM_MAX_MODEL_LEN` | Maximum context length |
| `VLLM_QUANTIZATION` | Quantization method (awq, gptq, none) |

### MLX (Apple Silicon)

For development on Apple Silicon Macs, Busibox supports MLX through a host-agent bridge.

```bash
# Install the host agent
bash scripts/host-agent/install-host-agent.sh

# Start an MLX model
bash scripts/llm/start-mlx-server.sh fast    # Fast local model
bash scripts/llm/start-mlx-server.sh frontier # Larger model

# Check status
bash scripts/llm/start-mlx-server.sh --status

# Stop
bash scripts/llm/start-mlx-server.sh --stop
```

The host agent bridges Docker containers to the MLX runtime on the host, since MLX requires direct Metal GPU access.

| Variable | Purpose |
|----------|---------|
| `HOST_AGENT_HOST` | Host agent address |
| `HOST_AGENT_PORT` | Host agent port (default: 8089) |
| `HOST_AGENT_TOKEN` | Authentication token |

### Embedding Service

Embeddings are generated by a dedicated service using FastEmbed:

```bash
make install SERVICE=embedding
```

| Variable | Purpose | Default |
|----------|---------|---------|
| `FASTEMBED_MODEL` | Text embedding model | BAAI/bge-large-en-v1.5 |
| `EMBEDDING_BATCH_SIZE` | Batch size | 32 |

Embeddings always run locally for speed and privacy -- no data is sent to external services for embedding generation.

### ColPali (Visual Embeddings)

ColPali provides visual document understanding using a vision-language model:

```bash
make install SERVICE=colpali
```

| Variable | Purpose | Default |
|----------|---------|---------|
| `COLPALI_ENABLED` | Enable visual embeddings | false |
| `COLPALI_BASE_URL` | ColPali service URL | (auto-configured) |

ColPali requires a GPU and is optional. It's most useful for scanned documents, forms, and documents where layout carries meaning.

## Per-Agent Model Selection

Different agents can use different models. Configure the default model for agents:

| Variable | Purpose |
|----------|---------|
| `AGENT_SERVER_DEFAULT_MODEL` | Default model for all agents |

Individual agents can override this in their configuration through the Busibox Agents interface.

### Recommended Model Assignments

| Task | Recommended | Why |
|------|-------------|-----|
| Simple Q&A | Local (fast model) | Low latency, no cost |
| Complex reasoning | Frontier (GPT-4o, Claude) | Higher capability |
| Document cleanup | Local (small model) | High volume, cost-sensitive |
| Code generation | Frontier (Claude, GPT-4o) | Best results |
| Embeddings | Local (FastEmbed) | Always local, fast |

## Search Reranking

After initial retrieval, search results can be reranked using an LLM for improved relevance:

| Variable | Purpose | Default |
|----------|---------|---------|
| `ENABLE_RERANKING` | Enable result reranking | false |
| `RERANKER_MODEL` | Model for reranking | (configurable) |

Reranking adds latency but significantly improves search quality, especially for ambiguous queries.

## Health Checks

```bash
# LiteLLM gateway
curl http://<agent-ip>:4000/health

# vLLM
curl http://<vllm-ip>:8000/health

# Embedding service
curl http://<data-ip>:8005/health

# MLX host agent
curl http://localhost:8089/health
```

## Monitoring

Monitor model usage and performance through:

- **LiteLLM dashboard** -- request counts, latency, error rates
- **Service logs** -- `make manage SERVICE=litellm ACTION=logs`
- **Health endpoints** -- automated monitoring of all model services

## Next Steps

- [Command-line management](06-manage.md)
- [Multiple deployments](07-multiple-deployments.md)
