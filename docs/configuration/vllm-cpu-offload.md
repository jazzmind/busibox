# vLLM CPU Offload Configuration

**Created**: 2024-11-19  
**Status**: Active  
**Category**: configuration

## Overview

vLLM supports offloading the KV (Key-Value) cache from GPU VRAM to system RAM (DRAM) when GPU memory is full. This dramatically increases concurrent request capacity by leveraging abundant system memory.

With **256GB of system RAM**, you can handle 20-40x more concurrent requests with only a modest latency increase for cache misses.

## How It Works

### Memory Architecture

```
┌─────────────────────────────────────────────────────┐
│ GPU VRAM (24 GB per RTX 3090)                       │
│  ├─ Model Weights: 12 GB (stays on GPU)            │
│  └─ KV Cache (Hot): 9 GB (active requests)         │
└─────────────────────────────────────────────────────┘
              ↕ PCIe Transfer (~20 GB/s)
┌─────────────────────────────────────────────────────┐
│ System RAM (256 GB available)                       │
│  └─ KV Cache (Cold): 150 GB (queued requests)      │
└─────────────────────────────────────────────────────┘
```

### Request Lifecycle

1. **New request arrives** → Allocated to GPU KV cache if space available
2. **GPU full** → New requests get KV cache allocated in system RAM
3. **Request becomes active** → vLLM transfers KV cache from RAM to GPU (~100-200ms)
4. **Request idle** → vLLM may swap KV cache back to RAM
5. **Request completes** → KV cache freed

## Configuration

### Main vLLM Service (Phi-4)

**File**: `provision/ansible/roles/vllm/defaults/main.yml`

```yaml
# CPU Offloading Configuration
vllm_cpu_offload_gb: 150  # Offload 150GB of KV cache to system RAM

# Increase systemd memory limit to allow vLLM to use more RAM
vllm_memory_limit: "180G"  # Model (12GB) + CPU offload (150GB) + overhead
```

**Expected Capacity**:
- Without offload: 2-3 concurrent 8K requests
- With 150GB offload: **~40 concurrent 8K requests** (20x improvement!)

### Embedding vLLM Service (Qwen3)

**File**: `provision/ansible/roles/vllm_embedding/defaults/main.yml`

```yaml
# CPU Offloading for embeddings
vllm_embedding_cpu_offload_gb: 50  # Embeddings use less memory

# Increase systemd memory limit
vllm_embedding_memory_limit: "70G"
```

## Performance Impact

### Latency Tradeoffs

| Request State | Latency | Notes |
|---------------|---------|-------|
| KV cache on GPU (hot) | 50-100ms | Normal GPU speed |
| KV cache in RAM (cold, inactive) | 50-100ms | No impact while idle |
| Transfer from RAM to GPU | +100-200ms | One-time cost when request activates |
| After transfer complete | 50-100ms | Back to normal GPU speed |

### Throughput Gains

**Phi-4 (6B model, 8K context):**

| Configuration | Concurrent Requests | Total Capacity |
|---------------|-------------------|----------------|
| GPU only (24GB) | 2-3 | ~10 requests/min |
| GPU + 50GB RAM | 12-15 | ~50 requests/min |
| GPU + 150GB RAM | 40+ | ~150 requests/min |
| GPU + 220GB RAM | 55+ | ~200 requests/min |

### PCIe Transfer Speed

- PCIe 4.0 x16: ~31.5 GB/s theoretical
- Real-world: ~20-25 GB/s for large transfers
- Transfer 4GB KV cache: ~150-200ms
- GPU VRAM bandwidth: ~900 GB/s (45x faster than PCIe)

**Implication**: First 2-3 requests get instant GPU speed, additional requests pay one-time transfer cost.

## Use Cases

### ✅ Excellent For

1. **High concurrency workloads**
   - Customer service chatbots (many concurrent users)
   - Public API services
   - Multi-tenant SaaS platforms

2. **Variable request lengths**
   - Mix of short queries and long documents
   - RAG applications with diverse document sizes
   - Interactive applications with session history

