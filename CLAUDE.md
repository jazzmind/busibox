# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and Cursor AI when working with code in this repository.

## Project Overview

**Busibox** is a local LLM infrastructure platform that provides secure file storage, automated document processing with embeddings, semantic search via RAG (Retrieval Augmented Generation), and AI agent operations—all running on isolated containers (Docker or LXC) with role-based access control.

## ⚠️ CRITICAL: Service Operations

**NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly.**

### For AI agents (you)

Prefer the `mcp-admin` MCP server tools when available — they handle SSH and targeting. When using `make` targets directly, most operations require the vault password via `ANSIBLE_VAULT_PASSWORD` environment variable. If the vault password is not available, check for `~/.busibox-vault-pass-*` files on the target host, or ask the user for the vault password.

### For humans

All service operations go through the **Busibox CLI** (`busibox`) — an interactive terminal UI that handles vault decryption, SSH connectivity, and service management. Humans should never need to run `make` targets directly.

### Why

- Secrets are injected from Ansible Vault at runtime
- Environment is auto-detected from state files
- Works identically for Docker, Proxmox, and Kubernetes backends
- The CLI and MCP servers handle vault password management automatically

See `.cursor/rules/010-make-commands.md` for the `make` target reference, and `docs/developers/reference/mcp-and-make-internals.md` for the vault password flow.

## Quick Start

### Key Documentation
- **Architecture**: `docs/developers/architecture/` - System design and components
- **Administrators**: `docs/administrators/` - Deployment, configuration, troubleshooting
- **Users**: `docs/users/` - End-user platform guides
- **Developers**: `docs/developers/` - Technical docs, API guides, reference
- **Testing**: `TESTING.md` - Testing strategy and procedures
- **Make Commands**: `.cursor/rules/010-make-commands.md` - Service management reference
- **Doc Organization**: `docs/README.md` - Documentation structure guide

### Common Commands (from repo root)

**Deploy Services**:
```bash
# Deploy a single service
make install SERVICE=authz

# Deploy multiple services
make install SERVICE=authz,agent,data

# Deploy service groups
make install SERVICE=apis            # All API services
make install SERVICE=infrastructure  # postgres, redis, minio, milvus
make install SERVICE=all             # Everything
```

**Manage Running Services**:
```bash
# Restart a service
make manage SERVICE=authz ACTION=restart

# Stop/start services
make manage SERVICE=authz ACTION=stop
make manage SERVICE=authz ACTION=start

# View logs (follows)
make manage SERVICE=authz ACTION=logs

# Check status
make manage SERVICE=authz,postgres ACTION=status

# Full rebuild and redeploy
make manage SERVICE=authz ACTION=redeploy
```

**Service Reference**:
- **Infrastructure**: `postgres`, `redis`, `minio`, `milvus`
- **APIs**: `authz`, `agent`, `data`, `search`, `deploy`, `docs`, `embedding`
- **LLM**: `litellm`, `ollama`, `vllm`
- **Frontend**: `proxy`, `core-apps` (with `nginx` alias support)
- **Groups**: `infrastructure`, `apis`, `llm`, `frontend`, `all`

**Interactive Menus**:
```bash
make                     # Main launcher menu
make install             # Installation wizard (no SERVICE=)
make manage              # Service management menu (no SERVICE=)
make test                # Testing menu
```

**Testing**:
```bash
# Docker testing (local development)
make test-docker SERVICE=authz

# Run specific test file or test function
make test-docker SERVICE=agent ARGS="tests/integration/test_schema_extraction.py::test_clean_markdown_for_extraction"

# Run multiple specific tests (space-separated paths)
make test-docker SERVICE=agent ARGS="tests/integration/test_file.py::test_one tests/integration/test_file.py::test_two"

# Run a test directory
make test-docker SERVICE=agent ARGS="tests/unit"

# Include slow/GPU tests (FAST=1 is default)
make test-docker SERVICE=agent ARGS="tests/integration/test_slow.py" FAST=0

# Remote testing (against staging/production)
make test-local SERVICE=agent INV=staging

# Interactive test menu
make test
```

**Important**: Use `ARGS=` (not `PYTEST_ARGS=`) to pass pytest arguments through the Makefile. When targeting specific tests by path, prefix with `tests/` so the script uses it as the test path directly and skips the default `-m` filter. Quoting `-k` filters through `make` is fragile; prefer full `tests/path::test_name` targeting instead.

**Proxmox Host Setup** (run ON Proxmox host as root):
```bash
cd /root/busibox/provision/pct
bash create_lxc_base.sh production  # or: staging
```

