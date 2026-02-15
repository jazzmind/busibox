# Busibox MCP Server

**Created**: 2025-11-06  
**Updated**: 2026-02-15  
**Version**: 3.0.0  
**Status**: DEPRECATED - Use the new focused MCP servers instead  
**Category**: Tools

> **DEPRECATED**: This monolithic MCP server has been replaced by three focused servers. Use `make mcp` to build the new servers:
>
> - **mcp-core-dev** (`tools/mcp-core-dev/`) - For developers building/testing busibox services
> - **mcp-app-builder** (`tools/mcp-app-builder/`) - For developers building Next.js apps for busibox
> - **mcp-admin** (`tools/mcp-admin/`) - For operators managing deployments (with destructive op confirmation)
>
> See CLAUDE.md for Cursor configuration. This server remains for backward compatibility but will not receive updates.

---

A Model Context Protocol (MCP) server that provides AI coding agents and maintainers with easy access to Busibox documentation, scripts, infrastructure operations, and project structure.

## Overview

This MCP server exposes Busibox's organizational structure and operations through a standardized protocol that AI coding assistants (like Claude, Cursor, etc.) can use to:

- **Run tests** using Docker, remote, or container testing modes
- **Deploy services** to staging or production via Makefile
- **Pull latest code** on Proxmox host
- **Run make targets** for deployment and testing
- **Browse documentation** by category (updated structure)
- **Search documentation** by keyword
- **Get container/service info** including IPs and ports
- **Execute SSH commands** on Proxmox and containers
- **View container logs** via journalctl
- **Get script information** including purpose, usage, and execution context
- **Get guided assistance** for testing, deployment, and Docker development workflows

## Features

### Resources (10+)

The server exposes the following resources:

- `busibox://docs/{category}` - Browse documentation by category
  - Top-level: architecture, deployment, development, guides, reference
  - Nested: session-notes, troubleshooting, tasks (in development/)
- `busibox://docs/all` - Complete documentation index
- `busibox://scripts/index` - Index of all scripts by execution context
- `busibox://rules` - Project organization rules
- `busibox://architecture` - All architecture documents combined
- `busibox://quickstart` - Quick start guide (CLAUDE.md)
- `busibox://containers` - Complete container map with IPs for staging/production
- `busibox://make-targets` - Available make targets with descriptions

### Tools (23)

#### Service & Testing Tools

| Tool | Description |
|------|-------------|
| `get_makefile_help` | Get comprehensive Makefile help by category |
| `run_docker_tests` | Run tests against local Docker services |
| `run_remote_tests` | Run tests locally against remote staging/production |
| `run_container_tests` | Run tests directly on containers via SSH |
| `docker_control` | ⚠️ **DEPRECATED** - Use `make manage SERVICE=x ACTION=y` instead |
| `init_test_databases` | Initialize test databases for testing |
| `check_test_databases` | Verify test databases are ready |
| `get_testing_guide` | Get comprehensive testing documentation |

> **Important**: For all service deployment and management, use `make` commands:
> - Deploy: `make install SERVICE=authz`
> - Manage: `make manage SERVICE=authz ACTION=restart`

#### Git & Deployment Tools

| Tool | Description |
|------|-------------|
| `git_pull_busibox` | Pull latest code on Proxmox host (supports branch, reset --hard) |
| `git_status` | Check git status of busibox repo on Proxmox |
| `run_make_target` | Run a make target with environment (staging/production) |
| `list_make_targets` | List available make targets by category |

#### Container & Service Tools

| Tool | Description |
|------|-------------|
| `list_containers` | List all containers with IPs and services |
| `get_container_info` | Get detailed info for a specific container |
| `get_service_endpoints` | Get IP/port for specific services |
| `get_deployment_info` | Get environment configuration (group_vars) |

#### SSH & Log Tools

| Tool | Description |
|------|-------------|
| `execute_proxmox_command` | Run any command on Proxmox host |
| `get_container_logs` | Get journalctl logs from a container |
| `get_container_service_status` | Get systemctl status for a service |

#### Documentation Tools

| Tool | Description |
|------|-------------|
| `search_docs` | Search documentation by keyword |
| `get_doc` | Get full content of a documentation file |
| `get_script_info` | Get info about a script (purpose, usage, context) |
| `find_scripts` | Find scripts by execution context or purpose |

### Prompts (10)

Guided assistance for common tasks:

