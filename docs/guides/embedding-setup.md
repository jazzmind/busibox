# Embedding Service Setup Guide

**Created:** 2025-11-14  
**Status:** Active  
**Category:** Guides

## Overview

This guide explains the hybrid embedding setup with vLLM primary and FastEmbed fallback for document ingestion.

## Architecture

### GPU Allocation (3x RTX 3090)

- **GPU 0**: Phi-4 multimodal chat model (vLLM port 8000)
- **GPU 1**: Qwen3 embedding model (vLLM port 8001)
- **GPU 2**: Available for future models

### Embedding Strategy

**Primary: vLLM + Qwen3**
- Model: `Alibaba-NLP/gte-Qwen2-1.5B-instruct`
- Dimension: 1024
- GPU-accelerated, high quality
- Routed through liteLLM proxy

**Fallback: FastEmbed**
- Model: `BAAI/bge-base-en-v1.5`
- Dimension: 768
- Local ONNX runtime, no GPU needed
- Automatic fallback if vLLM unavailable

### Data Flow

```
Document Upload
    ↓
Chunking
    ↓
Embedding Generation
    ├─→ Try vLLM (GPU 1, port 8001)
    │   └─→ Success: 1024-dim embeddings
    └─→ Fallback to FastEmbed (CPU)
        └─→ Success: 768-dim embeddings
    ↓
Store in Milvus
    ↓
Available for search
```

## Deployment

### 1. Pre-download Models (Required for Fast Startup)

**IMPORTANT:** Pre-download models before deploying vLLM to avoid 30+ minute wait on first start.

```bash
# SSH to Proxmox host
ssh root@proxmox-host

# Navigate to model download script
cd /root/busibox/provision/pct/host

# Run model pre-download
bash setup-llm-models.sh
```

This downloads and caches:
- `microsoft/Phi-4-multimodal-instruct` (~12GB) - for chat on GPU 0
- `Qwen/Qwen3-Embedding-8B` (~16GB) - for embeddings on GPU 1

Models are stored at `/var/lib/llm-models/huggingface` and mounted into vLLM containers.

**Expected time:** 15-30 minutes (one-time setup)

### 2. Deploy vLLM Embedding Service

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy embedding service to production
make vllm-embedding INV=inventory/production

# Or deploy all vLLM services (chat + embedding)
make vllm INV=inventory/production
```

**With pre-cached models:** vLLM starts in ~30 seconds  
**Without cache:** Deployment will fail with instructions to run step 1

To skip the cache check (not recommended):
```bash
make vllm-embedding INV=inventory/production EXTRA_ARGS="-e skip_model_check=true"
```

### 3. Verify vLLM Embedding

```bash
# SSH to vLLM host
ssh root@10.96.200.208

# Check service status
systemctl status vllm-embedding

# Check logs
journalctl -u vllm-embedding -f

# Test API
curl http://localhost:8001/v1/models

# Test embedding generation
curl http://localhost:8001/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-embedding",
    "input": "Test document"
  }'
```

### 4. Deploy liteLLM (if not already done)

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible
make litellm INV=inventory/production
```

### 5. Deploy Ingest Service

```bash
# Deploy ingest worker with updated embedder
make ingest INV=inventory/production

# Verify worker is running
ssh root@10.96.200.206
systemctl status ingest-worker
journalctl -u ingest-worker -f
```

### 6. Reinitialize Milvus Collection

The Milvus collection needs to be recreated for the new 4096-dim embeddings:

```bash
ssh root@10.96.200.204

# Backup existing data (if any)
cd /var/lib/milvus
tar -czf backup-$(date +%Y%m%d-%H%M%S).tar.gz rdb_data* wal

# Stop Milvus
cd /opt/milvus
docker-compose down

# Clear old collection data
rm -rf /var/lib/milvus/rdb_data*
rm -rf /var/lib/milvus/wal

# Start Milvus
docker-compose up -d

# Wait for startup
sleep 30

# Reinitialize schema
python3 /opt/milvus/hybrid_schema.py
```

## Configuration

### liteLLM Config

Located in `provision/ansible/roles/litellm/defaults/main.yml`:

```yaml
- model_name: "qwen3-embedding"
  litellm_params:
    model: "openai/Alibaba-NLP/gte-Qwen2-1.5B-instruct"
    api_base: "http://{{ vllm_ip }}:8001/v1"
```

### Ingest Service Config

Environment variables in `provision/ansible/roles/ingest/templates/ingest.env.j2`:

```bash
EMBEDDING_MODEL=qwen3-embedding
EMBEDDING_DIMENSION=1024
LITELLM_BASE_URL=http://10.96.200.30:4000
LITELLM_API_KEY=<from-vault>
```

### Embedder Behavior

The embedder (`srv/ingest/src/processors/embedder.py`) will:

1. **First request**: Try vLLM via liteLLM
   - Success: Use vLLM for all subsequent requests
   - Failure: Log warning, switch to FastEmbed

2. **Subsequent requests**: Use whichever backend succeeded
   - If vLLM failed once, use FastEmbed for entire session
   - No retry logic (avoids repeated failures)

3. **Dimension handling**:
   - vLLM: 1024 dimensions
   - FastEmbed: 768 dimensions
   - Milvus schema supports 1024 (can store 768 with zero-padding if needed)