**Core App Runtime Operations** (from `provision/ansible/`):
```bash
# Deploy/update core app at runtime (no container rebuild)
make install SERVICE=busibox-portal              # Deploy latest from main
make install SERVICE=busibox-portal REF=v1.2.3   # Deploy specific version

# Manage core app processes
make app-status                             # Show all app status
make app-restart SERVICE=busibox-portal          # Restart app
make app-stop SERVICE=busibox-agents         # Stop app
make app-start SERVICE=busibox-agents        # Start app
make app-logs SERVICE=busibox-portal             # View logs

# Nginx operations
make nginx-reload                           # Reload nginx config

# Debug access
make core-apps-shell                        # Open shell in core-apps container
```

### MCP Servers for Cursor

Busibox provides **three focused MCP servers** for different workflows. Add one or more to Cursor MCP settings:

| Server | Audience | Use Case |
|--------|----------|----------|
| **mcp-core-dev** | Core developers | Build, test, debug busibox services |
| **mcp-app-builder** | App developers | Build Next.js apps for busibox deployment |
| **mcp-admin** | Operators | Deploy, manage, troubleshoot (Claude Code/Cowork) |

**Build all servers** (also writes config to `.cursor/`):
```bash
make mcp
```

**Config auto-generated:**
- **Cursor:** `.cursor/mcp.json` — Cursor loads this automatically when you open the project
- **Claude Desktop:** `.cursor/claude-mcp.json` (template) and `.cursor/CLAUDE_MCP_README.md` — copy `mcpServers` into your Claude config, replacing `__BUSIBOX_ROOT__` with your busibox path

**What each provides:**
- **Core Dev**: Docs, scripts, testing (Docker/remote/container), container logs, make help
- **App Builder**: busibox-app exports, auth patterns, busibox-template reference, service endpoints
- **Admin**: Deployment (make targets), SSH/Proxmox, git, container management. Destructive ops require `confirm: true`

**Environments:** All servers support `staging` (10.96.201.x) and `production` (10.96.200.x).

**Documentation:** `tools/mcp-server/README.md` (deprecated - points to new servers)

## AI Agent Rules

This project uses structured rules to ensure consistency. **All rules are in `.cursor/rules/`**:

### Organization Rules

**Documentation**: See `.cursor/rules/001-documentation-organization.md`
- All documentation goes in `docs/` with category subdirectories
- Use `kebab-case` for filenames
- Include metadata at top of each document
- Follow category-based organization

**Scripts**: See `.cursor/rules/002-script-organization.md`
- `scripts/` - Run from admin workstation (orchestration, deployment)
- `provision/pct/` - Run on Proxmox host (container lifecycle, host config)
- `provision/ansible/roles/*/files/` - Static scripts for containers
- `provision/ansible/roles/*/templates/` - Templated scripts for containers

### Quick Decision Guides

**Creating Documentation:**
1. Determine audience (administrators/developers/users)
2. Place in `docs/{audience}/` with appropriate subdirectory
3. Use descriptive `kebab-case` filename
4. Include docs-api frontmatter (title, category, order, description, published)

**Creating Scripts:**
1. Determine execution context (Proxmox host / admin workstation / inside container)
2. Follow decision tree in script organization rules
3. Use appropriate prefix (deploy/setup/test/create/configure/check/list)
4. Include comprehensive header with context and usage

## Project Structure

```
busibox/
├── .cursor/rules/          # AI agent rules (READ THESE!)
├── docs/                   # All documentation (organized by audience)
│   ├── administrators/    # Deployment, configuration, troubleshooting
│   ├── developers/        # Architecture, API guides, reference
│   ├── users/             # End-user platform guides
│   └── archive/           # Historical/outdated content
├── scripts/                # Admin workstation scripts
├── provision/
│   ├── pct/               # Proxmox host scripts
│   └── ansible/           # Ansible configuration management
│       ├── inventory/     # Environment configurations
│       └── roles/         # Service roles
├── srv/                   # Service source code
│   ├── agent/            # Agent API (FastAPI)
│   ├── data/             # Data API and Ingest Worker
│   ├── docs/             # Docs API
│   └── deploy/           # Deploy API
├── specs/                 # Project specifications
└── tools/                 # Utility tools
```

### Manager Container

All `make install`, `make manage`, and orchestration commands run inside an
ephemeral **manager container** by default. This ensures consistent tool versions
regardless of the host OS:

```bash
make install SERVICE=authz              # Runs inside manager (default)
make install SERVICE=authz USE_MANAGER=0  # Direct host execution (legacy)
make build-manager                      # Rebuild the manager image
```