1. **deploy_service** - Guide for deploying a service
2. **troubleshoot_issue** - Guide for troubleshooting issues
3. **add_service** - Guide for adding a new service
4. **create_documentation** - Guide for creating documentation
5. **run_tests** - Guide for running tests
6. **deploy_app** - Guide for deploying applications
7. **update_and_deploy** - Guide for pulling code and deploying
8. **testing_workflow** - Complete testing workflow for Docker/staging/production (NEW)
9. **deployment_workflow** - Complete deployment workflow guide (NEW)
10. **docker_development** - Docker local development setup and workflow (NEW)

## Installation

### Prerequisites

- Node.js 18 or higher
- npm or yarn
- SSH key configured for Proxmox host access
- Access to the Busibox project directory

### Quick Setup

```bash
cd tools/mcp-server
bash setup.sh
```

### Manual Build

```bash
cd tools/mcp-server
npm install
npm run build
```

### Configuration

#### Environment Variables

The MCP server supports the following environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXMOX_HOST_IP` | `10.96.200.1` | Proxmox host IP |
| `PROXMOX_HOST_USER` | `root` | SSH user for Proxmox |
| `PROXMOX_SSH_KEY_PATH` | `~/.ssh/id_rsa` | SSH key for Proxmox |
| `CONTAINER_SSH_KEY_PATH` | `~/.ssh/id_rsa` | SSH key for containers |
| `BUSIBOX_PATH_ON_PROXMOX` | `/root/busibox` | Path to busibox on Proxmox |

#### For Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "busibox": {
      "command": "node",
      "args": [
        "/absolute/path/to/busibox/tools/mcp-server/dist/index.js"
      ],
      "env": {
        "PROXMOX_HOST_IP": "10.96.200.1",
        "PROXMOX_HOST_USER": "root",
        "PROXMOX_SSH_KEY_PATH": "/path/to/.ssh/id_rsa"
      }
    }
  }
}
```

#### For Cursor AI

Add to Cursor MCP settings (Settings > MCP Servers):

```json
{
  "busibox": {
    "command": "node",
    "args": [
      "/absolute/path/to/busibox/tools/mcp-server/dist/index.js"
    ],
    "env": {
      "PROXMOX_HOST_IP": "10.96.200.1",
      "PROXMOX_HOST_USER": "root",
      "PROXMOX_SSH_KEY_PATH": "/path/to/.ssh/id_rsa"
    }
  }
}
```

## Usage Examples

### Service Deployment and Management (UPDATED)

**IMPORTANT**: Always use `make` commands from the repo root. Never run `docker compose` or `ansible-playbook` directly.

```
"Deploy the authz service"
"Restart the agent API"
"Check the status of postgres and redis"
"View logs for the ingest service"
"Redeploy all APIs"
```

The MCP server guides you to use the correct commands:

1. **Deploy Services** - Install or redeploy via Ansible with vault secrets
   ```bash
   make install SERVICE=authz           # Single service
   make install SERVICE=authz,agent     # Multiple services
   make install SERVICE=apis            # Service group
   ```

2. **Manage Running Services** - Start, stop, restart, logs, status
   ```bash
   make manage SERVICE=authz ACTION=restart
   make manage SERVICE=authz ACTION=logs
   make manage SERVICE=authz ACTION=status
   ```

### Testing

```
"How do I run tests for the agent service?"
"Run agent tests against Docker"
"What's the testing workflow for staging?"
```

Testing commands:

1. **Docker Tests** - For local development
   ```bash
   make test-docker SERVICE=agent
   ```

2. **Remote Tests** - Test local code against staging/production
   ```bash
   make test-local SERVICE=agent INV=staging
   ```

3. **Container Tests** - Run directly on containers
   ```bash
   make test SERVICE=agent INV=staging
   ```

### Git Operations

```
"Pull the latest busibox code on Proxmox"
"Check git status on Proxmox"
"Reset busibox to origin/main on Proxmox"
```

### Deployments

```
"Deploy ingest to test"
"Deploy ai-portal to production"
"Run make all on staging environment"
"What make targets are available for testing?"
"Show me the deployment workflow for staging"
```

### Container Information

```
"What's the IP for milvus in test?"
"List all containers"
"Get info about the agent container"
"What services run on ingest-lxc?"
```

### Logs & Troubleshooting

```
"Get the last 100 lines of logs for search-api on milvus-lxc"
"Check the status of ingest-worker on ingest-lxc"
"Run 'systemctl restart search-api' on milvus container"
```

