# Research: Application Services Deployment

**Feature**: 002-deploy-app-servers  
**Created**: 2025-10-15  
**Purpose**: Resolve technical unknowns and establish best practices for application deployment, NGINX routing, and secrets management

## Research Tasks

### 1. SSL Certificate Automation with Let's Encrypt

**Question**: How to automate SSL certificate generation and renewal for wildcard domains (*.ai.jaycashman.com)?

**Decision**: Use certbot with DNS-01 challenge for wildcard certificates

**Rationale**:
- Let's Encrypt supports wildcard certificates via DNS-01 challenge (requires DNS TXT record updates)
- HTTP-01 challenge doesn't work for wildcard domains
- Certbot has built-in renewal automation via systemd timer
- DNS-01 can be automated with DNS provider plugins (e.g., Cloudflare, Route53)

**Implementation Approach**:
```bash
# Install certbot
apt-get install certbot python3-certbot-nginx

# For wildcard cert with DNS provider plugin (example: Cloudflare)
apt-get install python3-certbot-dns-cloudflare

# Obtain wildcard certificate
certbot certonly --dns-cloudflare \
  --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
  -d ai.jaycashman.com \
  -d *.ai.jaycashman.com

# Auto-renewal (certbot installs systemd timer by default)
systemctl status certbot.timer
```

**Alternatives Considered**:
- **Self-signed certificates**: Rejected - browser warnings unacceptable for production
- **Manual certificate management**: Rejected - doesn't meet FR-030 automation requirement
- **HTTP-01 challenge**: Rejected - doesn't support wildcard domains

**Configuration**:
- DNS credentials stored in Ansible vault (`/etc/letsencrypt/cloudflare.ini` deployed via secrets role)
- NGINX configured to use Let's Encrypt cert paths: `/etc/letsencrypt/live/ai.jaycashman.com/`
- Renewal hook to reload NGINX: `--deploy-hook "systemctl reload nginx"`

---

### 2. Secrets Management Strategy

**Question**: How to securely store and distribute application secrets (API keys, database passwords) without exposing them in logs or git?

**Decision**: Ansible Vault for storage + encrypted files on target hosts + environment variable injection

**Rationale**:
- Ansible Vault is built-in, no additional dependencies
- Secrets encrypted at rest in version control
- Runtime secrets delivered as environment files (`.env`) with restrictive permissions (0600)
- Applications read from environment variables (twelve-factor app pattern)

**Implementation Approach**:

1. **Storage**: `provision/ansible/roles/secrets/vars/vault.yml` (Ansible vault-encrypted)
   ```yaml
   # vault.yml (encrypted with ansible-vault)
   secrets:
     agent_server:
       database_url: "postgresql://user:pass@10.96.200.26/busibox"
       minio_access_key: "AKIAIOSFODNN7EXAMPLE"
       minio_secret_key: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
     cashman_portal:
       session_secret: "randomly-generated-secret-key"
       oauth_client_id: "github-oauth-app-id"
       oauth_client_secret: "github-oauth-app-secret"
   ```

2. **Distribution**: Ansible template generates `.env` files per application
   ```jinja2
   # app.env.j2 template
   DATABASE_URL={{ secrets[app_name].database_url }}
   MINIO_ACCESS_KEY={{ secrets[app_name].minio_access_key }}
   MINIO_SECRET_KEY={{ secrets[app_name].minio_secret_key }}
   ```

3. **Application Runtime**: PM2 or systemd service loads environment file
   ```ini
   [Service]
   EnvironmentFile=/srv/apps/{{ app_name }}/.env
   ```

**Security Controls**:
- `.env` files: `chown root:root`, `chmod 0600` (only root can read)
- Ansible vault password stored securely (not in git)
- Secrets never logged (Ansible `no_log: true` on tasks handling secrets)
- Git pre-commit hook to prevent accidental commit of unencrypted secrets

**Alternatives Considered**:
- **HashiCorp Vault**: Rejected - over-engineered for initial scale (violates Simplicity principle)
- **Kubernetes Secrets**: Rejected - not using Kubernetes
- **Environment variables in systemd**: Rejected - harder to audit and rotate

---

### 3. NGINX Routing Patterns

**Question**: How to implement both subdomain-based (agents.ai.jaycashman.com) and path-based (ai.jaycashman.com/agents) routing in NGINX?

