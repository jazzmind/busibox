# Busibox MCP Server

**Created**: 2025-11-06  
**Updated**: 2025-12-21  
**Version**: 2.0.0  
**Status**: Active  
**Category**: Tools

A Model Context Protocol (MCP) server that provides AI coding agents and maintainers with easy access to Busibox documentation, scripts, infrastructure operations, and project structure.

## Overview

This MCP server exposes Busibox's organizational structure and operations through a standardized protocol that AI coding assistants (like Claude, Cursor, etc.) can use to:

- **Pull latest code** on Proxmox host
- **Run make targets** for deployment and testing
- **Browse documentation** by category
- **Search documentation** by keyword
- **Get container/service info** including IPs and ports
- **Execute SSH commands** on Proxmox and containers
- **View container logs** via journalctl
- **Get script information** including purpose, usage, and execution context
- **Get guided assistance** for common tasks

## Features

### Resources (10+)

The server exposes the following resources:

- `busibox://docs/{category}` - Browse documentation by category
  - architecture, deployment, configuration, troubleshooting, reference, guides, session-notes, development
- `busibox://docs/all` - Complete documentation index
- `busibox://scripts/index` - Index of all scripts by execution context
- `busibox://rules` - Project organization rules
- `busibox://architecture` - All architecture documents combined
- `busibox://quickstart` - Quick start guide (CLAUDE.md)
- `busibox://containers` - Complete container map with IPs for test/production
- `busibox://make-targets` - Available make targets with descriptions

### Tools (15)

#### Git & Deployment Tools

| Tool | Description |
|------|-------------|
| `git_pull_busibox` | Pull latest code on Proxmox host (supports branch, reset --hard) |
| `git_status` | Check git status of busibox repo on Proxmox |
| `run_make_target` | Run a make target with environment (test/production) |
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

### Prompts (7)

Guided assistance for common tasks:

1. **deploy_service** - Guide for deploying a service
2. **troubleshoot_issue** - Guide for troubleshooting issues
3. **add_service** - Guide for adding a new service
4. **create_documentation** - Guide for creating documentation
5. **run_tests** - Guide for running tests
6. **deploy_app** - Guide for deploying applications
7. **update_and_deploy** - Guide for pulling code and deploying

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
"Run make all on test environment"
"What make targets are available for testing?"
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
| apps-lxc | 201 | 10.96.200.201 | ai-portal, agent-client |
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
`deploy-apps`, `deploy-ai-portal`, `deploy-agent-client`, `deploy-doc-intel`, `deploy-foundation`, `deploy-project-analysis`, `deploy-innovation`

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

- **v2.0.0** (2025-12-21): Added git operations, make target execution, enhanced container info, service endpoints
- **v1.0.0** (2025-11-06): Initial release with documentation and SSH commands

## Related Documentation

- [OVERVIEW.md](./OVERVIEW.md) - Non-technical overview
- [MCP Specification](https://modelcontextprotocol.io/)
- [Busibox Organization Rules](../../.cursor/rules/)
- [CLAUDE.md](../../CLAUDE.md) - Busibox quick start guide

## License

Part of the Busibox project. See project root for license information.
