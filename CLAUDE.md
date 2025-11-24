# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) and Cursor AI when working with code in this repository.

## Project Overview

**Busibox** is a local LLM infrastructure platform that provides secure file storage, automated document processing with embeddings, semantic search via RAG (Retrieval Augmented Generation), and AI agent operations—all running on isolated LXC containers with role-based access control.

## Quick Start

### Key Documentation
- **Architecture**: `docs/architecture/architecture.md` - System design and components
- **Deployment**: `docs/deployment/` - Deployment guides and procedures
- **Configuration**: `docs/configuration/` - Setup and configuration guides
- **Testing**: `TESTING.md` - Testing strategy and procedures

### Common Commands

**Proxmox Host Setup** (run on Proxmox host as root):
```bash
cd /root/busibox/provision/pct
# For production:
bash create_lxc_base.sh production
# For test:
bash create_lxc_base.sh test
```

**Service Deployment** (run from admin workstation):
```bash
cd provision/ansible

# Deploy all services to test environment:
make all INV=inventory/test

# Deploy all services to production:
make all

# Deploy specific service:
make milvus              # Deploy Milvus vector database
make search-api          # Deploy search API
make agent               # Deploy agent service
make ingest              # Deploy ingest service
make apps                # Deploy all apps

# Deploy individual applications:
make deploy-ai-portal    # Deploy AI Portal
make deploy-agent-client # Deploy Agent Client
make deploy-doc-intel    # Deploy Doc Intel
make deploy-foundation   # Deploy Foundation
make deploy-project-analysis
make deploy-innovation
```

**Testing**:
```bash
cd provision/ansible

# Interactive test menu (recommended):
make test-menu

# Run specific tests:
make test-ingest         # Test ingest service
make test-search         # Test search service
make test-agent          # Test agent service
make test-apps           # Test applications

# Run extraction strategy tests:
make test-extraction-simple   # Basic PDF extraction
make test-extraction-llm      # LLM-enhanced extraction
make test-extraction-marker   # Marker extraction (GPU)
make test-extraction-colpali  # ColPali visual extraction

# Run with coverage:
make test-ingest-coverage
make test-search-coverage

# Verification:
make verify              # Run all health checks
make verify-health       # Service health checks
make verify-smoke        # Database smoke tests
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
- Reference: `docs/reference/mcp-server.md`
- Usage Guide: `docs/guides/mcp-server-usage.md`

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
1. Determine category (architecture/deployment/configuration/troubleshooting/reference/guides/session-notes)
2. Place in `docs/{category}/`
3. Use descriptive `kebab-case` filename
4. Include metadata header

**Creating Scripts:**
1. Determine execution context (Proxmox host / admin workstation / inside container)
2. Follow decision tree in script organization rules
3. Use appropriate prefix (deploy/setup/test/create/configure/check/list)
4. Include comprehensive header with context and usage

## Project Structure

```
busibox/
├── .cursor/rules/          # AI agent rules (READ THESE!)
├── docs/                   # All documentation (organized by category)
├── scripts/                # Admin workstation scripts
├── provision/
│   ├── pct/               # Proxmox host scripts
│   └── ansible/           # Ansible configuration management
│       ├── inventory/     # Environment configurations
│       └── roles/         # Service roles
├── srv/                   # Service source code
│   ├── agent/            # Agent API (FastAPI)
│   └── ingest/           # Ingest worker (Python)
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
- **ingest-lxc** (206): Worker and Redis
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

## Development Workflow

### Adding a New Service

1. **Create container** in `provision/pct/vars.env`:
   ```bash
   CT_NEWSERVICE=208
   IP_NEWSERVICE=10.96.200.31
   ```

2. **Update creation script** `provision/pct/create_lxc_base.sh`

3. **Create Ansible role**: `provision/ansible/roles/newservice/`

4. **Add to site.yml** and inventory

5. **Document** in appropriate category under `docs/`

### Making Changes

1. **Check existing documentation** in `docs/` (organized by category)
2. **Follow organization rules** in `.cursor/rules/`
3. **Test locally** if possible
4. **Validate on test environment** before production
5. **Update documentation** in correct category
6. **Follow naming conventions** from rules

### Deploying Changes

1. **Test environment first**:
   ```bash
   cd provision/ansible
   make test
   ```

2. **Validate deployment**:
   ```bash
   bash scripts/test-infrastructure.sh
   ```

3. **Production deployment**:
   ```bash
   cd provision/ansible
   make production
   ```

## Error Handling

### Container Issues
```bash
# Check container status:
pct status <CTID>

# Check container logs:
pct enter <CTID>
journalctl -xe
```

### Service Issues
```bash
# SSH into container:
ssh root@<container-ip>

# Check service:
systemctl status <service>
journalctl -u <service> -n 50 --no-pager
```

### Ansible Issues
```bash
# Test connection:
ansible -i inventory/test/hosts.yml all -m ping

# Run playbook with verbose output:
ansible-playbook -i inventory/test/hosts.yml site.yml -vvv
```

## Best Practices

### When Creating Files

1. **Documentation**:
   - Place in appropriate `docs/{category}/` directory
   - Use descriptive `kebab-case` names
   - Include metadata header
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

1. **Check documentation** in `docs/troubleshooting/`
2. **Review logs** using journalctl
3. **Validate configuration** matches environment
4. **Document solution** in troubleshooting docs

## References

- **Rules**: `.cursor/rules/` - AI agent organization rules
- **Architecture**: `docs/architecture/architecture.md` - System design
- **Deployment**: `docs/deployment/` - Deployment procedures
- **Configuration**: `docs/configuration/` - Setup guides
- **Testing**: `TESTING.md` - Testing strategy
- **Ansible Setup**: `provision/ansible/SETUP.md` - Ansible usage

## Questions?

If you're unsure about:
- **Where to place a file** → Check `.cursor/rules/`
- **How to deploy** → Check `docs/deployment/`
- **How to configure** → Check `docs/configuration/`
- **System design** → Check `docs/architecture/architecture.md`
- **Testing** → Check `TESTING.md`

## Important Notes

1. **Read the rules** in `.cursor/rules/` before creating files
2. **Follow existing patterns** in the codebase
3. **Test before deploying** to production
4. **Document your changes** in the appropriate docs category
5. **Use descriptive names** that indicate purpose and context
6. **Include context** in script headers and doc metadata