**Decision**: Hybrid approach with server blocks for subdomains and location blocks for paths

**Rationale**:
- NGINX `server` blocks handle subdomain routing via `server_name` directive
- NGINX `location` blocks within main domain server block handle path routing
- Both can coexist and route to same backend (application accessible both ways)

**Implementation Pattern**:

**Subdomain Routing** (agents.ai.jaycashman.com):
```nginx
server {
    listen 443 ssl http2;
    server_name agents.ai.jaycashman.com;

    ssl_certificate /etc/letsencrypt/live/ai.jaycashman.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.jaycashman.com/privkey.pem;

    location / {
        proxy_pass http://10.96.200.25:3001;  # agent-client on apps-lxc
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Path Routing** (ai.jaycashman.com/agents):
```nginx
server {
    listen 443 ssl http2;
    server_name ai.jaycashman.com www.ai.jaycashman.com;

    ssl_certificate /etc/letsencrypt/live/ai.jaycashman.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ai.jaycashman.com/privkey.pem;

    # Main portal (root path)
    location / {
        proxy_pass http://10.96.200.25:3000;  # cashman portal on apps-lxc
        proxy_set_header Host $host;
        # ... same proxy headers
    }

    # Agent client (sub-path)
    location /agents {
        proxy_pass http://10.96.200.25:3001;  # agent-client on apps-lxc
        proxy_set_header Host $host;
        # ... same proxy headers
    }
}
```

**Key Considerations**:
- **Asset paths**: Applications must handle base path correctly (e.g., `/agents/static/style.css`)
  - Solution: Applications should use relative paths or environment variable `BASE_PATH=/agents`
- **Session cookies**: Must work across paths
  - Solution: Set cookie path to `/` in main portal
- **WebSocket support**: Add for apps that need it
  ```nginx
  proxy_http_version 1.1;
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  ```

**HTTP to HTTPS Redirect**:
```nginx
server {
    listen 80;
    server_name ai.jaycashman.com *.ai.jaycashman.com;
    return 301 https://$host$request_uri;
}
```

**Alternatives Considered**:
- **Subdomain-only**: Rejected - doesn't meet FR-015 requirement for path routing
- **Reverse proxy in each application**: Rejected - NGINX is more efficient and centralized
- **Application-level routing**: Rejected - violates separation of concerns

---

### 4. Application Configuration Schema

**Question**: What structure should `apps.yml` use to define deployable applications?

**Decision**: YAML list of application definitions with standard fields

**Schema**:
```yaml
# provision/ansible/group_vars/apps.yml
applications:
  - name: agent-server
    github_repo: jazzmind/agent-server
    container: agent-lxc
    container_ip: 10.96.200.30
    port: 8000
    deploy_path: /srv/agent
    health_endpoint: /health
    routes: []  # Internal only, no NGINX routes
    secrets:
      - database_url
      - minio_access_key
      - minio_secret_key
    
  - name: cashman-portal
    github_repo: jazzmind/cashman
    container: apps-lxc
    container_ip: 10.96.200.25
    port: 3000
    deploy_path: /srv/apps/cashman
    health_endpoint: /api/health
    routes:
      - type: domain
        domains: [ai.jaycashman.com, www.ai.jaycashman.com]
      - type: path
        domain: ai.jaycashman.com
        path: /home
    secrets:
      - session_secret
      - oauth_client_id
      - oauth_client_secret
    
  - name: agent-client
    github_repo: jazzmind/agent-client
    container: apps-lxc
    container_ip: 10.96.200.25
    port: 3001
    deploy_path: /srv/apps/agent-client
    health_endpoint: /health
    routes:
      - type: subdomain
        subdomain: agents
      - type: path
        domain: ai.jaycashman.com
        path: /agents
    env:
      AGENT_API_URL: http://10.96.200.30:8000
    secrets:
      - agent_api_key
