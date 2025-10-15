# App Deployer Role

**Purpose**: Automated application deployment from GitHub releases using deploywatch  
**Requirements**: FR-001, FR-004, FR-005, FR-030, FR-035 (GitHub deployment, health checks, secrets, extensibility, rollback)

## Overview

The `app_deployer` role provides centralized configuration management for all deployable applications in the busibox infrastructure. It:

1. Validates application configuration in `group_vars/apps.yml`
2. Generates per-application deploywatch scripts for automated GitHub release monitoring
3. Deploys application environment files (`.env`) with secrets from Ansible vault
4. Automatically triggers redeployment when configuration changes

## Application Configuration Schema

Applications are defined in `provision/ansible/group_vars/apps.yml` using the following schema:

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `name` | string | Unique application identifier (alphanumeric + hyphens) | `agent-server` |
| `github_repo` | string | GitHub repository in `owner/repo` format | `jazzmind/agent-server` |
| `container` | string | Target LXC container name | `agent-lxc` |
| `container_ip` | string | IP address of target container | `10.96.200.30` |
| `port` | integer | Port application listens on (1-65535) | `8000` |
| `deploy_path` | string | Absolute path for deployment | `/srv/agent` |
| `health_endpoint` | string | HTTP path for health checks (must start with `/`) | `/health` |

### Optional Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `process_manager` | string | `pm2` | Process manager: `pm2` or `systemd` |
| `routes` | list | `[]` | NGINX routing configuration (see Routing section) |
| `secrets` | list | `[]` | Secret keys from `vault.yml` |
| `env` | map | `{}` | Non-secret environment variables |
| `build_command` | string | none | Command to run after deployment |
| `start_command` | string | auto | Override default start command |

## Routing Configuration

Applications can be made publicly accessible via NGINX using the `routes` field. An empty `routes` list (`[]`) means the application is internal-only.

### Route Types

#### 1. Domain Routing

Serve application at one or more domain names:

```yaml
routes:
  - type: domain
    domains:
      - ai.jaycashman.com
      - www.ai.jaycashman.com
```

#### 2. Subdomain Routing

Serve application at a subdomain:

```yaml
routes:
  - type: subdomain
    subdomain: agents  # → agents.ai.jaycashman.com
    websocket: true    # Optional: enable WebSocket support
```

#### 3. Path Routing

Serve application at a URL path:

```yaml
routes:
  - type: path
    domain: ai.jaycashman.com
    path: /agents  # → ai.jaycashman.com/agents
    strip_path: false  # Optional: remove path prefix before proxying
```

### Multiple Routes

Applications can have multiple routes:

```yaml
routes:
  - type: subdomain
    subdomain: agents
  - type: path
    domain: ai.jaycashman.com
    path: /agents
```

### Internal-Only Applications

Set `routes: []` for internal services:

```yaml
routes: []  # Not accessible via NGINX
```

## Secrets Management

Secrets are referenced by key name in the `secrets` field and must exist in `provision/ansible/roles/secrets/vars/vault.yml`:

### In apps.yml:

```yaml
applications:
  - name: agent-server
    secrets:
      - database_url
      - minio_access_key
      - redis_url
```

### In vault.yml:

```yaml
secrets:
  agent_server:  # Note: hyphen becomes underscore
    database_url: "postgresql://user:pass@host/db"
    minio_access_key: "ACCESS_KEY_HERE"
    redis_url: "redis://host:6379"
```

**Important**: Encrypt `vault.yml` with `ansible-vault encrypt`:

```bash
ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml
```

## Environment Variables

Non-secret configuration can be specified in the `env` field:

```yaml
applications:
  - name: agent-server
    env:
      LOG_LEVEL: info
      MILVUS_HOST: "{{ milvus_host }}"  # Can use Ansible variables
      NODE_ENV: production
```

All `secrets` and `env` values are written to `<deploy_path>/.env` with mode `0600`.

## Deploywatch Integration

For each application, the role generates a deploywatch script at:

```
/srv/deploywatch/apps/<app-name>.sh
```

This script:

1. Checks GitHub for new releases
2. Downloads and deploys new versions
3. Runs database migrations (if `scripts/migrate.sh` exists)
4. Executes build command (if specified)
5. Performs health checks
6. Automatically rolls back on failure

### Deployment Workflow

```
GitHub Release Published
  ↓
Deploywatch Timer Runs (hourly)
  ↓
Check Latest Release vs Current
  ↓
Download & Extract New Release
  ↓
Run Migrations (if present)
  ↓
Build Application (if build_command set)
  ↓
Restart Application
  ↓
Health Check
  ↓
Success or Rollback
```

