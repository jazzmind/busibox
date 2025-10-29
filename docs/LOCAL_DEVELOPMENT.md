# Local Development Environment

This guide shows how to run the entire Cashman infrastructure locally on macOS using Docker + Ansible.

## Why This Approach?

- ✅ **Same Ansible roles** as production - test your actual deployment code
- ✅ **Fast iteration** - no Proxmox VM overhead
- ✅ **Isolated environment** - containers are disposable
- ✅ **Debug easily** - `docker exec` into any service
- ✅ **Works on macOS** - no Linux VM required

## Prerequisites

1. **Docker Desktop** for Mac
   ```bash
   brew install --cask docker
   ```

2. **Ansible** with Docker connection plugin
   ```bash
   brew install ansible
   pip3 install docker
   ```

3. **Required repositories** cloned:
   ```
   ~/Code/sonnenreich/
   ├── busibox/          # Infrastructure (this repo)
   ├── ai-portal/        # Next.js app
   ├── agent-server/     # Agent API
   └── doc-intel/        # Ingest worker
   ```

## Quick Start

### 1. Start Docker Containers

```bash
cd ~/Code/sonnenreich/busibox

# Start all containers
docker compose -f docker-compose.local.yml up -d

# Verify containers are running
docker ps
```

You should see 7 containers running:
- `local-postgres` (172.20.0.10)
- `local-minio` (172.20.0.11)
- `local-milvus` (172.20.0.12)
- `local-agent-api` (172.20.0.13)
- `local-ingest` (172.20.0.14)
- `local-apps` (172.20.0.15)
- `local-proxy` (172.20.0.16)

### 2. Configure Secrets

```bash
cd provision/ansible

# Create local vault password file (for convenience)
echo "local-dev-password" > .vault_pass_local

# Edit secrets for local environment
ansible-vault edit --vault-password-file .vault_pass_local \
  inventory/local/group_vars/vault.yml
```

Add your API keys:
```yaml
---
secrets:
  github_token: ghp_your_github_token_here
  
  ai-portal:
    database_url: "postgresql://postgres:devpassword@172.20.0.10:5432/cashman"
    better_auth_secret: "local-dev-secret-change-me"
    better_auth_url: "http://local.ai.localhost:3000"
    resend_api_key: "re_your_resend_key"
    email_from: "noreply@localhost"
    openai_api_key: "sk-your-openai-key"
    sso_jwt_secret: "local-sso-secret"
    litellm_api_key: "sk-local-dev-key"
    admin_email: "admin@localhost"
    allowed_email_domains: "*"
```

### 3. Deploy with Ansible

```bash
cd provision/ansible

# Deploy everything
ansible-playbook -i inventory/local/hosts.yml site.yml \
  --vault-password-file .vault_pass_local

# Or deploy specific services
ansible-playbook -i inventory/local/hosts.yml site.yml \
  --vault-password-file .vault_pass_local \
  --limit apps --tags nextjs
```

### 4. Access Services

- **AI Portal**: http://localhost:3000
- **Agent API**: http://localhost:3001
- **MinIO Console**: http://localhost:9001 (minioadmin/minioadmin)
- **PostgreSQL**: localhost:5432 (postgres/devpassword)

## Development Workflow

### Testing Configuration Changes

1. **Edit Ansible roles** in `provision/ansible/roles/`
2. **Run playbook** against local containers:
   ```bash
   ansible-playbook -i inventory/local/hosts.yml site.yml \
     --limit apps --tags nextjs
   ```
3. **Check logs**:
   ```bash
   docker exec local-apps journalctl -u ai-portal -f
   ```

### Debugging Application Issues

```bash
# Get shell in container
docker exec -it local-apps bash

# Check PM2 status
su - appuser
pm2 status
pm2 logs ai-portal

# Check environment
cat /srv/apps/ai-portal/current/.env

# Restart service
systemctl restart ai-portal
```

### Resetting Everything

```bash
# Stop and remove containers
docker compose -f docker-compose.local.yml down -v

# Start fresh
docker compose -f docker-compose.local.yml up -d
ansible-playbook -i inventory/local/hosts.yml site.yml
```

## Differences from Production

| Aspect | Local | Proxmox |
|--------|-------|---------|
| **Connection** | Docker | SSH |
| **Networking** | Docker bridge | LXC bridge |
| **Storage** | Docker volumes | ZFS datasets |
| **SSL** | None | Let's Encrypt |
| **DNS** | localhost | Real domains |
| **GPU** | N/A | Passthrough |

## Troubleshooting

### "Cannot connect to Docker daemon"
```bash
# Start Docker Desktop
open -a Docker

# Wait for Docker to start
docker ps
```

### "Container not found"
```bash
# Ensure containers are running
docker compose -f docker-compose.local.yml up -d

# Check container names
docker ps --format "table {{.Names}}\t{{.Status}}"
```

### "Permission denied" in container
```bash
# Containers need systemd - check they're running with /sbin/init
docker exec local-apps ps aux | grep systemd
```

### Ansible can't connect to containers
```bash
# Install Docker Python library
pip3 install docker

# Test connection
ansible -i inventory/local/hosts.yml all -m ping
```

### Port already in use
```bash
# Find what's using the port
lsof -i :3000

# Kill the process or change the port in docker-compose.local.yml
```

## Advanced Usage

### Running Individual Services

```bash
# Just start PostgreSQL and MinIO
docker compose -f docker-compose.local.yml up -d local-postgres local-minio

# Deploy just the agent API
ansible-playbook -i inventory/local/hosts.yml site.yml --limit agent
```

### Using Host Services

You can mix local containers with services running on your host:

```bash
# Run PostgreSQL on host instead of container
brew install postgresql@16
brew services start postgresql@16

# Update inventory/local/group_vars/all.yml
postgres_host: "host.docker.internal"
```

### Connecting to Proxmox Services

Point local dev to your Proxmox test environment:

```yaml
# inventory/local/group_vars/all.yml
litellm_base_url: "http://10.96.201.210:4000"  # Use Proxmox LiteLLM
```

## Next Steps

Once your local environment is working:

1. ✅ Test configuration changes locally first
2. ✅ Deploy to Proxmox test environment
3. ✅ Verify in test
4. ✅ Deploy to production

This workflow catches issues early and speeds up development significantly.






