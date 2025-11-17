# PCT Directory Structure Guide

## Quick Reference

```
provision/pct/
├── 📁 containers/       # Create LXC containers
├── 📁 host/            # Configure Proxmox host  
├── 📁 diagnostic/      # Test and debug
└── 📁 lib/             # Shared functions
```

## Directory Purposes

### `containers/` - Container Creation
**What**: Scripts that create and manage LXC containers  
**When**: After host is configured  
**Example**:
```bash
bash provision/pct/containers/create_lxc_base.sh production
bash provision/pct/containers/create-vllm.sh test
```

**Scripts**:
- `create_lxc_base.sh` - Main orchestrator (creates all containers)
- `create-core-services.sh` - proxy, apps, agent
- `create-data-services.sh` - postgres, milvus, minio
- `create-worker-services.sh` - ingest, litellm
- `create-vllm.sh` - vLLM with all GPUs
- `create-ollama.sh` - Ollama (optional, single GPU)

---

### `host/` - Host Configuration
**What**: Scripts that configure the Proxmox host itself  
**When**: Run ONCE before creating containers  
**Example**:
```bash
bash provision/pct/host/setup-proxmox-host.sh
bash provision/pct/host/configure-gpu-passthrough.sh 208
```

**Scripts**:
- `setup-proxmox-host.sh` - Complete host setup (START HERE)
- `install-nvidia-drivers.sh` - Install NVIDIA drivers
- `setup-zfs-storage.sh` - Configure ZFS storage
- `setup-llm-models.sh` - Download LLM models
- `add-data-mounts.sh` - Add storage mounts to containers
- `configure-gpu-passthrough.sh` - Add GPU access to containers

---

### `diagnostic/` - Testing & Debugging
**What**: Scripts for checking status and troubleshooting  
**When**: Anytime - for testing and debugging  
**Example**:
```bash
bash provision/pct/diagnostic/check-gpu-usage.sh
bash provision/pct/diagnostic/check-storage.sh
```

**Scripts**:
- `check-gpu-usage.sh` - Monitor GPU usage in containers
- `check-storage.sh` - Verify storage configuration
- `test-vllm-on-host.sh` - Test vLLM before container deployment
- `list-templates.sh` - List available LXC templates
- `destroy_test.sh` - Remove all test containers

---

### `lib/` - Shared Functions
**What**: Common functions used by other scripts  
**When**: Automatically loaded by other scripts (don't run directly)  
**Example**:
```bash
source "$(dirname "$0")/lib/functions.sh"
create_ct "$CTID" "$IP" "$NAME" unpriv
```

**Files**:
- `functions.sh` - Container management functions
  - `create_ct()` - Create container
  - `add_data_mount()` - Add storage mount
  - `add_gpu_passthrough()` - Single GPU
  - `add_all_gpus()` - All GPUs
  - `validate_env()` - Check configuration

---

## Decision Tree: Which Script Do I Need?

```
What do you want to do?

├─ Setup Proxmox host
│  └→ provision/pct/host/setup-proxmox-host.sh
│
├─ Create ALL containers
│  └→ provision/pct/containers/create_lxc_base.sh
│
├─ Create SINGLE container/service group
│  ├─ vLLM → provision/pct/containers/create-vllm.sh
│  ├─ Data services → provision/pct/containers/create-data-services.sh
│  └─ Others → provision/pct/containers/create-*.sh
│
├─ Check/debug something
│  ├─ GPU usage → provision/pct/diagnostic/check-gpu-usage.sh
│  ├─ Storage → provision/pct/diagnostic/check-storage.sh
│  └─ Templates → provision/pct/diagnostic/list-templates.sh
│
├─ Configure host feature
│  ├─ Install drivers → provision/pct/host/install-nvidia-drivers.sh
│  ├─ Add GPU to container → provision/pct/host/configure-gpu-passthrough.sh
│  └─ Add storage mount → provision/pct/host/add-data-mounts.sh
│
└─ Not sure / First time
   └→ provision/setup.sh (interactive guide)
```

## Typical Workflows

### First Time Setup
```bash
# Easiest - interactive guide
bash provision/setup.sh

# Or manual
bash provision/pct/host/setup-proxmox-host.sh
bash provision/pct/containers/create_lxc_base.sh production
cd provision/ansible && make production
```

### Recreate Single Service
```bash
# Example: Recreate vLLM
pct stop 208
pct destroy 208 --purge
bash provision/pct/containers/create-vllm.sh production
```

### Troubleshooting
```bash
# Check GPU
bash provision/pct/diagnostic/check-gpu-usage.sh

# Check storage
bash provision/pct/diagnostic/check-storage.sh

# Check container
pct status 208
pct config 208
```

### Add Feature to Existing Container
```bash
# Add GPU passthrough
bash provision/pct/host/configure-gpu-passthrough.sh 208 0

# Add data mount
bash provision/pct/host/add-data-mounts.sh 208 /host/path /container/path
```

## Configuration Files

Located in root of `provision/pct/`:

- `vars.env` - Production configuration
- `test-vars.env` - Test configuration  
- `README.md` - Complete documentation
- `REFACTORING-SUMMARY.md` - Refactoring notes

## Quick Commands

```bash
# Interactive setup (easiest)
bash provision/setup.sh

# Create all containers
bash provision/pct/containers/create_lxc_base.sh production

# Create with Ollama
bash provision/pct/containers/create_lxc_base.sh production --with-ollama

# Check GPU usage
bash provision/pct/diagnostic/check-gpu-usage.sh

# Destroy test containers
bash provision/pct/diagnostic/destroy_test.sh

# Setup host
bash provision/pct/host/setup-proxmox-host.sh
```

## Path Migration

If you have old scripts/commands, update them:

```bash
# Container creation
provision/pct/create_lxc_base.sh
→ provision/pct/containers/create_lxc_base.sh

# Host setup
provision/pct/setup-proxmox-host.sh  
→ provision/pct/host/setup-proxmox-host.sh

# Diagnostics
provision/pct/check-gpu-usage.sh
→ provision/pct/diagnostic/check-gpu-usage.sh
```

## Getting Help

1. **Start Here**: `provision/pct/README.md`
2. **Interactive Guide**: `bash provision/setup.sh`
3. **Quick Reference**: This file
4. **Detailed Docs**: `docs/` directory

