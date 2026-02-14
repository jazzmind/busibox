---
title: "Application Management"
category: "administrator"
order: 4
description: "Installing, updating, and managing applications on Busibox"
published: true
---

# Application Management

Busibox applications are installed and updated at runtime -- they're not baked into container images. This means you can deploy, update, and roll back apps without rebuilding any infrastructure.

## How App Deployment Works

```
Developer pushes code → Deploy API pulls & builds → PM2/supervisord runs app → nginx routes traffic
```

1. App code is cloned from a Git repository
2. Dependencies are installed and the app is built on the target container
3. A process manager (PM2 or supervisord) starts the app
4. nginx is updated to route traffic to the app at its configured path

## Core Apps

Core apps ship with Busibox and are deployed automatically:

| App | Path | Purpose |
|-----|------|---------|
| **AI Portal** | `/` | Main dashboard, document management, admin settings |
| **Agent Manager** | `/agents/` | Agent configuration, chat interface, workflow builder |

### Deploying Core Apps

```bash
# Deploy both core apps
make install SERVICE=core-apps

# Deploy individually
make install SERVICE=ai-portal
make install SERVICE=agent-manager

# Deploy a specific version
make install SERVICE=ai-portal REF=v1.2.3
```

To rebuild core apps without restarting the container, use the **Rebuild App** option in `make manage` → core-apps. See [Core App Rebuild](../developers/reference/core-app-rebuild.md).

### Managing Core Apps

```bash
# Check status
make manage SERVICE=ai-portal ACTION=status

# View logs
make manage SERVICE=ai-portal ACTION=logs

# Restart
make manage SERVICE=ai-portal ACTION=restart

# Full redeploy (pull, build, restart)
make manage SERVICE=ai-portal ACTION=redeploy
```

For GitHub Packages (npm) authentication and user app development mode, see [GitHub Packages Authentication](../developers/reference/github-packages-authentication.md) and [User App Dev Mode](../developers/reference/user-app-dev-mode.md).

## Add-On Apps

Additional apps can be installed from Git repositories. These are configured in `provision/ansible/group_vars/all/apps.yml`.

### App Configuration

Each app is defined in `apps.yml`:

```yaml
apps:
  - name: status-report
    repo: "https://github.com/org/status-report.git"
    port: 3003
    path: /status
    auto_deploy: false
    env_vars:
      DATA_API_URL: "http://{{ data_ip }}:8002"
      AGENT_API_URL: "http://{{ agent_ip }}:8000"
```

| Field | Purpose |
|-------|---------|
| `name` | App identifier |
| `repo` | Git repository URL |
| `port` | Port the app listens on |
| `path` | URL path for nginx routing |
| `auto_deploy` | Deploy automatically with `make install SERVICE=all` |
| `env_vars` | Environment variables injected at deploy time |

### Installing an Add-On App

```bash
# Deploy a specific app
make install SERVICE=status-report

# Deploy a specific version/branch
make install SERVICE=status-report REF=main
```

### Installing via AI Portal

Admins can also install apps through the AI Portal admin interface:

1. Navigate to **Admin > Apps**
2. Click **Install Custom App**
3. Enter the GitHub repository URL
4. The system fetches the app's `busibox.json` manifest
5. Review settings and click **Install**

### The `busibox.json` Manifest

Apps can include a `busibox.json` file in their repository root to declare their requirements:

```json
{
  "name": "My App",
  "id": "my-app",
  "defaultPath": "/my-app",
  "defaultPort": 3002,
  "database": {
    "required": true,
    "name": "my_app_db"
  },
  "requiredEnvVars": [
    "DATA_API_URL",
    "AGENT_API_URL"
  ]
}
```

If a database is required, the Deploy API will automatically provision it in PostgreSQL.

## App Lifecycle

### Updating Apps

```bash
# Pull latest code and rebuild
make manage SERVICE=status-report ACTION=redeploy

# Deploy a specific version
make install SERVICE=status-report REF=v2.0.0
```

### Rolling Back

Deploy a previous version by specifying the git ref:

```bash
make install SERVICE=status-report REF=v1.9.0
```

### Stopping and Starting

```bash
make manage SERVICE=status-report ACTION=stop
make manage SERVICE=status-report ACTION=start
```

### Viewing Logs

```bash
# Follow logs in real-time
make manage SERVICE=status-report ACTION=logs
```

Logs are also available through the AI Portal's admin log viewer.

## App Access Control

App access is controlled through RBAC roles in the AuthZ service:

1. **Create a role** (e.g., "Engineering") in the admin panel
2. **Bind the app** to the role
3. **Assign users** to the role

Users without the required role won't see the app in the portal launcher.

## App Secrets

Apps may need secrets (API keys, database credentials). These are managed through:

1. **Ansible Vault** -- for secrets defined in `apps.yml` env_vars
2. **AI Portal Admin** -- for secrets managed through the deployment UI

Secrets are injected as environment variables at deploy time and never stored in the app's git repository.

## Nginx Routing

Each app gets a path-based route through nginx:

| App | URL Path |
|-----|----------|
| AI Portal | `/` |
| Agent Manager | `/agents/` |
| Status Report | `/status/` |
| Custom App | `/<configured-path>/` |

Nginx configuration is updated automatically when apps are deployed. To manually reload:

```bash
make manage SERVICE=nginx ACTION=restart
```

## Next Steps

- [Configure AI models and services](05-ai-models.md)
- [Command-line management reference](06-manage.md)
