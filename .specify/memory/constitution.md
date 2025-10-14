<!--
Sync Impact Report:
- Version change: Initial creation → 1.0.0
- Modified principles: N/A (initial creation)
- Added sections: All core sections created
- Removed sections: N/A
- Templates requiring updates:
  ✅ plan-template.md - Reviewed, compatible with constitution principles
  ✅ spec-template.md - Reviewed, compatible with constitution principles
  ✅ tasks-template.md - Reviewed, compatible with constitution principles
- Follow-up TODOs: None
-->

# Busibox Infrastructure Constitution

## Core Principles

### I. Infrastructure as Code (NON-NEGOTIABLE)

All infrastructure MUST be defined as code and version controlled. No manual configuration or "snowflake" systems.

**Rules**:
- All Proxmox container creation scripts MUST be versioned in `provision/pct/`
- All service configuration MUST be managed via Ansible roles in `provision/ansible/roles/`
- Environment-specific variables MUST be externalized in `vars.env` or role defaults
- No SSH-ing into containers to "fix" things—changes go through Ansible playbooks

**Rationale**: Manual changes create drift, make systems unreproducible, and lead to "works on my machine" scenarios. IaC ensures consistent, repeatable deployments across environments.

### II. Service Isolation & Role-Based Security

Each service MUST run in its own LXC container with minimal privileges and clear security boundaries.

**Rules**:
- One primary service per container (e.g., MinIO in files-lxc, PostgreSQL in pg-lxc)
- Inter-service communication MUST use network policies and authentication
- PostgreSQL MUST use Row-Level Security (RLS) for multi-tenant data isolation
- API gateway (agent-lxc) MUST enforce RBAC before allowing data access
- Containers MUST NOT have unnecessary network access or privileges

**Rationale**: Defense in depth—if one service is compromised, blast radius is limited. Clear security boundaries make it easier to audit and reason about data flow.

### III. Observability & Debuggability

All services MUST produce structured logs and expose health endpoints. System behavior must be traceable.

**Rules**:
- Every service MUST expose a `/health` endpoint (or equivalent)
- Logs MUST be structured (JSON preferred) with consistent fields: timestamp, level, service, message
- Critical operations (file uploads, embedding creation, user actions) MUST be logged
- MinIO webhook events MUST be traceable through the entire ingestion pipeline
- Failed jobs MUST log errors with enough context for debugging (file path, user ID, error details)

**Rationale**: When things break at 2 AM, you need logs. Structured logging enables automated alerting and quick root-cause analysis.

### IV. Extensibility & Modularity

The system MUST be designed for easy addition of new services, LLM providers, and applications.

**Rules**:
- New LXC containers can be added by creating shell scripts in `provision/pct/` and Ansible roles in `provision/ansible/roles/`
- LLM routing via liteLLM allows adding new providers without code changes
- Application code (agent, ingest worker, apps) MUST be loosely coupled to infrastructure
- Ansible roles MUST be idempotent—running twice produces same result as running once
- Service discovery and configuration MUST use environment variables or config files

**Rationale**: The project aims to be a flexible platform. Tight coupling makes it hard to evolve. Modularity enables experimentation and incremental improvements.

### V. Test-Driven Infrastructure (TDI)

Infrastructure changes MUST be validated before deployment. Smoke tests required for critical services.

**Rules**:
- `provision/ansible/Makefile` targets MUST include validation steps (e.g., `make verify`)
- After provisioning, health endpoints MUST be checked programmatically
- Database migrations MUST have rollback procedures documented
- `tools/milvus_init.py` and similar setup scripts MUST report success/failure clearly
- Breaking changes to service contracts (APIs, schemas) require deprecation warnings

**Rationale**: Infrastructure failures cascade. Testing catches misconfigurations before they cause outages. Rollback plans prevent panic during incidents.

### VI. Documentation as Contract

Documentation MUST be kept in sync with code. It serves as the contract between services and as onboarding material.

**Rules**:
- `README.md` MUST describe the system architecture and link to setup instructions
- `QUICKSTART.md` MUST provide working commands to provision the entire stack
- Each Ansible role MUST document its purpose and configuration variables
- API contracts MUST be documented (OpenAPI preferred for agent-lxc)
- Changes to service interfaces MUST update corresponding documentation in the same commit

**Rationale**: Outdated docs are worse than no docs—they mislead. Documentation as code ensures it evolves with the system.

### VII. Simplicity & Pragmatism

Choose boring, proven technologies. Avoid premature optimization and unnecessary abstractions.

**Rules**:
- Default to standard tools: Ansible, PostgreSQL, MinIO, Milvus, Redis, liteLLM
- No custom service discovery unless proven necessary (flat IP assignment is fine to start)
- No custom message queue unless Redis Streams proves insufficient
- No microservices sprawl—combine related functions in one service until proven bottleneck
- Complexity MUST be justified with concrete requirements (performance, scale, security)