3. **Bursty traffic patterns**
   - Peak hours with high concurrent users
   - Batch processing with many simultaneous jobs
   - Web applications with variable load

4. **Budget-constrained deployments**
   - Maximize existing hardware value
   - Defer GPU upgrades
   - Better cost per concurrent user

### ❌ Not Ideal For

1. **Ultra-low latency requirements**
   - Real-time voice assistants (<100ms needed)
   - Gaming applications
   - High-frequency trading systems

2. **Consistently low concurrency**
   - Single-user research environments
   - Personal assistants with 1-2 users
   - Development/testing environments

3. **Purely short requests**
   - If all requests are <100 tokens, GPU-only is faster

## Deployment

### Step 1: Verify Container Memory Allocation

**CRITICAL**: The Proxmox container must have enough RAM allocated to support CPU offloading.

```bash
# On Proxmox host, check container memory allocation
pct config 208 | grep memory
# Should show: memory: 204800  (200GB)

# If memory is insufficient (e.g., 16384 = 16GB), update it:
pct set 208 -memory 204800  # 200GB in MB
pct reboot 208

# Or use the check script:
bash provision/pct/host/check-container-memory.sh production
```

**Memory Requirements**:
- **Container allocation**: 200GB (204800MB) minimum
- **Main vLLM service**: 180GB systemd limit
- **vLLM Embedding service**: 70GB systemd limit
- Both services run in the same container, so container needs 200GB total

### Step 2: Verify System Resources

```bash
# SSH to vLLM container
ssh root@10.96.200.208

# Check available RAM
free -h
# Should show ~200GB total (container limit), with plenty free

# Check current vLLM memory usage
systemctl status vllm
# Note current memory limit
```

### Step 3: Update Configuration

The configuration is already set with recommended values:

```yaml
# Main vLLM: 150GB CPU offload
vllm_cpu_offload_gb: 150
vllm_memory_limit: "180G"

# Embedding vLLM: 50GB CPU offload
vllm_embedding_cpu_offload_gb: 50
vllm_embedding_memory_limit: "70G"
```

To adjust, edit:
- `provision/ansible/inventory/production/group_vars/all.yml` (override defaults)
- Or environment-specific files in `inventory/production/group_vars/`

### Step 4: Deploy

```bash
cd provision/ansible

# Deploy main vLLM with CPU offload
make vllm INV=inventory/production

# Deploy embedding vLLM with CPU offload
make vllm-embedding INV=inventory/production

# Or deploy both
ansible-playbook -i inventory/production/hosts.yml site.yml --tags vllm
```

### Step 5: Verify Configuration

```bash
# Check vLLM service
ssh root@10.96.200.208

# View vLLM startup logs
journalctl -u vllm -n 100 --no-pager | grep -i "offload\|cache\|memory"

# Look for lines like:
# "Using 9.6 GB GPU memory and 150.0 GB CPU memory for KV cache"
# "Actual max_num_seqs: 45 (limited by available memory)"

# Watch memory usage in real-time
watch -n 1 'free -h && echo "---" && nvidia-smi'
```

## Monitoring

### Check Active Memory Usage

```bash
# On vLLM container
ssh root@10.96.200.208

# System RAM usage
free -h
# Look at "used" under "Mem:" - should grow as requests come in

# GPU memory usage
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv

# vLLM process memory
ps aux | grep vllm
# Look at RSS (resident set size) and VSZ (virtual size)
```

### Check vLLM Statistics

```bash
# vLLM logs show cache statistics
journalctl -u vllm -f | grep -E "cache|swap|offload|memory"

# Look for:
# - "KV cache hit rate" (higher is better)
# - "Swapped from CPU to GPU" (shows offloading working)
# - "Out of memory" (should not appear with proper config)
```

### Performance Metrics

```bash
# Test concurrent requests
# From your workstation:
curl -X POST http://your-server:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "phi-4", "prompt": "Test", "max_tokens": 100}'

# Monitor latency and throughput as you increase concurrent requests
```

