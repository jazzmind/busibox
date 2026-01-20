# Data Model: Application Services Deployment

**Feature**: 002-deploy-app-servers  
**Created**: 2025-10-15  
**Purpose**: Define data structures for application configuration, NGINX routing, and secrets management

## Overview

This feature introduces three primary data models:
1. **Application Definition** - Declarative configuration for deployable applications
2. **NGINX Configuration** - Virtual hosts and location blocks for routing
3. **Secrets Structure** - Encrypted storage and distribution of sensitive configuration

All models follow Infrastructure as Code principles and are managed through Ansible.

---

## 1. Application Definition

**Purpose**: Declarative specification of applications to be deployed  
**Storage**: `provision/ansible/group_vars/apps.yml`  
**Format**: YAML list structure

### Schema

```yaml
applications:
  - name: string                    # Required. Unique identifier (alphanumeric + hyphens)
    github_repo: string             # Required. Format: "owner/repo"
    container: string               # Required. Target LXC container name (from vars.env)
    container_ip: string            # Required. IP address of target container
    port: integer                   # Required. Port application listens on
    deploy_path: string             # Required. Absolute path for deployment
    health_endpoint: string         # Required. HTTP path for health checks (e.g., "/health")
    process_manager: string         # Optional. Default: "pm2". Options: "pm2", "systemd"
    routes: list[Route]             # Optional. Empty list = internal only (no NGINX routing)
    secrets: list[string]           # Optional. Keys from vault.yml
    env: map[string, string]        # Optional. Non-secret environment variables
    build_command: string           # Optional. Command to run after deployment (e.g., "npm run build")
    start_command: string           # Optional. Override default start command
```

### Route Sub-Schema

```yaml
Route:
  type: enum                        # Required. Values: "domain", "subdomain", "path"
  domain: string                    # Required if type=domain or path. Base domain name
  domains: list[string]             # Required if type=domain. List of domain names
  subdomain: string                 # Required if type=subdomain. Subdomain prefix
  path: string                      # Required if type=path. URL path (starts with /)
  strip_path: boolean               # Optional. Default: false. Remove path prefix before proxying
  websocket: boolean                # Optional. Default: false. Enable WebSocket support
```

### Validation Rules

- `name`: Must be unique across all applications, alphanumeric + hyphens only
- `github_repo`: Must match pattern `^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$`
- `container`: Must exist in `provision/pct/vars.env` container definitions
- `container_ip`: Must be valid IPv4 address in 10.96.200.0/21 range
- `port`: Must be integer between 1-65535
- `deploy_path`: Must be absolute path (starts with `/`)
- `health_endpoint`: Must start with `/`
- `process_manager`: Must be one of ["pm2", "systemd"]
- `routes[].type`: Must be one of ["domain", "subdomain", "path"]
- `routes[].path`: Must start with `/`, cannot be `/` alone (use domain type for root)
- `secrets`: Each key must exist in `vault.yml` under `secrets.<app_name>.*`

### Example

```yaml
applications:
  # Internal service (no NGINX routing)
  - name: agent-server
    github_repo: jazzmind/agent-server
    container: agent-lxc
    container_ip: 10.96.200.30
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
      LOG_LEVEL: "info"
      MILVUS_HOST: "10.96.200.27"
      MILVUS_PORT: "19530"
  
  # Main portal (multiple domain routing)
  - name: busibox-portal
    github_repo: jazzmind/busibox
    container: apps-lxc
    container_ip: 10.96.200.25
    port: 3000
    deploy_path: /srv/apps/busibox
    health_endpoint: /api/health
    build_command: "npm run build"
    routes:
      - type: domain
        domains: 
          - ai.localhost
          - www.ai.localhost
      - type: path
        domain: ai.localhost
        path: /home
    secrets:
      - session_secret
      - oauth_client_id
      - oauth_client_secret
      - database_url
    env:
      NODE_ENV: "production"
      JWT_ISSUER: "busibox-portal"
  
  # Agent client (subdomain + path routing)
  - name: agent-manager
    github_repo: jazzmind/agent-manager
    container: apps-lxc
    container_ip: 10.96.200.25
    port: 3001
    deploy_path: /srv/apps/agent-manager
    health_endpoint: /health
    build_command: "npm run build"
    routes:
      - type: subdomain
        subdomain: agents
        websocket: true  # For real-time updates
      - type: path
        domain: ai.localhost
        path: /agents
        strip_path: false
    env:
      AGENT_API_URL: "http://10.96.200.30:8000"
      NODE_ENV: "production"
    secrets:
      - agent_api_key
      - jwt_secret
```

