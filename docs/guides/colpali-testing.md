---
title: ColPali Testing and Troubleshooting Guide
created: 2025-11-16
updated: 2025-11-16
status: active
category: guides
tags: [colpali, testing, troubleshooting, visual-embeddings]
---

# ColPali Testing and Troubleshooting Guide

Complete guide for testing, diagnosing, and troubleshooting the ColPali visual document embedding service.

## Overview

ColPali is a visual document embedding model that generates multi-vector embeddings (128 patches × 128 dimensions) for PDF page images. This enables visual search without OCR.

**Key Details:**
- **Model**: vidore/colpali-v1.3 (LoRA adapters on PaliGemma-3B)
- **Service**: Native colpali-engine implementation
- **Port**: 8002
- **GPU**: GPU 2 (dedicated)
- **Container**: vllm-lxc (208 for production, 308 for test)

## Quick Start

### Basic Health Check

```bash
# Test environment
bash scripts/test-colpali.sh test

# Production environment
bash scripts/test-colpali.sh production
```

### Run Python Test Suite

```bash
# Navigate to ingest directory
cd srv/ingest

# Run all ColPali tests
pytest tests/test_colpali.py -v

# Run specific test class
pytest tests/test_colpali.py::TestServiceAvailability -v

# Run with diagnostic report
pytest tests/test_colpali.py::test_diagnostic_report -v -s
```

## Test Suite Overview

The comprehensive test suite includes:

### 1. Service Availability Tests
- Health endpoint accessibility
- Service timeout handling
- Network connectivity

### 2. Image Processing Tests
- Base64 encoding/decoding
- Multiple image encoding
- Various image size support

### 3. Embedding Generation Tests
- Single image embeddings
- Multiple image batch processing
- PDF page embeddings
- Embedding structure validation

### 4. API Compatibility Tests
- OpenAI-compatible API structure
- Batch embedding requests
- Response format validation

### 5. Error Handling Tests
- Empty input handling
- Invalid file paths
- Corrupted image data
- Service unavailability

### 6. Performance Benchmarks
- Embedding latency measurement
- Batch performance testing
- Memory usage validation

### 7. Integration Tests
- Configuration loading
- End-to-end workflow

## Running Tests

### Full Test Suite

```bash
cd srv/ingest

# Run all tests
pytest tests/test_colpali.py -v

# Run with coverage
pytest tests/test_colpali.py --cov=processors.colpali --cov-report=html

# Run slow tests (performance benchmarks)
pytest tests/test_colpali.py -v -m slow
```

### Individual Test Classes

```bash
# Service availability only
pytest tests/test_colpali.py::TestServiceAvailability -v

# Embedding generation only
pytest tests/test_colpali.py::TestEmbeddingGeneration -v

# Performance benchmarks
pytest tests/test_colpali.py::TestPerformance -v
```

### Quick Diagnostic

```bash
# Run diagnostic report
python tests/test_colpali.py

# Or with pytest
pytest tests/test_colpali.py::test_diagnostic_report -v -s
```

## Shell Script Tests

The `scripts/test-colpali.sh` script provides comprehensive system-level testing:

### Basic Usage

```bash
# Test default environment (test)
bash scripts/test-colpali.sh

# Test production
bash scripts/test-colpali.sh production

# With Python integration tests
RUN_PYTHON_TESTS=1 bash scripts/test-colpali.sh test
```

### What It Tests

1. **Network Connectivity**
   - Ping test
   - Port accessibility

2. **Service Health**
   - HTTP health endpoint
   - Model and device info

3. **Embedding Generation**
   - Real API request
   - Response validation
   - Timing measurement

4. **Container Service Status**
   - Systemd service status
   - GPU usage

5. **Model Files**
   - ColPali model cache
   - PaliGemma base model cache

6. **Diagnostic Report**
   - Full system report saved to `/tmp/`

## Troubleshooting

### Issue: Service Not Running

**Symptoms:**
- Connection refused errors
- Health check fails

**Diagnosis:**
```bash
# Check service status
ssh root@10.96.200.31  # or test: 10.96.201.208
systemctl status colpali

# View logs
journalctl -u colpali -n 50 --no-pager

# Check if port is listening
netstat -tlnp | grep 8002
```

**Solutions:**
```bash
# Restart service
systemctl restart colpali

# If that fails, redeploy
cd provision/ansible
ansible-playbook -i inventory/production/hosts.yml site.yml --tags colpali
```

### Issue: Model Not Loading

**Symptoms:**
- Long startup time
- Service starts but crashes
- "Model not loaded" errors

**Diagnosis:**
```bash
# Check model cache
ssh root@10.96.200.31
ls -lh /var/lib/llm-models/huggingface/hub/models--vidore--colpali-v1.3
ls -lh /var/lib/llm-models/huggingface/hub/models--google--paligemma-3b-pt-448

# Check disk space
df -h

# Check HuggingFace token
journalctl -u colpali -n 100 --no-pager | grep -i "token\|authentication\|401\|403"
```

**Solutions:**

1. **Pre-cache models** (recommended):
   ```bash
   # On Proxmox host
   cd /root/busibox/provision/pct/host
   bash setup-llm-models.sh
   ```