## Tuning

### Adjust CPU Offload Amount

**Conservative (safe):**
```yaml
vllm_cpu_offload_gb: 100  # Leave plenty of RAM for system
vllm_memory_limit: "130G"
```

**Balanced (recommended):**
```yaml
vllm_cpu_offload_gb: 150  # Good balance
vllm_memory_limit: "180G"
```

**Aggressive (maximum):**
```yaml
vllm_cpu_offload_gb: 220  # Use most of available RAM
vllm_memory_limit: "240G"
```

### Balance GPU vs CPU Cache

For different workload patterns:

**Low latency priority (more GPU cache):**
```yaml
vllm_gpu_memory_utilization: 0.95  # Push GPU to 95%
vllm_cpu_offload_gb: 100           # Less CPU offload
```

**High concurrency priority (more CPU cache):**
```yaml
vllm_gpu_memory_utilization: 0.90  # Standard GPU usage
vllm_cpu_offload_gb: 200           # More CPU offload
```

### Reduce Context Length to Increase Concurrency

```yaml
vllm_max_model_len: 4096  # Reduce from 8192
vllm_cpu_offload_gb: 150

# Result: 2x more concurrent requests (each uses half the KV cache)
```

## Troubleshooting

### OOM (Out of Memory) Errors

**Symptoms**: vLLM crashes with "out of memory" error

**Check**:
```bash
# System RAM exhausted?
free -h

# GPU VRAM exhausted?
nvidia-smi

# Check vLLM logs
journalctl -u vllm -n 100
```

**Solutions**:
1. Reduce `vllm_cpu_offload_gb` to leave more RAM for system
2. Reduce `vllm_max_model_len` to decrease per-request memory
3. Add swap space (not recommended, very slow)

### High Latency

**Symptoms**: All requests are slow, not just first token

**Check**:
```bash
# Is system swapping to disk?
vmstat 1
# Look at "si" (swap in) and "so" (swap out) - should be 0

# Is CPU saturated?
top
# Look at CPU usage - vLLM should use <50% when idle
```

**Solutions**:
1. Reduce `vllm_cpu_offload_gb` if system is swapping
2. Check PCIe bandwidth with `nvidia-smi topo -m`
3. Verify no other services consuming RAM

### Service Won't Start

**Symptoms**: vLLM fails to start after enabling CPU offload

**Check**:
```bash
# Check service status
systemctl status vllm

# Check logs for errors
journalctl -u vllm -n 50

# Common errors:
# - "Invalid value for --cpu-offload-gb" → vLLM version too old
# - "Cannot allocate memory" → systemd memory limit too low
```

**Solutions**:
1. Verify vLLM version supports CPU offload (0.6.0+)
2. Check systemd `MemoryMax` is high enough
3. Ensure container has enough RAM allocated (check `pct config <CTID>`)

## System Requirements

### Minimum

- **RAM**: 32GB system RAM (16GB for OS + 16GB for offload)
- **vLLM version**: 0.6.0 or later
- **Container memory**: **200GB minimum** (204800MB) - Must be allocated at Proxmox level
  - Check with: `pct config <CTID> | grep memory`
  - Update with: `pct set <CTID> -memory 204800`

### Recommended

- **RAM**: 128GB+ system RAM
- **vLLM version**: 0.6.3+ (latest stable)
- **Container memory**: At least 2x the `vllm_cpu_offload_gb` value

### Optimal (Your Setup)

- **RAM**: 256GB system RAM ✅
- **Configuration**: 150GB CPU offload + 180GB container limit ✅
- **Expected gain**: 20-40x concurrency increase ✅

## Related Documentation

- [GPU Allocation Strategy](../deployment/gpu-allocation-strategy.md) - Overall GPU allocation
- [vLLM Role README](../../provision/ansible/roles/vllm/README.md) - vLLM configuration
- [Performance Tuning](../guides/performance-tuning.md) - General performance optimization

