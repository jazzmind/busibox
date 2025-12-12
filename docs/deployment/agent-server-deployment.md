---
title: Agent Server Deployment Guide
category: deployment
created: 2025-12-12
updated: 2025-12-12
status: active
tags: [agent-server, deployment, ansible, python]
---

# Agent Server Deployment Guide

## Overview

The agent server is a Python/FastAPI application deployed to the `agent-lxc` container using Ansible. It provides AI agent execution, tool orchestration, workflow management, and intelligent query routing.

## Prerequisites

- **Python**: 3.11+ on agent-lxc container
- **PostgreSQL**: Database with `agent_server` schema
- **Redis**: For caching and queues
- **LiteLLM**: For LLM access
- **Ansible**: For deployment automation

## Quick Deployment

### Test Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent server to test
make agent INV=inventory/test
```

### Production Environment

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy agent server to production
make agent
```

## Deployment Architecture

### Ansible Role: `agent_api`

The agent server uses a dedicated Ansible role at `provision/ansible/roles/agent_api/`:

```
roles/agent_api/
├── defaults/main.yml          # Default variables
├── tasks/main.yml             # Deployment tasks
├── templates/
│   ├── agent-api.env.j2       # Environment file template
│   └── agent-api.service.j2   # Systemd service template
└── handlers/main.yml          # Service restart handlers
```

### Deployment Process

The Ansible deployment performs these steps:

1. **Stop Service**: Stops existing agent-api service
2. **Clean Directory**: Removes old `/srv/agent` directory
3. **Sync Source**: Copies `srv/agent/` from workstation to container
4. **Create Venv**: Creates Python virtual environment at `/srv/agent/.venv`
5. **Install Dependencies**: Runs `pip install -e .` from `pyproject.toml`
6. **Run Migrations**: Applies database migrations with `alembic upgrade head`
7. **Deploy Config**: Creates `.env` file from template with secrets
8. **Deploy Service**: Creates systemd service file
9. **Start Service**: Starts agent-api service
10. **Health Check**: Waits for `/health` endpoint to return 200

## Configuration

### Environment Variables

#### From Vault (`secrets['agent-server']`)

```bash
DATABASE_URL=postgresql+asyncpg://user:pass@pg-lxc:5432/agent_server
REDIS_URL=redis://ingest-lxc:6379/0
LITELLM_API_KEY=<litellm-virtual-key>
AUTH_CLIENT_ID=admin-management-client
AUTH_CLIENT_SECRET=<oauth-secret>
```

#### From Inventory

```bash
APP_NAME=agent-server
ENVIRONMENT=test  # or production
DEFAULT_MODEL=research  # Model purpose from model_registry.yml
SEARCH_API_URL=http://127.0.0.1:8003
INGEST_API_URL=http://10.96.201.206:8001
RAG_API_URL=http://10.96.201.204:8004
LITELLM_BASE_URL=http://10.96.201.207:4000/v1
CORS_ORIGINS=["*"]
LOG_LEVEL=INFO
```

### Systemd Service

The service runs as the `agent` user:

```ini
[Unit]
Description=Agent API Service (Python/FastAPI)
After=network.target

[Service]
Type=simple
User=agent
WorkingDirectory=/srv/agent
EnvironmentFile=/srv/agent/.env
ExecStart=/srv/agent/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 4111
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Database Migrations

### Applying Migrations

Migrations are automatically applied during deployment. To apply manually:

```bash
ssh root@<agent-lxc-ip>
cd /srv/agent
source .venv/bin/activate
alembic upgrade head
```

### Migration History

Check current migration version:

```bash
alembic current
```

View migration history:

```bash
alembic history
```

### Rollback Migration

```bash
# Rollback one version
alembic downgrade -1

# Rollback to specific version
alembic downgrade <revision>
```

## Post-Deployment Verification

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
curl http://<agent-lxc-ip>:4111/health
```

Expected response:
```json
{"status":"ok"}
```

### 3. Check Database Tables

```bash
ssh root@<pg-lxc-ip>
sudo -u postgres psql -d agent_server -c "\dt"
```

Expected tables:
- `agent_definitions`
- `tool_definitions`
- `workflow_definitions`
- `eval_definitions`
- `run_records`
- `token_grants`
- `dispatcher_decision_log`
- `alembic_version`

### 4. Check Logs

```bash
ssh root@<agent-lxc-ip>
journalctl -u agent-api -n 50 --no-pager
```

Look for:
- ✅ "Application startup complete"
- ✅ "Uvicorn running on http://0.0.0.0:4111"
- ❌ No error messages

### 5. Test API Endpoints

