---
title: "GPU Passthrough Configuration"
category: "developer"
order: 134
description: "NVIDIA GPU passthrough for LXC containers on Proxmox"
published: true
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
# Configure single GPU for container
bash provision/pct/configure-gpu-passthrough.sh 208 0

# Configure multiple GPUs for container (comma-separated)
bash provision/pct/configure-gpu-passthrough.sh 209 0,1,2

# Configure GPU range for container
bash provision/pct/configure-gpu-passthrough.sh 100 0-3  # GPUs 0, 1, 2, and 3
```

### Advanced Usage

```bash
# Force reconfiguration (removes old GPU config first)
bash provision/pct/configure-gpu-passthrough.sh 208 0,1 --force

# Share single GPU with multiple containers
bash provision/pct/configure-gpu-passthrough.sh 208 0  # Ollama
bash provision/pct/configure-gpu-passthrough.sh 210 0  # Another service

# Configure different GPU combinations
bash provision/pct/configure-gpu-passthrough.sh 208 0,1    # GPUs 0 and 1
bash provision/pct/configure-gpu-passthrough.sh 209 2,3    # GPUs 2 and 3
bash provision/pct/configure-gpu-passthrough.sh 210 0-3    # All 4 GPUs
```

### What the Script Does

1. **Validates** container and GPU exist
2. **Backs up** container configuration
3. **Adds** GPU device passthrough configuration to `/etc/pve/lxc/<ctid>.conf`
4. **Restarts** container (if `--force` flag used)
5. **Verifies** GPU devices are visible in container

### Configuration Added

The script adds these lines to the container config:

**For single GPU (e.g., GPU 0):**
```conf
# GPU Passthrough: NVIDIA GPUs 0
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

**For multiple GPUs (e.g., GPUs 0, 1, 2):**
```conf
# GPU Passthrough: NVIDIA GPUs 0 1 2
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidia1 dev/nvidia1 none bind,optional,create=file
lxc.mount.entry: /dev/nvidia2 dev/nvidia2 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-modeset dev/nvidia-modeset none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
```

## Post-Configuration Steps

### 1. Install NVIDIA Drivers in Container

After GPU passthrough is configured, install NVIDIA **proprietary** drivers inside the container. The driver version **MUST exactly match** the host version to avoid "Driver/library version mismatch" errors.

**⚠️ CRITICAL**: Do NOT use Debian packages (`nvidia-driver-535`) - they will cause version mismatch errors! Use the proprietary NVIDIA installer instead.

#### Automated Installation (Recommended)

```bash
# From Proxmox host - auto-detects host driver version
bash provision/pct/install-nvidia-drivers.sh 208

# Or specify driver version explicitly
bash provision/pct/install-nvidia-drivers.sh 208 580.82
```

This script automatically installs:
- ✅ NVIDIA proprietary driver (matching host version)
- ✅ `nvtop` for interactive GPU monitoring
- ✅ Required dependencies (libglvnd, etc.)

#### Manual Installation

```bash
# 1. Check host driver version
nvidia-smi | grep "Driver Version"
# Example output: Driver Version: 580.82

# 2. Enter the container
pct enter 208

# 3. Install prerequisites
apt update
apt install -y wget kmod libglvnd0 pkg-config libglvnd-dev

# 4. Download matching driver version
DRIVER_VERSION="580.82"  # Use your host version!
cd /tmp
wget https://us.download.nvidia.com/XFree86/Linux-x86_64/${DRIVER_VERSION}/NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run
chmod +x NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run

# 5. Install driver (NO kernel module - container uses host kernel)
./NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run --no-kernel-module --silent

# 6. Verify
nvidia-smi
```

### 2. Verify GPU Access

Inside the container, verify the GPUs are visible:

```bash
# Check GPU devices
ls -la /dev/nvidia*

# For single GPU, should show:
# /dev/nvidia0
# /dev/nvidiactl
# /dev/nvidia-modeset
# /dev/nvidia-uvm
# /dev/nvidia-uvm-tools

# For multiple GPUs (e.g., 0,1,2), should show:
# /dev/nvidia0
# /dev/nvidia1
# /dev/nvidia2
# /dev/nvidiactl
# ... (common devices)

# Check GPU info and count
nvidia-smi

# Should show all configured GPUs with details and memory

# Verify GPU count
nvidia-smi --list-gpus
```

### 3. Monitor GPU Usage

The installation script automatically installs `nvtop` for interactive GPU monitoring:

```bash
# Enter container for interactive monitoring
pct enter 208

# Launch nvtop (like htop but for GPUs)
nvtop

# Shows:
# - GPU utilization per GPU
# - Memory usage
# - Running processes
# - Temperature
# - Power consumption
```

Alternatively, use `nvidia-smi` for quick checks:

```bash
# One-time check
pct exec 208 -- nvidia-smi

# Watch mode (updates every 1 second)
pct exec 208 -- watch -n 1 nvidia-smi
```

### 4. Test CUDA (Optional)

```bash
# Install CUDA toolkit if not already installed
apt install -y nvidia-cuda-toolkit

# Test CUDA availability
python3 -c "import torch; print(torch.cuda.is_available())"
# Should output: True

# Check GPU count in PyTorch
python3 -c "import torch; print(f'GPU count: {torch.cuda.device_count()}')"
# Should output: GPU count: 3 (if you configured 3 GPUs)

# List all GPUs
python3 -c "import torch; [print(f'GPU {i}: {torch.cuda.get_device_name(i)}') for i in range(torch.cuda.device_count())]"
```

