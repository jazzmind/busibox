# Implementation Plan: Application Services Deployment

**Branch**: `002-deploy-app-servers` | **Date**: 2025-10-15 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/002-deploy-app-servers/spec.md`

## Summary

This feature implements the deployment and orchestration layer for application services on top of the existing busibox infrastructure (spec 001). It provides:

1. **Agent-server deployment** from GitHub releases using the existing deploywatch mechanism
2. **Centralized application configuration** with secure secrets management
3. **NGINX reverse proxy** for SSL-terminated HTTP/HTTPS traffic routing (subdomain and path-based)
4. **Main portal deployment** (cashman) for authentication and application access
5. **Agent-client deployment** for agent administration

The technical approach leverages existing infrastructure components (deploywatch, Ansible, LXC containers) and adds configuration management, NGINX routing, and application-specific deployment roles.

## Technical Context

**Language/Version**: Bash 5.x (scripts), Ansible 2.14+ (configuration management), NGINX 1.18+ (reverse proxy)  
**Primary Dependencies**: 
- Existing: Ansible, deploywatch systemd timer, PM2 (from node_common role), PostgreSQL  
- New: NGINX, certbot (Let's Encrypt), jq (JSON processing), envsubst (variable substitution)  
**Storage**: 
- Application config: `/etc/busibox/apps.yml` (application definitions)
- Secrets: `/etc/busibox/secrets/` (encrypted files, Ansible vault)
- SSL certificates: `/etc/letsencrypt/` (Let's Encrypt/certbot)
- NGINX configs: `/etc/nginx/sites-available/`, `/etc/nginx/sites-enabled/`  
**Testing**: 
- Ansible check mode for idempotency
- Health endpoint checks (curl-based)
- SSL certificate validation
- End-to-end deployment tests (existing test-infrastructure.sh extended)  
**Target Platform**: Debian 12 (LXC containers on Proxmox)  
**Project Type**: Infrastructure automation (shell scripts + Ansible roles)  
**Performance Goals**: 
- New app deployment within 5 minutes
- NGINX config reload < 1 second with zero connection drops
- SSL certificate renewal automated (certbot)  
**Constraints**: 
- Must work with existing 001 infrastructure
- Cannot break existing deploywatch mechanism
- Must support both subdomain and path-based routing
- Secrets must not appear in logs or git history  
**Scale/Scope**: 
- Support 5-10 applications initially
- 100 concurrent users across all apps
- Single NGINX instance (can scale horizontally later)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

### ✅ I. Infrastructure as Code
- **Compliant**: All NGINX configs, application definitions, and secrets management via Ansible roles
- **Implementation**: New Ansible roles in `provision/ansible/roles/nginx/` and `provision/ansible/roles/app_deployer/`
- **Version Control**: Application configs in `provision/ansible/group_vars/`, secrets via Ansible vault

### ✅ II. Service Isolation & Role-Based Security
- **Compliant**: Applications run in isolated containers (apps-lxc, openwebui-lxc)
- **Implementation**: NGINX runs in separate container (openwebui-lxc) acting as edge proxy
- **Security**: Agent-server remains internal-only (no NGINX route), applications use portal authentication

### ✅ III. Observability & Debuggability
- **Compliant**: All deployment activities logged via systemd journal
- **Implementation**: 
  - Deploywatch logs all GitHub release checks and deployments
  - NGINX access/error logs for all applications
  - Application health checks in deployment verification
- **Traceability**: Deployment events include timestamp, app name, version, success/failure

### ✅ IV. Extensibility & Modularity
- **Compliant**: New applications added via config file update, no code changes
- **Implementation**: 
  - `apps.yml` defines GitHub repo, container, routing, secrets per app
  - Ansible role reads config and generates deploywatch jobs + NGINX routes
  - Idempotent deployments (re-running playbook is safe)

### ✅ V. Test-Driven Infrastructure
- **Compliant**: Extended `test-infrastructure.sh` with application deployment tests
- **Implementation**:
  - Health check validation after deployment
  - SSL certificate verification
  - Routing test (subdomain and path)
  - Rollback procedure documented (revert config, re-run Ansible)

### ✅ VI. Documentation as Contract
- **Compliant**: Updated QUICKSTART.md with application deployment steps
- **Implementation**:
  - `apps.yml` serves as application registry documentation
  - NGINX configuration documented in role README
  - OpenAPI specs for agent-server in `/contracts/` (Phase 1)

### ✅ VII. Simplicity & Pragmatism
- **Compliant**: Uses standard tools (NGINX, Ansible, certbot)
- **Justification**: 
  - No custom service discovery (static config file)
  - No Kubernetes/complex orchestration (Ansible + PM2 sufficient for scale)
  - Let's Encrypt for SSL (proven, free, automated)
  - Simple file-based secrets (encrypted at rest via Ansible vault)

**GATE RESULT**: ✅ **PASSED** - All principles satisfied, no violations requiring justification

## Project Structure

### Documentation (this feature)

```
specs/002-deploy-app-servers/
├── plan.md              # This file
├── research.md          # Phase 0: Research SSL automation, secrets management, NGINX routing patterns
├── data-model.md        # Phase 1: Application config schema, NGINX vhost model, secrets structure
├── quickstart.md        # Phase 1: How to add/deploy applications, manage secrets, configure routing
├── contracts/           # Phase 1: OpenAPI spec for agent-server API (if not already exists)
│   └── agent-api.yaml
└── tasks.md             # Phase 2: Detailed implementation tasks (created by /speckit.tasks)
```

### Source Code (repository root)

```
provision/
├── pct/
│   └── vars.env                    # [EXISTING] Container configuration
├── ansible/
    ├── group_vars/
    │   ├── all.yml                 # [NEW] Global variables (domain, SSL email)
    │   └── apps.yml                # [NEW] Application definitions (GitHub repos, routing, secrets)
    ├── roles/
    │   ├── nginx/                  # [NEW] NGINX reverse proxy role
    │   │   ├── tasks/main.yml      # Install NGINX, configure SSL, create vhosts
    │   │   ├── templates/
    │   │   │   ├── nginx.conf.j2   # Main NGINX config
    │   │   │   ├── vhost.conf.j2   # Virtual host template (subdomain)
    │   │   │   └── location.conf.j2 # Location block template (path routing)
    │   │   └── handlers/main.yml   # NGINX reload handler
    │   ├── app_deployer/           # [NEW] Application deployment role
    │   │   ├── tasks/main.yml      # Read apps.yml, generate deploywatch jobs, create .env files
    │   │   ├── templates/
    │   │   │   ├── deploywatch-app.sh.j2  # Per-app deploywatch script
    │   │   │   └── app.env.j2      # Application environment file
    │   │   └── handlers/main.yml   # PM2 restart handler
    │   ├── secrets/                # [NEW] Secrets management role
    │   │   ├── tasks/main.yml      # Create secrets directory, deploy encrypted secrets
    │   │   └── vars/
    │   │       └── vault.yml       # [ENCRYPTED] Ansible vault with all secrets
    │   └── [existing roles...]     # From spec 001
    ├── site.yml                    # [UPDATED] Add nginx, app_deployer, secrets roles
    └── Makefile                    # [UPDATED] Add deploy-apps, verify-apps targets

