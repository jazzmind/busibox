# Embedding Model Caching for Proxmox - Implementation Summary

## Overview

Fixed the embedding model caching issue on Proxmox installations. Previously, embedding models were only cached when Docker was available, causing models to download on first service start in Proxmox environments.

## Changes Made

### 1. New Host-Based Download Script
**File**: `provision/pct/host/setup-embedding-models.sh`

Features:
- Downloads FastEmbed models (bge-small, bge-base, bge-large) to shared storage
- Uses Python virtual environment for fastembed library
- Stores models in `/var/lib/embedding-models/fastembed`
- Supports cleanup mode to remove orphaned models
- Follows same architecture as `setup-llm-models.sh`

### 2. Storage Infrastructure
**File**: `provision/pct/host/setup-proxmox-host.sh`

Changes:
- Added ZFS dataset creation for embedding models
- Creates `/var/lib/embedding-models/fastembed` directory structure
- Configures compression (lz4) for efficient storage
- Falls back to regular directories when ZFS unavailable

### 3. Container Mount Configuration
**File**: `provision/pct/containers/create-worker-services.sh`

Changes:
- Added bind mount for embedding model cache to `data-lxc` container
- Maps host `/var/lib/embedding-models/fastembed` to same path in container
- Models accessible to embedding-api service

### 4. Ansible Role Updates
**Files**:
- `roles/embedding_api/defaults/main.yml`
- `roles/embedding_api/tasks/main.yml`
- `roles/embedding_api/templates/embedding-api.env.j2`
- `roles/embedding_api/templates/embedding-api.service.j2`

Changes:
- Added platform-aware cache directory configuration
- Proxmox: `/var/lib/embedding-models/fastembed` (shared mount)
- Docker: `/home/embedding/.cache/fastembed` (local cache)
- Updated service permissions for cache access
- Set `FASTEMBED_CACHE_PATH` environment variable

### 5. Install Script Integration
**File**: `scripts/make/install.sh`

Changes:
- Modified `start_embedding_download_background()` to detect Proxmox
- Calls `setup-embedding-models.sh` in background on Proxmox
- Maintains Docker-based caching for Docker platform
- Tracks download progress via PID for synchronization

## Architecture

```
Proxmox Host
├── /var/lib/embedding-models/fastembed/  (ZFS dataset)
│   └── [model files downloaded by setup script]
│
└── data-lxc Container (206)
    ├── /var/lib/embedding-models/fastembed/  (bind mount from host)
    └── embedding-api service
        └── Uses FASTEMBED_CACHE_PATH=/var/lib/embedding-models/fastembed
```

## Benefits

1. **No More Download Warnings**: Models cached during installation
2. **Faster Service Startup**: No download delay on first use
3. **Shared Storage**: Multiple containers can use same model files
4. **Platform Consistency**: Works identically on Proxmox and Docker
5. **Background Processing**: Models download in parallel with other setup tasks

## Testing Checklist

- [ ] Fresh Proxmox installation runs without "Docker not available" warning
- [ ] Models downloaded to `/var/lib/embedding-models/fastembed` on host
- [ ] data-lxc container has bind mount configured
- [ ] embedding-api service starts without downloading models
- [ ] Docker installation still works with local cache

## Usage

### Automatic (During Installation)
```bash
make install  # Models download automatically on Proxmox
```

### Manual Model Management
```bash
# Download models
bash provision/pct/host/setup-embedding-models.sh

# Remove unused models
bash provision/pct/host/setup-embedding-models.sh --cleanup
```

## Model Registry

Models downloaded (from `model_registry.yml`):
- `BAAI/bge-small-en-v1.5` (~134MB) - Development/demo
- `BAAI/bge-base-en-v1.5` (~438MB) - Balanced
- `BAAI/bge-large-en-v1.5` (~1.3GB) - Production (best quality)

## Related Components

- LLM model caching: `provision/pct/host/setup-llm-models.sh`
- Model registry: `provision/ansible/group_vars/all/model_registry.yml`
- Embedding API: `srv/embedding/`

## Next Steps

1. Test on fresh Proxmox installation
2. Verify model caching works correctly
3. Update deployment documentation if needed
4. Consider adding model verification/checksums
