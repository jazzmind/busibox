# Proxmox Container (pct) Scripts

This directory contains scripts for creating and managing LXC containers on Proxmox VE for the Busibox infrastructure.

## Directory Structure

```
provision/pct/
├── README.md                      # This file
├── REFACTORING-SUMMARY.md         # Quick refactoring reference
├── vars.env                       # Production configuration
├── test-vars.env                  # Test configuration
├── lib/
│   └── functions.sh              # Shared functions for container management
├── containers/
│   ├── create_lxc_base.sh        # Main orchestrator script
│   ├── create-core-services.sh   # Creates proxy, apps, agent
│   ├── create-data-services.sh   # Creates postgres, milvus, minio
│   ├── create-worker-services.sh # Creates ingest, litellm
│   ├── create-vllm.sh            # Creates vLLM container (all GPUs)
│   └── create-ollama.sh          # Creates Ollama container (optional, single GPU)
├── host/
│   ├── setup-proxmox-host.sh     # Initial host setup
│   ├── setup-llm-models.sh       # Download LLM models
│   ├── setup-zfs-storage.sh      # Configure ZFS storage
│   ├── add-data-mounts.sh        # Utility for adding data mounts
│   ├── configure-gpu-passthrough.sh # Utility for GPU configuration
│   └── install-nvidia-drivers.sh # NVIDIA driver installation
└── diagnostic/
    ├── check-gpu-usage.sh        # Monitor GPU usage
    ├── check-storage.sh          # Verify storage configuration
    ├── test-vllm-on-host.sh      # Test vLLM before deployment
    ├── list-templates.sh         # List available LXC templates
    └── destroy_test.sh           # Remove all test containers
```

## Quick Start

### 0. Interactive Setup (Recommended)

**New users should start here:**

```bash
bash provision/setup.sh
```

This interactive script guides you through:
1. Host configuration (drivers, storage, templates)
2. Container creation (with environment and option selection)
3. Ansible configuration (with tag selection)

### 1. Initial Host Setup

Or run host setup manually:

```bash
bash provision/pct/host/setup-proxmox-host.sh
```

This sets up:
- NVIDIA drivers (if GPU present)
- Persistent storage directories
- SSH keys
- Network configuration

### 2. Create All Containers

**Production (without Ollama):**
```bash
bash provision/pct/containers/create_lxc_base.sh production
```

**Test Environment (without Ollama):**
```bash
bash provision/pct/containers/create_lxc_base.sh test
```

**With Optional Ollama Container:**
```bash
bash provision/pct/containers/create_lxc_base.sh production --with-ollama
bash provision/pct/containers/create_lxc_base.sh test --with-ollama
```

### 3. Create Individual Containers

Each container can be created independently for debugging or updates:

```bash
# Core services
bash provision/pct/containers/create-core-services.sh [test|production]

# Data services
bash provision/pct/containers/create-data-services.sh [test|production]

# Worker services
bash provision/pct/containers/create-worker-services.sh [test|production]

# vLLM (all GPUs)
bash provision/pct/containers/create-vllm.sh [test|production]

# Ollama (optional, single GPU)
bash provision/pct/containers/create-ollama.sh [test|production] [GPU_NUM]
```

## Container IDs and IPs

### Production

| Service  | Container ID | IP Address      | Notes                    |
|----------|--------------|-----------------|--------------------------|
| proxy    | 200          | 10.96.200.200   | nginx reverse proxy      |
| apps     | 201          | 10.96.200.201   | Next.js applications     |
| agent    | 202          | 10.96.200.202   | Agent API server         |
| postgres | 203          | 10.96.200.203   | PostgreSQL database      |
| milvus   | 204          | 10.96.200.204   | Vector database          |
| minio    | 205          | 10.96.200.205   | S3-compatible storage    |
| ingest   | 206          | 10.96.200.206   | Document worker + Redis  |
| litellm  | 207          | 10.96.200.30    | LLM API gateway          |
| vllm     | 208          | 10.96.200.31    | vLLM inference (all GPUs)|
| ollama   | 210          | 10.96.200.32    | Ollama (optional, GPU 0) |

### Test

Test containers have IDs offset by +100 and use TEST- prefix:
- Container IDs: 300-310
- IP Range: 10.96.201.200-210
- Names: TEST-{service}-lxc

## GPU Configuration

### vLLM Container (208/308)
- **Receives ALL available GPUs** for maximum inference performance
- Automatically detects and configures all NVIDIA GPUs on host
- Requires 40GB disk space for models

