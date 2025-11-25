---
title: GPU Performance Debugging Guide
created: 2025-11-21
updated: 2025-11-21
status: stable
category: troubleshooting
tags: [gpu, vllm, performance, debugging, phi4]
---

# GPU Performance Debugging Guide

## Problem

A model that was previously fast (e.g., phi4-multimodal-instruct) is now running extremely slowly on a specific GPU. This guide provides systematic tests to diagnose whether the issue is:

1. Model configuration issue
2. Corrupt model loading
3. GPU resource contention
4. GPU hardware failure

## Prerequisites

- SSH access to vLLM container
- Model is deployed and accessible
- Basic knowledge of GPU commands

## Quick Diagnosis Checklist

Before diving into detailed tests, run these quick checks:

```bash
# SSH into vLLM container
ssh root@<vllm-container-ip>

# 1. Check GPU visibility and basic health
nvidia-smi

# 2. Check which GPU the model is using
systemctl status vllm-8000  # or appropriate port
cat /etc/systemd/system/vllm-8000.service | grep CUDA_VISIBLE_DEVICES

# 3. Check current GPU processes
nvidia-smi pmon -c 1

# 4. Check GPU error count
nvidia-smi --query-gpu=index,name,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total --format=csv
```

## Systematic Testing Procedure

### Phase 1: Model Configuration Validation

**Goal**: Verify the model is configured correctly and using the expected GPU.

#### Test 1.1: Check vLLM Service Configuration

```bash
# Find which service is running phi4-multimodal-instruct
grep -r "phi-4\|Phi-4-multimodal" /etc/systemd/system/vllm-*.service

# Example output should show something like:
# /etc/systemd/system/vllm-8000.service:ExecStart=/opt/vllm/venv/bin/python -m vllm.entrypoints.openai.api_server \
#   --model microsoft/Phi-4-multimodal-instruct \
#   ...

# Check the service configuration
SERVICE_NAME="vllm-8000"  # Replace with actual service name
systemctl cat $SERVICE_NAME

# Look for:
# - CUDA_VISIBLE_DEVICES (which GPU?)
# - --gpu-memory-utilization (should be 0.9 for phi4)
# - --max-model-len (should be 16384)
# - --tensor-parallel-size (should be 1 for phi4)
```

**Expected Results**:
- `CUDA_VISIBLE_DEVICES` should be set to a single GPU (e.g., "1" or "2")
- `gpu_memory_utilization` should be 0.9
- `max_model_len` should be 16384
- `tensor_parallel_size` should be 1

**If configuration looks wrong**: Check `/etc/vllm/vllm-<port>.env` and service file.

#### Test 1.2: Verify Model Loading Location

```bash
# Check where model is cached
SERVICE_NAME="vllm-8000"  # Replace with actual service name
systemctl cat $SERVICE_NAME | grep -E "HF_HOME|TRANSFORMERS_CACHE|HF_HUB_CACHE"

# Check model exists and isn't corrupted
MODEL_CACHE="/var/lib/llm-models/huggingface/hub"
ls -lh $MODEL_CACHE/models--microsoft--Phi-4-multimodal-instruct/

# Check model size (should be ~10-15GB for phi4)
du -sh $MODEL_CACHE/models--microsoft--Phi-4-multimodal-instruct/
```

**Expected Results**:
- Model directory exists
- Total size is approximately 10-15GB
- No obvious corruption (missing files, 0-byte files)

#### Test 1.3: Compare Configuration with Working Model

If you have another model that works well, compare configurations:

```bash
# List all vLLM services
systemctl list-units "vllm-*" --all

# Compare configurations
diff <(systemctl cat vllm-8000) <(systemctl cat vllm-8001)

# Check GPU assignments
for service in /etc/systemd/system/vllm-*.service; do
    echo "=== $(basename $service) ==="
    grep CUDA_VISIBLE_DEVICES $service
done
```

### Phase 2: GPU Hardware Testing

**Goal**: Determine if the GPU itself is failing or degraded.

#### Test 2.1: GPU Information and Health Check

```bash
# Get detailed GPU information
nvidia-smi -q -i 0  # Replace 0 with your GPU index

# Key things to check:
# - Temperature (should be < 85°C under load)
# - Power Draw (should match spec under load)
# - GPU Utilization (should be 0% when idle)
# - Memory Errors (should be 0 or very low)
# - Compute Mode (should be "Default" or "Exclusive Process")

# Check for ECC errors (if supported)
nvidia-smi --query-gpu=index,name,ecc.errors.corrected.volatile.total,ecc.errors.uncorrected.volatile.total --format=csv

# Check GPU clocks
nvidia-smi --query-gpu=index,clocks.current.graphics,clocks.max.graphics,clocks.current.memory,clocks.max.memory --format=csv
```

