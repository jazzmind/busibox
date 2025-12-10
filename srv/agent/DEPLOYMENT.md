# Deployment Guide: Agent Server

## Overview

The agent server is deployed to the `agent-lxc` container on Busibox using Ansible with **local source deployment** (not GitHub).

## Deployment Methods

### 1. Deploy to Test Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent-server to test
make deploy-agent-server INV=inventory/test

# Or use the full command
ansible-playbook -i inventory/test/hosts.yml site.yml --tags agent-server
```

### 2. Deploy to Production

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent-server to production
make deploy-agent-server

# Or use the full command
ansible-playbook -i inventory/production/hosts.yml site.yml --tags agent-server
```

### 3. Stop Old Agent Server (Node.js)

If the old Node.js agent-server is still running, stop it first:

```bash
# SSH to agent-lxc container
ssh root@<agent-lxc-ip>

# Check if old service is running
systemctl status agent-server
pm2 list

# Stop old service
systemctl stop agent-server
systemctl disable agent-server

# Or if using PM2
pm2 stop agent-server
pm2 delete agent-server
pm2 save

# Remove old deployment
rm -rf /srv/agent-old
mv /srv/agent /srv/agent-old  # Backup if needed
```

## Deployment Process

The Ansible deployment performs these steps:

1. **Sync Local Source**
   - Uses `rsync` to copy `srv/agent/` to agent-lxc
   - Excludes: `.venv/`, `__pycache__/`, `.pytest_cache/`, `.env`

2. **Create Python Virtual Environment**
   - Creates `.venv` in `/srv/agent`
   - Installs dependencies from `pyproject.toml`

3. **Configure Environment**
   - Injects secrets from Ansible vault
   - Sets environment variables from `apps.yml`
   - Creates `.env` file on container

4. **Deploy Systemd Service**
   - Creates `/etc/systemd/system/agent-server.service`
   - Configures service to run uvicorn
   - Enables auto-restart on failure

5. **Start Service**
   - Reloads systemd daemon
   - Restarts agent-server service
   - Waits for health check

6. **Verify Deployment**
   - Checks `/health` endpoint returns 200
   - Verifies service is running
   - Checks logs for errors

## Configuration

### apps.yml Configuration

Located at: `provision/ansible/group_vars/all/apps.yml`

```yaml
- name: agent-server
  local_src: srv/agent  # Deploy from local source
  marker_file: pyproject.toml
  container: agent-lxc
  container_ip: "{{ agent_ip }}"
  port: 8000
  deploy_path: /srv/agent
  health_endpoint: /health
  start_command: ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
  process_manager: systemd
  routes: []  # Internal only
  secrets:
    - database_url
    - redis_url
    - litellm_api_key
    - management_client_id
    - management_client_secret
  env:
    APP_NAME: "agent-server"
    ENVIRONMENT: "{{ environment }}"
    DEFAULT_MODEL: "anthropic:claude-3-5-sonnet"
    SEARCH_API_URL: "http://{{ search_api_ip }}:8003"
    INGEST_API_URL: "http://{{ ingest_ip }}:8001"
    RAG_API_URL: "http://{{ milvus_ip }}:8004"
    # ... more env vars
```

### Required Secrets (Ansible Vault)

These secrets must be defined in your Ansible vault:

- `database_url` - PostgreSQL connection string
- `redis_url` - Redis connection string
- `litellm_api_key` - LiteLLM API key
- `management_client_id` - OAuth client ID for token exchange
- `management_client_secret` - OAuth client secret

## Post-Deployment Verification

### 1. Check Service Status

```bash
ssh root@<agent-lxc-ip>

# Check systemd service
systemctl status agent-server

# Should show:
# ● agent-server.service - Agent Server (Python/FastAPI)
#    Active: active (running) since ...
```

### 2. Check Health Endpoint

```bash
# From agent-lxc
curl http://localhost:8000/health

# Expected response:
# {"status":"ok","service":"agent-server"}

# From Proxmox host or admin workstation
curl http://<agent-lxc-ip>:8000/health
```