The manager container includes Ansible, Docker CLI, SSH client, vault tools, and
all required dependencies. It mounts the Docker socket, vault password files, SSH
keys, and the busibox repo from the host. If Docker is unavailable (bare-metal
Proxmox), it automatically falls back to direct host execution.

**Key files**:
- `provision/docker/manager.Dockerfile` - Manager image definition
- `scripts/make/manager-run.sh` - Runner script (handles mounts, env vars)
- `docker-compose.yml` - Manager service definition (profiles: ["manager"])

## Technology Stack

### Infrastructure
- **Hypervisor**: Proxmox VE (LXC containers)
- **Provisioning**: Bash scripts in `provision/pct/`
- **Configuration**: Ansible (provision/ansible/)
- **Service Management**: systemd

### Data Layer
- **Object Storage**: MinIO (S3-compatible)
- **Relational DB**: PostgreSQL 15+ with RLS
- **Vector DB**: Milvus 2.3+
- **Queue**: Redis Streams

### Application Layer
- **Agent API**: FastAPI (Python 3.11+)
- **Ingest Worker**: Python 3.11+
- **LLM Gateway**: liteLLM
- **App Servers**: Next.js (Node 18+)
- **Reverse Proxy**: nginx

## Key Concepts

### Container Architecture
Each service runs in an isolated LXC container:
- **files-lxc** (205): MinIO for S3 storage
- **pg-lxc** (203): PostgreSQL database
- **milvus-lxc** (204): Milvus vector database
- **agent-lxc** (207): Agent API and liteLLM
- **data-lxc** (206): Data API, Worker, and Redis
- **apps-lxc** (202): Next.js apps
- **proxy-lxc** (200): Main reverse proxy
- **LLM containers** (210-219): Ollama, vLLM, etc.

### Network
- **Subnet**: 10.96.200.0/21
- **Gateway**: 10.96.200.1
- **Internal**: Container-to-container communication
- **External**: nginx reverse proxy with SSL

### Security
- **Authentication**: JWT tokens from Agent API
- **Authorization**: Role-based (RBAC)
- **Data Security**: PostgreSQL Row-Level Security (RLS)
- **Network**: Container isolation, ufw firewall
- **Secrets**: Ansible Vault with encrypted vault key management (see below)

### Vault Password Architecture

Vault passwords are managed through the `busibox` CLI with a dual-key encrypted system:

```
~/.busibox/vault-keys/{profile}.enc   ← AES-256-GCM encrypted vault password
```

- **Vault password**: A random 32-char string generated during setup, never shown to anyone
- **Admin master password (A)**: Encrypts the vault password on the admin workstation
- **Remote user password (B)**: Encrypts the same vault password on the remote host
- **Key derivation**: Argon2id(master_password, salt) → AES-256 key
- **Remote delivery**: Vault password piped via SSH stdin; never written to disk on remote

**Flow**:
1. First setup: CLI generates vault password, encrypts vault file, saves encrypted vault keys
2. Admin deploys: Types master password A → decrypts vault password → pipes via SSH
3. Remote user updates: Types password B → decrypts locally → sets env var for Ansible

**Backward compatibility**: Legacy plaintext files (`~/.busibox-vault-pass-{prefix}`) are auto-migrated on first use.

**Shell integration**: `ANSIBLE_VAULT_PASSWORD` env var is supported by `vault.sh` and `service-deploy.sh` via `scripts/lib/vault-pass-from-env.sh`.

### Deployment Architecture

**Unified Deploy API**: All application deployments (core and user apps) go through the Deploy API service:

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐
│ Busibox Portal  │────▶│  Deploy API  │────▶│  core-apps       │
│  Admin UI       │     │  (Python)    │     │  (supervisord)   │
└─────────────────┘     └──────────────┘     └──────────────────┘
                              │
                              ▼
                        ┌──────────────────┐
                        │  user-apps       │
                        │  (systemd)       │
                        └──────────────────┘