```

**Field Definitions**:
- `name`: Unique identifier for the application
- `github_repo`: Repository in format `owner/repo`
- `container`: Target LXC container name (from vars.env)
- `container_ip`: IP address of target container
- `port`: Port application listens on
- `deploy_path`: Absolute path where app is deployed
- `health_endpoint`: HTTP endpoint for health checks
- `routes`: List of NGINX routing configurations (empty = internal only)
- `secrets`: List of secret keys from vault.yml
- `env`: Additional environment variables (non-secret)

**Rationale**:
- Declarative configuration (what, not how)
- Human-readable and version-controlled
- Ansible can loop over `applications` list to generate configs
- Easy to add new applications (just add to list)
- Supports multiple routing methods per app

**Validation**:
- Ansible role will validate required fields exist
- Health endpoint check confirms app is responsive
- NGINX config test before reload (`nginx -t`)

**Alternatives Considered**:
- **JSON**: Rejected - less human-readable, no comments
- **Separate file per app**: Rejected - harder to see full application inventory
- **Hardcoded in Ansible vars**: Rejected - not extensible (violates FR-030)

---

### 5. Deploywatch Integration

**Question**: How to extend deploywatch to support multiple applications from different GitHub repos?

**Decision**: Generate per-application deploywatch scripts from template, managed by Ansible

**Implementation**:

**Current deploywatch** (from spec 001):
- Systemd timer runs `/srv/deploywatch/deploywatch.sh` periodically
- Checks GitHub API for latest release
- Downloads and deploys if newer than current

**Extension for multiple apps**:
- Ansible generates `/srv/deploywatch/apps/<app-name>.sh` for each app in `apps.yml`
- Main `deploywatch.sh` loops over all scripts in `/srv/deploywatch/apps/`
- Each app script follows same pattern but with app-specific GitHub repo and deploy path

**Template** (`provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2`):
```bash
#!/usr/bin/env bash
# Generated by Ansible for {{ app.name }}
set -euo pipefail

APP_NAME="{{ app.name }}"
GITHUB_REPO="{{ app.github_repo }}"
DEPLOY_PATH="{{ app.deploy_path }}"
HEALTH_ENDPOINT="{{ app.health_endpoint }}"
CONTAINER_IP="{{ app.container_ip }}"
PORT="{{ app.port }}"

# Check latest GitHub release
LATEST=$(curl -s "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" | jq -r '.tag_name')

# Compare with current version
CURRENT_VERSION_FILE="${DEPLOY_PATH}/.version"
if [[ -f "$CURRENT_VERSION_FILE" ]]; then
  CURRENT=$(cat "$CURRENT_VERSION_FILE")
else
  CURRENT="none"
fi

if [[ "$LATEST" != "$CURRENT" ]]; then
  echo "[$(date)] New release ${LATEST} detected for ${APP_NAME} (current: ${CURRENT})"
  
  # Download release
  DOWNLOAD_URL=$(curl -s "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" | jq -r '.tarball_url')
  TEMP_DIR=$(mktemp -d)
  curl -sL "$DOWNLOAD_URL" -o "${TEMP_DIR}/release.tar.gz"
  
  # Extract and deploy
  mkdir -p "$DEPLOY_PATH"
  tar -xzf "${TEMP_DIR}/release.tar.gz" -C "$DEPLOY_PATH" --strip-components=1
  echo "$LATEST" > "$CURRENT_VERSION_FILE"
  
  # Install dependencies and restart (for Node.js apps)
  cd "$DEPLOY_PATH"
  npm install --production
  pm2 restart "${APP_NAME}" || pm2 start --name "${APP_NAME}"
  
  # Health check
  sleep 5
  if curl -sf "http://${CONTAINER_IP}:${PORT}${HEALTH_ENDPOINT}" > /dev/null; then
    echo "[$(date)] ${APP_NAME} ${LATEST} deployed successfully"
  else
    echo "[$(date)] ${APP_NAME} health check failed after deployment"
    exit 1
  fi
  
  # Cleanup
  rm -rf "$TEMP_DIR"
else
  echo "[$(date)] ${APP_NAME} is up to date (${CURRENT})"
fi
```

**Main deploywatch orchestrator** (`/srv/deploywatch/deploywatch.sh`):
```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Deploywatch cycle started at $(date) ==="