### 3. Check Logs

```bash
ssh root@<agent-lxc-ip>

# View service logs
journalctl -u agent-server -f

# View recent logs
journalctl -u agent-server -n 100 --no-pager

# Check for errors
journalctl -u agent-server -p err -n 50
```

### 4. Test Agent Execution

```bash
# From agent-lxc or with proper auth token
curl -X POST http://localhost:8000/runs \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent-uuid>",
    "input": {"prompt": "test query"}
  }'

# Expected response:
# {
#   "id": "...",
#   "status": "running",
#   "agent_id": "...",
#   "input": {"prompt": "test query"},
#   ...
# }
```

### 5. Run Tests on Container

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent

# Activate venv
source .venv/bin/activate

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
journalctl -u agent-server -n 100 --no-pager

# Common issues:
# 1. Missing dependencies
cd /srv/agent && source .venv/bin/activate && pip install -e .

# 2. Database connection failed
# Check DATABASE_URL in /srv/agent/.env

# 3. Port already in use
netstat -tlnp | grep 8000
# Kill process if needed

# 4. Permission errors
chown -R root:root /srv/agent
chmod +x /srv/agent/.venv/bin/uvicorn
```

### Health Check Fails

```bash
# Check if service is listening
netstat -tlnp | grep 8000

# Test locally
curl -v http://localhost:8000/health

# Check firewall
ufw status
# Should allow port 8000 from internal network

# Check environment variables
cat /srv/agent/.env
# Verify all required vars are set
```

### Import Errors

```bash
# Reinstall dependencies
cd /srv/agent
source .venv/bin/activate
pip install -e .

# Check Python version
python --version  # Should be 3.11+

# Check installed packages
pip list | grep -E "(fastapi|pydantic|sqlalchemy)"
```

### Database Connection Errors

```bash
# Test PostgreSQL connection
psql -h <pg-ip> -U agent_server -d agent_server

# Check DATABASE_URL format
# Should be: postgresql+asyncpg://user:pass@host:5432/db

# Verify pg-lxc is accessible
ping <pg-ip>
telnet <pg-ip> 5432
```

### Token Exchange Errors

```bash
# Check OAuth credentials
echo $AUTH_CLIENT_ID
echo $AUTH_CLIENT_SECRET

# Test token endpoint
curl -X POST http://<auth-service>:8080/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=$AUTH_CLIENT_ID&client_secret=$AUTH_CLIENT_SECRET"
```

## Rollback

If deployment fails, rollback to previous version:

```bash
ssh root@<agent-lxc-ip>

# Stop new service
systemctl stop agent-server

# Restore old version (if backed up)
rm -rf /srv/agent
mv /srv/agent-old /srv/agent

# Restart old service
systemctl start agent-server

# Or restore old Node.js version
# ... (follow old deployment process)
```

## Monitoring

### Service Health

```bash
# Add to monitoring system
watch -n 10 'curl -s http://<agent-lxc-ip>:8000/health | jq'
```

### Log Monitoring

```bash
# Real-time log monitoring
journalctl -u agent-server -f

# Error monitoring
journalctl -u agent-server -p err -f
```

### Resource Usage

```bash
# Check memory/CPU
systemctl status agent-server

# Detailed stats
ps aux | grep uvicorn
```

## Continuous Deployment

For automated deployments:

```bash
# Add to CI/CD pipeline
- name: Deploy to Busibox Test
  run: |
    cd provision/ansible
    make deploy-agent-server INV=inventory/test

- name: Verify Deployment
  run: |
    curl -f http://<agent-lxc-ip>:8000/health
```

## Next Steps

1. ✅ Deploy to test environment
2. ✅ Verify health checks pass
3. ✅ Run tests on container
4. ✅ Test agent execution with real services
5. ✅ Monitor logs for errors
6. ✅ Deploy to production after validation
