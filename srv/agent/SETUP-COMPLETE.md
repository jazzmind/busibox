# Setup Complete: Agent Server Ready for Testing & Deployment

## ✅ What's Been Done

### 1. Environment Configuration
- ✅ Created `.env.example` with all required variables
- ✅ Added comprehensive comments for each setting
- ✅ Separated local dev vs production deployment configs

### 2. Deployment Configuration
- ✅ Added `agent-server` to `provision/ansible/group_vars/all/apps.yml`
- ✅ Configured for **local source deployment** (Python project)
- ✅ Set up systemd service management
- ✅ Configured all required secrets and environment variables

### 3. Testing Setup
- ✅ Tests require **NO environment variables** (fully standalone)
- ✅ Use in-memory SQLite database
- ✅ All external services mocked (search/ingest/RAG/auth)
- ✅ Created comprehensive `TESTING.md` guide

### 4. Documentation
- ✅ Created `DEPLOYMENT.md` with step-by-step deployment guide
- ✅ Created `TESTING.md` with local and integration testing guide
- ✅ Updated configuration for Busibox integration

## 🚀 Next Steps

### Step 1: Run Tests Locally ✅ READY

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/srv/agent

# Install dependencies
pip install -e ".[dev]"

# Run all tests (NO .env needed!)
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=html
```

**Expected Result**: All tests pass with no environment setup required.

### Step 2: Deploy to Busibox Test Environment

#### A. Stop Old Agent Server (if running)

```bash
# SSH to agent-lxc
ssh root@<test-agent-lxc-ip>

# Check if old Node.js agent-server is running
systemctl status agent-server
pm2 list

# Stop and disable old service
systemctl stop agent-server
systemctl disable agent-server

# Or if using PM2
pm2 stop agent-server
pm2 delete agent-server
pm2 save

# Backup old deployment (optional)
mv /srv/agent /srv/agent-nodejs-backup
```

#### B. Deploy Python Agent Server

```bash
cd /Users/wessonnenreich/Code/sonnenreich/busibox/provision/ansible

# Deploy to test environment
make deploy-agent-server INV=inventory/test

# Or use full command
ansible-playbook -i inventory/test/hosts.yml site.yml --tags agent-server
```

**What Ansible Does**:
1. Syncs `srv/agent/` to agent-lxc via rsync
2. Creates Python virtual environment
3. Installs dependencies from `pyproject.toml`
4. Injects secrets from vault
5. Creates systemd service
6. Starts service and waits for health check

#### C. Verify Deployment

```bash
# SSH to agent-lxc
ssh root@<test-agent-lxc-ip>

# 1. Check service status
systemctl status agent-server
# Should show: Active: active (running)

# 2. Check health endpoint
curl http://localhost:8000/health
# Expected: {"status":"ok","service":"agent-server"}

# 3. View logs
journalctl -u agent-server -n 50 --no-pager

# 4. Check environment variables
cat /srv/agent/.env | grep -E "(DATABASE_URL|SEARCH_API_URL|LITELLM)"
```

### Step 3: Test on Busibox

#### A. Run Tests on Container

```bash
ssh root@<test-agent-lxc-ip>
cd /srv/agent

# Activate venv
source .venv/bin/activate

# Run tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=app --cov-report=term
```

#### B. Test Agent Execution

You'll need a valid JWT token from the Busibox auth service:

```bash
# Get a token (method depends on your auth setup)
# For testing, you can use the management client credentials

# Test agent execution
curl -X POST http://localhost:8000/runs \
  -H "Authorization: Bearer <your-jwt-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<existing-agent-uuid>",
    "input": {"prompt": "What is the weather?"}
  }'

# Expected response:
# {
#   "id": "...",
#   "status": "running" or "succeeded",
#   "agent_id": "...",
#   "input": {"prompt": "What is the weather?"},
#   "output": {...}  # If completed
# }
```

#### C. Verify Environment Variables

Check that all secrets are properly injected:

```bash
ssh root@<test-agent-lxc-ip>

# Check .env file
cat /srv/agent/.env

# Verify critical variables:
# - DATABASE_URL (should point to pg-lxc)
# - SEARCH_API_URL (should point to search API)
# - INGEST_API_URL (should point to ingest API)
# - RAG_API_URL (should point to Milvus)
# - LITELLM_BASE_URL (should point to liteLLM)
# - AUTH_CLIENT_ID (from vault)
# - AUTH_CLIENT_SECRET (from vault)
# - REDIS_URL (should point to Redis)
```

#### D. Test with LiteLLM Models

```bash
# From agent-lxc, test that LiteLLM is accessible
curl http://<litellm-ip>:4000/v1/models

