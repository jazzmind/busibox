# LLM Infrastructure Deployment Guide

**Project**: Spec 003 - LLM Infrastructure  
**Created**: 2025-10-16  
**Status**: Active

## Overview

This guide walks through deploying the LLM infrastructure stack: LiteLLM proxy, Ollama, and vLLM model servers with GPU passthrough.

---

## Prerequisites

### Required Hardware
- ✅ Proxmox host with 2+ NVIDIA GPUs
- ✅ GPUs support CUDA 12.1+
- ✅ 32GB+ RAM recommended
- ✅ 200GB+ free storage (for models)

### Required Software
- ✅ Proxmox VE 8.x
- ✅ NVIDIA drivers installed on host
- ✅ IOMMU enabled in BIOS
- ✅ vfio kernel modules loaded

### Verify Prerequisites

```bash
# On Proxmox host, verify GPUs
nvidia-smi

# Check IOMMU
dmesg | grep -i iommu

# Check vfio modules
lsmod | grep vfio
```

---

## Phase 1: Container Provisioning (T002)

### Test Environment

```bash
# SSH to Proxmox host
ssh root@proxmox

# Navigate to provisioning directory
cd /root/busibox/provision/pct

# Load test configuration
source test-vars.env
print_test_config

# Create LXC containers
bash create_lxc_base.sh test

# Verify containers created
pct list | grep TEST

# Expected output:
# 307   TEST-litellm-lxc  running
# 308   TEST-ollama-lxc   running
# 309   TEST-vllm-lxc     running
```

### Verify Container Connectivity

```bash
# Test ping to each container
ping -c 2 10.96.201.207  # LiteLLM
ping -c 2 10.96.201.208  # Ollama
ping -c 2 10.96.201.209  # vLLM

# Test SSH access
ssh root@10.96.201.207
exit

ssh root@10.96.201.208
exit

ssh root@10.96.201.209
exit
```

---

## Phase 2: GPU Passthrough Configuration (T003)

### Step 1: Verify Host GPU Setup

```bash
# On Proxmox host
nvidia-smi

# Expected output:
# +-----------------------------------------------------------------------------+
# | NVIDIA-SMI 535.xx.xx    Driver Version: 535.xx.xx    CUDA Version: 12.2   |
# |-------------------------------+----------------------+----------------------+
# |   0  NVIDIA GPU 0       | ...                                              |
# |   1  NVIDIA GPU 1       | ...                                              |
# +-----------------------------------------------------------------------------+
```

### Step 2: Check Device Numbers

```bash
# List NVIDIA devices
ls -la /dev/nvidia*

# Expected output:
# crw-rw-rw- 1 root root 195, 0 Oct 16 10:00 /dev/nvidia0
# crw-rw-rw- 1 root root 195, 1 Oct 16 10:00 /dev/nvidia1
# crw-rw-rw- 1 root root 195, 255 Oct 16 10:00 /dev/nvidiactl
# crw-rw-rw- 1 root root 234, 0 Oct 16 10:00 /dev/nvidia-uvm
# crw-rw-rw- 1 root root 234, 1 Oct 16 10:00 /dev/nvidia-uvm-tools
```

### Step 3: Configure GPU for Ollama Container (GPU 0)

```bash
# Edit Ollama container config
nano /etc/pve/lxc/308.conf

# Add these lines at the end:
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file

# Save and exit (Ctrl+X, Y, Enter)

# Restart container
pct stop 308
sleep 2
pct start 308
```

### Step 4: Configure GPU for vLLM Container (GPU 1)

```bash
# Edit vLLM container config
nano /etc/pve/lxc/309.conf

# Add these lines at the end (note: nvidia1 instead of nvidia0):
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.mount.entry: /dev/nvidia1 dev/nvidia1 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file

# Save and exit (Ctrl+X, Y, Enter)

# Restart container
pct stop 309
sleep 2
pct start 309
```

### Step 5: Verify GPU Passthrough

