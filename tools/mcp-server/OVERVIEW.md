# Busibox MCP Server - Project Overview

## What Is This?

The Busibox MCP Server is a **Model Context Protocol server** that makes the Busibox project documentation, scripts, and infrastructure easily accessible to AI coding assistants like Claude, Cursor, and others. It's like having an expert assistant that knows exactly where everything is in the project and can execute operations on your behalf.

## Why Was It Created?

**Problem**: Busibox has extensive documentation organized across multiple categories, dozens of scripts with different execution contexts, specific organization rules, and requires regular deployments via git pull + make commands on Proxmox.

**Solution**: An MCP server that provides:
- **Instant documentation access** - Browse by category, search by keyword
- **Script discovery** - Find scripts by context and purpose
- **Guided assistance** - Step-by-step help for common tasks
- **Standards enforcement** - Implements project organization rules
- **Always up-to-date** - Reads directly from the filesystem
- **SSH command execution** - Execute commands on Proxmox host and containers
- **Git operations** - Pull latest code on Proxmox
- **Make target execution** - Run deployments and tests with proper environment flags
- **Log gathering** - Get logs and service status from containers via SSH
- **Container/Service lookup** - Quick access to IPs, ports, and service mappings

## Critical: Always Use Make Commands

**NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly.**

All service operations MUST go through the unified `make` interface:

```bash
# ❌ NEVER DO THIS
docker compose up -d authz-api
docker restart prod-authz-api
ansible-playbook -i inventory/docker docker.yml --tags authz

# ✅ ALWAYS DO THIS
make install SERVICE=authz              # Deploy/redeploy
make manage SERVICE=authz ACTION=restart # Manage running service
```

**Why?** The `make` commands inject secrets from Ansible Vault at runtime. Direct docker/ansible commands skip this and cause authentication failures.

## What Can It Do?

### Service Deployment and Management

**Deploy Services** (installs/redeploys via Ansible with secrets):
```bash
make install SERVICE=authz              # Single service
make install SERVICE=authz,agent        # Multiple services
make install SERVICE=apis               # Service group
```

**Manage Running Services**:
```bash
make manage SERVICE=authz ACTION=restart  # Restart
make manage SERVICE=authz ACTION=logs     # View logs
make manage SERVICE=authz ACTION=status   # Check status
make manage SERVICE=authz ACTION=redeploy # Full rebuild
```

### Git Operations on Proxmox
- Pull latest code from git
- Check git status
- Reset to origin (discard local changes)

### Testing
```bash
make test-docker SERVICE=authz            # Docker tests
make test-local SERVICE=agent INV=staging # Remote tests
make test SERVICE=agent INV=staging       # Container tests
```

### Container & Service Information
- Complete container inventory with IPs for staging and production
- Service port mappings
- Quick endpoint lookups
- SSH connection info

### Browse Documentation by Category
- Architecture and design decisions
- Deployment guides and procedures
- Configuration and setup guides
- Troubleshooting guides
- Reference documentation
- How-to guides
- Session notes
- Development tasks

### Search Documentation
- Keyword-based search
- Category filtering
- Context-aware results

### SSH Command Execution
- Execute any command on Proxmox host
- Get container logs via journalctl
- Check service status via systemctl

## How Does It Work?

```
┌─────────────────────────────────────────┐
│         AI Assistant                    │
│    (Claude, Cursor, etc.)              │
└─────────────────┬───────────────────────┘
                  │ MCP Protocol
                  │ (stdio)
┌─────────────────▼───────────────────────┐
│       Busibox MCP Server v3.0           │
│   (Node.js/TypeScript)                  │
│                                          │
│  ┌──────────────────────────────────┐  │
│  │ Resources │ Tools   │ Prompts    │  │
│  │ 10+       │ 23      │ 10         │  │
│  └──────────────────────────────────┘  │
└─────────────────┬───────────────────────┘
                  │ SSH / File System
┌─────────────────▼───────────────────────┐
│         Proxmox Host                    │
│  ┌────────────────────────────────────┐│
│  │ /root/busibox                      ││
│  │ Git repo, Ansible, Make targets    ││
│  └────────────────────────────────────┘│
│         LXC Containers                  │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐      │
│  │proxy│ │apps │ │agent│ │ingest│ ... │
│  └─────┘ └─────┘ └─────┘ └─────┘      │
└──────────────────────────────────────────┘
```