### State Tracking

**Deployment Version**: Tracked in `<deploy_path>/.version` file
- Contains GitHub release tag (e.g., `v1.2.3`)
- Updated by deploywatch after successful deployment
- Used to compare with latest GitHub release

**Application Process**: Tracked by process manager
- PM2: `pm2 list` shows status, uptime, restarts
- Systemd: `systemctl status <app-name>` shows status

---

## 2. NGINX Configuration Model

**Purpose**: Generated NGINX configuration for routing HTTP/HTTPS traffic  
**Storage**: `/etc/nginx/sites-available/<config-name>` (symlinked to `sites-enabled/`)  
**Format**: NGINX configuration syntax (generated from Jinja2 templates)

### Virtual Host Structure

Each entry in `applications[].routes` generates NGINX configuration:

**Subdomain Route** → Dedicated server block:
```nginx
server {
    listen 443 ssl http2;
    server_name <subdomain>.ai.localhost;
    
    ssl_certificate /etc/letsencrypt/live/ai.localhost/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.localhost/privkey.pem;
    
    # SSL security settings (Mozilla Intermediate)
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256...;
    ssl_prefer_server_ciphers off;
    
    # Security headers
    add_header Strict-Transport-Security "max-age=63072000" always;
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    
    location / {
        proxy_pass http://<container_ip>:<port>;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if enabled)
        {% if route.websocket %}
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        {% endif %}
    }
}
```

**Domain Route** → Server block with multiple server_names:
```nginx
server {
    listen 443 ssl http2;
    server_name ai.localhost www.ai.localhost;
    
    # ... SSL and security settings ...
    
    location / {
        proxy_pass http://<container_ip>:<port>;
        # ... proxy settings ...
    }
}
```

**Path Route** → Location block within main domain server:
```nginx
server {
    listen 443 ssl http2;
    server_name ai.localhost www.ai.localhost;
    
    # ... SSL settings ...
    
    location <path> {
        {% if route.strip_path %}
        rewrite ^<path>(.*)$ $1 break;
        {% endif %}
        
        proxy_pass http://<container_ip>:<port>;
        # ... proxy settings ...
    }
}
```

### Configuration Files

**Main NGINX config**: `/etc/nginx/nginx.conf`
- Global settings (worker processes, connections, logging)
- Includes `/etc/nginx/sites-enabled/*.conf`

**Per-application configs**: `/etc/nginx/sites-available/<app-name>.conf`
- Generated from Jinja2 template
- Symlinked to `sites-enabled/` if app has routes

**HTTP redirect**: `/etc/nginx/sites-available/redirect-to-https.conf`
```nginx
server {
    listen 80;
    server_name ai.localhost *.ai.localhost;
    return 301 https://$host$request_uri;
}
```

### Template Variables

**Nginx role template** (`provision/ansible/roles/nginx/templates/vhost.conf.j2`):
```jinja2
{# Input variables from apps.yml #}
{% set app = item %}  # Application definition

{% for route in app.routes %}
  {% if route.type == 'subdomain' %}
    # Subdomain virtual host
    server {
        listen 443 ssl http2;
        server_name {{ route.subdomain }}.{{ domain }};
        # ... rest of config ...
    }
  {% elif route.type == 'domain' %}
    # Domain virtual host
    server {
        listen 443 ssl http2;
        server_name {{ route.domains | join(' ') }};
        # ... rest of config ...
    }
  {% elif route.type == 'path' %}
    # Path location (added to main domain server block)
    # Handled separately in main domain config
  {% endif %}
{% endfor %}
```