# Should return list of available models

# Test agent execution with LiteLLM model
# (use the curl command from Step 3B above)
```

### Step 4: Continue Building Functionality

Once tests pass and deployment works, continue with:

1. **Add More Agents** - Create additional agent definitions
2. **Implement Workflows** - Build multi-step workflow execution
3. **Add Scorers** - Implement evaluation and scoring
4. **Expand Tests** - Add e2e tests for complete user journeys
5. **Add OpenTelemetry** - Wire up tracing and metrics
6. **Deploy to Production** - After validation on test

## 📋 Environment Variables Reference

### Required for Local Development

**NONE!** Tests use in-memory SQLite and mocks.

### Required for Busibox Deployment

These are injected by Ansible from vault:

- `DATABASE_URL` - PostgreSQL connection
- `REDIS_URL` - Redis connection
- `LITELLM_API_KEY` - LiteLLM API key
- `AUTH_CLIENT_ID` - OAuth client ID
- `AUTH_CLIENT_SECRET` - OAuth client secret

These are set by Ansible from `apps.yml`:

- `APP_NAME` - "agent-server"
- `ENVIRONMENT` - "test" or "production"
- `DEFAULT_MODEL` - "anthropic:claude-3-5-sonnet"
- `SEARCH_API_URL` - Search API endpoint
- `INGEST_API_URL` - Ingest API endpoint
- `RAG_API_URL` - RAG/Milvus endpoint
- `AUTH_TOKEN_URL` - OAuth token endpoint
- `CORS_ORIGINS` - "*"
- `LITELLM_BASE_URL` - LiteLLM endpoint

## 🔍 Troubleshooting Quick Reference

### Tests Fail Locally

```bash
# Reinstall dependencies
pip install -e ".[dev]"

# Check Python version
python --version  # Must be 3.11+

# Run with verbose output
pytest tests/ -vv -s
```

### Deployment Fails

```bash
# Check Ansible output for errors
# Common issues:
# 1. SSH access to agent-lxc
# 2. Python 3.11+ not installed on container
# 3. Secrets not in vault

# Manual deployment for debugging
ssh root@<agent-lxc-ip>
cd /srv/agent
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Service Won't Start

```bash
ssh root@<agent-lxc-ip>

# Check logs
journalctl -u agent-server -n 100 --no-pager

# Check if port is in use
netstat -tlnp | grep 8000

# Test manually
cd /srv/agent
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Health Check Fails

```bash
# Check service is running
systemctl status agent-server

# Test locally
curl -v http://localhost:8000/health

# Check environment
cat /srv/agent/.env
```

## 📚 Documentation Files

- `README.md` - Project overview and quick start
- `TESTING.md` - Comprehensive testing guide
- `DEPLOYMENT.md` - Deployment procedures and troubleshooting
- `.env.example` - Environment variable template
- `pyproject.toml` - Python dependencies and configuration

## 🎯 Success Criteria

### Local Testing ✅
- [ ] Tests run with no environment variables
- [ ] All tests pass
- [ ] Coverage > 60% (baseline)

### Deployment ✅
- [ ] Old Node.js agent-server stopped
- [ ] Python agent-server deployed to test
- [ ] Service starts successfully
- [ ] Health check returns 200

### Integration Testing ✅
- [ ] Tests run on container
- [ ] Environment variables correct
- [ ] Can connect to Busibox services
- [ ] Agent execution works with LiteLLM

### Production Ready 🚧
- [ ] All tests pass on test environment
- [ ] No errors in logs
- [ ] Performance acceptable
- [ ] Ready for production deployment

## 🚦 Current Status

- ✅ Code complete (Option 1 MVP)
- ✅ Tests added (unit + integration)
- ✅ Configuration complete
- ✅ Documentation complete
- ⏳ **Ready for Step 1: Local Testing**

## 💡 Quick Commands

```bash
# Local testing
cd srv/agent && pip install -e ".[dev]" && pytest tests/ -v

# Deploy to test
cd provision/ansible && make deploy-agent-server INV=inventory/test

# Check service on test
ssh root@<test-agent-ip> "systemctl status agent-server"

# View logs on test
ssh root@<test-agent-ip> "journalctl -u agent-server -f"

# Deploy to production (after test validation)
cd provision/ansible && make deploy-agent-server
```

---

**Ready to proceed!** Start with Step 1 (local testing) to verify everything works, then move to deployment.