## Monitoring

### Check Embedding Status

```bash
# Run diagnostic script
cd /Users/wessonnenreich/Code/sonnenreich/busibox
bash scripts/check-document-status.sh

# Or check specific services
ssh root@10.96.200.210 "systemctl status vllm-embedding"
ssh root@10.96.200.30 "systemctl status litellm"
ssh root@10.96.200.206 "systemctl status ingest-worker"
```

### Check GPU Usage

```bash
ssh root@10.96.200.210
nvidia-smi

# Expected output:
# GPU 0: Phi-4 (main vLLM)
# GPU 1: Qwen3-embedding (vllm-embedding)
# GPU 2: Idle
```

### Check Embedding Logs

```bash
# vLLM embedding service
ssh root@10.96.200.210 "journalctl -u vllm-embedding -n 100"

# Ingest worker (shows which backend is used)
ssh root@10.96.200.206 "journalctl -u ingest-worker -n 100 | grep -i embedding"
```

## Troubleshooting

### vLLM Embedding Not Starting

**Symptoms:**
- Service fails to start
- `systemctl status vllm-embedding` shows failed

**Solutions:**
```bash
# Check GPU availability
nvidia-smi

# Check logs
journalctl -u vllm-embedding -n 50

# Verify model is downloaded
ls -lh /var/lib/llm-models/huggingface/hub/ | grep gte-Qwen

# Check port availability
netstat -tlnp | grep 8001

# Restart service
systemctl restart vllm-embedding
```

### Embedder Using FastEmbed Fallback

**Symptoms:**
- Worker logs show "Switching to FastEmbed fallback"
- Embeddings are 768-dim instead of 1024-dim

**Solutions:**
```bash
# Check if vLLM embedding is running
curl http://10.96.200.210:8001/v1/models

# Check if liteLLM can reach vLLM
ssh root@10.96.200.30
curl http://10.96.200.210:8001/v1/models

# Check liteLLM config
cat /opt/litellm/config.yaml | grep -A 5 qwen3-embedding

# Restart ingest worker to retry vLLM
ssh root@10.96.200.206
systemctl restart ingest-worker
```

### Out of Memory on GPU 1

**Symptoms:**
- vLLM embedding crashes
- CUDA out of memory errors

**Solutions:**
```bash
# Reduce GPU memory utilization
# Edit: provision/ansible/roles/vllm_embedding/defaults/main.yml
vllm_embedding_gpu_memory_utilization: 0.6  # Reduce from 0.8

# Redeploy
cd provision/ansible
make vllm-embedding INV=inventory/production
```

### Milvus Dimension Mismatch

**Symptoms:**
- Insert errors: "dimension mismatch"
- Search errors: "invalid dimension"

**Solutions:**
```bash
# Verify Milvus schema
ssh root@10.96.200.204
python3 /opt/milvus/hybrid_schema.py

# If schema is wrong, reinitialize (see step 5 above)
```

## Performance

### Expected Throughput

**vLLM (GPU):**
- ~500-1000 embeddings/second
- Batch size: 32
- Latency: ~50-100ms per batch

**FastEmbed (CPU):**
- ~50-100 embeddings/second
- Batch size: 32
- Latency: ~300-500ms per batch

### Optimization Tips

1. **Increase batch size** for better GPU utilization:
   ```yaml
   # In ingest service config
   EMBEDDING_BATCH_SIZE=64
   ```

2. **Use vLLM for all requests** (avoid fallback):
   - Ensure vLLM embedding is always running
   - Monitor GPU health
   - Set up alerts for vLLM downtime

3. **Parallel processing**:
   - Run multiple ingest workers if needed
   - Each worker will use the same vLLM instance

## Cost & Resource Usage

### GPU Memory

- **Phi-4**: ~12-16GB (GPU 0)
- **Qwen3-embedding**: ~3-4GB (GPU 1)
- **Total**: ~16-20GB / 72GB available (27%)
- **Remaining**: ~52GB for future models

### CPU/RAM (FastEmbed Fallback)

- **Memory**: ~2GB per worker
- **CPU**: ~2-4 cores per worker
- **Disk**: ~500MB for ONNX model

## Related Documentation

- [Ingest Service Specification](../architecture/ingest-service-specification.md)
- [vLLM Embedding Role README](../../provision/ansible/roles/vllm_embedding/README.md)
- [Milvus Hybrid Schema](../../provision/ansible/roles/milvus/files/hybrid_schema.py)
- [Document Processing Status Script](../../scripts/check-document-status.sh)

## Next Steps

1. **Deploy vLLM embedding service** (step 1 above)
2. **Test embedding generation** (step 2 above)
3. **Redeploy ingest service** (step 4 above)
4. **Upload a test document** via AI Portal
5. **Monitor logs** to confirm vLLM is being used
6. **Verify search works** with new embeddings

## Summary

This setup provides:
- ✅ High-quality embeddings via vLLM + Qwen3 (1024 dims)
- ✅ Automatic fallback to FastEmbed if vLLM unavailable
- ✅ Dedicated GPU for embeddings (no interference with chat)
- ✅ Graceful degradation (system keeps working even if GPU fails)
- ✅ Easy monitoring and troubleshooting