## Common Scenarios

### Scenario 1: Two LLM Containers, Dedicated GPUs

```bash
# Ollama gets GPU 0
bash provision/pct/configure-gpu-passthrough.sh 208 0

# vLLM gets GPU 1
bash provision/pct/configure-gpu-passthrough.sh 209 1
```

### Scenario 2: One Container with Multiple GPUs

```bash
# vLLM gets all 4 GPUs for distributed inference
bash provision/pct/configure-gpu-passthrough.sh 209 0,1,2,3

# Or use range notation
bash provision/pct/configure-gpu-passthrough.sh 209 0-3
```

### Scenario 3: Multiple Containers Share Single GPU

```bash
# Multiple services share GPU 0 (good for light workloads)
bash provision/pct/configure-gpu-passthrough.sh 208 0  # Ollama
bash provision/pct/configure-gpu-passthrough.sh 210 0  # liteLLM
bash provision/pct/configure-gpu-passthrough.sh 211 0  # Custom service
```

### Scenario 4: Split GPUs Between Containers

```bash
# Container 208 gets GPUs 0 and 1
bash provision/pct/configure-gpu-passthrough.sh 208 0,1

# Container 209 gets GPUs 2 and 3
bash provision/pct/configure-gpu-passthrough.sh 209 2,3
```

### Scenario 5: Reconfigure GPU Assignment

```bash
# Change from single GPU to multiple GPUs
bash provision/pct/configure-gpu-passthrough.sh 208 0,1,2 --force

# Move container to different GPUs
bash provision/pct/configure-gpu-passthrough.sh 208 2-3 --force
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

**Problem**: "Failed to initialize NVML: Driver/library version mismatch"

**Cause**: LXC containers share the host kernel, so driver versions must match exactly. Using Debian packages (`apt install nvidia-driver-535`) installs a different version than the host.

**Solution**: Install the proprietary NVIDIA driver that matches the host version:

```bash
# Method 1: Automated (recommended)
bash provision/pct/install-nvidia-drivers.sh <container-id>

# Method 2: Manual
# Check host version
nvidia-smi | grep "Driver Version"
# Example: Driver Version: 580.82

# Install matching proprietary driver in container
pct enter <container-id>
cd /tmp
DRIVER_VERSION="580.82"  # Use YOUR host version
wget https://us.download.nvidia.com/XFree86/Linux-x86_64/${DRIVER_VERSION}/NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run
chmod +x NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run
./NVIDIA-Linux-x86_64-${DRIVER_VERSION}.run --no-kernel-module --silent

# Verify versions match
nvidia-smi | grep "Driver Version"  # Should match host
```

**Key Points**:
- ❌ Don't use: `apt install nvidia-driver-*` (wrong version)
- ✅ Do use: Proprietary NVIDIA `.run` installer with `--no-kernel-module`
- 🔑 Critical flag: `--no-kernel-module` (container uses host kernel)

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
- [ ] NVIDIA drivers installed: `pct exec <container-id> -- nvidia-smi`
- [ ] Driver versions match (host vs container): `nvidia-smi | grep "Driver Version"`
- [ ] `nvtop` works for monitoring: `pct exec <container-id> -- nvtop --version`
- [ ] CUDA available (if needed): `pct exec <container-id> -- python3 -c "import torch; print(torch.cuda.is_available())"`
- [ ] Application can use GPU (test your specific workload)

## Best Practices

1. **Match Driver Versions**: Container driver MUST exactly match host driver version
   - Use proprietary NVIDIA installer, not Debian packages
   - Use `install-nvidia-drivers.sh` script for automated installation
   
2. **Backup Configs**: Script automatically creates backups before changes

3. **Test After Changes**: Always verify GPU access after configuration
   - `pct exec <id> -- nvidia-smi` should show GPUs
   - Driver versions should match: host vs container
   
4. **Monitor GPU Usage**: Use `nvtop` or `nvidia-smi` to monitor GPU utilization
   - `nvtop` - Interactive, user-friendly GPU monitor (installed automatically)
   - `watch -n 1 nvidia-smi` - Command-line monitoring
   - Check VRAM usage when sharing GPUs
   
5. **Share Carefully**: Multiple containers can share a GPU, but consider VRAM limits
   - Each container sees the full GPU but shares compute/memory
   - Monitor for OOM errors when sharing

## Reference

### Scripts
- GPU passthrough: `provision/pct/configure-gpu-passthrough.sh`
- Driver installation: `provision/pct/install-nvidia-drivers.sh`

### Configuration Files
- Container configs: `/etc/pve/lxc/<container-id>.conf`
- Driver install log: `/var/log/nvidia-installer.log` (in container)

### Device Files
- Host GPU devices: `/dev/nvidia*`
- Container GPU devices: `/dev/nvidia*` (bind-mounted from host)

### External Links
- NVIDIA driver downloads: https://www.nvidia.com/Download/index.aspx
- NVIDIA driver docs: https://docs.nvidia.com/datacenter/tesla/tesla-installation-notes/
- NVIDIA container toolkit: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/

## Related Documentation

- [02-install](../../administrators/02-install.md) - Installation
- [05-ai-models](../../administrators/05-ai-models.md) - LLM infrastructure
- [08-troubleshooting](../../administrators/08-troubleshooting.md) - Troubleshooting


