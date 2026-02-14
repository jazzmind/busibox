---
title: "Deploy API Architecture"
category: "developer"
order: 20
description: "Deployment orchestration for Busibox applications with two-tier security model"
published: true
---

# Deploy-API Architecture

## Overview

The deploy-api service provides deployment orchestration for Busibox applications. It uses a two-tier security model to handle both trusted (core) and untrusted (user) applications.

## Security Model

### Two Deployment Paths

```
┌─────────────────────────────────────────────────────────────────┐
│                        deploy-api                                │
│                  (FastAPI in container)                         │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┴───────────────────┐
          │                                       │
          ▼                                       ▼
┌─────────────────────────┐           ┌─────────────────────────┐
│   TRUSTED PATH          │           │   UNTRUSTED PATH        │
│   (Core Apps)           │           │   (User Apps)           │
├─────────────────────────┤           ├─────────────────────────┤
│ • Bridge script         │           │ • docker exec           │
│ • Makefile/Ansible      │           │ • Sandboxed container   │
│ • Full host access      │           │ • No host access        │
│ • SSH to Proxmox        │           │ • Resource limits       │
└─────────────────────────┘           └─────────────────────────┘
```

### Core Apps (Trusted)

Core apps are system infrastructure components that require host-level access:

- **ai-portal** - Main user interface
- **agent-manager** - Agent management UI
- **authz-api** - Authentication/authorization
- **ingest-api** - Document ingestion
- **search-api** - Search service
- **agent-api** - AI agent service
- **docs-api** - Documentation service
- **deploy-api** - This deployment service

**Deployment Flow:**
1. deploy-api receives deployment request
2. Identifies app as core app
3. Calls bridge script with make command
4. Bridge script executes: `make deploy-{app} INV=inventory/{env}`
5. Makefile invokes Ansible playbook
6. Ansible deploys via Docker Compose or LXC

### User Apps (Untrusted)

User apps are external applications deployed by users from GitHub:

**Security Measures:**
- Run in isolated `user-apps` container
- Resource limits (CPU: 2 cores, Memory: 2GB)
- Dropped Linux capabilities
- No privilege escalation (`no-new-privileges`)
- GitHub clone happens inside container
- npm install/build happens inside container

**Deployment Flow:**
1. deploy-api receives deployment request
2. Identifies app as user app
3. Uses `docker exec` to run commands in user-apps container
4. Git clone, npm install, build all happen inside container
5. App runs inside container (never on host)

## Components

### Bridge Script

Location: `scripts/bridge/execute.sh`

Simple passthrough that:
- Validates commands against allowlist
- Logs all executions for audit
- Executes make targets from repo root

```bash
# Example usage
scripts/bridge/execute.sh make deploy-ai-portal INV=inventory/staging
```

Allowed commands:
- `make deploy-*`
- `make docker-deploy*`
- `make update`
- `make docker-status`
- `make docker-logs`

### Bridge Executor (Python)

Location: `srv/deploy/src/bridge_executor.py`

Python module that:
- Determines if app is core or user app
- Executes commands via bridge script
- Streams output for real-time logs
- Handles timeouts and errors

### Container Executor (Python)

Location: `srv/deploy/src/container_executor.py`

Handles user app deployments:
- All operations via `docker exec`
- Manages Docker volumes for node_modules
- Handles dev mode (local source mounts)

### User App Deployer (Ansible)

Location: `provision/ansible/roles/user_app_deployer/`

Ansible role for user apps:
- `tasks/docker.yml` - Docker deployment
- `tasks/lxc.yml` - LXC deployment
- `tasks/undeploy.yml` - Removal

## API Endpoints

| Endpoint | Purpose | Path |
|----------|---------|------|
| `POST /deploy` | Deploy app | Core: Bridge → Ansible, User: Container Exec |
| `POST /undeploy` | Remove app | Core: Bridge, User: Container Exec |
| `POST /stop/{app_id}` | Stop app | Container Exec |
| `GET /deploy/{id}/status` | Get status | In-memory store |
| `GET /deploy/{id}/stream` | SSE log stream | Real-time |

## Makefile Targets

### Core App Deployment

```bash
# Deploy specific core app
make deploy-ai-portal INV=inventory/staging
make deploy-agent-manager

# Use Ansible for Docker deployment
make docker-deploy USE_ANSIBLE=1
```

### User App Deployment

```bash
# Deploy user app
make deploy-user-app APP_ID=myapp REPO=owner/repo BRANCH=main

# Undeploy user app
make undeploy-user-app APP_ID=myapp

# List deployed user apps
make list-user-apps

# View user app logs
make user-app-logs APP_ID=myapp
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `BRIDGE_SCRIPT_PATH` | Path to bridge script | `/busibox/scripts/bridge/execute.sh` |
| `BUSIBOX_HOST_PATH` | Host path to busibox repo | - |
| `CONTAINER_PREFIX` | Docker container prefix | `dev` |

### User Apps Container Security

```yaml
# docker-compose.yml
user-apps:
  deploy:
    resources:
      limits:
        cpus: '2.0'
        memory: 2G
  cap_drop:
    - ALL
  cap_add:
    - CHOWN
    - SETUID
    - SETGID
    - NET_BIND_SERVICE
  security_opt:
    - no-new-privileges:true
```

## Development

### Testing Core App Deployment

```bash
# Test bridge script directly
./scripts/bridge/execute.sh make docker-status

# Test via deploy-api (requires running services)
curl -X POST http://localhost:8011/api/v1/deployment/deploy \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"manifest": {"id": "ai-portal", ...}}'
```

### Testing User App Deployment

```bash
# Test Ansible role directly
cd provision/ansible
ansible-playbook -i inventory/docker user-app-deploy.yml \
  -e "app_id=testapp" \
  -e "github_repo=owner/repo"
```

## Troubleshooting

### Bridge Script Not Found

```bash
# Check if bridge script exists
ls -la /busibox/scripts/bridge/execute.sh

# Check BUSIBOX_HOST_PATH
echo $BUSIBOX_HOST_PATH
```

### User Apps Container Issues

```bash
# Check container status
docker ps | grep user-apps

# View container logs
docker logs dev-user-apps

# Execute command manually
docker exec dev-user-apps ls /srv/apps
```

### Deployment Logs

```bash
# View bridge execution logs
cat /path/to/busibox/.bridge-logs/bridge-$(date +%Y%m%d).log

# View user app deployment logs
docker exec dev-user-apps cat /var/log/user-apps/myapp/app.log
```

## Related Documentation

- [Ansible Deployment Guide](../deployment/ansible-deployment.md)
- [External App Installation](../guides/external-app-installation-quickstart.md)
- [Security Model](../architecture/security-model.md)
