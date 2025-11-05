# PCT Scripts Refactoring Summary

## Quick Overview

The `provision/pct/` directory has been refactored for better maintainability and usability.

## Key Changes

### 1. Container IDs Updated
- **vLLM**: 209 → **208** (now primary LLM service)
- **Ollama**: 208 → **210** (now optional)

### 2. Ollama is Optional
```bash
# Without Ollama (default)
bash create_lxc_base.sh production

# With Ollama
bash create_lxc_base.sh production --with-ollama
```

### 3. vLLM Gets ALL GPUs
- **Before**: vLLM got 1 GPU
- **After**: vLLM gets ALL available GPUs automatically

### 4. Modular Architecture

#### Before (Monolithic)
```
provision/pct/
├── create_lxc_base.sh        # 279 lines, all logic inline
├── vars.env
├── test-vars.env
└── [utility scripts...]
```

**Problems**:
- Hard to debug individual containers
- Can't reuse logic
- Difficult to understand
- No way to create just one container

#### After (Modular)
```
provision/pct/
├── lib/
│   └── functions.sh              # Shared functions
├── containers/
│   ├── create-core-services.sh   # proxy, apps, agent
│   ├── create-data-services.sh   # postgres, milvus, minio
│   ├── create-worker-services.sh # ingest, litellm
│   ├── create-vllm.sh            # vLLM with all GPUs
│   └── create-ollama.sh          # Ollama (optional)
├── create_lxc_base.sh            # 180 lines, calls support scripts
├── vars.env
├── test-vars.env
├── README.md                     # New comprehensive docs
└── [utility scripts...]
```

**Benefits**:
- ✅ Debug individual containers
- ✅ Reusable functions
- ✅ Clear organization
- ✅ Independent script execution
- ✅ Better documentation

## Usage Examples

### Create All Containers
```bash
# Production (no Ollama)
bash provision/pct/create_lxc_base.sh production

# Test with Ollama
bash provision/pct/create_lxc_base.sh test --with-ollama
```

### Create Individual Services
```bash
# Just vLLM
bash provision/pct/containers/create-vllm.sh production

# Just data services
bash provision/pct/containers/create-data-services.sh test

# Ollama with specific GPU
bash provision/pct/containers/create-ollama.sh production 1
```

### Debug Single Container
```bash
# Recreate just the vLLM container
pct stop 208
pct destroy 208 --purge
bash provision/pct/containers/create-vllm.sh production
```

## Container Reference

| Service    | Prod ID | Test ID | IP Production  | IP Test        | GPUs           | Optional |
|------------|---------|---------|----------------|----------------|----------------|----------|
| proxy      | 200     | 300     | 10.96.200.200  | 10.96.201.200  | None           | No       |
| apps       | 201     | 301     | 10.96.200.201  | 10.96.201.201  | None           | No       |
| agent      | 202     | 302     | 10.96.200.202  | 10.96.201.202  | None           | No       |
| postgres   | 203     | 303     | 10.96.200.203  | 10.96.201.203  | None           | No       |
| milvus     | 204     | 304     | 10.96.200.204  | 10.96.201.204  | None           | No       |
| minio      | 205     | 305     | 10.96.200.205  | 10.96.201.205  | None           | No       |
| ingest     | 206     | 306     | 10.96.200.206  | 10.96.201.206  | None           | No       |
| litellm    | 207     | 307     | 10.96.200.30   | 10.96.201.207  | None           | No       |
| **vllm**   | **208** | **308** | 10.96.200.31   | 10.96.201.208  | **ALL**        | No       |
| **ollama** | **210** | **310** | 10.96.200.32   | 10.96.201.210  | Single (GPU 0) | **Yes**  |

### Changes from Old IDs
- vLLM: ~~209~~ → **208**
- Ollama: ~~208~~ → **210**

## New Shared Functions

From `lib/functions.sh`:

```bash
# Create container
create_ct CTID IP NAME PRIVILEGE [DISK_SIZE]

# Add data mount
add_data_mount CTID HOST_PATH CONTAINER_PATH [MP_NUM]

# Single GPU passthrough
add_gpu_passthrough CTID GPU_NUM

# All GPUs passthrough (NEW!)
add_all_gpus CTID

# Validate environment
validate_env
```

## Migration Notes

### If You Have Existing Containers

The container IDs changed. You have two options:

**Option 1: Keep existing containers** (if they work)
- Update Ansible inventory to match old IDs
- No changes needed to containers

**Option 2: Recreate with new IDs**
```bash
# Destroy old vLLM and Ollama
pct stop 208 209
pct destroy 208 209 --purge

# Create new ones
bash provision/pct/containers/create-vllm.sh production
# Optionally:
bash provision/pct/containers/create-ollama.sh production
```

## Documentation

Full documentation available in:
- `provision/pct/README.md` - Complete guide
- `docs/session-notes/2025-11-05-pct-scripts-refactoring.md` - Detailed changes

## Testing Checklist

- [ ] Test individual container creation scripts
- [ ] Verify GPU passthrough with `check-gpu-usage.sh`
- [ ] Test full infrastructure creation
- [ ] Update Ansible inventory if needed
- [ ] Test with `--with-ollama` flag
- [ ] Verify data mounts
- [ ] Run infrastructure tests

## Quick Reference

```bash
# See all options
bash provision/pct/create_lxc_base.sh

# Individual scripts
ls provision/pct/containers/

# Check GPU status
bash provision/pct/check-gpu-usage.sh

# Check storage
bash provision/pct/check-storage.sh

# Read full docs
cat provision/pct/README.md
```

