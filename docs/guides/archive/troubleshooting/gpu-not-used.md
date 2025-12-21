---
title: GPU Not Being Used by Application
created: 2025-10-30
updated: 2025-10-30
status: stable
category: troubleshooting
tags: [gpu, cuda, ollama, open-webui, debugging]
---

# GPU Not Being Used by Application

## Problem

You've configured GPU passthrough and installed NVIDIA drivers, but your application (Ollama, Open WebUI, etc.) is still using the CPU instead of the GPU.

## Quick Diagnosis

Run the diagnostic script:

```bash
# From Proxmox host
bash provision/pct/check-gpu-usage.sh <container-id>

# Or inside container
bash check-gpu-usage.sh
```

This will check:
- GPU device visibility
- NVIDIA driver status  
- GPU compute mode
- Current GPU processes
- GPU utilization
- CUDA environment
- Application-specific issues

## Common Causes & Fixes

### 1. Ollama Not Using GPU

**Symptoms:**
- `nvidia-smi` shows 0% GPU utilization
- `nvtop` shows no processes
- Model inference is slow

**Diagnosis:**
```bash
# Check Ollama logs
journalctl -u ollama -f

# Look for messages like:
# "CUDA not available"
# "Using CPU"
```

**Fix A: Set GPU Environment Variables**

Edit `/etc/systemd/system/ollama.service`:

```ini
[Service]
Environment="OLLAMA_NUM_GPU=1"
Environment="CUDA_VISIBLE_DEVICES=0"
Environment="NVIDIA_VISIBLE_DEVICES=all"

# If you have multiple GPUs and want to use all:
Environment="OLLAMA_NUM_GPU=3"
Environment="CUDA_VISIBLE_DEVICES=0,1,2"
```

Then restart:
```bash
systemctl daemon-reload
systemctl restart ollama
```

**Fix B: Verify Ollama Installation**

```bash
# Check Ollama can see CUDA
ollama run llama2 --verbose

# Should show GPU initialization in logs
```

**Fix C: Reinstall Ollama (if needed)**

```bash
# Ensure CUDA toolkit is installed first
apt install -y nvidia-cuda-toolkit

# Reinstall Ollama
curl -fsSL https://ollama.com/install.sh | sh
```

### 2. Open WebUI Not Using GPU

**Symptoms:**
- Open WebUI runs but models are slow
- GPU utilization is 0%

**Root Cause:**
Open WebUI itself doesn't use the GPU directly - it connects to Ollama (or another backend) which should use the GPU.

**Fix:**

1. **Ensure Ollama is using GPU** (see section 1 above)

2. **Check Open WebUI → Ollama connection**:
   - Open WebUI Settings → Connections
   - Verify Ollama URL is correct: `http://localhost:11434`
   - Test connection

3. **Verify model is loaded**:
   ```bash
   # In Ollama container
   ollama list
   ollama ps  # Shows currently loaded models
   ```

4. **Monitor GPU while making a request**:
   ```bash
   # Terminal 1: Monitor GPU
   watch -n 0.5 nvidia-smi
   
   # Terminal 2: Make inference request via Open WebUI
   # You should see GPU spike to 90-100%
   ```

### 3. PyTorch Can't See CUDA

**Symptoms:**
```python
import torch
torch.cuda.is_available()  # Returns False
```

**Fix:**

```bash
# Check CUDA toolkit is installed
nvidia-smi

# Reinstall PyTorch with CUDA support
pip3 uninstall torch torchvision torchaudio
pip3 install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# Verify
python3 -c "import torch; print(torch.cuda.is_available())"
# Should print: True
```

### 4. Wrong GPU Being Used (Multi-GPU)

**Symptoms:**
- GPU 0 shows usage but you want to use GPU 1
- Application not using the right GPU

**Fix:**

Set `CUDA_VISIBLE_DEVICES`:

```bash
# For specific application
export CUDA_VISIBLE_DEVICES=1  # Use only GPU 1
export CUDA_VISIBLE_DEVICES=0,2  # Use GPUs 0 and 2
export CUDA_VISIBLE_DEVICES=all  # Use all GPUs

# For systemd service (e.g., Ollama)
# Edit /etc/systemd/system/ollama.service
[Service]
Environment="CUDA_VISIBLE_DEVICES=1"
```

### 5. Driver Version Mismatch

**Symptoms:**
```
Failed to initialize NVML: Driver/library version mismatch
```

