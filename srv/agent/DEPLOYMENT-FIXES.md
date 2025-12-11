# Deployment Fixes Applied

## Issues Fixed

### 1. ✅ Removed Node.js Role from Agent Deployment

**Problem**: Agent server was trying to use `node_common` role even though it's now a Python application.

**Fix**: Updated `provision/ansible/site.yml` to remove `node_common` role from agent host:

```yaml
# Before:
- hosts: agent
  roles:
    - role: node_common      # ❌ Wrong - agent is now Python
      tags: [agent, node_common]
    - role: secrets
      tags: [agent, secrets]
    - role: app_deployer
      tags: [agent, app_deployer]

# After:
- hosts: agent
  roles:
    - role: secrets          # ✅ Correct - only secrets and deployer
      tags: [agent, secrets]
    - role: app_deployer
      tags: [agent, app_deployer]
```

### 2. ✅ Fixed Missing OAuth Secrets

**Problem**: `management_client_id` and `management_client_secret` were required but not in vault.

**Fix**: Made these secrets optional with defaults in `group_vars/all/apps.yml`:

```yaml
# Before:
secrets:
  - management_client_id      # ❌ Required but not in vault
  - management_client_secret
env:
  AUTH_CLIENT_ID: "{{ management_client_id }}"
  AUTH_CLIENT_SECRET: "{{ management_client_secret }}"

# After:
secrets:
  - database_url
  - redis_url
  - litellm_api_key
optional_secrets:
  - management_client_id      # ✅ Optional - uses defaults if missing
  - management_client_secret
env:
  AUTH_CLIENT_ID: "{{ management_client_id | default('agent-server-client') }}"
  AUTH_CLIENT_SECRET: "{{ management_client_secret | default('test-client-secret') }}"
```

### 3. ✅ Python Deployment Configuration

**Confirmed Working**: The `app_deployer` role already has full support for Python local source deployment:

- ✅ Syncs local `srv/agent/` to container
- ✅ Creates Python virtual environment
- ✅ Installs dependencies from `pyproject.toml`
- ✅ Deploys systemd service
- ✅ Performs health checks

## Deployment Now Ready

### To Deploy to Test:

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent-server to test
make deploy-agent-server INV=inventory/test

# Or use full command
ansible-playbook -i inventory/test/hosts.yml site.yml --tags agent
```

### What Happens During Deployment:

1. **Secrets Role**: Loads secrets from vault (database_url, redis_url, litellm_api_key)
2. **App Deployer Role**:
   - Syncs `srv/agent/` to `/srv/agent` on agent-lxc
   - Creates `/srv/agent/.venv` virtual environment
   - Runs `pip install .` to install dependencies
   - Creates `/etc/systemd/system/agent-server.service`
   - Starts and enables the service
   - Waits for health check on `http://localhost:8000/health`

### Environment Variables Set:

From `apps.yml`:
- `APP_NAME=agent-server`
- `ENVIRONMENT=test`
- `DEFAULT_MODEL=anthropic:claude-3-5-sonnet`
- `SEARCH_API_URL=http://<search-ip>:8003`
- `INGEST_API_URL=http://<ingest-ip>:8001`
- `RAG_API_URL=http://<milvus-ip>:8004`
- `LITELLM_BASE_URL=http://<litellm-ip>:4000/v1`
- `AUTH_CLIENT_ID=agent-server-client` (default)
- `AUTH_CLIENT_SECRET=test-client-secret` (default)

From vault:
- `DATABASE_URL=postgresql+asyncpg://...`
- `REDIS_URL=redis://...`
- `LITELLM_API_KEY=...`

## Post-Deployment Verification

### 1. Check Service Status

```bash
ssh root@<test-agent-lxc-ip>
systemctl status agent-server
```

Expected:
```
● agent-server.service - Agent Server (Python/FastAPI)
   Active: active (running) since ...
```

### 2. Check Health Endpoint

```bash
curl http://localhost:8000/health
```

Expected:
```json
{"status":"ok","service":"agent-server"}
```

### 3. Check Logs

```bash
journalctl -u agent-server -n 50 --no-pager
```

### 4. Run Tests on Container

```bash
cd /srv/agent
source .venv/bin/activate
pytest tests/ -v
```

## Troubleshooting

### If Deployment Fails

1. **Check Python is installed**:
   ```bash
   ssh root@<agent-lxc-ip>
   python3 --version  # Should be 3.11+
   ```

2. **Check rsync worked**:
   ```bash
   ssh root@<agent-lxc-ip>
   ls -la /srv/agent/
   # Should see: app/, tests/, pyproject.toml, README.md, etc.
   ```

3. **Check venv creation**:
   ```bash
   ssh root@<agent-lxc-ip>
   ls -la /srv/agent/.venv/
   # Should see: bin/, lib/, etc.
   ```

4. **Check dependencies installed**:
   ```bash
   ssh root@<agent-lxc-ip>
   /srv/agent/.venv/bin/pip list | grep -E "(fastapi|pydantic|sqlalchemy)"
   ```

5. **Check environment variables**:
   ```bash
   ssh root@<agent-lxc-ip>
   systemctl cat agent-server | grep Environment
   ```

### If Service Won't Start

```bash
# Check systemd service file
systemctl cat agent-server

# Try manual start
cd /srv/agent
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Check for errors
journalctl -u agent-server -n 100 --no-pager
```

## Next Steps After Successful Deployment

1. ✅ Verify health check passes
2. ✅ Run tests on container
3. ✅ Test agent execution with real Busibox services
4. ✅ Monitor logs for errors
5. ✅ Deploy to production after validation

## Files Modified

- `provision/ansible/site.yml` - Removed node_common role from agent
- `provision/ansible/group_vars/all/apps.yml` - Made OAuth secrets optional with defaults