srv/
├── agent/                          # [EXISTING] Agent-server (deployed from GitHub)
├── ingest/                         # [EXISTING] Ingest worker (deployed from GitHub)
└── apps/                           # [NEW] Application deployment directory
    ├── cashman/                    # [DEPLOYED] Main portal
    ├── agent-client/               # [DEPLOYED] Agent admin interface
    └── [future apps]/              # [EXTENSIBLE] Additional applications

test-infrastructure.sh              # [UPDATED] Add application deployment tests
QUICKSTART.md                       # [UPDATED] Document application deployment workflow
```

**Structure Decision**: This feature extends the existing infrastructure provisioning structure from spec 001. New Ansible roles are added for NGINX (openwebui-lxc container), application deployment orchestration (runs on all app containers), and secrets management (centralized). The `/srv/apps/` directory structure mirrors the dynamic deployment model where applications are cloned from GitHub releases. Configuration remains centralized in Ansible `group_vars/` following IaC principles.

## Complexity Tracking

*No violations - all complexity justified within Constitution principles.*

| Aspect | Complexity Level | Justification |
|--------|-----------------|---------------|
| NGINX Configuration | Moderate (templates + dynamic vhosts) | Required for subdomain + path routing as per FR-014, FR-015 |
| Secrets Management | Moderate (Ansible vault + file encryption) | Required for FR-008 (secrets not in logs/git) |
| Application Config Schema | Low (YAML with standard fields) | Follows twelve-factor app principles (FR-030) |
| Deploywatch Extension | Low (per-app scripts) | Reuses existing deploywatch mechanism (FR-001) |

**Re-evaluation after Phase 1 design**: TBD (will verify no new complexity introduced)