```bash
# Install NVIDIA drivers in Ollama container
ssh root@10.96.201.208

# Update and install dependencies
apt update
apt install -y wget software-properties-common

# Install NVIDIA driver and CUDA toolkit
wget https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
apt update
apt install -y cuda-toolkit-12-1 nvidia-driver-535

# Verify GPU is visible
nvidia-smi

# Expected output should show GPU 0
exit
```

```bash
# Install NVIDIA drivers in vLLM container
ssh root@10.96.201.209

# Same installation steps
apt update
apt install -y wget software-properties-common
wget https://developer.download.nvidia.com/compute/cuda/repos/debian12/x86_64/cuda-keyring_1.1-1_all.deb
dpkg -i cuda-keyring_1.1-1_all.deb
apt update
apt install -y cuda-toolkit-12-1 nvidia-driver-535

# Verify GPU is visible
nvidia-smi

# Expected output should show GPU 1
exit
```

### Step 6: Create GPU Setup Script (Automation for Future)

```bash
# On Proxmox host, create helper script
cat > /root/busibox/provision/pct/configure-gpu-passthrough.sh << 'EOF'
#!/usr/bin/env bash
# Configure GPU passthrough for LXC containers
set -euo pipefail

OLLAMA_CTID="${1:-308}"  # Default to test container
VLLM_CTID="${2:-309}"

echo "==> Configuring GPU passthrough"
echo "    Ollama container: ${OLLAMA_CTID} (GPU 0)"
echo "    vLLM container: ${VLLM_CTID} (GPU 1)"

# Verify containers exist
if ! pct status "${OLLAMA_CTID}" &>/dev/null; then
    echo "ERROR: Ollama container ${OLLAMA_CTID} not found"
    exit 1
fi

if ! pct status "${VLLM_CTID}" &>/dev/null; then
    echo "ERROR: vLLM container ${VLLM_CTID} not found"
    exit 1
fi

# Configure Ollama container (GPU 0)
echo "==> Configuring Ollama container..."
cat >> "/etc/pve/lxc/${OLLAMA_CTID}.conf" << 'OLLAMA_EOF'
# GPU Passthrough: NVIDIA GPU 0
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
OLLAMA_EOF

# Configure vLLM container (GPU 1)
echo "==> Configuring vLLM container..."
cat >> "/etc/pve/lxc/${VLLM_CTID}.conf" << 'VLLM_EOF'
# GPU Passthrough: NVIDIA GPU 1
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.mount.entry: /dev/nvidia1 dev/nvidia1 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
VLLM_EOF

# Restart containers
echo "==> Restarting containers..."
pct stop "${OLLAMA_CTID}"
pct stop "${VLLM_CTID}"
sleep 2
pct start "${OLLAMA_CTID}"
pct start "${VLLM_CTID}"
sleep 3

echo "==> GPU passthrough configured successfully!"
echo "    Verify with: ssh root@<container-ip> nvidia-smi"
EOF

chmod +x /root/busibox/provision/pct/configure-gpu-passthrough.sh

# Run the script
bash /root/busibox/provision/pct/configure-gpu-passthrough.sh 308 309
```

---

## Phase 3: Ansible Deployment (T005-T016)

### Prerequisites Check

```bash
# On your workstation (where Ansible runs)
cd /path/to/busibox/provision/ansible

# Test Ansible connectivity
ansible -i inventory/test -m ping llm_services

# Expected output:
# 10.96.201.207 | SUCCESS => { "ping": "pong" }
# 10.96.201.208 | SUCCESS => { "ping": "pong" }
# 10.96.201.209 | SUCCESS => { "ping": "pong" }
```

### Deploy Ollama (T005-T008)

```bash
# Deploy Ollama role
ansible-playbook -i inventory/test \
  --limit ollama \
  --tags ollama \
  site.yml

# Verify Ollama is running
ssh root@10.96.201.208 "systemctl status ollama"

# Test Ollama API
curl http://10.96.201.208:11434/api/tags
```

