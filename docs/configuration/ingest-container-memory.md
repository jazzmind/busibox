# Ingest Container Memory Allocation

**Created**: 2024-11-19  
**Status**: Active  
**Category**: configuration

## Overview

The ingest container runs Marker (PDF extraction) and ColPali (visual embeddings), both GPU-accelerated services. This document explains memory allocation considerations and whether increasing container RAM will help.

## Key Findings

### 1. Marker and ColPali Do NOT Support CPU Offloading

**Unlike vLLM**, Marker and ColPali **do not support CPU memory offloading**. They are GPU-bound applications:

- **Marker**: Uses GPU VRAM (~3.5GB per task) for OCR and layout detection models
- **ColPali**: Uses GPU VRAM (~15GB total) for vision model inference
- Both load models entirely into GPU memory - they cannot offload to system RAM

**Implication**: Increasing container RAM won't directly help Marker/ColPali performance. They are limited by GPU VRAM, not system RAM.

### 2. Memory Sharing Between Containers

**Containers are isolated** - they don't directly share memory:

- **vLLM container**: 200GB allocated (for CPU offloading)
- **Ingest container**: 32GB allocated (current)
- **Total**: 232GB allocated

**Host RAM**: Typically 256GB, leaving ~24GB headroom for:
- Proxmox host OS (~4GB)
- Other containers (~10GB)
- Buffer (~10GB)

**Risk**: If total allocations exceed host RAM, Proxmox will swap to disk (very slow).

## Current Memory Usage

### Ingest Container (32GB allocated)

| Component | Memory Usage | Notes |
|-----------|-------------|-------|
| Marker (GPU) | ~3.5GB VRAM/task | GPU memory, not system RAM |
| ColPali (GPU) | ~15GB VRAM | GPU memory, not system RAM |
| Worker processes | ~2-4GB RAM | Python workers, Redis client |
| Temporary files | ~1-2GB RAM | PDF processing buffers |
| System overhead | ~2GB RAM | OS, systemd, etc. |
| **Total (typical)** | **~5-8GB RAM** | Well within 32GB limit |

### vLLM Container (200GB allocated)

| Component | Memory Usage | Notes |
|-----------|-------------|-------|
| Main vLLM service | 180GB limit | CPU offloading enabled |
| vLLM Embedding | 70GB limit | CPU offloading enabled |
| System overhead | ~2GB RAM | OS, systemd, etc. |
| **Total (peak)** | **~180GB RAM** | Uses allocated memory |

## Should We Increase Ingest Container RAM?

### Benefits of More RAM

1. **Worker Process Headroom**
   - Multiple concurrent PDF extractions
   - Batch processing overhead
   - Redis stream processing buffers

2. **Temporary File Processing**
   - Large PDF files (100+ MB)
   - Image extraction buffers
   - Chunking operations

3. **Avoid OOM Kills**
   - Burst workloads
   - Memory spikes during processing
   - Safety margin

### Drawbacks

1. **No Direct Benefit to Marker/ColPali**
   - They use GPU VRAM, not system RAM
   - More RAM won't improve their performance

2. **Reduces Headroom for vLLM**
   - vLLM needs 200GB for CPU offloading
   - Less headroom = higher swap risk

3. **Potential Swap Risk**
   - If total > 256GB, Proxmox will swap
   - Swap is 100x slower than RAM

## Recommendations

### Option 1: Keep Current Allocation (32GB) ✅ **Recommended**

**Pros**:
- Safe headroom for host (24GB free)
- Sufficient for current workload
- No swap risk

**Cons**:
- May need to increase if workload grows

**When to use**: Current workload is stable, no memory pressure observed.

### Option 2: Moderate Increase (48GB)

**Pros**:
- More headroom for worker processes
- Better handling of large files
- Still safe (total: 248GB, leaves 8GB headroom)

**Cons**:
- Reduces vLLM headroom slightly
- No benefit to Marker/ColPali GPU performance

**When to use**: Observing memory pressure, frequent OOM kills, or planning for growth.

### Option 3: Aggressive Increase (64GB) ⚠️ **Not Recommended**

**Pros**:
- Maximum headroom for workers
- Handles burst workloads easily

**Cons**:
- Total: 264GB (exceeds 256GB host!)
- **Will cause swapping** (very slow)
- No benefit to Marker/ColPali

**When to use**: Only if host RAM is upgraded to 512GB+.

## Memory Allocation Strategy

### Current Setup (256GB Host RAM)

```
Total Host RAM: 256GB
├─ Proxmox OS: ~4GB
├─ vLLM Container: 200GB (CPU offloading)
├─ Ingest Container: 32GB (current)
├─ Other Containers: ~10GB
└─ Buffer: ~10GB
```

### Recommended Allocation

```yaml
# In create-worker-services.sh
MEM_MB_INGEST=32768  # 32GB (current) - sufficient for workload
# Or if memory pressure observed:
MEM_MB_INGEST=49152  # 48GB (moderate increase)
```

### Maximum Safe Allocation

```yaml
# With 256GB host RAM:
# vLLM: 200GB
# Ingest: 48GB (max safe)
# Other: 8GB
# Total: 256GB (no swap)
```

## Monitoring

### Check Current Memory Usage

```bash
# On Proxmox host
pct config 206 | grep memory
# Should show: memory: 32768 (32GB)

# Inside ingest container
ssh root@10.96.200.206
free -h
# Check "used" vs "total" - should have headroom

# Check for OOM kills
dmesg | grep -i oom
journalctl -u ingest-worker | grep -i "killed\|oom"
```

### Check GPU Memory (Marker/ColPali)

```bash
# Inside ingest container
nvidia-smi
# Check GPU 0 memory usage
# Marker: ~3.5GB per task
# ColPali: ~15GB total
```

## When to Increase Memory

**Increase ingest container RAM if**:
1. ✅ Observing OOM kills (`dmesg | grep oom`)
2. ✅ Memory usage consistently >80% (`free -h`)
3. ✅ Worker processes being killed
4. ✅ Planning for higher concurrency

**Do NOT increase if**:
1. ❌ Only Marker/ColPali are slow (they need GPU VRAM, not RAM)
2. ❌ Total allocations would exceed host RAM
3. ❌ No memory pressure observed

## GPU vs RAM Memory

### GPU VRAM (What Marker/ColPali Use)

- **GPU 0**: 24GB total
  - ColPali: ~15GB (base model)
  - Marker: ~3.5GB per task
  - **Total**: ~18.5GB (fits comfortably)

- **Cannot be increased** by adding system RAM
- **Limited by GPU hardware** (24GB per RTX 3090)

### System RAM (What Workers Use)

- **Ingest Container**: 32GB allocated
  - Worker processes: ~2-4GB
  - Temporary files: ~1-2GB
  - System: ~2GB
  - **Total**: ~5-8GB (plenty of headroom)

- **Can be increased** by adjusting container allocation
- **Limited by host RAM** (256GB total)

## Summary

1. **Marker/ColPali don't benefit from more RAM** - they use GPU VRAM
2. **Current 32GB allocation is sufficient** for typical workloads
3. **Can increase to 48GB** if memory pressure observed (still safe)
4. **Don't exceed 48GB** with 256GB host (would cause swap)
5. **Monitor actual usage** before increasing

## Related Documentation

- [GPU Allocation Strategy](../deployment/gpu-allocation-strategy.md) - GPU memory sharing
- [vLLM CPU Offload](../configuration/vllm-cpu-offload.md) - vLLM memory requirements
- [Marker GPU Setup](../deployment/marker-gpu-setup.md) - Marker GPU configuration