**Rationale**: Over-engineering wastes time and creates maintenance burden. Start simple, evolve when real needs emerge.

## Infrastructure Constraints

### Technology Stack

**Container Platform**: Proxmox LXC containers (privileged only when required for Docker-in-LXC like Milvus)

**Orchestration**: Ansible for configuration management, shell scripts for LXC creation

**Storage**: 
- MinIO (S3-compatible) for file storage
- PostgreSQL for structured data, metadata, user/role management
- Milvus for vector embeddings

**Queue**: Redis Streams for file ingestion jobs

**LLM Gateway**: liteLLM for unified interface to local LLMs (Ollama, vLLM, etc.)

**Monitoring**: systemd for service management, journalctl for logs (future: Prometheus/Grafana if needed)

**Deployment**: `deploywatch` systemd timer for GitHub-release-based auto-deployment

### Network & Security

**Network Model**: Static IP assignment for predictable service discovery (adjust in `provision/pct/vars.env`)

**Authentication**: 
- PostgreSQL: User/role-based authentication with RLS
- MinIO: Access keys and bucket policies
- Agent API: JWT or session-based authentication for RBAC enforcement

**Secrets Management**: Environment files (`.env` per service), Ansible vault for sensitive variables

**Encryption**: 
- TLS for production (nginx reverse proxy)
- Encryption at rest for sensitive data (future: encrypt MinIO buckets, PostgreSQL tablespaces)

### Performance & Scale

**Initial Target**: Single Proxmox host, 5-10 LXC containers, suitable for small teams (10-100 users)

**LLM Performance**: Depends on local hardware (GPUs, CPU cores) and LLM provider configuration

**Ingestion Throughput**: Redis Streams handles moderate load (100s of files per hour). Scale workers horizontally if needed.

**Storage Capacity**: MinIO and Milvus storage limited by host disk. Monitor usage and plan capacity.

## Development Workflow

### Change Process

1. **Feature/Fix Branch**: Create a branch for infrastructure changes (e.g., `feature/add-openwebui-container`)
2. **Update IaC**: Modify Ansible roles, shell scripts, or configuration files
3. **Local Testing**: Test changes in a dev Proxmox environment or VM
4. **Documentation**: Update README, QUICKSTART, or role docs
5. **Code Review**: Peer review for critical infrastructure changes
6. **Deployment**: Apply via Ansible (`make all` or targeted role)
7. **Validation**: Run health checks, verify logs, test end-to-end flow

### Testing Requirements

**Smoke Tests** (MUST pass before declaring deployment successful):
- All health endpoints return 200
- PostgreSQL accepts connections and can query users table
- MinIO console accessible and can list buckets
- Milvus accepts connections (`tools/milvus_init.py` succeeds)
- Agent API responds to authenticated requests
- Ingest worker can process a test file

**Integration Tests** (RECOMMENDED for production):
- End-to-end: Upload file to MinIO → Webhook triggers agent → Job queued → Worker processes → Embeddings in Milvus + metadata in PostgreSQL
- RBAC: Verify user with limited permissions cannot access restricted files

### Deployment Strategy

**Initial Provisioning**: 
1. Run `provision/pct/create_lxc_base.sh` on Proxmox host
2. Run `make all` from Ansible directory

**Updates**: 
- Service code updates: `deploywatch` pulls GitHub releases and restarts systemd services
- Infrastructure changes: Re-run targeted Ansible roles (`make role-name`)
- Database migrations: Run manually with versioned scripts, document in commit message

**Rollback**: 
- Ansible roles are idempotent—revert code and re-run playbook
- Database migrations must have documented rollback SQL
- Container snapshots before major changes (LXC snapshot feature)

## Governance

### Amendment Process

1. **Proposal**: Document proposed change to constitution with rationale
2. **Review**: Team discussion (or maintainer approval for solo projects)
3. **Migration Plan**: If change affects existing infrastructure, document migration steps
4. **Update**: Amend constitution, update templates, update runtime guidance

### Versioning Policy

**Constitution Version Format**: MAJOR.MINOR.PATCH

- **MAJOR**: Backward-incompatible governance changes (e.g., removing a principle, changing tech stack)
- **MINOR**: New principle added, section materially expanded
- **PATCH**: Clarifications, typo fixes, non-semantic refinements

### Compliance & Enforcement

- All infrastructure changes MUST comply with constitution principles
- Violations must be justified in writing and linked to concrete requirements
- Complexity that violates "Simplicity & Pragmatism" must pass through review
- Periodic reviews (quarterly recommended) to ensure constitution remains relevant

### Runtime Guidance

For day-to-day development and AI agent interactions, refer to this constitution as the source of truth for architectural decisions and principles.

**Version**: 1.0.0 | **Ratified**: 2025-10-14 | **Last Amended**: 2025-10-14