---

## 3. Secrets Structure

**Purpose**: Secure storage and distribution of sensitive configuration values  
**Storage**: `provision/ansible/roles/secrets/vars/vault.yml` (Ansible vault-encrypted)  
**Format**: YAML map structure

### Schema

```yaml
secrets:
  <app_name>:                       # Matches applications[].name
    <secret_key>: <secret_value>    # Referenced by applications[].secrets[]
```

### Example (Encrypted)

```yaml
# vault.yml (encrypted with ansible-vault encrypt)
secrets:
  agent_server:
    database_url: "postgresql://busibox_user:SecurePass123@10.96.200.26/busibox"
    minio_access_key: "AKIAIOSFODNN7EXAMPLE"
    minio_secret_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    redis_url: "redis://10.96.200.29:6379"
    jwt_secret: "randomly-generated-256-bit-secret"
    
  busibox_portal:
    session_secret: "another-random-256-bit-secret"
    oauth_client_id: "Iv1.1234567890abcdef"
    oauth_client_secret: "1234567890abcdef1234567890abcdef12345678"
    database_url: "postgresql://busibox_user:SecurePass123@10.96.200.26/busibox"
    jwt_secret: "shared-jwt-secret-for-cross-app-auth"
    
  agent_client:
    agent_api_key: "api-key-for-agent-server-access"
    jwt_secret: "shared-jwt-secret-for-cross-app-auth"  # Same as portal

  letsencrypt:
    dns_provider: "cloudflare"
    cloudflare_api_token: "1234567890abcdef1234567890abcdef12345678"
    email: "admin@localhost"
```

### Runtime Distribution

Secrets are distributed to applications as environment files:

**File**: `<deploy_path>/.env`  
**Permissions**: `0600` (owner read/write only)  
**Owner**: `root:root`

**Content** (generated from template):
```bash
# Auto-generated by Ansible - DO NOT EDIT MANUALLY
# Application: {{ app.name }}
# Generated: {{ ansible_date_time.iso8601 }}

# Secrets from vault.yml
{% for secret_key in app.secrets %}
{{ secret_key | upper }}={{ secrets[app.name][secret_key] }}
{% endfor %}

# Non-secret environment variables
{% for key, value in app.env.items() %}
{{ key }}={{ value }}
{% endfor %}
```

**Example output** (`/srv/apps/busibox/.env`):
```bash
# Auto-generated by Ansible - DO NOT EDIT MANUALLY
# Application: busibox-portal
# Generated: 2025-10-15T14:30:00Z

# Secrets from vault.yml
SESSION_SECRET=another-random-256-bit-secret
OAUTH_CLIENT_ID=Iv1.1234567890abcdef
OAUTH_CLIENT_SECRET=1234567890abcdef1234567890abcdef12345678
DATABASE_URL=postgresql://busibox_user:SecurePass123@10.96.200.26/busibox
JWT_SECRET=shared-jwt-secret-for-cross-app-auth

# Non-secret environment variables
NODE_ENV=production
JWT_ISSUER=busibox-portal
```

### Access Control

**Ansible Vault**:
- Password stored in secure location (not in git)
- Referenced via `--vault-password-file` flag or `ANSIBLE_VAULT_PASSWORD_FILE` env var
- All tasks handling secrets use `no_log: true` to prevent logging

**Runtime Secrets**:
- `.env` files: `chmod 0600`, `chown root:root`
- Only accessible by application running as root or service user
- Never logged by application code (use environment variable access directly)

**Git Protection**:
- `.env` files added to `.gitignore`
- Pre-commit hook scans for potential secrets
- Vault files committed encrypted only

### Secret Rotation

**Process**:
1. Update secret value in `vault.yml`
2. Re-encrypt vault file: `ansible-vault encrypt vault.yml`
3. Run Ansible playbook: `make deploy-apps` (regenerates `.env` files)
4. Applications automatically restart with new secrets

**Rollback**:
1. Restore previous `vault.yml` from git history
2. Re-run playbook

---

## 4. Deployment Tracking Model

