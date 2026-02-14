# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and Cursor AI when working with code in this repository.

## Project Overview

**Busibox** is a local LLM infrastructure platform that provides secure file storage, automated document processing with embeddings, semantic search via RAG (Retrieval Augmented Generation), and AI agent operations—all running on isolated containers (Docker or LXC) with role-based access control.

## ⚠️ CRITICAL: Always Use `make` Commands

**NEVER run `docker compose`, `docker`, or `ansible-playbook` commands directly.**

All service operations MUST go through the unified `make` interface because:
- Secrets are injected from Ansible Vault at runtime
- Environment is auto-detected from state files
- Works identically for Docker and Proxmox backends

See `.cursor/rules/010-make-commands.md` for complete details.

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
make install SERVICE=authz,agent,ingest

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
- **APIs**: `authz`, `agent`, `ingest`, `search`, `deploy`, `docs`, `embedding`
- **LLM**: `litellm`, `ollama`, `vllm`
- **Frontend**: `core-apps`, `nginx`
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
# Docker testing
make test-docker SERVICE=authz

# Remote testing (against staging/production)
make test-local SERVICE=agent INV=staging

# Interactive test menu
make test
```

**Proxmox Host Setup** (run ON Proxmox host as root):
```bash
cd /root/busibox/provision/pct
bash create_lxc_base.sh production  # or: staging
```

**Core App Runtime Operations** (from `provision/ansible/`):
```bash
# Deploy/update core app at runtime (no container rebuild)
make install SERVICE=ai-portal              # Deploy latest from main
make install SERVICE=ai-portal REF=v1.2.3   # Deploy specific version

# Manage core app processes
make app-status                             # Show all app status
make app-restart SERVICE=ai-portal          # Restart app
make app-stop SERVICE=agent-manager         # Stop app
make app-start SERVICE=agent-manager        # Start app
make app-logs SERVICE=ai-portal             # View logs

# Nginx operations
make nginx-reload                           # Reload nginx config

# Debug access
make core-apps-shell                        # Open shell in core-apps container
```

### MCP Server for Cursor

**Busibox MCP Server** provides structured access to documentation and scripts for Cursor:

```bash
# Quick setup (shows Cursor configuration):
cd tools/mcp-server
bash setup.sh

# Or install manually:
npm install && npm run build
```

**What it provides:**
- Browse documentation by category
- Search documentation by keyword
- Get script information and usage
- Find scripts by execution context
- Guided assistance for common tasks (deployment, troubleshooting, etc.)

**Usage in Cursor:**
- "Show me the architecture documentation"
- "Search docs for GPU passthrough"
- "Tell me about deploy-ai-portal.sh"
- "How do I deploy agent-lxc to test?"

**Documentation:**
- Setup: `tools/mcp-server/README.md`
- Reference: `docs/developers/reference/mcp-server.md`

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
- **apps-lxc** (202): nginx and Next.js apps
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
- **Secrets**: Ansible vault

### Deployment Architecture

**Unified Deploy API**: All application deployments (core and user apps) go through the Deploy API service:

```
┌─────────────────┐     ┌──────────────┐     ┌──────────────────┐
│   AI Portal     │────▶│  Deploy API  │────▶│  core-apps       │
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

**Core Apps (ai-portal, agent-manager)**:
- Run in `core-apps` container
- Managed by supervisord (Docker) or systemd (Proxmox)
- Deployed via `make install SERVICE=ai-portal`

**User Apps**:
- Run in `user-apps` container
- Deployed via Deploy API or AI Portal Admin UI
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
   - Secrets in `provision/ansible/roles/secrets/vars/vault.yml`
   - Never commit unencrypted secrets

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