**Red Flags**:
- High uncorrected ECC errors (> 0)
- Temperature > 90°C
- Clock speeds significantly below max
- Power draw much lower than expected under load

#### Test 2.2: GPU Memory Test

```bash
# Install GPU memory test tool if not present
apt-get update && apt-get install -y cuda-toolkit-12-1

# Run GPU memory test (WARNING: This will stop vLLM services)
# Only run this if you can afford downtime
systemctl stop vllm-*

# Test GPU memory (replace 0 with your GPU index)
# This runs for ~5 minutes
cd /usr/local/cuda/samples/1_Utilities/bandwidthTest
./bandwidthTest --device=0

# Or use a Python-based test
python3 << 'EOF'
import torch
import time

# Replace with your GPU index
gpu_id = 0
device = torch.device(f'cuda:{gpu_id}')

print(f"Testing GPU {gpu_id}: {torch.cuda.get_device_name(gpu_id)}")

# Test 1: Memory allocation
print("\n1. Testing memory allocation...")
try:
    # Allocate 90% of GPU memory
    total_memory = torch.cuda.get_device_properties(gpu_id).total_memory
    test_size = int(total_memory * 0.9 / 4)  # float32 = 4 bytes
    x = torch.randn(test_size, device=device)
    print(f"✓ Successfully allocated {total_memory * 0.9 / 1e9:.2f}GB")
    del x
    torch.cuda.empty_cache()
except Exception as e:
    print(f"✗ Memory allocation failed: {e}")

# Test 2: Compute performance
print("\n2. Testing compute performance...")
size = 8192
x = torch.randn(size, size, device=device)
y = torch.randn(size, size, device=device)

# Warmup
for _ in range(3):
    z = torch.matmul(x, y)
torch.cuda.synchronize()

# Benchmark
start = time.time()
iterations = 20
for _ in range(iterations):
    z = torch.matmul(x, y)
torch.cuda.synchronize()
elapsed = time.time() - start

tflops = (2 * size**3 * iterations) / elapsed / 1e12
print(f"✓ Performance: {tflops:.2f} TFLOPS")
print(f"  ({iterations} iterations of {size}x{size} matmul in {elapsed:.2f}s)")

# Test 3: Memory bandwidth
print("\n3. Testing memory bandwidth...")
size = 100_000_000  # 100M floats = 400MB
x = torch.randn(size, device=device)
torch.cuda.synchronize()

start = time.time()
iterations = 50
for _ in range(iterations):
    y = x * 2.0
torch.cuda.synchronize()
elapsed = time.time() - start

bandwidth = (size * 4 * 2 * iterations) / elapsed / 1e9  # Read + Write
print(f"✓ Bandwidth: {bandwidth:.2f} GB/s")

print("\n✓ GPU hardware tests completed")
EOF

# Restart services
systemctl start vllm-*
```

**Expected Results** (for modern GPUs like RTX 3090/4090, A100):
- Memory allocation: Should succeed
- Compute performance: 
  - RTX 3090: ~30-35 TFLOPS
  - RTX 4090: ~80-90 TFLOPS
  - A100: ~150-300 TFLOPS (depending on precision)
- Memory bandwidth:
  - RTX 3090: ~900 GB/s
  - RTX 4090: ~1000 GB/s
  - A100: ~1500-2000 GB/s

**If performance is significantly lower**: GPU may be failing or throttling.

#### Test 2.3: GPU Stress Test

```bash
# Install stress test tool
pip3 install gpu-burn

# Run 5-minute stress test (WARNING: GPU will run at 100%)
# Monitor temperature with: watch -n 1 nvidia-smi
python3 -m gpu_burn 300  # 300 seconds = 5 minutes

# Watch for:
# - Temperature should stabilize < 85°C
# - No errors or crashes
# - Consistent power draw
# - GPU utilization stays at 100%
```

#### Test 2.4: Compare GPU Performance

If you have multiple GPUs, compare them:

```bash
# Test all GPUs
python3 << 'EOF'
import torch
import time

def benchmark_gpu(gpu_id):
    device = torch.device(f'cuda:{gpu_id}')
    size = 8192
    
    x = torch.randn(size, size, device=device)
    y = torch.randn(size, size, device=device)
    
    # Warmup
    for _ in range(3):
        z = torch.matmul(x, y)
    torch.cuda.synchronize()
    
    # Benchmark
    start = time.time()
    iterations = 20
    for _ in range(iterations):
        z = torch.matmul(x, y)
    torch.cuda.synchronize()
    elapsed = time.time() - start
    
    tflops = (2 * size**3 * iterations) / elapsed / 1e12
    return tflops

print("GPU Performance Comparison:")
print("-" * 50)
for i in range(torch.cuda.device_count()):
    name = torch.cuda.get_device_name(i)
    tflops = benchmark_gpu(i)
    print(f"GPU {i} ({name}): {tflops:.2f} TFLOPS")
EOF
```