### Ollama Container (210/310) - Optional
- **Receives single GPU** (GPU 0 by default)
- Not created by default - use `--with-ollama` flag
- Can specify different GPU: `create-ollama.sh production 1`

## Common Functions (lib/functions.sh)

The shared library provides these functions:

- `create_ct()` - Create and start an LXC container
- `add_data_mount()` - Add persistent storage bind mount
- `add_gpu_passthrough()` - Configure single GPU passthrough
- `add_all_gpus()` - Configure all GPUs for passthrough
- `validate_env()` - Validate required environment variables

Usage in your scripts:
```bash
source "$(dirname "$0")/lib/functions.sh"
validate_env || exit 1
create_ct "$CTID" "$IP" "$NAME" unpriv
```

## Configuration Files

### vars.env (Production)
Default production configuration. Edit these values for your environment:
- Network settings (BRIDGE, CIDR, GW)
- Container IDs and IPs
- Storage paths
- Resource limits (MEM_MB, CPUS, DISK_GB)

### test-vars.env (Test)
Test environment with IDs offset by +100 and TEST- prefix. Automatically loaded when using `test` mode.

## Utility Scripts

### Setup Scripts (host/)
- `setup-proxmox-host.sh` - Initial Proxmox host configuration
- `setup-llm-models.sh` - Download and configure LLM models
- `setup-zfs-storage.sh` - Configure ZFS storage pool
- `install-nvidia-drivers.sh` - Install NVIDIA drivers
- `add-data-mounts.sh` - Add persistent storage to containers
- `configure-gpu-passthrough.sh` - Add GPU access to containers

### Diagnostic Scripts (diagnostic/)
- `check-storage.sh` - Verify storage configuration
- `check-gpu-usage.sh` - Monitor GPU usage across containers
- `list-templates.sh` - List available LXC templates
- `test-vllm-on-host.sh` - Test vLLM on Proxmox host before container deployment
- `destroy_test.sh` - Remove all test containers

## Examples

### Interactive Setup (Easiest)
```bash
# Guided setup with prompts
bash provision/setup.sh
```

### Create Production Infrastructure
```bash
# Full production deployment
bash provision/pct/host/setup-proxmox-host.sh
bash provision/pct/containers/create_lxc_base.sh production

# With Ollama
bash provision/pct/containers/create_lxc_base.sh production --with-ollama
```

### Create Test Environment
```bash
# Test environment with all services
bash provision/pct/containers/create_lxc_base.sh test --with-ollama
```

### Recreate Single Container
```bash
# Destroy and recreate just vLLM
pct stop 208
pct destroy 208 --purge
bash provision/pct/containers/create-vllm.sh production
```

### Debug Individual Service
```bash
# Create just data services for testing
bash provision/pct/containers/create-data-services.sh test
```

## Troubleshooting

### Container Creation Fails
```bash
# Check storage
bash provision/pct/diagnostic/check-storage.sh

# Verify host setup
bash provision/pct/host/setup-proxmox-host.sh

# Check if container already exists
pct status <CTID>
```

### GPU Not Available
```bash
# Check GPU status
nvidia-smi

# Verify drivers
bash provision/pct/host/install-nvidia-drivers.sh

# Check GPU usage in containers
bash provision/pct/diagnostic/check-gpu-usage.sh
```

### Data Mount Issues
```bash
# Verify host paths exist
ls -la /var/lib/data/

# Check container configuration
pct config <CTID> | grep mp

# Manually add mount
bash provision/pct/host/add-data-mounts.sh <CTID>
```

## Architecture Notes

### Container Privilege Levels
- **Unprivileged** (default): proxy, apps, agent, postgres, ingest, litellm
  - Better security isolation
  - Suitable for most services
  
- **Privileged**: milvus, minio, ollama, vllm
  - Required for GPU access (ollama, vllm)
  - Better performance for storage services (milvus, minio)

### Network Design
- All containers on same bridge (vmbr0)
- Private subnet: 10.96.200.0/21
- Gateway: 10.96.200.1
- Test subnet: 10.96.201.0/24

### Storage Strategy
- Container rootfs: ZFS storage pool (configurable)
- Persistent data: Bind mounts from /var/lib/data/
- LLM models: Bind mounts from /var/lib/llm-models/

## Next Steps

After creating containers, configure them with Ansible:

```bash
cd provision/ansible

# Configure test environment
make test

# Configure production
make production
```

Then test the infrastructure:

```bash
bash scripts/test-infrastructure.sh
```

## References

- Main documentation: `docs/architecture/architecture.md`
- Deployment guide: `docs/deployment/`
- Troubleshooting: `docs/troubleshooting/`