2. **Accept PaliGemma license**:
   - Visit: https://huggingface.co/google/paligemma-3b-pt-448
   - Accept the license agreement
   - Verify HuggingFace token is set in vault

3. **Check permissions**:
   ```bash
   ssh root@10.96.200.31
   ls -la /var/lib/llm-models/huggingface/hub
   # Should be readable by colpali user/vllm group
   ```

### Issue: GPU Not Available

**Symptoms:**
- "CUDA not available" errors
- Service fails to start
- Falls back to CPU

**Diagnosis:**
```bash
ssh root@10.96.200.31
nvidia-smi

# Check GPU 2 specifically
nvidia-smi -i 2

# Check container GPU passthrough
pct config 208 | grep -i gpu  # or 308 for test
```

**Solutions:**

1. **Verify GPU passthrough** (on Proxmox host):
   ```bash
   pct config 208 | grep dev
   # Should show: dev0: /dev/nvidia0,gid=44,uid=0
   #              dev2: /dev/nvidia2,gid=44,uid=0
   ```

2. **Re-add GPU passthrough** (if missing):
   ```bash
   cd provision/pct
   source lib/functions.sh
   add_all_gpus 208  # or 308 for test
   pct reboot 208
   ```

### Issue: Embedding Generation Fails

**Symptoms:**
- API returns 500 errors
- Embeddings are None
- "Invalid image" errors

**Diagnosis:**
```bash
# Test with curl
curl -X POST http://10.96.200.31:8002/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "input": ["base64_encoded_image_here"],
    "model": "colpali"
  }' -v

# Check recent errors
ssh root@10.96.200.31
journalctl -u colpali -n 100 --no-pager | grep -i error
```

**Solutions:**

1. **Validate image format**:
   ```python
   from PIL import Image
   img = Image.open("test.png")
   print(f"Format: {img.format}, Size: {img.size}, Mode: {img.mode}")
   # Should be: Format: PNG, Mode: RGB
   ```

2. **Check image size**:
   - Very large images (>4096x4096) may cause issues
   - Scale down large images before embedding

3. **Verify base64 encoding**:
   ```python
   import base64
   with open("test.png", "rb") as f:
       encoded = base64.b64encode(f.read()).decode()
   print(f"Length: {len(encoded)}")
   # Should be reasonable size (not 0, not enormous)
   ```

### Issue: Slow Performance

**Symptoms:**
- Embedding generation takes >5 seconds
- High latency
- GPU underutilized

**Diagnosis:**
```bash
# Monitor GPU usage during embedding
ssh root@10.96.200.31
watch -n 1 nvidia-smi

# Check system resources
htop

# Check for other GPU processes
nvidia-smi
```

**Solutions:**

1. **Check GPU memory**:
   ```bash
   nvidia-smi -i 2 --query-gpu=memory.used,memory.total --format=csv
   # Should have ~8-10GB free for ColPali
   ```

2. **Adjust batch size** (in colpali config):
   ```yaml
   colpali_batch_size: 4  # Default, try 2 or 8
   colpali_max_workers: 2
   ```

3. **Check for GPU contention**:
   - GPU 2 should be dedicated to ColPali
   - Other services should use GPU 0 and 1

### Issue: Memory Leaks

**Symptoms:**
- Memory usage grows over time
- Service crashes after many requests
- OOM errors

**Diagnosis:**
```bash
# Monitor memory usage
ssh root@10.96.200.31
watch -n 5 'systemctl status colpali | grep Memory'

# Check for memory leaks
journalctl -u colpali -n 200 --no-pager | grep -i "memory\|oom"
```

**Solutions:**

1. **Restart service periodically** (if needed):
   ```bash
   # Add timer for periodic restart
   systemctl edit colpali
   # Add: RuntimeMaxSec=86400  (restart after 24h)
   ```

2. **Adjust resource limits**:
   ```yaml
   # In colpali defaults/main.yml
   colpali_memory_limit: "16G"  # Increase if needed
   ```

## Performance Benchmarks

Expected performance metrics:

### Latency
- **Single image**: 0.5-2.0s (after warm-up)
- **Batch (4 images)**: 1.5-4.0s (0.4-1.0s per image)
- **First request**: 2-5s (model loading)

### Throughput
- **Sequential**: ~1-2 images/second
- **Batch**: ~2-4 images/second

### Resource Usage
- **GPU Memory**: 8-10GB (PaliGemma-3B + ColPali)
- **System Memory**: 4-6GB
- **Disk Space**: ~11GB (model cache)

## Configuration

### Environment Variables

```bash
# Ingest service configuration
COLPALI_BASE_URL=http://10.96.200.31:8002/v1
COLPALI_API_KEY=EMPTY
COLPALI_ENABLED=true
```

### Model Registry

```yaml
# group_vars/all/model_registry.yml
visual:
  model: "colpali-v1.3"
  description: "Visual document embedding"
  max_tokens: 4096
  provider: "colpali"
  endpoint: "/v1/embeddings"
```

### Service Configuration