**If one GPU is significantly slower**: That GPU may be failing.

### Phase 3: Model Loading and Inference Testing

**Goal**: Test if the model loads correctly and performs as expected.

#### Test 3.1: Check Model Loading Time

```bash
# Stop the vLLM service
SERVICE_NAME="vllm-8000"  # Replace with actual service name
systemctl stop $SERVICE_NAME

# Monitor GPU during startup
# Terminal 1:
watch -n 0.5 nvidia-smi

# Terminal 2: Start service and time it
time systemctl start $SERVICE_NAME

# Check logs for loading time
journalctl -u $SERVICE_NAME -n 100 --no-pager | grep -E "Loading|Loaded|model"

# Expected: Model should load in 30-60 seconds for phi4
```

**Red Flags**:
- Loading takes > 2 minutes
- Errors in logs about model loading
- GPU memory doesn't increase during loading

#### Test 3.2: Test Model Inference Performance

```bash
# Get the vLLM API port
SERVICE_NAME="vllm-8000"
PORT=$(systemctl cat $SERVICE_NAME | grep -oP 'port \K[0-9]+' | head -1)

# Test 1: Simple text completion (no vision)
echo "Testing text completion..."
time curl -X POST "http://localhost:$PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "microsoft/Phi-4-multimodal-instruct",
    "prompt": "The capital of France is",
    "max_tokens": 10,
    "temperature": 0
  }'

# Test 2: Longer generation
echo -e "\n\nTesting longer generation..."
time curl -X POST "http://localhost:$PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "microsoft/Phi-4-multimodal-instruct",
    "prompt": "Write a short story about a robot:",
    "max_tokens": 100,
    "temperature": 0.7
  }'

# Test 3: Monitor GPU during inference
# Terminal 1:
watch -n 0.1 nvidia-smi

# Terminal 2: Run inference
curl -X POST "http://localhost:$PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "microsoft/Phi-4-multimodal-instruct",
    "prompt": "Write a detailed essay about artificial intelligence:",
    "max_tokens": 500,
    "temperature": 0.7
  }'
```

**Expected Results**:
- Short completion (10 tokens): < 1 second
- Longer generation (100 tokens): 2-5 seconds
- GPU utilization should spike to 80-100% during inference
- GPU memory should be stable (not growing)

**Red Flags**:
- Completions take > 10 seconds
- GPU utilization stays low (< 30%)
- Errors in response

#### Test 3.3: Compare with Another Model

If you have another working model (e.g., qwen3-30b), compare performance:

```bash
# Test qwen3-30b (or another model)
QWEN_PORT=8001  # Replace with actual port

time curl -X POST "http://localhost:$QWEN_PORT/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "cpatonn/Qwen3-30B-A3B-Instruct-2507-AWQ-4bit",
    "prompt": "The capital of France is",
    "max_tokens": 10,
    "temperature": 0
  }'

# Compare:
# - Response time
# - GPU utilization pattern
# - Any errors
```

#### Test 3.4: Reload Model from Scratch

If model loading seems corrupted, try reloading:

```bash
SERVICE_NAME="vllm-8000"

# Stop service
systemctl stop $SERVICE_NAME

# Clear vLLM cache (not model cache)
rm -rf /tmp/vllm_cache/*
rm -rf /var/lib/vllm/.cache/*

# Optional: Re-download model (if you suspect corruption)
# WARNING: This will re-download ~15GB
# rm -rf /var/lib/llm-models/huggingface/hub/models--microsoft--Phi-4-multimodal-instruct

# Restart service
systemctl start $SERVICE_NAME

# Monitor logs
journalctl -u $SERVICE_NAME -f
```

### Phase 4: Resource Contention Testing

**Goal**: Check if another process is interfering with GPU performance.

#### Test 4.1: Check for Other GPU Processes

```bash
# List all GPU processes
nvidia-smi pmon -c 1

# Detailed process information
nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv

# Check if multiple vLLM instances are on same GPU
ps aux | grep vllm

# Check systemd services
systemctl list-units "vllm-*" --all
for service in vllm-8000 vllm-8001 vllm-8002 vllm-8003 vllm-8004 vllm-8005; do
    if systemctl is-active $service &>/dev/null; then
        echo "$service: $(systemctl show $service -p Environment | grep CUDA_VISIBLE_DEVICES)"
    fi
done
```

**Red Flags**:
- Multiple vLLM services assigned to same GPU
- Unknown processes using GPU
- ColPali or other services on same GPU

#### Test 4.2: Test with Isolated GPU

If you suspect contention, test with GPU isolation:

```bash
# Stop all vLLM services
systemctl stop vllm-*

# Start only the problematic service
systemctl start vllm-8000

# Verify it's the only process
nvidia-smi pmon -c 1

# Test performance
# (Use Test 3.2 commands)
```

