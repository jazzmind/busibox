---
created: 2025-11-05
updated: 2025-11-05
status: completed
category: session-notes
tags: [refactoring, organization, pct, scripts, setup]
---

# PCT Directory Reorganization - November 5, 2025

## Overview

Reorganized the `provision/pct/` directory to better categorize scripts by purpose and created a universal interactive setup script to guide users through the deployment process.

## Changes Made

### 1. Directory Reorganization

Moved scripts into categorized subdirectories for better organization:

#### Before
```
provision/pct/
├── create_lxc_base.sh               # Container creation
├── create-core-services.sh
├── create-data-services.sh
├── create-worker-services.sh
├── create-vllm.sh
├── create-ollama.sh
├── setup-proxmox-host.sh            # Host scripts
├── setup-llm-models.sh
├── setup-zfs-storage.sh
├── add-data-mounts.sh
├── configure-gpu-passthrough.sh
├── install-nvidia-drivers.sh
├── check-gpu-usage.sh               # Diagnostic scripts
├── check-storage.sh
├── test-vllm-on-host.sh
├── list-templates.sh
├── destroy_test.sh
├── lib/
│   └── functions.sh
├── vars.env
└── test-vars.env
```

#### After
```
provision/pct/
├── containers/                       # Container creation scripts
│   ├── create_lxc_base.sh           # Main orchestrator
│   ├── create-core-services.sh
│   ├── create-data-services.sh
│   ├── create-worker-services.sh
│   ├── create-vllm.sh
│   └── create-ollama.sh
├── host/                             # Host-specific scripts
│   ├── setup-proxmox-host.sh
│   ├── setup-llm-models.sh
│   ├── setup-zfs-storage.sh
│   ├── add-data-mounts.sh
│   ├── configure-gpu-passthrough.sh
│   └── install-nvidia-drivers.sh
├── diagnostic/                       # Diagnostic/testing scripts
│   ├── check-gpu-usage.sh
│   ├── check-storage.sh
│   ├── test-vllm-on-host.sh
│   ├── list-templates.sh
│   └── destroy_test.sh
├── lib/                              # Shared functions
│   └── functions.sh
├── vars.env
├── test-vars.env
├── README.md
└── REFACTORING-SUMMARY.md
```

**Benefits**:
- ✅ **Clearer Purpose**: Directory name indicates script function
- ✅ **Easier Navigation**: Related scripts grouped together
- ✅ **Better Organization**: Follows script organization rules
- ✅ **Logical Grouping**: host/containers/diagnostic separation

### 2. Created Universal Interactive Setup Script

Created `provision/setup.sh` - a comprehensive interactive setup script that guides users through the entire deployment process.

**Location**: `provision/setup.sh`

#### Features

**Step 1: Host Configuration**
- Checks if Proxmox host is already configured
- Lists what will be installed/configured
- Prompts to run `host/setup-proxmox-host.sh`
- Allows skipping if already configured

**Step 2: Container Creation**
- Interactive environment selection (production/test)
- Option to include Ollama container
- Shows summary before proceeding
- Can skip if containers already exist
- Offers to destroy and recreate existing containers

**Step 3: Ansible Configuration**
- Detects environment from previous step
- Multiple deployment options:
  - Full deployment (all services)
  - Tag-based deployment (specific services)
  - Custom command
- Lists available tags with descriptions

#### User Experience

```bash
# Simple one-command setup
bash provision/setup.sh

# Guided through:
# 1. Host configuration check
# 2. Environment selection (prod/test)
# 3. Ollama option (y/N)
# 4. Summary and confirmation
# 5. Ansible deployment options
```

**Color-coded output:**
- 🔵 Blue: Headers and info
- ✅ Green: Success messages
- ⚠️ Yellow: Warnings
- ❌ Red: Errors

**Smart features:**
- Validates prerequisites (Proxmox, root)
- Detects existing configuration
- Remembers environment between steps
- Offers to continue on errors
- Provides helpful commands on failure

### 3. Updated Path References

Fixed all path references in scripts to work with new directory structure:

#### create_lxc_base.sh
```bash
# Old
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/vars.env"
bash "${SCRIPT_DIR}/containers/create-core-services.sh"

# New
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # containers/
PCT_DIR="$(dirname "$SCRIPT_DIR")"                           # pct/
source "${PCT_DIR}/vars.env"
bash "${SCRIPT_DIR}/create-core-services.sh"
```

#### Individual container scripts
```bash
# Already had correct structure
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PCT_DIR="$(dirname "$SCRIPT_DIR")"
source "${PCT_DIR}/lib/functions.sh"
source "${PCT_DIR}/vars.env"
```

### 4. Updated Documentation

Updated all documentation to reflect new structure:

