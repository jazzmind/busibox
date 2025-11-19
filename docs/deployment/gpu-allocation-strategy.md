# GPU Allocation Strategy

**Created**: 2024-11-19  
**Status**: Active  
**Category**: deployment

## Overview

Busibox uses a strategic GPU allocation to optimize performance across different services. This document explains the GPU allocation strategy and memory sharing considerations.

## GPU Allocation

### Standard Allocation (2+ GPUs)

| GPU | Service | Purpose | Memory Usage |
|-----|---------|---------|--------------|
| GPU 0 | Ingest Container | Marker PDF extraction + ColPali visual embeddings | Marker: ~3.5GB/task, ColPali: ~15GB |
| GPU 1+ | vLLM Container | LLM inference (tensor parallelism) | Model-dependent |

### Minimum Configuration (2 GPUs)

| GPU | Service | Purpose | Notes |
|-----|---------|---------|-------|
| GPU 0 | Ingest Container | Marker PDF extraction + ColPali | Shares GPU 0, memory managed automatically |
| GPU 1 | vLLM Container | LLM inference | Limited tensor parallelism |

**Note**: With only 2 GPUs, vLLM performance will be reduced. Consider disabling GPU for ingest if vLLM is the priority.

### Single GPU Configuration

With only 1 GPU, you must choose:

**Option A: vLLM Priority**
- GPU 0 → vLLM Container
- Ingest → CPU-only (Marker disabled or CPU mode)
- ColPali → Not available

**Option B: Ingest Priority**
- GPU 0 → Ingest Container (Marker + ColPali sharing)
- vLLM → CPU-only (not recommended)

## Memory Sharing Considerations

### Marker + ColPali on Same GPU (GPU 0)

If Marker and ColPali share GPU 0 (24GB), memory allocation:

| Service | Memory Usage | Notes |
|---------|--------------|-------|
| ColPali Model | ~11GB | Base model (google/paligemma-3b-pt-448) |
| ColPali Processing | ~4GB | Batch processing, activations |
| Marker per Task | ~3.5GB | Configurable via `MARKER_VRAM_PER_TASK` |
| **Total (1 task)** | ~18.5GB | Fits in 24GB with headroom |
| **Total (2 tasks)** | ~22GB | Tight, may cause OOM |

**Recommendations**:
- Set `MARKER_VRAM_PER_TASK=3.0` to allow 2 concurrent Marker tasks
- Set `MARKER_INFERENCE_RAM=20` to reserve more for Marker
- Monitor GPU memory: `watch -n 1 nvidia-smi`
- Consider disabling ColPali if Marker is priority

### Configuration for Shared GPU

```bash
# In ingest container environment
export MARKER_USE_GPU=true
export MARKER_GPU_DEVICE=cuda
export MARKER_INFERENCE_RAM=20  # Reserve 20GB for Marker
export MARKER_VRAM_PER_TASK=3.0  # Reduce per-task memory

# ColPali uses CUDA_VISIBLE_DEVICES=0 (same GPU)
# ColPali memory is managed by the service itself
```

## Container GPU Configuration

### Ingest Container (206)

**GPU**: GPU 0 (dedicated)  
**Purpose**: Marker PDF extraction  
**Memory**: ~3.5GB per task (configurable)

**Configuration**:
- GPU passthrough configured automatically during container creation
- NVIDIA drivers must be installed in container
- Marker automatically detects and uses GPU

**Verify**:
```bash
ssh root@10.96.200.206
nvidia-smi
python3 -c "import torch; print(torch.cuda.is_available())"
```

### vLLM Container (208)

**GPU**: GPUs 1+ (all remaining GPUs)  
**Purpose**: LLM inference with tensor parallelism  
**Memory**: Model-dependent (typically 20-40GB per GPU)

**Configuration**:
- Automatically uses GPUs 1 onwards
- Requires 2+ GPUs for optimal performance
- Uses tensor parallelism for model sharding

**Verify**:
```bash
ssh root@10.96.200.208
nvidia-smi
# Should show GPUs 1, 2, 3... (not GPU 0)
```

### ColPali Service