```bash
# Test agent list (requires auth token)
curl -X GET http://<agent-lxc-ip>:4111/agents \
  -H "Authorization: Bearer <token>"

# Test dispatcher routing
curl -X POST http://<agent-lxc-ip>:4111/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the weather in London?",
    "available_tools": ["web_search"],
    "available_agents": [],
    "user_settings": {"enabled_tools": ["web_search"]}
  }'
```

## Troubleshooting

### Service Won't Start

**Check logs**:
```bash
journalctl -u agent-api -n 100 --no-pager
```

**Common issues**:

1. **Missing dependencies**:
   ```bash
   cd /srv/agent
   source .venv/bin/activate
   pip install -e .
   ```

2. **Database connection failed**:
   ```bash
   # Check DATABASE_URL in .env
   cat /srv/agent/.env | grep DATABASE_URL
   
   # Test connection
   psql "postgresql://user:pass@pg-lxc:5432/agent_server"
   ```

3. **Port already in use**:
   ```bash
   netstat -tlnp | grep 4111
   # Kill process if needed
   ```

4. **Permission errors**:
   ```bash
   chown -R agent:agent /srv/agent
   chmod +x /srv/agent/.venv/bin/uvicorn
   ```

### Health Check Fails

**Check if service is listening**:
```bash
netstat -tlnp | grep 4111
```

**Test locally on container**:
```bash
curl -v http://localhost:4111/health
```

**Check firewall**:
```bash
ufw status
# Should allow port 4111 from internal network
```

### Import Errors

**Reinstall dependencies**:
```bash
cd /srv/agent
source .venv/bin/activate
pip install -e .
```

**Check Python version**:
```bash
python --version  # Should be 3.11+
```

**Check installed packages**:
```bash
pip list | grep -E "(fastapi|pydantic|sqlalchemy)"
```

### Database Connection Errors

**Test PostgreSQL connection**:
```bash
psql -h <pg-ip> -U agent_server -d agent_server
```

**Verify DATABASE_URL format**:
```bash
# Should be: postgresql+asyncpg://user:pass@host:5432/db
cat /srv/agent/.env | grep DATABASE_URL
```

**Check pg-lxc accessibility**:
```bash
ping <pg-ip>
telnet <pg-ip> 5432
```

### LiteLLM Connection Errors

**Test LiteLLM endpoint**:
```bash
curl http://<litellm-ip>:4000/v1/models
```

**Check environment variables**:
```bash
cat /srv/agent/.env | grep LITELLM
```

**Verify API key**:
```bash
curl -H "Authorization: Bearer <LITELLM_API_KEY>" \
  http://<litellm-ip>:4000/v1/models
```

## Rollback Procedure

If deployment fails, rollback to previous version:

### 1. Stop New Service

```bash
ssh root@<agent-lxc-ip>
systemctl stop agent-api
```

### 2. Rollback Database

```bash
cd /srv/agent
source .venv/bin/activate
alembic downgrade -1
```

Or restore from backup:
```bash
psql -U agent_server -d agent_server < /tmp/backup_YYYYMMDD.sql
```

### 3. Rollback Code

```bash
cd /srv/agent
git checkout <previous-commit>
pip install -e .
```

### 4. Restart Service

```bash
systemctl start agent-api
systemctl status agent-api
```

### 5. Verify Rollback

```bash
curl http://localhost:4111/health
```

## Monitoring

### Service Health

```bash
# Add to monitoring system
watch -n 10 'curl -s http://<agent-lxc-ip>:4111/health | jq'
```

### Log Monitoring

```bash
# Real-time logs
journalctl -u agent-api -f

# Error logs only
journalctl -u agent-api -p err -f
```

### Resource Usage

```bash
# Check memory/CPU
systemctl status agent-api

# Detailed stats
ps aux | grep uvicorn
```

### Database Queries

```sql
-- Check decision log growth
SELECT COUNT(*) FROM dispatcher_decision_log;

-- Check average confidence scores
SELECT AVG(confidence) FROM dispatcher_decision_log;

-- Check personal agents created
SELECT COUNT(*) FROM agent_definitions WHERE is_builtin = false;

-- Check snapshots being captured
SELECT COUNT(*) FROM run_records WHERE definition_snapshot IS NOT NULL;
```

## Continuous Deployment

For automated deployments:

```yaml
# .github/workflows/deploy-agent.yml
- name: Deploy to Busibox Test
  run: |
    cd provision/ansible
    make agent INV=inventory/test

- name: Verify Deployment
  run: |
    curl -f http://<agent-lxc-ip>:4111/health
```

## Related Documentation

- **Architecture**: `docs/architecture/agent-server-architecture.md`
- **Testing**: `docs/guides/agent-server-testing.md`
- **API Reference**: `docs/reference/agent-server-api.md`
- **Troubleshooting**: `docs/troubleshooting/agent-server-issues.md`