### Documentation

```
"Show me the architecture documentation"
"Search documentation for 'GPU passthrough'"
"Get the ingestion architecture doc"
```

## Container Reference

### Production (10.96.200.x)

| Container | ID | IP | Key Services |
|-----------|----|----|--------------|
| proxy-lxc | 200 | 10.96.200.200 | nginx |
| apps-lxc | 201 | 10.96.200.201 | ai-portal, agent-manager |
| agent-lxc | 202 | 10.96.200.202 | agent-api |
| pg-lxc | 203 | 10.96.200.203 | postgresql |
| milvus-lxc | 204 | 10.96.200.204 | milvus, search-api |
| files-lxc | 205 | 10.96.200.205 | minio |
| ingest-lxc | 206 | 10.96.200.206 | ingest-api, ingest-worker, redis |
| litellm-lxc | 207 | 10.96.200.207 | litellm |
| vllm-lxc | 208 | 10.96.200.208 | vllm, vllm-embedding, colpali |
| ollama-lxc | 209 | 10.96.200.209 | ollama |
| authz-lxc | 210 | 10.96.200.210 | authz |

### Test (10.96.201.x)

Same containers with ID + 100 and IP in 201 subnet.

## Make Targets

### Deployment
`all`, `files`, `pg`, `milvus`, `search`, `search-api`, `agent`, `ingest`, `apps`, `nginx`, `authz`, `litellm`, `vllm`, `colpali`

### App Deployment
`deploy-apps`, `deploy-ai-portal`, `deploy-agent-manager`, `deploy-doc-intel`, `deploy-foundation`, `deploy-project-analysis`, `deploy-innovation`

### Testing
`test-all`, `test-ingest`, `test-search`, `test-agent`, `test-authz`, `test-apps`, `test-extraction-simple`, `test-extraction-llm`, `test-extraction-marker`, `test-extraction-colpali`

### Verification
`verify`, `verify-health`, `verify-smoke`

## Development

### Project Structure

```
mcp-server/
├── src/
│   └── index.ts          # Main server implementation
├── dist/                 # Compiled output (generated)
├── package.json
├── tsconfig.json
├── README.md
└── OVERVIEW.md
```

### Development Workflow

```bash
# Install dependencies
npm install

# Watch mode (rebuilds on changes)
npm run dev

# Build for production
npm run build

# Test the server
node dist/index.js
```

## Troubleshooting

### SSH Connection Failed
- Verify SSH key is configured for Proxmox host
- Check Proxmox host IP is correct (default: 10.96.200.1)
- Ensure network connectivity to Proxmox

### Make Target Not Found
- Use `list_make_targets` to see available targets
- Check spelling of target name

### Container Not Found
- Container names can be partial (e.g., "milvus" matches "milvus-lxc")
- Use `list_containers` to see all available containers

## Version History

- **v3.0.0** (2026-01-17): **BREAKING CHANGE** - Renamed "test" environment to "staging":
  - All API parameters: `environment: 'test'` → `environment: 'staging'`
  - All inventory references: `inventory/test` → `inventory/staging`
  - This aligns with the actual Ansible inventory structure where the pre-production environment is called "staging"
  - TEST-* container prefix remains unchanged (refers to staging environment containers)
  - Updated all tools, prompts, and documentation to use "staging" terminology
- **v2.2.0** (2026-01-16): Added comprehensive testing/deployment tools and prompts for AI agents:
  - New tools: `run_docker_tests`, `run_remote_tests`, `run_container_tests`, `docker_control`, `init_test_databases`, `check_test_databases`, `get_makefile_help`, `get_testing_guide`
  - New prompts: `testing_workflow`, `deployment_workflow`, `docker_development`
  - Updated docs structure to reflect current organization (development/session-notes, etc.)
- **v2.1.0** (2026-01-16): Updated documentation paths to match refactored docs structure
- **v2.0.0** (2025-12-21): Added git operations, make target execution, enhanced container info, service endpoints
- **v1.0.0** (2025-11-06): Initial release with documentation and SSH commands

## Related Documentation

- [OVERVIEW.md](./OVERVIEW.md) - Non-technical overview
- [MCP Specification](https://modelcontextprotocol.io/)
- [Busibox Organization Rules](../../.cursor/rules/)
- [CLAUDE.md](../../CLAUDE.md) - Busibox quick start guide

## License

Part of the Busibox project. See project root for license information.