**Purpose**: Track deployment state and version history

### Version File

**Location**: `<deploy_path>/.version`  
**Format**: Plain text, single line  
**Content**: GitHub release tag (e.g., `v1.2.3`)

**Example**:
```
v1.2.3
```

**Usage**:
- Created/updated by deploywatch after successful deployment
- Read by deploywatch to compare with GitHub latest release
- Can be manually checked: `cat /srv/apps/busibox/.version`

### Deployment Log

**Location**: Systemd journal (`journalctl -u deploywatch.service`)  
**Format**: Structured log entries

**Log Fields**:
- Timestamp (automatic from systemd)
- Application name
- Action (check, download, deploy, health_check)
- Version (current, new)
- Status (success, failure)
- Error message (if failure)

**Example log entries**:
```
Oct 15 14:30:01 deploywatch[1234]: [agent-server] Checking for new release
Oct 15 14:30:02 deploywatch[1234]: [agent-server] Current version: v1.2.2, Latest: v1.2.3
Oct 15 14:30:03 deploywatch[1234]: [agent-server] Downloading release v1.2.3
Oct 15 14:30:10 deploywatch[1234]: [agent-server] Deploying to /srv/agent
Oct 15 14:30:15 deploywatch[1234]: [agent-server] Health check passed
Oct 15 14:30:15 deploywatch[1234]: [agent-server] Deployment successful: v1.2.3
```

---

## Entity Relationships

```
apps.yml (Application Definition)
    |
    ├──> secrets.vault.yml (Secrets)
    |       └──> <deploy_path>/.env (Runtime Environment)
    |
    ├──> NGINX vhost config (Routing)
    |       └──> /etc/nginx/sites-enabled/<app>.conf
    |
    └──> deploywatch script (Deployment)
            └──> <deploy_path>/.version (Version Tracking)
```

**Flow**:
1. Administrator updates `apps.yml` (add/modify application)
2. Ansible reads `apps.yml` and `vault.yml`
3. Generates:
   - `.env` file with secrets
   - NGINX virtual host config
   - Deploywatch script for the application
4. Deploywatch runs periodically:
   - Checks GitHub for new release
   - Downloads and deploys if newer
   - Updates `.version` file
   - Logs to systemd journal
5. NGINX routes traffic to application based on vhost config

---

## Validation & Constraints

### Application Definition Validation

Performed by Ansible role `app_deployer`:

```yaml
- name: Validate application definition
  assert:
    that:
      - item.name is defined
      - item.name | regex_search('^[a-zA-Z0-9-]+$')
      - item.github_repo is defined
      - item.github_repo | regex_search('^[a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+$')
      - item.container is defined
      - item.container_ip is defined
      - item.container_ip | ipaddr
      - item.port is defined
      - item.port | int > 0
      - item.port | int < 65536
      - item.deploy_path is defined
      - item.deploy_path is match('^/')
      - item.health_endpoint is defined
      - item.health_endpoint is match('^/')
    fail_msg: "Application {{ item.name }} has invalid configuration"
  loop: "{{ applications }}"
```

### NGINX Configuration Validation

Before reload:

```bash
nginx -t  # Test configuration syntax
```

If test fails, deployment is aborted and previous config remains active.

### Secrets Validation

```yaml
- name: Validate secrets exist for application
  assert:
    that:
      - secrets[item.0.name][item.1] is defined
    fail_msg: "Secret '{{ item.1 }}' not found for app '{{ item.0.name }}'"
  with_subelements:
    - "{{ applications }}"
    - secrets
    - skip_missing: yes
```

---

## Summary

This data model provides:

1. **Declarative application configuration** - All apps defined in single YAML file
2. **Secure secrets management** - Encrypted at rest, distributed securely
3. **Flexible routing** - Supports subdomain and path-based routing
4. **Version tracking** - Deployment history via `.version` files and logs
5. **Validation** - Ansible ensures configuration correctness before deployment

The model aligns with Infrastructure as Code principles (Constitution I) and enables extensibility (Constitution IV) by making new application deployment a configuration change rather than code change.

