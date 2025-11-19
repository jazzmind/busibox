# Marker GPU Setup Guide

**Created**: 2024-11-19  
**Status**: Active  
**Category**: deployment

## Overview

Marker PDF extraction can use GPU acceleration for significantly faster processing. This guide explains how to configure GPU access for the ingest container.

## Current Status

**Marker GPU Configuration**: ✅ Code supports GPU (as of 2024-11-19)  
**Ingest Container GPU Access**: ⚠️ Needs to be configured

## GPU Configuration

### Environment Variables

Marker uses these environment variables (set automatically by code):

- `TORCH_DEVICE` - Set to `cuda` for GPU, `cpu` for CPU
- `INFERENCE_RAM` - GPU VRAM capacity in GB (default: 16)
- `VRAM_PER_TASK` - VRAM per task in GB (default: 3.5)

### Application Config

Set these in your environment or Ansible vars:

```bash
MARKER_USE_GPU=true              # Enable GPU (default: true)
MARKER_GPU_DEVICE=cuda          # Device: cuda, cpu, or auto (default: cuda)
MARKER_INFERENCE_RAM=16         # GPU VRAM in GB (default: 16)
MARKER_VRAM_PER_TASK=3.5        # VRAM per task in GB (default: 3.5)
```

## Enabling GPU for Ingest Container

### Step 1: Configure GPU Passthrough

The ingest container (206) needs GPU passthrough configured on the Proxmox host:

```bash
# On Proxmox host
cd /root/busibox/provision/pct

# Add GPU 0 to ingest container (or use specific GPU number)
bash host/configure-gpu-passthrough.sh 206 0

# Or add all GPUs (if you want to share with other containers)
bash host/configure-gpu-passthrough.sh 206 0,1,2
```

### Step 2: Install NVIDIA Drivers in Container

After GPU passthrough is configured, install drivers in the container:

```bash
# SSH into ingest container
ssh root@10.96.200.206

# Install NVIDIA drivers
apt update
apt install -y nvidia-driver-535 nvidia-cuda-toolkit

# Verify GPU access
nvidia-smi
```

### Step 3: Verify GPU Detection

Check that PyTorch can see the GPU:

```bash
# In ingest container
cd /srv/ingest
source venv/bin/activate

python3 -c "import torch; print(f'CUDA available: {torch.cuda.is_available()}'); print(f'GPU count: {torch.cuda.device_count()}')"
```

Expected output:
```
CUDA available: True
GPU count: 1
```

### Step 4: Test Marker with GPU

Run a test extraction to verify GPU is being used:

```bash
# Check GPU usage while Marker runs
watch -n 1 nvidia-smi

# In another terminal, trigger Marker extraction
# (upload a PDF via API or run test)
```

You should see GPU utilization increase during Marker processing.

## Performance Comparison

**CPU-only Marker**: ~30-60 seconds per page  
**GPU-accelerated Marker**: ~2-5 seconds per page

**Speedup**: 10-30x faster with GPU

## Troubleshooting

### GPU Not Detected

**Symptoms**: Marker runs slowly, logs show "using CPU"

**Check**:
1. GPU passthrough configured: `grep -i nvidia /etc/pve/lxc/206.conf`
2. NVIDIA drivers installed: `nvidia-smi` in container
3. PyTorch CUDA available: `python3 -c "import torch; print(torch.cuda.is_available())"`

**Fix**:
```bash
# Reconfigure GPU passthrough
bash provision/pct/host/configure-gpu-passthrough.sh 206 0 --force

# Reinstall drivers
apt install --reinstall nvidia-driver-535 nvidia-cuda-toolkit
```

### Out of Memory Errors

**Symptoms**: Marker crashes with CUDA OOM errors

**Fix**: Reduce `MARKER_VRAM_PER_TASK` or `MARKER_INFERENCE_RAM`:

```bash
# In container environment or Ansible vars
export MARKER_VRAM_PER_TASK=2.0  # Reduce from 3.5
export MARKER_INFERENCE_RAM=8    # Reduce from 16
```

### Multiple Containers Sharing GPU

Multiple containers can share the same GPU:

```bash
# Configure GPU 0 for multiple containers
bash configure-gpu-passthrough.sh 206 0  # ingest
bash configure-gpu-passthrough.sh 208 0  # vllm (if needed)
```

**Note**: GPU sharing works, but performance will be reduced if both containers use GPU simultaneously.

## GPU Requirements

### Minimum
- NVIDIA GPU with CUDA support
- 8GB+ VRAM (for Marker models)
- NVIDIA driver 535+ on Proxmox host

### Recommended
- 16GB+ VRAM (allows larger batch sizes)
- Dedicated GPU for ingest (not shared with vLLM/Ollama)
- NVIDIA driver 550+ for best performance

## Monitoring GPU Usage

### Check GPU Status

```bash
# On Proxmox host
nvidia-smi

# In container
ssh root@10.96.200.206 nvidia-smi
```

### Monitor During Processing

```bash
# Watch GPU usage in real-time
watch -n 1 'ssh root@10.96.200.206 nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total --format=csv'
```

## Related Documentation

- [GPU Passthrough Configuration](../configuration/gpu-passthrough.md)
- [Ingest Service Deployment](ingest-service.md)
- [Marker Extraction Strategy](../../testing/extraction-test-targets.md)

## Next Steps

1. ✅ Code supports GPU (completed)
2. ⏭️ Configure GPU passthrough for ingest container
3. ⏭️ Install NVIDIA drivers in container
4. ⏭️ Verify GPU detection and usage
5. ⏭️ Monitor performance improvements

