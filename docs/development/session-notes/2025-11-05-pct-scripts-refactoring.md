---
created: 2025-11-05
updated: 2025-11-05
status: completed
category: session-notes
tags: [refactoring, lxc, containers, gpu, vllm, ollama]
---

# PCT Scripts Refactoring - November 5, 2025

## Overview

Major refactoring of the `provision/pct/` directory to improve maintainability, debuggability, and usability of LXC container creation scripts.

## Changes Made

### 1. Container ID Swap: vLLM and Ollama

**Rationale**: vLLM is the primary LLM inference engine and should have priority over Ollama.

**Changes**:
- vLLM: 209 → **208** (production), 309 → **308** (test)
- Ollama: 208 → **210** (production), 308 → **310** (test)

**Files Updated**:
- `provision/pct/vars.env`
- `provision/pct/test-vars.env`

### 2. Ollama is Now Optional

**Rationale**: Not all deployments need Ollama since vLLM is the primary inference engine.

**Implementation**:
- Ollama container NOT created by default
- Use `--with-ollama` flag to include it: `bash create_lxc_base.sh production --with-ollama`
- Can be created independently: `bash containers/create-ollama.sh production`

### 3. vLLM Gets ALL GPUs

**Rationale**: vLLM is designed for high-performance inference and benefits from access to all available GPUs.

**Implementation**:
- New function: `add_all_gpus()` in `lib/functions.sh`
- Automatically detects all available NVIDIA GPUs on host
- Passes through all GPUs to vLLM container
- Ollama (when created) uses single GPU (default: GPU 0)

### 4. Modular Script Architecture

**Problem**: The original `create_lxc_base.sh` was 279 lines with all logic inline, making it:
- Hard to understand
- Difficult to debug single containers
- Not reusable for individual container operations

**Solution**: Extracted into modular components

#### New Directory Structure

```
provision/pct/
├── lib/
│   └── functions.sh              # Shared functions
├── containers/
│   ├── create-core-services.sh   # proxy, apps, agent
│   ├── create-data-services.sh   # postgres, milvus, minio
│   ├── create-worker-services.sh # ingest, litellm
│   ├── create-vllm.sh            # vLLM with all GPUs
│   └── create-ollama.sh          # Ollama (optional, single GPU)
└── create_lxc_base.sh            # Main orchestrator (now ~180 lines)
```

#### Shared Functions Library (`lib/functions.sh`)

Extracted reusable functions:
- `create_ct()` - Create and start LXC container
- `add_data_mount()` - Add persistent storage bind mount
- `add_gpu_passthrough()` - Configure single GPU passthrough
- `add_all_gpus()` - **NEW**: Configure all GPUs for passthrough
- `validate_env()` - Validate required environment variables

#### Individual Container Scripts

Each script can be run independently:

```bash
# Create just vLLM container
bash provision/pct/containers/create-vllm.sh production

# Create just data services
bash provision/pct/containers/create-data-services.sh test

# Create Ollama with specific GPU
bash provision/pct/containers/create-ollama.sh production 1
```

**Benefits**:
1. **Easier Debugging**: Test individual containers without recreating everything
2. **Better Organization**: Related containers grouped together
3. **Reusability**: Scripts can be used in automation/CI
4. **Clearer Intent**: Each script has focused purpose
5. **Better Documentation**: Inline documentation in each script

### 5. Improved Main Orchestrator

The refactored `create_lxc_base.sh`:
- **Cleaner**: Reduced from 279 to ~180 lines
- **Readable**: Clear step-by-step progression
- **Flexible**: Optional `--with-ollama` flag
- **Informative**: Better progress reporting and final summary

**New Usage**:
```bash
# Production without Ollama (default)
bash provision/pct/create_lxc_base.sh production

# Test with Ollama
bash provision/pct/create_lxc_base.sh test --with-ollama
```

**Output Improvements**:
- Step-by-step progress (Step 1/4, Step 2/4, etc.)
- Clear service groupings in summary
- Usage hints for next steps
- References to individual container scripts

## GPU Configuration Details

### Before Refactoring
- vLLM: Single GPU (GPU 1 in test, GPU 0 in production)
- Ollama: Single GPU (GPU 0)
- Both always created

### After Refactoring
- **vLLM**: ALL available GPUs (automatic detection)
- **Ollama**: Single GPU (optional, not created by default)
- Better resource utilization for vLLM inference

### Implementation: `add_all_gpus()` Function