**Files Updated:**
- `provision/pct/README.md` - Complete rewrite with new paths
  - Added section for interactive setup
  - Updated all script paths
  - Reorganized utility scripts section
  - Updated troubleshooting commands

**Key documentation changes:**
- Added "Interactive Setup (Recommended)" section at top
- Organized utility scripts by subdirectory
- Updated all example commands with new paths
- Added clear categorization (host/, diagnostic/, containers/)

## New User Workflow

### Easiest Path (Recommended for new users)
```bash
# One command to rule them all
bash provision/setup.sh
```

The script guides through everything with prompts.

### Advanced Path (Manual control)
```bash
# Step 1: Host setup
bash provision/pct/host/setup-proxmox-host.sh

# Step 2: Create containers
bash provision/pct/containers/create_lxc_base.sh production --with-ollama

# Step 3: Configure with Ansible
cd provision/ansible
make production
```

### Debug/Development Path
```bash
# Check specific issues
bash provision/pct/diagnostic/check-gpu-usage.sh
bash provision/pct/diagnostic/check-storage.sh

# Recreate single service
bash provision/pct/containers/create-vllm.sh test

# Add GPU to existing container
bash provision/pct/host/configure-gpu-passthrough.sh 208
```

## Directory Purpose Guide

### `containers/`
**Purpose**: Scripts that CREATE and MANAGE LXC containers
- Main orchestrator: `create_lxc_base.sh`
- Individual service groups
- Run these to create containers

### `host/`
**Purpose**: Scripts that CONFIGURE the Proxmox HOST
- Must run on Proxmox host (not in container)
- Modify host configuration
- Install drivers, setup storage, etc.

### `diagnostic/`
**Purpose**: Scripts for TESTING and DEBUGGING
- Check status and configuration
- Test before deployment
- Troubleshoot issues
- Clean up test environments

### `lib/`
**Purpose**: SHARED FUNCTIONS used by other scripts
- Not run directly
- Sourced by other scripts
- Provides reusable functions

## Path Reference Quick Guide

```bash
# Old paths → New paths

# Host setup
provision/pct/setup-proxmox-host.sh
→ provision/pct/host/setup-proxmox-host.sh

# Container creation
provision/pct/create_lxc_base.sh
→ provision/pct/containers/create_lxc_base.sh

# Individual containers
provision/pct/create-vllm.sh
→ provision/pct/containers/create-vllm.sh

# Diagnostics
provision/pct/check-gpu-usage.sh
→ provision/pct/diagnostic/check-gpu-usage.sh

provision/pct/check-storage.sh
→ provision/pct/diagnostic/check-storage.sh

# GPU configuration
provision/pct/configure-gpu-passthrough.sh
→ provision/pct/host/configure-gpu-passthrough.sh
```

## Benefits Summary

### 1. Better Organization
- Scripts grouped by purpose
- Clear directory naming
- Follows organizational rules

### 2. Improved User Experience
- Interactive setup for new users
- Clear guidance at each step
- Helpful error messages

### 3. Easier Maintenance
- Related scripts in same directory
- Consistent path structure
- Better documentation

### 4. Flexible Usage
- Can use interactive script OR manual commands
- Individual scripts still work independently
- Multiple deployment paths

## Testing Checklist

- [x] Path references updated in all scripts
- [x] Interactive setup script created
- [x] Documentation updated
- [ ] Test interactive setup on fresh Proxmox host
- [ ] Verify all scripts work from new locations
- [ ] Test individual container creation scripts
- [ ] Test diagnostic scripts from new paths

## Files Modified

### Moved Files
- `create_lxc_base.sh` → `containers/create_lxc_base.sh`
- `setup-proxmox-host.sh` → `host/setup-proxmox-host.sh`
- `setup-llm-models.sh` → `host/setup-llm-models.sh`
- `setup-zfs-storage.sh` → `host/setup-zfs-storage.sh`
- `add-data-mounts.sh` → `host/add-data-mounts.sh`
- `configure-gpu-passthrough.sh` → `host/configure-gpu-passthrough.sh`
- `install-nvidia-drivers.sh` → `host/install-nvidia-drivers.sh`
- `check-gpu-usage.sh` → `diagnostic/check-gpu-usage.sh`
- `check-storage.sh` → `diagnostic/check-storage.sh`
- `test-vllm-on-host.sh` → `diagnostic/test-vllm-on-host.sh`
- `list-templates.sh` → `diagnostic/list-templates.sh`
- `destroy_test.sh` → `diagnostic/destroy_test.sh`

### Updated Files
- `containers/create_lxc_base.sh` - Path references
- `provision/pct/README.md` - Complete documentation update

### New Files
- `provision/setup.sh` - Universal interactive setup script

## Migration Notes

### For Existing Deployments

**No changes needed** - Existing containers continue to work.