## Quick Start

### 1. Install

```bash
cd tools/mcp-server
bash setup.sh
```

This will:
- Check Node.js version
- Install dependencies
- Build the server
- Configure Claude Desktop and/or Cursor
- Display usage information

### 2. Use

In Claude or Cursor, just ask naturally:

```
"Deploy the authz service"
"Restart the agent API"
"Check the status of postgres and redis"
"View logs for the ingest service"
"Pull the latest busibox code on Proxmox"
"Run the agent tests against Docker"
"What's the IP for milvus in staging?"
"Show me the container logs for agent-lxc"
"Search docs for GPU passthrough"
```

## Available Tools (23 total)

### Service & Testing Tools
| Tool | Description |
|------|-------------|
| `get_makefile_help` | Get comprehensive Makefile help by category |
| `run_docker_tests` | Run tests against local Docker services |
| `run_remote_tests` | Run tests locally against remote staging/production |
| `run_container_tests` | Run tests directly on containers via SSH |
| `init_test_databases` | Initialize test databases for testing |
| `check_test_databases` | Verify test databases are ready |
| `get_testing_guide` | Get comprehensive testing documentation |

> **Note**: `docker_control` is deprecated. Use `make manage SERVICE=x ACTION=y` instead.

### Git & Deployment
| Tool | Description |
|------|-------------|
| `git_pull_busibox` | Pull latest code on Proxmox (supports branch selection, reset --hard) |
| `git_status` | Check git status on Proxmox |
| `run_make_target` | Run any make target with environment (staging/production) |
| `list_make_targets` | List available make targets by category |

### Container & Service Info
| Tool | Description |
|------|-------------|
| `list_containers` | List all containers with IPs and services |
| `get_container_info` | Get detailed info for a specific container |
| `get_service_endpoints` | Get IP/port for specific services |
| `get_deployment_info` | Get environment configuration (group_vars) |

### SSH & Logs
| Tool | Description |
|------|-------------|
| `execute_proxmox_command` | Run any command on Proxmox host |
| `get_container_logs` | Get journalctl logs from a container |
| `get_container_service_status` | Get systemctl status for a service |

### Documentation
| Tool | Description |
|------|-------------|
| `search_docs` | Search documentation by keyword |
| `get_doc` | Get full content of a documentation file |
| `get_script_info` | Get info about a script (purpose, usage, context) |
| `find_scripts` | Find scripts by execution context or purpose |

## Container Reference

### Production (10.96.200.x)
| Container | ID | IP | Services |
|-----------|----|----|----------|
| proxy-lxc | 200 | 10.96.200.200 | nginx |
| apps-lxc | 201 | 10.96.200.201 | busibox-portal, busibox-agents, etc. |
| agent-lxc | 202 | 10.96.200.202 | agent-api |
| pg-lxc | 203 | 10.96.200.203 | postgresql |
| milvus-lxc | 204 | 10.96.200.204 | milvus, search-api |
| files-lxc | 205 | 10.96.200.205 | minio |
| ingest-lxc | 206 | 10.96.200.206 | ingest-api, ingest-worker, redis |
| litellm-lxc | 207 | 10.96.200.207 | litellm |
| vllm-lxc | 208 | 10.96.200.208 | vllm, vllm-embedding, colpali |
| ollama-lxc | 209 | 10.96.200.209 | ollama |
| authz-lxc | 210 | 10.96.200.210 | authz |

### Staging (10.96.201.x)
Same containers with ID + 100 and IP in 201 subnet (e.g., TEST-milvus-lxc: 304, 10.96.201.204)

## Service Management Quick Reference

### Deploy Services
```bash
make install SERVICE=authz              # Single service
make install SERVICE=authz,agent        # Multiple services
make install SERVICE=apis               # All API services
make install SERVICE=infrastructure     # postgres, redis, minio, milvus
make install SERVICE=all                # Everything
```