```yaml
# roles/colpali/defaults/main.yml
colpali_model: "vidore/colpali-v1.3"
colpali_device: "cuda:0"  # Within CUDA_VISIBLE_DEVICES=2
colpali_port: 8002
colpali_batch_size: 4
colpali_cuda_visible_devices: "2"
```

## Deployment

### Initial Deployment

```bash
cd provision/ansible

# Test environment
ansible-playbook -i inventory/test/hosts.yml site.yml --tags colpali

# Production environment
ansible-playbook -i inventory/production/hosts.yml site.yml --tags colpali
```

### Update Configuration

```bash
# Edit configuration
vi roles/colpali/defaults/main.yml

# Redeploy
make colpali ENV=production
```

### Pre-cache Models

```bash
# On Proxmox host (recommended before first deployment)
cd /root/busibox/provision/pct/host
bash setup-llm-models.sh

# This downloads:
# - google/paligemma-3b-pt-448 (~11GB)
# - vidore/colpali-v1.3 (~20MB)
```

## API Reference

### Health Check

```bash
curl http://10.96.200.31:8002/health
```

Response:
```json
{
  "status": "healthy",
  "model": "vidore/colpali-v1.3",
  "device": "cuda:0"
}
```

### Generate Embeddings

```bash
# Base64 encode image
BASE64_IMAGE=$(base64 -w 0 image.png)

# Request embeddings
curl -X POST http://10.96.200.31:8002/v1/embeddings \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer EMPTY" \
  -d "{
    \"input\": [\"$BASE64_IMAGE\"],
    \"model\": \"colpali\",
    \"encoding_format\": \"float\"
  }"
```

Response:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.123, 0.456, ...],  // 16384 floats (128*128)
      "index": 0
    }
  ],
  "model": "colpali",
  "usage": {
    "prompt_tokens": 1,
    "total_tokens": 1
  }
}
```

## Python API Usage

```python
from processors.colpali import ColPaliEmbedder

# Initialize
config = {
    "colpali_base_url": "http://10.96.200.31:8002/v1",
    "colpali_api_key": "EMPTY",
    "colpali_enabled": True,
}
embedder = ColPaliEmbedder(config)

# Check health
is_healthy = await embedder.check_health()

# Generate embeddings
image_paths = ["page1.png", "page2.png"]
embeddings = await embedder.embed_pages(image_paths)

# embeddings is List[List[List[float]]]
# Structure: [page][patch][dimension]
# Example: embeddings[0] = first page with 128 patches of 128 dims each
```

## Monitoring

### Service Logs

```bash
# Realtime logs
ssh root@10.96.200.31
journalctl -u colpali -f

# Recent errors
journalctl -u colpali -n 100 --no-pager | grep -i error

# Performance metrics
journalctl -u colpali -n 1000 --no-pager | grep "embedding"
```

### GPU Monitoring

```bash
# Continuous monitoring
ssh root@10.96.200.31
watch -n 1 nvidia-smi

# Check specific GPU (GPU 2)
nvidia-smi -i 2 -l 1

# Memory usage
nvidia-smi -i 2 --query-gpu=memory.used,memory.total --format=csv -l 1
```

### System Metrics

```bash
# Service status
systemctl status colpali

# Resource usage
systemctl status colpali | grep -E "Memory|CPU"

# Network connections
netstat -an | grep 8002
```

## Common Workflows

### Verify ColPali is Working

```bash
# 1. Quick health check
curl http://10.96.200.31:8002/health

# 2. Run test suite
bash scripts/test-colpali.sh production

# 3. Test embedding generation
cd srv/ingest
pytest tests/test_colpali.py::TestEmbeddingGeneration::test_single_image_embedding -v
```

### Debug Embedding Issues

```bash
# 1. Enable debug logging
ssh root@10.96.200.31
# Edit: /opt/colpali/colpali_server.py
# Add: import logging; logging.basicConfig(level=logging.DEBUG)

# 2. Restart service
systemctl restart colpali

# 3. Watch logs
journalctl -u colpali -f

# 4. Test with simple image
# Use test script
```

### Performance Tuning

```bash
# 1. Run benchmarks
cd srv/ingest
pytest tests/test_colpali.py::TestPerformance -v -s

# 2. Adjust configuration
cd provision/ansible
vi roles/colpali/defaults/main.yml
# Modify: colpali_batch_size, colpali_max_workers

# 3. Redeploy
make colpali ENV=production

# 4. Re-run benchmarks
```

## References

- **ColPali Model**: https://huggingface.co/vidore/colpali-v1.3
- **ColPali Engine**: https://github.com/illuin-tech/colpali
- **PaliGemma Base**: https://huggingface.co/google/paligemma-3b-pt-448
- **Role README**: `provision/ansible/roles/colpali/README.md`
- **Implementation**: `srv/ingest/src/processors/colpali.py`

## Related Documentation

- [Architecture Overview](../architecture/architecture.md)
- [Deployment Guide](../deployment/services.md)
- [Ingestion Pipeline](../reference/ingestion-pipeline.md)
- [Troubleshooting Guide](../troubleshooting/common-issues.md)

