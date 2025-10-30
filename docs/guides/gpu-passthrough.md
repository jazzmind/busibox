---
title: GPU Passthrough Configuration
created: 2025-10-30
updated: 2025-10-30
status: stable
category: guides
tags: [gpu, lxc, nvidia, proxmox]
---

# GPU Passthrough for LXC Containers

This guide explains how to configure NVIDIA GPU passthrough for LXC containers on Proxmox.

## Overview

GPU passthrough allows LXC containers to directly access NVIDIA GPUs on the Proxmox host. This is essential for:

- Running LLM inference servers (Ollama, vLLM)
- Machine learning workloads
- CUDA-accelerated applications
- GPU-based video processing

## Prerequisites

### 1. NVIDIA Drivers on Proxmox Host

Install NVIDIA drivers on the Proxmox host:

```bash
# Update package list
apt update

# Install NVIDIA drivers
apt install -y nvidia-driver nvidia-smi

# Reboot to load driver
reboot

# After reboot, verify drivers
nvidia-smi
```

### 2. Verify Available GPUs

Check which GPUs are available:

```bash
# List all GPUs
nvidia-smi -L

# Example output:
# GPU 0: NVIDIA GeForce RTX 4090 (UUID: GPU-xxxxx)
# GPU 1: NVIDIA GeForce RTX 4090 (UUID: GPU-xxxxx)
```

### 3. Container Must Exist

The container must be created before configuring GPU passthrough:

```bash
# Check container exists
pct status 208

# Create container if needed
bash provision/pct/create_lxc_base.sh production
```

## Configuration Script

Use the canonical GPU passthrough script: `provision/pct/configure-gpu-passthrough.sh`

### Basic Usage

```bash
# Configure GPU 0 for container 208 (ollama)
bash provision/pct/configure-gpu-passthrough.sh 208 0

# Configure GPU 1 for container 209 (vLLM)
bash provision/pct/configure-gpu-passthrough.sh 209 1

# Configure GPU 0 for any container
bash provision/pct/configure-gpu-passthrough.sh 100 0
```

### Advanced Usage

```bash
# Force reconfiguration (removes old GPU config first)
bash provision/pct/configure-gpu-passthrough.sh 208 0 --force

# Share GPU 0 with multiple containers
bash provision/pct/configure-gpu-passthrough.sh 208 0  # Ollama
bash provision/pct/configure-gpu-passthrough.sh 210 0  # Another service
```

### What the Script Does

1. **Validates** container and GPU exist
2. **Backs up** container configuration
3. **Adds** GPU device passthrough configuration to `/etc/pve/lxc/<ctid>.conf`
4. **Restarts** container (if `--force` flag used)
5. **Verifies** GPU devices are visible in container

### Configuration Added

The script adds these lines to the container config:

```conf
# GPU Passthrough: NVIDIA GPU 0
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
```

## Post-Configuration Steps

### 1. Install NVIDIA Drivers in Container

After GPU passthrough is configured, install NVIDIA drivers **inside the container**:

```bash
# Enter the container
pct enter 208

# Install NVIDIA drivers (match host driver version)
apt update
apt install -y nvidia-driver-535 nvidia-cuda-toolkit

# Verify GPU is accessible
nvidia-smi
```

### 2. Verify GPU Access

Inside the container, verify the GPU is visible:

```bash
# Check GPU devices
ls -la /dev/nvidia*

# Should show:
# /dev/nvidia0
# /dev/nvidiactl
# /dev/nvidia-modeset
# /dev/nvidia-uvm
# /dev/nvidia-uvm-tools

# Check GPU info
nvidia-smi

# Should show GPU details and memory
```

### 3. Test CUDA (Optional)

```bash
# Install CUDA toolkit if not already installed
apt install -y nvidia-cuda-toolkit

# Test CUDA
python3 -c "import torch; print(torch.cuda.is_available())"
# Should output: True
```

## Common Scenarios

### Scenario 1: Two LLM Containers, Dedicated GPUs

```bash
# Ollama gets GPU 0
bash provision/pct/configure-gpu-passthrough.sh 208 0

# vLLM gets GPU 1
bash provision/pct/configure-gpu-passthrough.sh 209 1
```

