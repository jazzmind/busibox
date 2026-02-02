---
created: 2025-02-01
updated: 2025-02-01
status: complete
category: deployment
tags: [proxmox, embedding, caching, models, fastembed]
---

# Embedding Model Caching for Proxmox

## Problem

When running `make install` on Proxmox, the installer showed a warning:

```
[WARNING] Docker not available - embedding model will be downloaded later
```

This caused embedding models to be downloaded on first container start instead of being pre-cached during installation. The issue occurred because:

1. The `start_embedding_download_background()` function in `install.sh` only supported Docker-based caching
2. On Proxmox hosts, Docker is not available (containers use native LXC)
3. Embedding models (~134MB to 1.3GB) would download during service startup, causing delays

## Solution

Implemented a Proxmox-native embedding model caching system that mirrors the existing LLM model caching pattern:

### 1. Host-Based Download Script

**Created**: `provision/pct/host/setup-embedding-models.sh`

- Downloads FastEmbed models (bge-small, bge-base, bge-large) to shared storage
- Uses Python virtual environment with fastembed library
- Stores models in `/var/lib/embedding-models/fastembed` on Proxmox host
- Follows same pattern as `setup-llm-models.sh`
- Supports cleanup mode: `bash setup-embedding-models.sh --cleanup`

### 2. Storage Infrastructure

**Modified**: `provision/pct/host/setup-proxmox-host.sh`

Added ZFS dataset creation:
```bash
zfs create -o mountpoint=/var/lib/embedding-models rpool/embedding-models
zfs create rpool/embedding-models/fastembed
```

For non-ZFS systems, creates regular directory:
```bash
mkdir -p /var/lib/embedding-models/fastembed
```

### 3. Container Mount Configuration

**Modified**: `provision/pct/containers/create-worker-services.sh`

Added bind mount for `data-lxc` container:
```bash
add_data_mount "$CT_DATA" "/var/lib/embedding-models/fastembed" "/var/lib/embedding-models/fastembed" "0"
```

### 4. Ansible Configuration

**Modified Files**:
- `roles/embedding_api/defaults/main.yml`: Added `embedding_cache_dir` variable
- `roles/embedding_api/tasks/main.yml`: Create shared cache directory
- `roles/embedding_api/templates/embedding-api.env.j2`: Set `FASTEMBED_CACHE_PATH`
- `roles/embedding_api/templates/embedding-api.service.j2`: Allow write access to cache

**Cache Location Logic**:
```yaml
embedding_cache_dir: "{{ '/var/lib/embedding-models/fastembed' if ansible_connection != 'docker' else '/home/' + embedding_api_user + '/.cache/fastembed' }}"
```

This ensures:
- Proxmox: Uses shared mounted cache at `/var/lib/embedding-models/fastembed`
- Docker: Uses local cache at `/home/embedding/.cache/fastembed`

### 5. Install Script Integration

**Modified**: `scripts/make/install.sh`

Updated `start_embedding_download_background()` to:
1. Detect Proxmox platform
2. Call `setup-embedding-models.sh` in background
3. Track PID for later synchronization
4. Fall back to Docker-based download for Docker platform

## Benefits

1. **Faster Deployment**: Models downloaded during installation, not on first use
2. **Shared Cache**: Multiple containers can use the same model files
3. **Consistent Pattern**: Mirrors existing LLM model caching architecture
4. **Platform Aware**: Works correctly on both Proxmox and Docker
5. **Background Download**: Models download in parallel with container provisioning

## Model Sizes

- **bge-small-en-v1.5**: ~134MB (development/demo default)
- **bge-base-en-v1.5**: ~438MB
- **bge-large-en-v1.5**: ~1.3GB (production default)

## Usage

### Initial Setup

On Proxmox host:
```bash
# Included in setup-proxmox-host.sh
bash provision/pct/host/setup-proxmox-host.sh
```

### Manual Model Download

On Proxmox host:
```bash
# Download all registry models
bash provision/pct/host/setup-embedding-models.sh

# Remove orphaned models
bash provision/pct/host/setup-embedding-models.sh --cleanup
```

### Automatic During Installation

```bash
# From busibox repo root
make install
```

Models download automatically in background when Proxmox platform is detected.

## Files Modified

```
provision/pct/host/setup-embedding-models.sh        (new)
provision/pct/host/setup-proxmox-host.sh            (modified)
provision/pct/containers/create-worker-services.sh  (modified)
provision/ansible/roles/embedding_api/defaults/main.yml            (modified)
provision/ansible/roles/embedding_api/tasks/main.yml               (modified)
provision/ansible/roles/embedding_api/templates/embedding-api.env.j2    (modified)
provision/ansible/roles/embedding_api/templates/embedding-api.service.j2 (modified)
scripts/make/install.sh                             (modified)
```

## Testing

1. **Fresh Installation**: Run `make install` on Proxmox and verify no Docker warning
2. **Model Verification**: Check `/var/lib/embedding-models/fastembed` for model files
3. **Service Start**: Verify `embedding-api` starts without downloading models
4. **Container Access**: Verify data-lxc can access models at mount point

## Future Enhancements

1. Add progress indicators for large model downloads
2. Implement resume capability for interrupted downloads
3. Add model verification/checksum validation
4. Support selective model download based on environment

## Related Documentation

- `provision/pct/host/setup-llm-models.sh` - LLM model caching pattern
- `docs/guides/model-registry-usage.md` - Model registry configuration
- `provision/ansible/group_vars/all/model_registry.yml` - Model definitions