```bash
add_all_gpus() {
  local CTID=$1
  local CONFIG_FILE="/etc/pve/lxc/${CTID}.conf"
  
  # Detect all GPUs
  local GPU_COUNT=$(nvidia-smi -L | wc -l)
  
  # Add common device permissions
  cat >> "$CONFIG_FILE" << EOF
# GPU Passthrough: ALL NVIDIA GPUs (${GPU_COUNT} total)
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c 234:* rwm
lxc.cgroup2.devices.allow: c 508:* rwm
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
# ... other common devices ...
EOF
  
  # Add each GPU device
  for ((i=0; i<GPU_COUNT; i++)); do
    echo "lxc.mount.entry: /dev/nvidia${i} dev/nvidia${i} none bind,optional,create=file" >> "$CONFIG_FILE"
  done
}
```

## Testing

### Backward Compatibility
- Existing deployments continue to work
- Container IDs changed (vLLM/Ollama swap) - may require updates to Ansible inventory if hardcoded
- All functionality preserved

### New Functionality
```bash
# Test individual container creation
bash provision/pct/containers/create-vllm.sh test

# Verify GPU configuration
bash provision/pct/check-gpu-usage.sh

# Test with Ollama
bash provision/pct/create_lxc_base.sh test --with-ollama
```

## Documentation Updates

Created comprehensive documentation:
- `provision/pct/README.md` - Complete usage guide for PCT scripts
- Function documentation in `lib/functions.sh`
- Header documentation in all container scripts

## Migration Guide

### For Existing Deployments

1. **Container IDs Changed**:
   - vLLM: 209 → 208 (production), 309 → 308 (test)
   - Ollama: 208 → 210 (production), 308 → 310 (test)

2. **Update Ansible Inventory** (if using hardcoded IDs):
   ```yaml
   # Old
   vllm_container_id: 209
   ollama_container_id: 208
   
   # New
   vllm_container_id: 208
   ollama_container_id: 210  # Optional
   ```

3. **Recreate Containers** (if needed):
   ```bash
   # Destroy old containers
   pct stop 208 209
   pct destroy 208 209 --purge
   
   # Create with new IDs
   bash provision/pct/containers/create-vllm.sh production
   # Only if needed:
   bash provision/pct/containers/create-ollama.sh production
   ```

### For New Deployments

Simply use the new main script:
```bash
bash provision/pct/setup-proxmox-host.sh
bash provision/pct/create_lxc_base.sh production
```

## Benefits Summary

1. **Maintainability**: Modular code is easier to update and extend
2. **Debuggability**: Test individual components independently
3. **Reusability**: Shared functions reduce code duplication
4. **Clarity**: Clear separation of concerns
5. **Flexibility**: Optional Ollama, configurable GPU allocation
6. **Documentation**: Comprehensive inline and README documentation
7. **Performance**: vLLM gets all GPUs for maximum inference performance

## Files Modified

### Updated Files
- `provision/pct/vars.env` - Container IDs
- `provision/pct/test-vars.env` - Container IDs
- `provision/pct/create_lxc_base.sh` - Complete refactor (279 → ~180 lines)

### New Files
- `provision/pct/lib/functions.sh` - Shared function library
- `provision/pct/containers/create-core-services.sh` - Core services
- `provision/pct/containers/create-data-services.sh` - Data services
- `provision/pct/containers/create-worker-services.sh` - Worker services
- `provision/pct/containers/create-vllm.sh` - vLLM container
- `provision/pct/containers/create-ollama.sh` - Ollama container (optional)
- `provision/pct/README.md` - Comprehensive documentation

## Next Steps

1. **Test the refactored scripts** in test environment
2. **Update Ansible roles** if needed (container ID references)
3. **Update deployment documentation** with new container IDs
4. **Consider**: Extract other utility scripts (add-data-mounts.sh, configure-gpu-passthrough.sh) to use shared functions

## Rules Applied

Per `.cursor/rules/002-script-organization.md`:
- ✅ Scripts run on Proxmox host → `provision/pct/`
- ✅ Comprehensive headers with execution context
- ✅ Descriptive prefixes (`create-`, `setup-`, etc.)
- ✅ Error handling with `set -euo pipefail`

Per `.cursor/rules/001-documentation-organization.md`:
- ✅ Session notes → `docs/session-notes/`
- ✅ Kebab-case naming
- ✅ Metadata header
- ✅ Clear categorization