### Scenario 2: Multiple Containers Share GPU

```bash
# Multiple services share GPU 0
bash provision/pct/configure-gpu-passthrough.sh 208 0  # Ollama
bash provision/pct/configure-gpu-passthrough.sh 210 0  # liteLLM
bash provision/pct/configure-gpu-passthrough.sh 211 0  # Custom service
```

### Scenario 3: Reconfigure GPU Assignment

```bash
# Move container from GPU 0 to GPU 1
bash provision/pct/configure-gpu-passthrough.sh 208 1 --force
```

## Troubleshooting

### GPU Not Visible in Container

**Problem**: `nvidia-smi` not found or "No devices were found"

**Solutions**:

1. **Check host GPU is accessible**:
   ```bash
   # On Proxmox host
   ls -la /dev/nvidia*
   nvidia-smi
   ```

2. **Install NVIDIA drivers in container**:
   ```bash
   pct enter <container-id>
   apt update
   apt install -y nvidia-driver-535
   ```

3. **Verify container config**:
   ```bash
   cat /etc/pve/lxc/<container-id>.conf | grep nvidia
   ```

4. **Restart container**:
   ```bash
   pct stop <container-id>
   pct start <container-id>
   ```

### Container Won't Start After Configuration

**Problem**: Container fails to start after GPU passthrough

**Solutions**:

1. **Check for config errors**:
   ```bash
   cat /etc/pve/lxc/<container-id>.conf
   ```

2. **Restore from backup**:
   ```bash
   # Script creates backups automatically
   ls -la /etc/pve/lxc/<container-id>.conf.backup-*
   
   # Restore backup
   cp /etc/pve/lxc/<container-id>.conf.backup-<timestamp> \
      /etc/pve/lxc/<container-id>.conf
   ```

3. **Try alternative start method**:
   ```bash
   # Use systemctl
   systemctl start pve-container@<container-id>
   
   # Or lxc-start
   lxc-start -n <container-id>
   ```

### Driver Version Mismatch

**Problem**: Host and container have different NVIDIA driver versions

**Solution**: Match container driver to host driver:

```bash
# Check host driver version
nvidia-smi | grep "Driver Version"

# Install matching version in container
pct enter <container-id>
apt install -y nvidia-driver-<version>
```

### Permission Denied for GPU Devices

**Problem**: GPU devices exist but permission denied

**Solutions**:

1. **Check cgroup permissions** in container config:
   ```bash
   grep "lxc.cgroup2.devices.allow" /etc/pve/lxc/<container-id>.conf
   ```

2. **Reconfigure with force**:
   ```bash
   bash provision/pct/configure-gpu-passthrough.sh <container-id> <gpu-num> --force
   ```

## Verification Checklist

After configuration, verify:

- [ ] Container starts successfully: `pct status <container-id>`
- [ ] GPU devices visible: `pct exec <container-id> -- ls -la /dev/nvidia*`
- [ ] NVIDIA drivers installed in container: `pct exec <container-id> -- nvidia-smi`
- [ ] CUDA available (if needed): `pct exec <container-id> -- python3 -c "import torch; print(torch.cuda.is_available())"`
- [ ] Application can use GPU (test your specific workload)

## Best Practices

1. **Match Driver Versions**: Keep host and container NVIDIA drivers synchronized
2. **Backup Configs**: Script automatically creates backups before changes
3. **Test After Changes**: Always verify GPU access after configuration
4. **Monitor GPU Usage**: Use `nvidia-smi` to monitor GPU utilization
5. **Share Carefully**: Multiple containers can share a GPU, but consider VRAM limits

## Reference

- Script location: `provision/pct/configure-gpu-passthrough.sh`
- Container configs: `/etc/pve/lxc/<container-id>.conf`
- Host GPU devices: `/dev/nvidia*`
- NVIDIA driver docs: https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/

## Related Documentation

- [LXC Container Creation](../deployment/lxc-containers.md)
- [LLM Infrastructure Setup](../deployment/llm-infrastructure.md)
- [Troubleshooting Guide](../troubleshooting/common-issues.md)


