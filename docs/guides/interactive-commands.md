# Interactive Commands Guide

**Category:** Guides  
**Created:** 2025-01-21  
**Updated:** 2025-01-21  
**Status:** Active

## Overview

Busibox provides an interactive command system for easy management without requiring directory navigation. All commands are accessed through simple `make` targets at the repository root.

## Available Commands

### Quick Reference

```bash
make setup      # Initial setup (Proxmox host + containers)
make configure  # Configure models, GPUs, and containers
make deploy     # Deploy services with Ansible
make test       # Run tests (infrastructure and services)
make mcp        # Build MCP server for Cursor AI
make help       # Show available commands
```

## Detailed Command Documentation

### make setup

**Purpose:** Initial setup of Proxmox host and LXC container creation

**Execution Context:** Proxmox host (as root)

**What it does:**
1. Sets up Proxmox host (NVIDIA drivers, ZFS, dependencies)
2. Creates LXC containers for selected environment (test/production)
3. Optionally includes Ollama container

**Interactive prompts:**
- Run Proxmox host setup? (Y/n)
- Select environment (test/production)
- Create LXC containers? (Y/n)
- Include optional Ollama container? (y/N)

**Script:** `scripts/setup.sh`

**Next steps after setup:**
- Configure models and GPUs: `make configure`
- Deploy services: `make deploy`

---

### make configure

**Purpose:** Configure models, GPUs, and container settings

**Execution Context:** 
- Proxmox host (for container configuration)
- Any system (for model configuration)

**Main Menu:**
1. **Model Configuration**
   - Download/Manage LLM Models
     - Download Models from Registry
     - Cleanup Orphaned Models
   - Update Model Config (analyze downloaded models)
   - Configure vLLM Model Routing (GPU assignments)

2. **Container Configuration**
   - Check Container Memory Allocation
   - Install NVIDIA Drivers in Container
   - Configure GPU Passthrough for Container
   - Configure GPU Allocation (All Containers)
   - Configure All GPUs for Container
   - Setup ZFS Storage
   - Add Data Mounts to Containers

**Script:** `scripts/configure.sh`

**Common workflows:**

**Download Models:**
```bash
make configure
# Select: 1 (Model Configuration)
# Select: 1 (Download/Manage LLM Models)
# Select: 1 (Download Models from Registry)
```

**Model Configuration:**
```bash
make configure
# Select: 1 (Model Configuration)
# Select: 2 (Update Model Config)
# Then: 3 (Configure vLLM Model Routing)
```

**GPU Configuration:**
```bash
make configure
# Select: 2 (Container Configuration)
# Select: 4 (Configure GPU Allocation)
```

---

### make deploy

**Purpose:** Deploy services using Ansible

**Execution Context:** Admin workstation or Proxmox host

**Prerequisites:**
- Ansible installed
- Containers created and running
- SSH access to containers configured

**Interactive prompts:**
- Select environment (test/production)
- Select service to deploy

**Deployment Menu:**
1. Deploy All Services
2. Deploy Core Services (files, pg, milvus)
3. Deploy Search API
4. Deploy Agent API
5. Deploy Ingest Service
6. Deploy Apps (AI Portal)
7. Deploy LiteLLM
8. Deploy vLLM (Main)
9. Deploy vLLM Embedding Service
10. Deploy ColPali Service
11. Deploy OpenWebUI
12. Verify Deployment (Health Checks)

**Script:** `scripts/deploy.sh`

**Common workflows:**

**Full deployment:**
```bash
make deploy
# Select: 2 (Production)
# Select: 1 (Deploy All Services)
```

**Incremental deployment:**
```bash
make deploy
# Select: 1 (Test)
# Select: 3 (Deploy Search API)
```

**Verify deployment:**
```bash
make deploy
# Select: 2 (Production)
# Select: 12 (Verify Deployment)
```

---

### make test

**Purpose:** Run infrastructure and service tests

**Execution Context:** 
- Proxmox host (for infrastructure tests)
- Admin workstation (for service tests)

**Test Menu:**
1. Infrastructure Tests (Full Suite)
2. Infrastructure Tests (Provision Only)
3. Infrastructure Tests (Verify Only)
4. Service Tests
   - Ingest Service Tests
     - Unit Tests
     - All Tests (Unit + Integration)
     - With Coverage
     - SIMPLE Extraction
     - LLM Cleanup Extraction
     - Marker Extraction
     - ColPali Extraction
   - Search Service Tests
     - Unit Tests
     - Integration Tests
     - With Coverage
   - Agent Service Tests
   - Apps Service Tests
   - All Service Tests
5. All Tests (Infrastructure + Services)

**Script:** `scripts/test.sh`

**Common workflows:**

**Quick service test:**
```bash
make test
# Select: 1 (Test)
# Select: 4 (Service Tests)
# Select: 1 (Ingest Service Tests)
# Select: 1 (Run Unit Tests)
```

**Full test suite:**
```bash
make test
# Select: 2 (Production)
# Select: 5 (All Tests)
```

**Specific extraction test:**
```bash
make test
# Select: 1 (Test)
# Select: 4 (Service Tests)
# Select: 1 (Ingest Service Tests)
# Select: 4 (Test SIMPLE Extraction)
```

