---
title: "Runtime Deployment Architecture"
category: "administrator"
order: 26
description: "Runtime installation pattern for application deployments without baked-in images"
published: true
---

# Runtime Deployment Architecture

## Overview

Busibox uses a **runtime installation pattern** for all application deployments. This means applications are NOT baked into Docker images at build time. Instead, they are cloned and built inside running containers.

This approach provides:
- **Fast container builds** - No app code to clone/build during image creation
- **Quick app updates** - Deploy new versions without container rebuilds
- **Consistent patterns** - Same approach works for Docker and Proxmox
- **Easy rollbacks** - Switch app versions without affecting container

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Deploy API                                │
│                    (srv/deploy/)                                 │
├─────────────────────────────────────────────────────────────────┤
│                           │                                      │
│         ┌─────────────────┴─────────────────┐                   │
│         ▼                                   ▼                   │
│  ┌──────────────────┐             ┌──────────────────┐          │
│  │ core_app_executor│             │container_executor│          │
│  │  (ai-portal,     │             │  (user apps)     │          │
│  │   agent-manager) │             │                  │          │
│  └────────┬─────────┘             └────────┬─────────┘          │
│           │                                │                    │
│    ┌──────┴──────┐                  ┌──────┴──────┐             │
│    ▼             ▼                  ▼             ▼             │
│ Docker:      Proxmox:            Docker:      Proxmox:          │
│ docker exec  SSH                docker exec  SSH                │
│ supervisord  systemd            nohup        systemd            │
└─────────────────────────────────────────────────────────────────┘
```

## Core Apps (ai-portal, agent-manager)

Core apps run in the `core-apps` container with:
- **Docker**: supervisord for process management
- **Proxmox**: systemd services

### Container Components

```
core-apps container:
├── nginx (reverse proxy)
├── supervisord (process manager)
├── /srv/ai-portal/ (persistent volume)
└── /srv/agent-manager/ (persistent volume)
```

### Deployment Flow

1. **First Start**: Container starts with nginx only, apps are deployed automatically
2. **Subsequent Starts**: Apps already in persistent volumes, start immediately
3. **Updates**: Deploy API calls entrypoint to clone/build new version

### Commands

```bash
# From provision/ansible/

# Deploy/update an app
make install SERVICE=ai-portal              # Deploy from main branch
make install SERVICE=ai-portal REF=v1.2.3   # Deploy specific version

# Process management
make app-status                             # Show all app status
make app-restart SERVICE=ai-portal          # Restart app
make app-logs SERVICE=ai-portal             # View logs

# Nginx
make nginx-reload                           # Reload nginx config
```

## User Apps

User apps run in the `user-apps` container and are deployed via:
- **AI Portal Admin UI**: Visual deployment interface
- **Deploy API**: REST API for programmatic deployment

### Security Isolation

User apps are sandboxed:
- Run in separate container from core apps
- No direct database access (only via APIs)
- Controlled GitHub access via Deploy API

### Deployment Methods

**Via AI Portal Admin UI**:
1. Go to Admin → Applications
2. Click "Deploy" on the app
3. Select version/branch
4. Monitor deployment progress

**Via Deploy API**:
```bash
curl -X POST http://localhost:8011/api/v1/deployment/deploy \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {
      "id": "my-app",
      "name": "My App",
      "defaultPath": "/myapp",
      "defaultPort": 3002
    },
    "config": {
      "githubRepoOwner": "myorg",
      "githubRepoName": "my-app",
      "githubBranch": "main"
    }
  }'
```

## Docker Implementation

### Dockerfile

The `core-apps.Dockerfile.runtime` is lightweight:
- Node.js 20 Alpine base
- nginx, supervisord, git installed
- NO app code baked in

### Persistent Volumes

```yaml
# docker-compose.github.yml
volumes:
  - core-apps-portal:/srv/ai-portal
  - core-apps-agent:/srv/agent-manager
  - core-apps-logs:/var/log/supervisor
```

### Entrypoint

The `core-apps-entrypoint-runtime.sh` script:
1. Generates SSL certificates (if needed)
2. Starts supervisord (nginx starts)
3. Checks if apps are deployed
4. Deploys apps if not present
5. Starts apps via supervisorctl

## Proxmox Implementation

### Ansible Integration

The `app_deployer` role now uses Deploy API:

```yaml
# roles/app_deployer/tasks/deploy_via_api.yml
- name: Deploy app via Deploy API
  uri:
    url: "http://{{ deploy_host }}:{{ deploy_port }}/api/v1/deployment/deploy"
    method: POST
    body_format: json
    body:
      manifest:
        id: "{{ app.name }}"
        # ...
```

### Legacy Deploywatch

The old `deploywatch` scripts are deprecated. To use legacy mode:

```yaml
# group_vars/all/00-main.yml
use_deploy_api: false  # Default is true
```

## Troubleshooting

### App Not Starting

```bash
# Check supervisord status
make app-status

# View app logs
make app-logs SERVICE=ai-portal

# Check if app is deployed
docker compose exec core-apps ls -la /srv/ai-portal/
```

### Deployment Fails

```bash
# Manual deployment with verbose output
docker compose exec core-apps /usr/local/bin/entrypoint.sh deploy ai-portal main

# Check GitHub token
docker compose exec core-apps bash -c 'echo $GITHUB_AUTH_TOKEN | head -c 10'
```

### Nginx Issues

```bash
# Test nginx config
docker compose exec core-apps nginx -t

# Reload nginx
make nginx-reload

# View nginx logs
docker compose exec core-apps tail -f /var/log/nginx/error.log
```

## Migration from Baked-In Images

If you were using the old `core-apps.Dockerfile.github` (baked-in approach):

1. **Backup data**: Export any data from existing containers
2. **Remove old volumes**: `docker volume rm <prefix>-core-apps-*`
3. **Update docker-compose**: Ensure using `core-apps.Dockerfile.runtime`
4. **First start**: Container will auto-deploy apps from GitHub
5. **Verify**: Check apps are running with `make app-status`

## Related Documentation

- [Deploy API Reference](../reference/deploy-api.md)
- [Container Architecture](../architecture/containers.md)
- [Nginx Configuration](../configuration/nginx.md)
