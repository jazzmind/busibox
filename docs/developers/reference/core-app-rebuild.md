---
title: "Core App Rebuild"
category: "developer"
order: 30
description: "Rebuild core applications (busibox-portal, busibox-agents) via make manage menu without container restart"
published: true
---

# Core App Rebuild via Management Menu

## Overview

The `make manage` interactive menu now includes an option to rebuild core applications (busibox-portal and busibox-agents) without restarting the Docker container. This is useful for quick deployments when you've made code changes but don't want to rebuild/restart the entire core-apps container.

## How to Access

1. Run the management menu:
   ```bash
   make manage
   ```

2. Select **"Manage Service"** (option 4)

3. Select **"core-apps"** from the service list

4. Choose **"Rebuild App"** (option 6)

5. Select which app to rebuild:
   - **busibox-portal** - Only rebuild Busibox Portal
   - **busibox-agents** - Only rebuild Busibox Agents
   - **both** - Rebuild both apps sequentially

## What It Does

The rebuild process:

1. **Pulls latest code** from the configured Git branch (usually `main`)
2. **Installs dependencies** (`npm install`)
3. **Builds the app** (`npm run build`)
4. **Restarts the app process** (via supervisord or systemd)
5. **Does NOT restart the container** - the core-apps container keeps running

This is much faster than:
- Full container rebuild: `make manage SERVICE=core-apps ACTION=redeploy`
- Full installation: `make install SERVICE=core-apps`

## When to Use

**Use "Rebuild App" when:**
- You've pushed code changes to the app repository
- You want to deploy new app features quickly
- The container and nginx are already configured correctly
- You only need to update the application code

**Use "Redeploy" instead when:**
- You've changed Docker configuration
- You've changed nginx configuration
- You've changed environment variables in Ansible vault
- The container is broken or in a bad state

## Performance Comparison

**Rebuild App** (option 6):
- Time: 1-3 minutes
- Container: Stays running (no downtime for other apps)
- Network: No container restart, no network reconfiguration
- Risk: Very low

**Redeploy** (option 5):
- Time: 5-10 minutes
- Container: Full rebuild and restart
- Network: Container may be briefly unavailable
- Risk: Higher (affects all apps in core-apps container)

## Technical Details

### Backend Commands

The rebuild option calls these Ansible make targets:

```bash
# For busibox-portal
cd provision/ansible
make deploy-busibox-portal INV=inventory/staging  # or production

# For busibox-agents
make deploy-busibox-agents INV=inventory/staging
```

### Ansible Implementation

The Ansible playbook:

1. Uses the `app_deployer` role
2. Clones/pulls code from GitHub
3. Runs `npm install` and `npm run build`
4. Restarts the app via supervisor/systemd:
   - Docker: `supervisorctl restart busibox-portal`
   - Proxmox: `systemctl restart busibox-portal`

### Environment Detection

The script automatically detects your environment:
- Reads from `.busibox-state-prod` or `.busibox-state-staging`
- Uses appropriate inventory: `inventory/production` or `inventory/staging`
- Defaults to staging if no state file found

## Example Workflow

### Scenario: Deploy Busibox Portal code changes

```bash
# 1. Make code changes in busibox-portal repo
cd ~/Code/busibox-portal
# ... edit files ...
git add .
git commit -m "feat: new feature"
git push origin main

# 2. Rebuild via menu
cd ~/Code/busibox
make manage
# Select: 4 (Manage Service)
# Select: core-apps
# Select: 6 (Rebuild App)
# Select: 1 (busibox-portal)

# 3. Wait for rebuild (1-3 minutes)
# 4. Test at https://your-domain.com
```

## Troubleshooting

### "Failed to pull code"
- Check Git credentials in Ansible vault
- Ensure GitHub access from apps container
- Verify repository URL is correct

### "Build failed"
- Check build logs: `make manage` → core-apps → View Logs
- Verify Node.js version compatibility
- Check for dependency issues

### "App not restarting"
- Check supervisor/systemd status manually:
  ```bash
  # Docker
  docker exec -it prod-core-apps supervisorctl status
  
  # Proxmox
  ssh root@apps-lxc systemctl status busibox-portal
  ```

### "Changes not visible"
- Clear browser cache (Ctrl+Shift+R)
- Check nginx is routing correctly
- Verify correct environment was deployed to

## Related Documentation

- [04-apps](../../administrators/04-apps.md) - Application management
- `.cursor/rules/010-make-commands.md` - Make command reference
- `provision/ansible/README.md` - Ansible deployment details

## Related Commands

### Command Line Alternatives

If you prefer command line over the menu:

```bash
# Rebuild busibox-portal
cd provision/ansible
make deploy-busibox-portal INV=inventory/staging

# Rebuild busibox-agents
make deploy-busibox-agents INV=inventory/staging

# Rebuild both (from repo root)
make install SERVICE=busibox-portal
make install SERVICE=busibox-agents
```

### Quick Restart (No Rebuild)

If code hasn't changed and you just want to restart the process:

```bash
cd provision/ansible

# Docker
make app-restart SERVICE=busibox-portal
make app-restart SERVICE=busibox-agents

# Check status
make app-status
```

## Limitations

1. **Container must be running**: Can't rebuild if core-apps is stopped
2. **Code must be pushed**: Changes must be committed and pushed to GitHub
3. **Same branch only**: Deploys from the branch configured in Ansible (usually `main`)
4. **No environment variable changes**: Use full redeploy for vault/env changes
5. **No nginx changes**: Use full redeploy for proxy configuration changes

## Best Practices

1. **Test locally first**: Always test changes on your local machine before deploying
2. **Commit message quality**: Write clear commit messages for deployment tracking
3. **One app at a time**: Unless both apps changed, rebuild individually for speed
4. **Monitor logs**: Watch logs during rebuild to catch issues early
5. **Verify after deploy**: Always test the app after rebuild to ensure it works

## See Also

- `make install SERVICE=busibox-portal` - Install from repo root
- `make manage SERVICE=core-apps ACTION=restart` - Restart container
- `make manage SERVICE=core-apps ACTION=logs` - View container logs