### Phase 5: Configuration Comparison

**Goal**: Compare current configuration with known-good configuration.

#### Test 5.1: Check Model Registry Configuration

```bash
# On your admin workstation
cd /path/to/busibox/provision/ansible

# Check model registry
cat group_vars/all/model_registry.yml | grep -A 10 "phi-4"

# Expected configuration:
# "phi-4":
#   provider: "vllm"
#   model: "phi-4"
#   model_name: "microsoft/Phi-4-multimodal-instruct"
#   gpu_memory_utilization: 0.9
#   max_model_len: 16384
#   max_num_seqs: 10
#   cpu_offload_gb: 100
```

#### Test 5.2: Check Generated Model Config

```bash
# On Proxmox host
cat /root/busibox/provision/ansible/model_config.yml

# Look for phi-4 entry and verify:
# - Correct GPU assignment
# - Correct port
# - Correct parameters
```

## Diagnostic Decision Tree

Based on test results, follow this decision tree:

### Scenario A: GPU Hardware Issue
**Symptoms**:
- Phase 2 tests show poor performance across all tests
- GPU significantly slower than other GPUs
- High ECC errors or temperature issues

**Actions**:
1. Check GPU cooling (fans, thermal paste)
2. Update NVIDIA drivers on host
3. Run extended hardware diagnostics
4. Consider RMA if under warranty
5. Reassign model to different GPU

### Scenario B: Model Configuration Issue
**Symptoms**:
- Phase 1 tests show wrong configuration
- GPU hardware tests (Phase 2) pass
- Other models on same GPU work fine

**Actions**:
1. Check `model_registry.yml` configuration
2. Regenerate model config: `bash update-model-config.sh`
3. Redeploy vLLM service: `cd provision/ansible && ansible-playbook -i inventory/test/hosts.yml site.yml --tags vllm_8000`
4. Verify configuration after deployment

### Scenario C: Corrupt Model Cache
**Symptoms**:
- Model loads slowly or fails
- Errors in vLLM logs about model loading
- GPU hardware tests pass

**Actions**:
1. Clear vLLM cache (see Test 3.4)
2. Re-download model (see Test 3.4)
3. Verify model integrity after download
4. Restart service

### Scenario D: GPU Resource Contention
**Symptoms**:
- Phase 4 tests show multiple processes on GPU
- Performance improves when other services stopped
- GPU utilization fluctuates

**Actions**:
1. Review GPU allocation strategy
2. Reassign services to different GPUs
3. Update `model_config.yml` with new assignments
4. Redeploy services

### Scenario E: vLLM Software Issue
**Symptoms**:
- All hardware tests pass
- Configuration looks correct
- Model cache is valid
- No contention

**Actions**:
1. Check vLLM version: `pip show vllm`
2. Check for known issues: https://github.com/vllm-project/vllm/issues
3. Try downgrading/upgrading vLLM
4. Check vLLM logs for errors: `journalctl -u vllm-8000 -n 500`

## Quick Reference Commands

### Essential Monitoring Commands

```bash
# Real-time GPU monitoring
watch -n 0.5 nvidia-smi

# GPU process monitoring
nvidia-smi pmon -c 1

# vLLM service logs
journalctl -u vllm-8000 -f

# Check all vLLM services and their GPUs
for service in vllm-8000 vllm-8001 vllm-8002 vllm-8003 vllm-8004 vllm-8005; do
    if systemctl is-active $service &>/dev/null; then
        echo "=== $service ==="
        systemctl show $service -p Environment | grep CUDA_VISIBLE_DEVICES
        echo ""
    fi
done

# Test inference performance
time curl -X POST "http://localhost:8000/v1/completions" \
  -H "Content-Type: application/json" \
  -d '{"model": "microsoft/Phi-4-multimodal-instruct", "prompt": "Hello", "max_tokens": 10}'
```

### GPU Health Check

```bash
# One-command health check
nvidia-smi --query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,clocks.current.graphics,ecc.errors.uncorrected.volatile.total --format=csv
```

## Related Documentation

- [GPU Not Being Used](gpu-not-used.md) - Basic GPU setup troubleshooting
- [GPU Allocation Strategy](../deployment/gpu-allocation-strategy.md) - How GPUs are assigned
- [vLLM Configuration](../configuration/vllm-configuration.md) - vLLM setup details
- [Model Registry](../reference/model-registry.md) - Model configuration reference

## Next Steps

After identifying the issue:

1. **Document findings**: Note which tests failed and symptoms
2. **Apply fix**: Follow the appropriate action from decision tree
3. **Verify fix**: Re-run relevant tests to confirm resolution
4. **Update configuration**: Ensure changes are reflected in Ansible vars
5. **Monitor**: Watch GPU performance for 24 hours to ensure stability