### Manage Running Services
```bash
make manage SERVICE=authz ACTION=restart  # Restart
make manage SERVICE=authz ACTION=logs     # View logs
make manage SERVICE=authz ACTION=status   # Check status
make manage SERVICE=authz ACTION=redeploy # Full rebuild
```

## Make Target Categories

### Deployment
- `all`, `files`, `pg`, `milvus`, `search`, `search-api`, `agent`, `ingest`, `apps`, `nginx`, `authz`, `litellm`, `vllm`, `colpali`

### App Deployment
- `deploy-apps`, `deploy-busibox-portal`, `deploy-busibox-agents`, `deploy-doc-intel`, `deploy-foundation`, `deploy-busibox-analysis`, `deploy-innovation`

### Testing
- `test-all`, `test-ingest`, `test-search`, `test-agent`, `test-authz`, `test-apps`
- `test-extraction-simple`, `test-extraction-llm`, `test-extraction-marker`, `test-extraction-colpali`

### Verification
- `verify`, `verify-health`, `verify-smoke`

## Example Workflows

### Deploy a Service

```
User: "Deploy the authz service"

AI responds with:
  make install SERVICE=authz

User: "Restart the agent API"

AI responds with:
  make manage SERVICE=agent ACTION=restart
```

### Update and Deploy to Staging

```
User: "Update busibox and deploy ingest to staging"

AI uses:
1. git_pull_busibox - Pull latest code
2. run_make_target(target: "ingest", environment: "staging") - Deploy
3. get_container_service_status(container: "ingest-lxc", service: "ingest-api") - Verify
```

### Troubleshoot a Service

```
User: "The search API seems slow, check the logs"

AI responds with:
  make manage SERVICE=search ACTION=logs

Or uses:
1. get_container_info(container: "milvus") - Get IP
2. get_container_service_status(container: "milvus-lxc", service: "search-api") - Check status
3. get_container_logs(container: "milvus-lxc", service: "search-api", lines: 100) - Get logs
```

### Run Tests

```
User: "Run the agent tests"

AI responds with:
  make test-docker SERVICE=agent

User: "Run tests against staging"

AI responds with:
  make test-local SERVICE=agent INV=staging
```

## Environment Variables

Configure via environment variables if defaults don't work:

```bash
PROXMOX_HOST_IP=10.96.200.1        # Proxmox host IP
PROXMOX_HOST_USER=root              # SSH user for Proxmox
PROXMOX_SSH_KEY_PATH=~/.ssh/id_rsa  # SSH key path
CONTAINER_SSH_KEY_PATH=~/.ssh/id_rsa # SSH key for containers
BUSIBOX_PATH_ON_PROXMOX=/root/busibox # Path to busibox on Proxmox
```

## Troubleshooting

### SSH Connection Failed
- Verify SSH key is configured for Proxmox host
- Check Proxmox host IP is correct
- Ensure network connectivity to Proxmox

### Make Target Failed
- Use `list_make_targets` to see available targets
- Check `git_status` to ensure code is up to date
- Use `get_container_logs` to see detailed errors

### Container Not Found
- Use `list_containers` to see all available containers
- Container names can be partial (e.g., "milvus" matches "milvus-lxc")

## Security

- **SSH Key Required**: Uses SSH key authentication (no passwords)
- **Read-Only Docs**: Documentation access is read-only
- **Command Execution**: SSH commands require valid key
- **No Secrets Exposed**: Vault contents are not exposed

## Version History

- **v3.0.0** (2026-01-17): Renamed "test" environment to "staging", updated all tools and prompts
- **v2.2.0** (2026-01-16): Added comprehensive testing/deployment tools and prompts
- **v2.0.0** (2025-12-21): Added git operations, make target execution, enhanced container info
- **v1.0.0** (2025-11-06): Initial release with documentation and SSH commands

---

**Version**: 3.0.0  
**Created**: 2025-11-06  
**Updated**: 2026-01-17  
**Status**: Production Ready  
**License**: Part of Busibox project
