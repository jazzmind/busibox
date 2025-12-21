# Application Deployment Guide

## Overview

The `app_deployer` role now **automatically deploys applications** when you run the playbook. No manual script execution required!

## How It Works

1. **Configuration Phase**:
   - Validates application definitions
   - Generates deploywatch scripts
   - Creates .env files with secrets
   
2. **Deployment Phase** (NEW):
   - Checks if application is already deployed
   - Runs deploywatch script for new applications
   - Verifies health endpoints
   - Reports deployment status

## Deployment Scenarios

### 1. Initial Deployment (Clean Environment)

```bash
cd ~/busibox/provision/ansible
ansible-playbook -i inventory/test site.yml --ask-vault-pass
```

**Result**: All applications are automatically deployed from GitHub

### 2. Update Existing Deployment

```bash
# Normal run - only deploys NEW applications
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer
```

**Result**: Only new apps are deployed, existing apps are left untouched

### 3. Force Redeployment

```bash
# Force ALL applications to redeploy
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer -e force_redeploy=true
```

**Result**: All applications are redeployed from latest GitHub releases

### 4. Configuration Only (No Deployment)

```bash
# Generate scripts and .env files but don't deploy
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer -e deploy_apps=false
```

**Result**: Scripts generated but not executed

## Application Detection

An application is considered "deployed" if `package.json` exists in the deploy path:

- `/srv/apps/ai-portal/package.json` → Already deployed ✅
- `/srv/apps/agent-client/` (empty) → Needs deployment ⚠️

## Deployment Process

For each new application:

1. **Execute deploywatch script** (`/srv/deploywatch/apps/{app-name}.sh`)
2. **Download from GitHub** (latest release or main branch)
3. **Install dependencies** (`npm install`)
4. **Run build command** (if specified, e.g., `npm run build`)
5. **Start with systemd** (`systemctl start {app-name}.service`)
6. **Health check** (verify `http://localhost:{port}{health_endpoint}`)

## Troubleshooting

### Check Deployment Logs

```bash
# View deploywatch logs
tail -f /var/log/deploywatch/{app-name}.log

# View service status
systemctl status {app-name}.service

# View application logs
journalctl -u {app-name}.service -f
journalctl -u {app-name}.service -n 50 --no-pager
```

### Manual Deployment

If automatic deployment fails, run manually:

```bash
# Deploy specific app
bash /srv/deploywatch/apps/ai-portal.sh

# Deploy all apps
for app in /srv/deploywatch/apps/*.sh; do bash "$app"; done
```

### Clean Slate Deployment

```bash
# Stop and remove old deployment
systemctl stop ai-portal.service
systemctl disable ai-portal.service
rm -rf /srv/apps/ai-portal/*

# Re-run playbook (will detect missing app and redeploy)
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer
```

## Deployment Timing

- **Async execution**: Up to 30 minutes per application
- **Polling interval**: Check status every 10 seconds
- **Health check**: 20 retries × 5 seconds = 100 seconds timeout

## Application Status

After deployment, check status:

```bash
# Service status
systemctl status ai-portal.service
systemctl status agent-client.service
systemctl status doc-intel.service
systemctl status innovation.service

# Health checks
curl http://localhost:3000/api/health  # ai-portal
curl http://localhost:3001/api/health  # agent-client
curl http://localhost:3002/api/health  # doc-intel
curl http://localhost:3003/api/health  # innovation
```

## Deployment Flow Diagram

```
playbook run
    ↓
app_deployer role
    ↓
configuration tasks
    ├─ validate apps
    ├─ generate scripts
    └─ create .env files
    ↓
deployment tasks (deploy.yml)
    ├─ check if deployed (package.json?)
    ├─ run deploywatch script (if new)
    ├─ wait for completion (async)
    └─ verify health endpoint
    ↓
deployment complete
```

## Container Assignment

Applications are only deployed to their assigned container:

```yaml
applications:
  - name: agent-server
    container: agent-lxc    # Deploys to agent-lxc only
    
  - name: ai-portal
    container: apps-lxc     # Deploys to apps-lxc only
```

The deployment task checks `inventory_hostname` to ensure it only runs on the correct host.

## Next Steps After Deployment

1. **Verify applications are running**: `systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-client|doc-intel|innovation)'`
2. **Configure NGINX proxy**: 
   ```bash
   ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags proxy
   ```
3. **Test applications**:
   - AI Portal: https://test.ai.jaycashman.com
   - Agent Client: https://agents.test.ai.jaycashman.com
   - Doc Intel: https://docs.test.ai.jaycashman.com
   - Innovation: https://innovation.test.ai.jaycashman.com