### Deploy vLLM (T009-T012)

```bash
# Deploy vLLM role
ansible-playbook -i inventory/test \
  --limit vllm \
  --tags vllm \
  site.yml

# Verify vLLM is running
ssh root@10.96.201.209 "systemctl status vllm"

# Test vLLM API
curl http://10.96.201.209:8000/health
```

### Deploy LiteLLM (T013-T016)

```bash
# Deploy LiteLLM role
ansible-playbook -i inventory/test \
  --limit litellm \
  --tags litellm \
  site.yml

# Verify LiteLLM is running
ssh root@10.96.201.207 "systemctl status litellm"

# Test LiteLLM API
curl http://10.96.201.207:4000/health

# Check API docs
curl http://10.96.201.207:4000/docs
```

---

## Phase 4: Integration Testing (T017-T020)

### End-to-End API Test

```bash
# Test via LiteLLM proxy to Ollama
curl -X POST http://10.96.201.207:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -d '{
    "model": "llama3-8b",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 50
  }'

# Test via LiteLLM proxy to vLLM
curl -X POST http://10.96.201.207:4000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $LITELLM_KEY" \
  -d '{
    "model": "llama3-70b",
    "messages": [{"role": "user", "content": "Explain quantum computing"}],
    "max_tokens": 100
  }'
```

### Performance Benchmarking

```bash
# Install benchmark tools
apt install -y apache2-utils

# Benchmark Ollama (via LiteLLM)
ab -n 100 -c 10 http://10.96.201.207:4000/health

# Monitor GPU usage during load
watch -n 1 nvidia-smi
```

---

## Production Deployment

### Create Production Containers

```bash
# On Proxmox host
cd /root/busibox/provision/pct
source vars.env

# Create production containers
bash create_lxc_base.sh production

# Configure GPU passthrough (production container IDs: 207, 208, 209)
bash configure-gpu-passthrough.sh 208 209
```

### Deploy to Production

```bash
# On workstation
cd /path/to/busibox/provision/ansible

# Deploy all LLM services to production
ansible-playbook -i inventory/production \
  --limit llm_services \
  site.yml
```

---

## Troubleshooting

### GPU Not Visible in Container

```bash
# Check container config
cat /etc/pve/lxc/308.conf | grep nvidia

# Check host GPU
nvidia-smi

# Check device permissions
ls -la /dev/nvidia*

# Restart container
pct stop 308 && pct start 308
```

### Ollama Service Won't Start

```bash
# Check logs
ssh root@10.96.201.208
journalctl -u ollama -f

# Check GPU availability
nvidia-smi

# Verify Ollama binary
which ollama
ollama --version
```

### vLLM Out of Memory

```bash
# Check GPU memory
nvidia-smi

# Reduce model size or use tensor parallelism
# Edit vLLM service configuration
systemctl edit vllm

# Add:
Environment="VLLM_TENSOR_PARALLEL_SIZE=2"
```

### LiteLLM Cannot Connect to Backends

```bash
# Test direct connectivity
curl http://10.96.201.208:11434/api/tags
curl http://10.96.201.209:8000/health

# Check LiteLLM config
cat /etc/litellm/config.yaml

# Check LiteLLM logs
journalctl -u litellm -f
```

---

## Next Steps

After successful deployment:

1. ✅ Update agent-server to use LiteLLM endpoint
2. ✅ Set up monitoring and alerting
3. ✅ Configure model rotation schedules
4. ✅ Implement cost tracking
5. ✅ Set up automated backups

---

## References

- [Proxmox GPU Passthrough](https://pve.proxmox.com/wiki/PCI_Passthrough)
- [Ollama Documentation](https://github.com/ollama/ollama/blob/main/docs/linux.md)
- [vLLM Documentation](https://docs.vllm.ai/en/latest/)
- [LiteLLM Documentation](https://docs.litellm.ai/docs/)