**Fix:**

```bash
# Check versions
nvidia-smi | grep "Driver Version"  # Host version
# vs
pct exec <container-id> -- nvidia-smi | grep "Driver Version"  # Container version

# If they don't match, reinstall driver in container
bash provision/pct/install-nvidia-drivers.sh <container-id>
```

### 6. Compute Mode is Prohibited

**Symptoms:**
```bash
nvidia-smi
# Shows: Compute Mode: Prohibited
```

**Fix:**

```bash
# Change compute mode to Default
nvidia-smi -c DEFAULT

# Or Exclusive Process (one process per GPU)
nvidia-smi -c EXCLUSIVE_PROCESS
```

## Testing GPU Usage

### Simple CUDA Test

```bash
# Test PyTorch CUDA
python3 -c "
import torch
x = torch.rand(5000, 5000).cuda()
y = x @ x
print(f'CUDA available: {torch.cuda.is_available()}')
print(f'Result computed on: {y.device}')
"
```

While running this, check `nvtop` or `nvidia-smi` - you should see GPU spike to 100%.

### Test with Ollama

```bash
# Terminal 1: Monitor GPU
watch -n 0.5 nvidia-smi

# Terminal 2: Run inference
ollama run llama2 "Write a story about a robot"

# GPU should spike during generation
```

### Benchmark GPU

```bash
# Simple benchmark
python3 << 'EOF'
import torch
import time

device = torch.device('cuda')
size = 10000

# Warmup
x = torch.rand(size, size, device=device)
y = x @ x
torch.cuda.synchronize()

# Benchmark
start = time.time()
for _ in range(10):
    y = x @ x
torch.cuda.synchronize()
elapsed = time.time() - start

print(f"10 matrix multiplications ({size}x{size}): {elapsed:.2f}s")
print(f"Using device: {device}")
print(f"GPU: {torch.cuda.get_device_name(0)}")
EOF
```

## Monitoring GPU in Real-Time

### Option 1: nvtop (Interactive)

```bash
pct enter <container-id>
nvtop

# Press 'q' to quit
```

### Option 2: nvidia-smi (Watch Mode)

```bash
# Update every 0.5 seconds
pct exec <container-id> -- watch -n 0.5 nvidia-smi

# Or just GPU utilization
pct exec <container-id> -- nvidia-smi --query-gpu=utilization.gpu,utilization.memory,temperature.gpu --format=csv -l 1
```

### Option 3: Continuous Logging

```bash
# Log GPU stats to file
pct exec <container-id> -- bash -c "
while true; do
  nvidia-smi --query-gpu=timestamp,utilization.gpu,utilization.memory,temperature.gpu,power.draw --format=csv,noheader >> /tmp/gpu-stats.log
  sleep 1
done
"

# View log
pct exec <container-id> -- tail -f /tmp/gpu-stats.log
```

## Checklist

After making changes, verify:

- [ ] `nvidia-smi` shows correct driver version
- [ ] `nvidia-smi` shows GPU(s) in default/exclusive compute mode
- [ ] Application logs show GPU initialization
- [ ] Environment variables are set (`CUDA_VISIBLE_DEVICES`, `OLLAMA_NUM_GPU`)
- [ ] Making an inference request shows GPU spike in `nvtop`/`nvidia-smi`
- [ ] GPU utilization goes above 50% during inference
- [ ] Inference is noticeably faster than CPU

## Still Not Working?

Run full diagnostics:

```bash
bash provision/pct/check-gpu-usage.sh <container-id>
```

Check the output for specific recommendations based on your configuration.

## Common Environment Variables

```bash
# CUDA runtime
CUDA_VISIBLE_DEVICES=0,1,2      # Which GPUs to use
NVIDIA_VISIBLE_DEVICES=all      # Alternative to CUDA_VISIBLE_DEVICES
CUDA_HOME=/usr/local/cuda       # CUDA installation path
LD_LIBRARY_PATH=/usr/local/cuda/lib64:$LD_LIBRARY_PATH

# Ollama
OLLAMA_NUM_GPU=1                # Number of GPUs for Ollama
OLLAMA_GPU_LAYERS=35            # Number of model layers on GPU

# PyTorch
PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:512  # Memory management
```

## Related Documentation

- [GPU Passthrough Guide](../guides/gpu-passthrough.md)
- [LLM Infrastructure Setup](../deployment/llm-infrastructure.md)
- [Common Issues](common-issues.md)