---

### make mcp

**Purpose:** Build and manage the MCP server for Cursor AI integration

**Execution Context:** Any system with Node.js installed

**Prerequisites:**
- Node.js >= 18.0.0

**MCP Server Menu:**
1. Build MCP Server
2. Clean Build Artifacts
3. Show Cursor Configuration
4. Install Dependencies Only

**Script:** `scripts/mcp.sh`

**Common workflows:**

**Initial build:**
```bash
make mcp
# Select: 1 (Build MCP Server)
# Select: 3 (Show Cursor Configuration)
# Copy configuration to Cursor settings
```

**Rebuild after changes:**
```bash
make mcp
# Select: 2 (Clean Build Artifacts)
# Select: 1 (Build MCP Server)
```

---

## UI Features

All interactive scripts use a consistent terminal UI:

- **Colored output**: Blue (info), Green (success), Yellow (warning), Red (error), Cyan (headers)
- **ASCII boxes**: Clean visual separation with `╔═╗║╚╝` characters
- **Numbered menus**: Easy selection with clear options
- **Progress indicators**: Step counters for multi-step operations
- **Status symbols**: ✓ (success), ✗ (error), ○ (pending)

## Shared UI Library

All scripts use the shared UI library at `scripts/lib/ui.sh`, which provides:

- `box()` - Display ASCII box with title
- `header()` - Section headers
- `menu()` - Interactive numbered menus
- `info()`, `success()`, `warn()`, `error()` - Status messages
- `confirm()` - Yes/no prompts
- `select_environment()` - Environment selection
- `pause()` - Wait for keypress
- `check_proxmox()` - Verify running on Proxmox host

## Error Handling

All scripts include:
- Prerequisite validation
- Clear error messages with suggested actions
- Confirmation prompts for destructive operations
- Non-zero exit codes on failure

## Examples

### Complete Setup Workflow

```bash
# 1. Initial setup on Proxmox host
make setup
# Select: production
# Include Ollama: no

# 2. Configure models and GPUs
make configure
# Model Configuration > Update Model Config
# Model Configuration > Configure vLLM Model Routing

# 3. Deploy all services
make deploy
# Select: production
# Deploy All Services

# 4. Verify deployment
make test
# Select: production
# Service Tests > All Service Tests
```

### Development Workflow

```bash
# 1. Setup test environment
make setup
# Select: test

# 2. Deploy specific service
make deploy
# Select: test
# Deploy Ingest Service

# 3. Run tests for that service
make test
# Select: test
# Service Tests > Ingest Service Tests > Run Unit Tests

# 4. Make code changes...

# 5. Redeploy and retest
make deploy  # Redeploy
make test    # Retest
```

### Model Setup Workflow

```bash
# 1. Download models from registry
make configure
# Model Configuration > Download/Manage LLM Models > Download Models from Registry

# 2. Analyze downloaded models
make configure
# Model Configuration > Update Model Config

# 3. Configure model routing
make configure
# Model Configuration > Configure vLLM Model Routing

# 4. (Optional) Clean up orphaned models
make configure
# Model Configuration > Download/Manage LLM Models > Cleanup Orphaned Models
```

### GPU Configuration Workflow

```bash
# 1. Configure GPU allocation
make configure
# Container Configuration > Configure GPU Allocation

# 2. Install drivers in containers
make configure
# Container Configuration > Configure All GPUs for Container
# Enter container ID: 208

# 3. Update model routing
make configure
# Model Configuration > Configure vLLM Model Routing

# 4. Deploy vLLM services
make deploy
# Deploy vLLM (Main)
# Deploy vLLM Embedding Service
```

## Troubleshooting

### Command not found: make

Install make:
```bash
# Debian/Ubuntu
apt install -y make

# macOS
xcode-select --install
```

### Ansible not found

Install Ansible:
```bash
# Debian/Ubuntu
apt install -y ansible

# macOS
brew install ansible
```

### Node.js version too old (for MCP server)

Install newer Node.js:
```bash
# Using nvm (recommended)
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash
nvm install 18
nvm use 18

# Or download from https://nodejs.org/
```

### Not running on Proxmox host

Some commands require Proxmox host:
- `make setup` (container creation)
- `make configure` (container configuration options)
- Infrastructure tests in `make test`

Run these commands directly on your Proxmox host via SSH:
```bash
ssh root@proxmox-host
cd /root/busibox
make setup
```

## Related Documentation

- [MCP Server Usage](mcp-server-usage.md) - Detailed MCP server documentation
- [Testing Strategy](../testing/master-guide.md) - Complete testing guide
- [Deployment Guide](../deployment/environment-specific.md) - Deployment details
- [GPU Configuration](../deployment/gpu-allocation-strategy.md) - GPU setup guide
- [Architecture](../architecture/architecture.md) - System architecture overview

## Support

For issues or questions:
1. Check the specific guide for your task
2. Review error messages for suggested actions
3. Consult the troubleshooting sections in relevant documentation
4. Check container logs: `ssh root@<container-ip> journalctl -u <service>`