## Validation Rules

The role validates configuration before deployment:

- **Unique names**: Each application must have a unique `name`
- **GitHub repo format**: Must match `owner/repo` pattern
- **Valid IP address**: `container_ip` must be a valid IPv4 address
- **Port range**: Must be between 1-65535
- **Absolute paths**: `deploy_path` and `health_endpoint` must start with `/`
- **No duplicate routes**: Subdomain and path combinations must be unique across all applications
- **Secrets exist**: All keys in `secrets` list must exist in `vault.yml`

## Example Application Definition

### Internal Service (Agent Server)

```yaml
applications:
  - name: agent-server
    github_repo: jazzmind/agent-server
    container: agent-lxc
    container_ip: "{{ agent_ip }}"
    port: 8000
    deploy_path: /srv/agent
    health_endpoint: /health
    routes: []  # Internal only
    secrets:
      - database_url
      - minio_access_key
      - minio_secret_key
      - redis_url
    env:
      LOG_LEVEL: "{{ log_level }}"
      MILVUS_HOST: "{{ milvus_host }}"
      MILVUS_PORT: "{{ milvus_port }}"
```

### Public Web Application (Main Portal)

```yaml
  - name: cashman-portal
    github_repo: jazzmind/cashman
    container: apps-lxc
    container_ip: "{{ apps_ip }}"
    port: 3000
    deploy_path: /srv/apps/cashman
    health_endpoint: /api/health
    build_command: "npm run build"
    routes:
      - type: domain
        domains:
          - "{{ domain }}"
          - "{{ www_domain }}"
      - type: path
        domain: "{{ domain }}"
        path: /home
    secrets:
      - session_secret
      - oauth_client_id
      - database_url
    env:
      NODE_ENV: production
      JWT_ISSUER: cashman-portal
```

## Adding a New Application

1. Add application definition to `provision/ansible/group_vars/apps.yml`
2. Add secrets to `provision/ansible/roles/secrets/vars/vault.yml`
3. Encrypt vault: `ansible-vault encrypt provision/ansible/roles/secrets/vars/vault.yml`
4. Run deployment: `make deploy-apps`
5. Manually trigger initial deployment (or wait for deploywatch timer):
   ```bash
   ssh agent-lxc
   bash /srv/deploywatch/apps/<app-name>.sh
   ```

## Testing

To add a test application without affecting production:

```yaml
  - name: test-app
    github_repo: your-org/test-repo
    container: apps-lxc
    container_ip: "{{ apps_ip }}"
    port: 3002
    deploy_path: /srv/apps/test
    health_endpoint: /health
    routes:
      - type: subdomain
        subdomain: test
```

Then run:

```bash
make deploy-apps
ssh apps-lxc
bash /srv/deploywatch/apps/test-app.sh
```

## Troubleshooting

### Configuration Validation Errors

If `make deploy-apps` fails with validation errors:

- Check that all required fields are present
- Verify GitHub repo format (`owner/repo`)
- Ensure no duplicate application names or routes
- Confirm all secrets exist in `vault.yml`

### Deployment Failures

Check deploywatch logs:

```bash
ssh <container>
journalctl -u deploywatch.service -f
```

Check application-specific logs:

```bash
# For PM2 apps
pm2 logs <app-name>

# For systemd apps
journalctl -u <app-name>.service -f
```

### Health Check Failures

1. Verify health endpoint is correct: `curl http://<container_ip>:<port><health_endpoint>`
2. Check application logs for startup errors
3. Verify all required secrets are present in `.env` file
4. Ensure database and dependencies are accessible

### Rollback

If a deployment fails, deploywatch automatically rolls back. To manually rollback:

```bash
ssh <container>
cd <deploy_path>
# Backup is in <deploy_path>.backup
rm -rf <deploy_path>
mv <deploy_path>.backup <deploy_path>
pm2 restart <app-name>  # or systemctl restart <app-name>.service
```

## Files Generated

For each application, the role creates:

- `/srv/deploywatch/apps/<app-name>.sh` - Deployment script (mode: 0755)
- `<deploy_path>/.env` - Environment file with secrets (mode: 0600)
- `<deploy_path>/.version` - Current deployed version (created by deploywatch)

## See Also

- [Data Model](../../../specs/002-deploy-app-servers/data-model.md) - Complete schema reference
- [NGINX Role README](../nginx/README.md) - Routing configuration details
- [Secrets Role](../secrets/) - Secrets management
- [QUICKSTART.md](../../../../QUICKSTART.md) - Deployment guide