for APP_SCRIPT in /srv/deploywatch/apps/*.sh; do
  if [[ -f "$APP_SCRIPT" ]]; then
    echo "Running $(basename "$APP_SCRIPT")..."
    bash "$APP_SCRIPT" || echo "WARNING: $(basename "$APP_SCRIPT") failed"
  fi
done

echo "=== Deploywatch cycle completed at $(date) ==="
```

**Rationale**:
- Reuses existing deploywatch infrastructure
- Each app is independently deployable
- Failures in one app don't block others
- Ansible manages all deploywatch scripts (IaC compliance)

**Alternatives Considered**:
- **Single monolithic script**: Rejected - hard to maintain, not modular
- **Separate systemd timer per app**: Rejected - overhead, harder to manage
- **Docker-based deployment**: Rejected - adds complexity, containers-in-containers

---

### 6. Session Management Across Applications

**Question**: How to share authentication state between main portal (cashman) and sub-applications (agent-client)?

**Decision**: JWT tokens in HTTP-only cookies, validated by applications or NGINX auth_request module

**Approach**:

**Option A: Application-level JWT validation** (recommended for initial implementation)
1. Main portal (cashman) issues JWT on successful login
2. JWT stored in HTTP-only cookie with domain=`.ai.jaycashman.com` (wildcard subdomain)
3. All applications validate JWT on each request
4. Shared secret for JWT signing stored in vault.yml

**JWT Structure**:
```json
{
  "sub": "user_id_uuid",
  "email": "user@example.com",
  "roles": ["admin", "user"],
  "exp": 1234567890,
  "iss": "cashman-portal"
}
```

**Application Integration**:
- Each app uses same JWT library with shared secret
- Apps extract user ID and roles from validated JWT
- Apps set `app.user_id` PostgreSQL session variable for RLS

**NGINX Configuration** (pass cookie to backend):
```nginx
location / {
    proxy_pass http://backend;
    proxy_set_header Cookie $http_cookie;
    # ... other headers
}
```

**Option B: NGINX auth_request** (future enhancement if needed)
```nginx
location = /auth {
    internal;
    proxy_pass http://10.96.200.25:3000/api/validate-session;
    proxy_pass_request_body off;
    proxy_set_header Content-Length "";
    proxy_set_header X-Original-URI $request_uri;
}

location /agents {
    auth_request /auth;
    proxy_pass http://10.96.200.25:3001;
}
```

**Rationale**:
- JWT is stateless, no session store needed
- HTTP-only cookie prevents XSS attacks
- Wildcard domain cookie works for all subdomains
- Applications maintain control over authorization (can check roles)

**Alternatives Considered**:
- **Shared session store (Redis)**: Rejected - adds dependency, not necessary for current scale
- **OAuth2 between apps**: Rejected - over-engineered for internal applications
- **No shared auth**: Rejected - violates FR-023 (session state recognized by other apps)

---

## Summary of Decisions

| Area | Decision | Primary Rationale |
|------|----------|-------------------|
| **SSL** | Let's Encrypt + certbot with DNS-01 challenge | Free, automated, supports wildcard domains |
| **Secrets** | Ansible Vault + encrypted `.env` files | Built-in, secure at rest, twelve-factor compatible |
| **Routing** | NGINX server blocks (subdomain) + location blocks (path) | Supports both routing methods, efficient, centralized |
| **Config Schema** | YAML list with standard fields | Declarative, extensible, version-controlled |
| **Deploywatch** | Per-app scripts from Ansible template | Modular, reuses existing infrastructure |
| **Auth** | JWT in HTTP-only cookies | Stateless, secure, works across subdomains |

## Dependencies & Prerequisites

**From Spec 001 Infrastructure**:
- ✅ LXC containers provisioned (apps-lxc, openwebui-lxc, agent-lxc)
- ✅ Node.js and PM2 installed (node_common role)
- ✅ Deploywatch systemd timer running
- ✅ PostgreSQL for user management and RLS

**New External Dependencies**:
- NGINX (apt package)
- Certbot + DNS provider plugin (e.g., `python3-certbot-dns-cloudflare`)
- jq (JSON processing in bash scripts)

**Configuration Prerequisites**:
- DNS records: `ai.jaycashman.com` and `*.ai.jaycashman.com` → NGINX IP (10.96.200.24)
- DNS provider API credentials (for Let's Encrypt DNS-01 challenge)
- Ansible vault password (for secrets encryption)

## Next Steps (Phase 1)

1. **Data Model** (`data-model.md`):
   - Application definition schema (detailed field specs)
   - NGINX virtual host model
   - Secrets structure and access controls

2. **Contracts** (`contracts/`):
   - OpenAPI spec for agent-server API (if not exists)
   - Main portal authentication API
   - Health check endpoint standards

3. **Quickstart** (`quickstart.md`):
   - How to add a new application to `apps.yml`
   - How to manage secrets (add/rotate)
   - How to configure NGINX routing
   - How to troubleshoot deployment issues