**GPU**: GPU 0 (shares with Marker)  
**Purpose**: Visual document embeddings  
**Memory**: ~15GB (model + processing)

**Configuration**:
- Set via `CUDA_VISIBLE_DEVICES` environment variable
- Configured in Ansible: `colpali_cuda_visible_devices: "0"` (default)
- Runs as systemd service in ingest container

**Current Setup**:
- ColPali shares GPU 0 with Marker by default
- Memory is managed automatically (Marker: ~3.5GB/task, ColPali: ~15GB)
- Future: Will support multi-GPU ColPali for larger workloads

## Automatic Configuration

GPU passthrough is configured automatically during container creation:

1. **Ingest Container**: `create-worker-services.sh` adds GPU 0
2. **vLLM Container**: `create-vllm.sh` adds GPUs 1+
3. **ColPali**: Configured via Ansible (uses GPU 0 by default, shares with Marker)

## Manual GPU Reconfiguration

If you need to change GPU allocation:

```bash
# On Proxmox host
cd /root/busibox/provision/pct

# Reconfigure ingest to use GPU 0
bash host/configure-gpu-passthrough.sh 206 0 --force

# Reconfigure vLLM to use GPUs 1,2,3
bash host/configure-gpu-passthrough.sh 208 1,2,3 --force

# Or use range format
bash host/configure-gpu-passthrough.sh 208 1-3 --force
```

## Monitoring GPU Usage

### Check GPU Allocation

```bash
# On Proxmox host
nvidia-smi

# Check container GPU access
pct exec 206 -- nvidia-smi  # Ingest (should see GPU 0)
pct exec 208 -- nvidia-smi  # vLLM (should see GPUs 1+)
```

### Monitor Memory Usage

```bash
# Watch GPU memory in real-time
watch -n 1 'nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv'

# Check specific container
ssh root@10.96.200.206 nvidia-smi  # Ingest container
```

## Troubleshooting

### GPU Not Visible in Container

**Symptoms**: `nvidia-smi` fails or shows "No devices found"

**Check**:
1. GPU passthrough configured: `grep -i nvidia /etc/pve/lxc/206.conf`
2. NVIDIA drivers installed: `nvidia-smi` in container
3. Container has access: `ls -la /dev/nvidia*` in container

**Fix**:
```bash
# Reconfigure GPU passthrough
bash provision/pct/host/configure-gpu-passthrough.sh 206 0 --force

# Install drivers in container
ssh root@10.96.200.206
apt install -y nvidia-driver-535 nvidia-cuda-toolkit
```

### Out of Memory Errors

**Symptoms**: CUDA OOM errors, services crash

**Solutions**:
1. Reduce Marker memory per task: `MARKER_VRAM_PER_TASK=2.5`
2. Reduce Marker inference RAM: `MARKER_INFERENCE_RAM=16`
3. Disable ColPali if not needed
4. Reduce vLLM batch size or model size

### vLLM Using Wrong GPUs

**Symptoms**: vLLM uses GPU 0 instead of GPUs 1+

**Check**:
```bash
# Check vLLM container config
grep -i nvidia /etc/pve/lxc/208.conf

# Check CUDA_VISIBLE_DEVICES in vLLM service
systemctl cat vllm.service | grep CUDA_VISIBLE_DEVICES
```

**Fix**: Reconfigure GPU passthrough for vLLM container

## Best Practices

1. **Dedicate GPU 0 to Ingest**: Marker benefits significantly from GPU acceleration
2. **Use 2+ GPUs for vLLM**: Tensor parallelism improves throughput
3. **Monitor Memory**: Use `nvidia-smi` to track memory usage
4. **Adjust Config**: Tune `MARKER_VRAM_PER_TASK` based on workload
5. **Separate ColPali**: Use GPU 2 if available, share GPU 0 if needed

## Related Documentation

- [Marker GPU Setup](marker-gpu-setup.md) - Detailed Marker GPU configuration
- [GPU Passthrough Configuration](../configuration/gpu-passthrough.md) - Technical GPU passthrough guide
- [Ingest Service Deployment](ingest-service.md) - Ingest container setup
- [vLLM Deployment](vllm-deployment.md) - vLLM container setup

