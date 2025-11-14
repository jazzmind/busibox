# vLLM Embedding Role

Deploys a dedicated vLLM instance for embedding model inference on a separate GPU.

## Purpose

Runs Qwen3 embedding model (1024-dim) on GPU 1 while the main vLLM instance serves Phi-4 on GPU 0. This provides:
- Dedicated GPU resources for embeddings
- No interference with chat model inference
- High-throughput embedding generation for document ingestion

## Requirements

- Same host as main vLLM role
- Separate GPU (configured via `vllm_embedding_cuda_visible_devices`)
- Shared vLLM installation and HuggingFace cache
- Port 8001 available (different from main vLLM on 8000)

## Configuration

Key variables (see `defaults/main.yml`):

```yaml
vllm_embedding_model: "Alibaba-NLP/gte-Qwen2-1.5B-instruct"
vllm_embedding_port: 8001
vllm_embedding_cuda_visible_devices: "1"  # GPU 1
vllm_embedding_served_model_name: "qwen3-embedding"
```

## Deployment

```bash
# Deploy both vLLM instances
cd provision/ansible
make vllm INV=inventory/production

# Or deploy just embedding instance
make vllm-embedding INV=inventory/production
```

## Integration

liteLLM is configured to route `qwen3-embedding` requests to this instance:

```yaml
- model_name: "qwen3-embedding"
  litellm_params:
    model: "openai/Alibaba-NLP/gte-Qwen2-1.5B-instruct"
    api_base: "http://{{ vllm_ip }}:8001/v1"
```

## Service Management

```bash
# Status
systemctl status vllm-embedding

# Logs
journalctl -u vllm-embedding -f

# Restart
systemctl restart vllm-embedding
```

## Health Check

```bash
# Check if service is responding
curl http://localhost:8001/v1/models

# Test embedding generation
curl http://localhost:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding",
    "input": "Hello world"
  }'
```

## GPU Assignment

With 3x RTX 3090s:
- **GPU 0**: Main vLLM (Phi-4) - port 8000
- **GPU 1**: vLLM Embedding (Qwen3) - port 8001
- **GPU 2**: Available for future models

## Model Details

**Alibaba-NLP/gte-Qwen2-1.5B-instruct**
- Parameters: 1.5B
- Embedding dimension: 1024
- Max sequence length: 8192
- VRAM usage: ~3-4GB
- Optimized for semantic search and retrieval

## Troubleshooting

**Service won't start:**
- Check GPU availability: `nvidia-smi`
- Verify CUDA_VISIBLE_DEVICES: `journalctl -u vllm-embedding | grep CUDA`
- Check model download: `ls -lh /var/lib/llm-models/huggingface/hub/`

**Port conflict:**
- Ensure port 8001 is not in use: `netstat -tlnp | grep 8001`

**Out of memory:**
- Reduce `vllm_embedding_gpu_memory_utilization` (default 0.8)
- Check GPU memory: `nvidia-smi`