**To use new scripts:**
```bash
# Update any automation/documentation to use new paths
# Old
bash provision/pct/create_lxc_base.sh production

# New
bash provision/pct/containers/create_lxc_base.sh production
```

### For New Deployments

Start with the interactive setup:
```bash
bash provision/setup.sh
```

Or follow the manual path with new paths as documented in README.

## Rules Applied

Per `.cursor/rules/002-script-organization.md`:
- ✅ Host scripts → `provision/pct/host/`
- ✅ Container creation → `provision/pct/containers/`
- ✅ Diagnostic scripts → `provision/pct/diagnostic/`
- ✅ Shared functions → `provision/pct/lib/`

Per `.cursor/rules/001-documentation-organization.md`:
- ✅ Session notes → `docs/session-notes/`
- ✅ Updated technical docs with new paths
- ✅ Kebab-case naming
- ✅ Metadata headers

## Follow-up Improvements

After initial implementation, additional enhancements were made based on user feedback:

### 1. Ansible Vault Detection
**Issue**: Encrypted vault files require `--ask-vault-pass` flag  
**Solution**: Added automatic detection of encrypted vault files
- Checks if `roles/secrets/vars/vault.yml` starts with `$ANSIBLE_VAULT`
- Automatically adds `--ask-vault-pass` flag when detected
- Warns user that vault password will be required

### 2. Fixed Test Container Range
**Issue**: Documentation said 300-310 but should be 300-308  
**Solution**: Corrected all references to test containers
- Production: 200-208 (9 containers)
- Test: 300-308 (9 containers, matching production)

### 3. Enhanced Container Management Options
**Issue**: When containers exist, only option was to destroy all or skip  
**Solution**: Added flexible container management menu:
1. **Skip** - Keep existing, create any missing (smart handling)
2. **Destroy specific** - Enter container IDs to destroy and recreate
3. **Destroy all** - Complete rebuild (requires 'yes' confirmation)
4. **Cancel** - Skip container creation entirely

**Features**:
- Lists existing containers with names
- Shows missing containers
- Allows selective destruction
- Safety confirmation for destructive operations

### 4. Ansible Deployment Loop
**Issue**: Could only run one tag-based deployment at a time  
**Solution**: Added deployment loop that allows multiple operations
- Choose deployment option
- Execute deployment
- Returns to menu for next deployment
- Select "Done" when finished

**Benefits**:
- Deploy multiple services in sequence: nginx → postgres → agent
- Test individual services iteratively
- No need to restart script for each deployment
- Better for incremental updates

### 5. Individual Container Management Loop
**Issue**: No way to create/recreate individual containers interactively  
**Solution**: Added "Individual container management" option with loop
- Select environment (production/test)
- Shows current container status
- Menu-driven container creation:
  1. Core services (proxy, apps, agent)
  2. Data services (postgres, milvus, minio)
  3. Worker services (ingest, litellm)
  4. vLLM (all GPUs)
  5. Ollama (optional, with GPU selection)
  6. Destroy specific container(s)
  7. Show container status
  8. Done

**Benefits**:
- Create containers one group at a time
- Test each service before creating next
- Destroy and recreate specific containers
- Iterative development workflow
- No need to run full deployment

## Example Workflows

### Incremental Deployment
```bash
bash provision/setup.sh

# Step 3: Ansible Configuration
# 1. Choose "Specific services"
# 2. Deploy: nginx
# 3. Test nginx...
# 4. Choose "Specific services" again
# 5. Deploy: postgres,milvus
# 6. Test databases...
# 7. Choose "Specific services" again
# 8. Deploy: agent
# 9. Choose "Done"
```

### Recreate Single Container
```bash
bash provision/setup.sh

# Step 2: Container Creation
# 1. Select environment: production
# 2. Choose "Destroy specific containers"
# 3. Enter: 208 (vLLM)
# 4. Confirm: y
# 5. Container recreated
```

### With Encrypted Vault
```bash
bash provision/setup.sh

# Step 3: Ansible Configuration
# > Warning: Encrypted Ansible vault detected
# > You will be prompted for vault password
# (Script automatically adds --ask-vault-pass)
```

### Individual Container Management
```bash
bash provision/setup.sh

# Step 2: Container Creation
# 1. Choose "Individual container management"
# 2. Select environment: production
# 3. Shows current container status
# 4. Choose "Data services" → creates postgres, milvus, minio
# 5. Test databases...
# 6. Choose "vLLM" → creates vLLM with all GPUs
# 7. Test vLLM...
# 8. Choose "Destroy specific containers" → enter: 208
# 9. Choose "vLLM" again → recreates with fresh config
# 10. Choose "Done"
```

## Next Steps

1. Test interactive setup on fresh Proxmox host
2. Update any external documentation/links
3. Update CI/CD scripts if using automated deployment
4. Consider creating similar setup scripts for other components

