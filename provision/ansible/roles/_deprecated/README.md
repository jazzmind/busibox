# Deprecated Roles

This directory contains Ansible roles that are no longer actively maintained or have been replaced by newer implementations.

## Deprecated Roles

### deploywatch

**Deprecated as of:** January 2026
**Replaced by:** Deploy API (`srv/deploy/`)

The `deploywatch` role was originally designed to monitor GitHub releases and automatically deploy updates. This functionality has been consolidated into the Deploy API service, which provides:

- Manual and automated deployments via REST API
- Real-time deployment logs via WebSocket/SSE
- Version checking and update notifications
- Integration with AI Portal admin UI
- Runtime deployment for both Docker and Proxmox environments

**Contents:**
- `files/deploywatch.sh` - Original deploywatch daemon script
- `handlers/main.yml` - Ansible handlers
- `tasks/main.yml` - Original task definitions
- `templates/deploywatch-app.sh.j2` - Per-app deployment script template (moved from app_deployer)
- `templates/deploywatch-orchestrator.sh.j2` - Orchestrator script template (moved from app_deployer)

**Migration Guide:**

1. Set `use_deploy_api: true` in group_vars (this is now the default)
2. Use AI Portal Admin UI for app deployments
3. For programmatic deployments, use the Deploy API endpoints:
   - `POST /api/v1/deployment/deploy` - Deploy an app
   - `POST /api/v1/deployment/version-check` - Check for updates
   - `GET /api/v1/deployment/deploy/{id}/status` - Get deployment status

**For Docker:**
```bash
# Deploy via Deploy API
curl -X POST http://localhost:8011/api/v1/deployment/deploy \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"manifest": {"id": "ai-portal", ...}}'

# Or use entrypoint directly
docker compose exec core-apps /usr/local/bin/entrypoint.sh deploy ai-portal main
```

**For Proxmox/LXC:**
```bash
# Deploy via Ansible (uses Deploy API internally)
cd provision/ansible
make deploy-ai-portal INV=inventory/production
```

## Do Not Use

These roles are kept for historical reference only. Do not use them in new deployments.
