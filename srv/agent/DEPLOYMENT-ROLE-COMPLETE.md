# Agent API Deployment Role - Complete

## ✅ What Was Done

### 1. Removed agent-server from apps.yml
- Agent-server is **not** a Node.js app, so it shouldn't be in the `applications` list
- Removed the entire agent-server entry from `group_vars/all/apps.yml`

### 2. Created Dedicated `agent_api` Role
Following the same pattern as `search_api` and `ingest_api`, created a complete role:

```
roles/agent_api/
├── defaults/main.yml          # Default variables
├── tasks/main.yml             # Deployment tasks
├── templates/
│   ├── agent-api.env.j2       # Environment file template
│   └── agent-api.service.j2   # Systemd service template
└── handlers/main.yml          # Service restart handlers
```

### 3. Updated site.yml
Changed agent host to use the new `agent_api` role:

```yaml
# Before:
- hosts: agent
  roles:
    - role: node_common         # ❌ Wrong
    - role: secrets
    - role: app_deployer

# After:
- hosts: agent
  roles:
    - role: agent_api           # ✅ Correct - dedicated Python API role
      tags: [agent, agent_api]
```

## 🔧 How It Works

### Secrets Access
The role accesses secrets from the vault at `secrets['agent-server']`:

```yaml
# In vault.yml:
secrets:
  agent-server:
    database_url: "postgresql://..."
    redis_url: "redis://..."
    litellm_api_key: "..."
    management_client_id: "admin-management-client"
    management_client_secret: "..."
```

### Environment Variables Set
The `agent-api.env.j2` template creates `/srv/agent/.env` with:

```bash
APP_NAME=agent-server
ENVIRONMENT=test  # or production
DEFAULT_MODEL=anthropic:claude-3-5-sonnet
SEARCH_API_URL=http://<search-ip>:8003
INGEST_API_URL=http://<ingest-ip>:8001
RAG_API_URL=http://<milvus-ip>:8004
AUTH_CLIENT_ID=admin-management-client
AUTH_CLIENT_SECRET=<from-vault>
DATABASE_URL=<from-vault>
REDIS_URL=<from-vault>
LITELLM_BASE_URL=http://<litellm-ip>:4000/v1
```

### Deployment Process
1. Creates `agent` system user
2. Creates `/srv/agent` directory structure
3. Syncs source code from `srv/agent/` to container
4. Creates Python virtual environment at `/srv/agent/.venv`
5. Installs dependencies with `pip install -e .`
6. Deploys `.env` file from template
7. Deploys systemd service
8. Starts service and waits for health check

## 🚀 How to Deploy

### Deploy to Test Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent API to test
make agent INV=inventory/test

# Or use full command
ansible-playbook -i inventory/test/hosts.yml site.yml --tags agent
```

### Deploy to Production

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent API to production
make agent

# Or use full command
ansible-playbook -i inventory/production/hosts.yml site.yml --tags agent
```

## 📋 Verification Steps

### 1. Check Service Status

```bash
ssh root@<agent-lxc-ip>
systemctl status agent-api
```

Expected output:
```
● agent-api.service - Agent API Service (Python/FastAPI)
   Active: active (running) since ...
```

### 2. Check Health Endpoint

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{"status":"ok","service":"agent-server"}
```

### 3. Check Environment Variables

```bash
ssh root@<agent-lxc-ip>
cat /srv/agent/.env
```

Should show all variables from vault.

### 4. Check Logs

```bash
journalctl -u agent-api -n 50 --no-pager
```

### 5. Run Tests

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent
source .venv/bin/activate
pytest tests/ -v
```

## 🔍 Troubleshooting

### If Secrets Are Missing

Check that vault has the required secrets:
```bash
cd provision/ansible
ansible-vault view inventory/test/group_vars/all/vault.yml | grep -A 10 "agent-server:"
```

Required secrets:
- `secrets.agent-server.database_url`
- `secrets.agent-server.redis_url`
- `secrets.agent-server.litellm_api_key`
- `secrets.agent-server.management_client_id`
- `secrets.agent-server.management_client_secret`

### If Service Won't Start

```bash
# Check systemd service
systemctl cat agent-api

# Check environment file
cat /srv/agent/.env

# Try manual start
cd /srv/agent
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### If Dependencies Fail to Install

```bash
# Check Python version
python3 --version  # Should be 3.11+

# Check venv
ls -la /srv/agent/.venv/

# Reinstall
cd /srv/agent
rm -rf .venv
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## 📝 Files Modified

1. **`provision/ansible/group_vars/all/apps.yml`**
   - Removed agent-server entry (it's not a Node.js app)

2. **`provision/ansible/site.yml`**
   - Changed agent host to use `agent_api` role instead of `node_common` + `app_deployer`

3. **`provision/ansible/roles/agent_api/`** (NEW)
   - Created complete role following search_api/ingest_api pattern
   - Includes defaults, tasks, templates, handlers

## ✅ Benefits of This Approach

1. **Consistency**: Follows the same pattern as other Python APIs (search_api, ingest_api)
2. **Secrets**: Properly accesses vault secrets at `secrets['agent-server']`
3. **Simplicity**: Single dedicated role, no mixing with Node.js or app_deployer
4. **Maintainability**: Clear separation from Node.js apps
5. **Testability**: Can deploy/test agent API independently

## 🎯 Next Steps

1. ✅ Deploy to test environment
2. ✅ Verify health check passes
3. ✅ Run tests on container
4. ✅ Test agent execution with real services
5. ✅ Monitor logs for errors
6. ✅ Deploy to production after validation