```

**Runtime Installation Pattern**:
- Apps are NOT baked into Docker images
- Apps are cloned and built at runtime into persistent volumes
- App updates don't require container rebuilds
- Consistent approach for Docker and Proxmox environments

**Core Apps (busibox-portal, busibox-agents)**:
- Run in `core-apps` container
- Managed by supervisord (Docker) or systemd (Proxmox)
- Deployed via `make install SERVICE=busibox-portal`

**User Apps**:
- Run in `user-apps` container
- Deployed via Deploy API or Busibox Portal Admin UI
- Sandboxed for security

## Development Workflow

### Adding a New Service

1. **Create container** in `provision/pct/vars.env` (Proxmox only):
   ```bash
   CT_NEWSERVICE=208
   IP_NEWSERVICE=10.96.200.31
   ```

2. **Update creation script** `provision/pct/create_lxc_base.sh` (Proxmox only)

3. **Create Ansible role**: `provision/ansible/roles/newservice/`

4. **Add to site.yml, docker.yml, and inventory**

5. **Add to Makefile** service mappings

6. **Document** in appropriate category under `docs/`

### Making Changes

1. **Check existing documentation** in `docs/` (organized by category)
2. **Follow organization rules** in `.cursor/rules/`
3. **Test locally** if possible
4. **Validate on staging environment** before production
5. **Update documentation** in correct category
6. **Follow naming conventions** from rules

### Deploying Changes

1. **Deploy to your service**:
   ```bash
   make install SERVICE=myservice
   ```

2. **Check status**:
   ```bash
   make manage SERVICE=myservice ACTION=status
   ```

3. **View logs if needed**:
   ```bash
   make manage SERVICE=myservice ACTION=logs
   ```

## Error Handling

### Service Issues (Docker or Proxmox)

```bash
# Check service status
make manage SERVICE=authz ACTION=status

# View service logs
make manage SERVICE=authz ACTION=logs

# Restart a service
make manage SERVICE=authz ACTION=restart

# Full redeploy (rebuild + restart with fresh secrets)
make manage SERVICE=authz ACTION=redeploy
```

### Proxmox-Specific Issues

```bash
# Check container status (on Proxmox host):
pct status <CTID>

# Enter container (on Proxmox host):
pct enter <CTID>

# Check service inside container:
systemctl status <service>
journalctl -u <service> -n 50 --no-pager
```

### "Password authentication failed" Errors

This usually means secrets weren't injected. **Always use make commands**:
```bash
# Wrong - bypasses secrets
docker compose up -d authz-api  # ❌

# Correct - injects secrets from vault
make manage SERVICE=authz ACTION=redeploy  # ✅
```

## Best Practices

### When Creating Files

1. **Documentation**:
   - Place in appropriate `docs/{audience}/` directory (administrators/developers/users)
   - Use descriptive `kebab-case` names
   - Include docs-api frontmatter (title, category, order, description, published)
   - Link to related docs

2. **Scripts**:
   - Determine execution context first
   - Place in correct directory
   - Include comprehensive header
   - Add error handling (`set -euo pipefail`)
   - Make executable (`chmod +x`)

3. **Configuration**:
   - Environment-specific configs in `inventory/{env}/group_vars/`
   - Secrets in `provision/ansible/roles/secrets/vars/vault.{prefix}.yml`
   - Vault keys in `~/.busibox/vault-keys/{profile}.enc` (AES-256-GCM encrypted)
   - Never commit unencrypted secrets or plaintext vault password files

### When Modifying Infrastructure

1. **Test first** - Always test on test environment
2. **Document changes** - Update appropriate docs
3. **Validate** - Run validation scripts
4. **Rollback plan** - Know how to revert changes

### When Troubleshooting

1. **Check documentation** in `docs/administrators/08-troubleshooting.md`
2. **Review logs** using journalctl
3. **Validate configuration** matches environment
4. **Document solution** in `docs/administrators/`

## References

- **Rules**: `.cursor/rules/` - AI agent organization rules
- **Doc Organization**: `docs/README.md` - Documentation structure
- **Architecture**: `docs/developers/architecture/` - System design
- **Deployment**: `docs/administrators/` - Deployment and operations
- **Testing**: `TESTING.md` - Testing strategy
- **Ansible Setup**: `provision/ansible/SETUP.md` - Ansible usage

## Questions?

If you're unsure about:
- **Where to place a file** → Check `docs/README.md` and `.cursor/rules/`
- **How to deploy** → Check `docs/administrators/02-install.md`
- **How to configure** → Check `docs/administrators/03-configure.md`
- **System design** → Check `docs/developers/architecture/`
- **Testing** → Check `TESTING.md`

## Important Notes

1. **ALWAYS use `make` commands** - Never run docker/ansible directly
2. **Read the rules** in `.cursor/rules/` before creating files
3. **Follow existing patterns** in the codebase
4. **Test before deploying** to production
5. **Document your changes** in the appropriate docs category
6. **Use descriptive names** that indicate purpose and context
7. **Include context** in script headers and doc metadata

## Key Rules Files

- `.cursor/rules/010-make-commands.md` - **READ THIS FIRST** - Service management
- `.cursor/rules/001-documentation-organization.md` - Where to put docs
- `.cursor/rules/002-script-organization.md` - Where to put scripts
- `.cursor/rules/003-zero-trust-authentication.md` - Auth patterns



