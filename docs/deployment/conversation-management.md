---
created: 2025-12-12
updated: 2025-12-12
status: active
category: deployment
tags: [agent-server, conversations, deployment, migration]
---

# Deploying Conversation Management

## Overview

This guide covers deploying the conversation management feature to the agent-server in test and production environments.

**Feature**: Conversation and message management for agent-client chat interface  
**Migration**: `003_add_conversations.py`  
**Endpoints**: 8 new endpoints  
**Tests**: 27 integration tests

---

## Pre-Deployment Checklist

- [ ] Code merged to main branch
- [ ] All tests passing locally
- [ ] Database backup created
- [ ] Migration reviewed and tested locally
- [ ] Documentation updated

---

## Deployment Steps

### 1. Test Environment Deployment

#### A. Deploy Code

```bash
# From busibox admin workstation
cd /path/to/busibox/provision/ansible

# Deploy agent-server to test environment
make agent INV=inventory/test
```

#### B. Run Database Migration

```bash
# SSH to test agent-lxc container
ssh root@10.96.201.202  # Test IP

# Navigate to agent directory
cd /srv/agent

# Activate virtual environment
source venv/bin/activate

# Check current migration status
alembic current

# Run migration
alembic upgrade head

# Verify migration applied
alembic current
# Should show: 003 (head)
```

#### C. Restart Service

```bash
# Still on agent-lxc container
systemctl restart agent-api

# Check service status
systemctl status agent-api

# Check logs
journalctl -u agent-api -n 50 --no-pager

# Verify health
curl http://localhost:8000/health
```

#### D. Validate Deployment

```bash
# From admin workstation or test container
cd /path/to/busibox/srv/agent

# Run integration tests
pytest tests/integration/test_api_conversations.py -v

# Expected: 27 tests passed
```

#### E. Manual Testing

```bash
# Get auth token (from AI Portal or agent-client)
export TOKEN="your-jwt-token"

# Test create conversation
curl -X POST http://10.96.201.202:8000/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Test Conversation"}'

# Test list conversations
curl http://10.96.201.202:8000/conversations \
  -H "Authorization: Bearer $TOKEN"

# Test chat settings
curl http://10.96.201.202:8000/users/me/chat-settings \
  -H "Authorization: Bearer $TOKEN"
```

---

### 2. Production Deployment

**Prerequisites:**
- Test environment validated for 24+ hours
- No issues reported
- Stakeholder approval

#### A. Backup Database

```bash
# SSH to production pg-lxc container
ssh root@10.96.200.20

# Create backup
pg_dump -U busibox_user -d busibox -F c -f /backup/agent_pre_conv_mgmt_$(date +%Y%m%d).dump

# Verify backup
ls -lh /backup/agent_pre_conv_mgmt_*.dump
```

#### B. Deploy Code

```bash
# From busibox admin workstation
cd /path/to/busibox/provision/ansible

# Deploy to production
make agent

# Or full site deployment
make all
```

#### C. Run Migration

```bash
# SSH to production agent-lxc container
ssh root@10.96.200.30

# Navigate and activate
cd /srv/agent
source venv/bin/activate

# Check current migration
alembic current

# Run migration
alembic upgrade head

# Verify
alembic current
```

#### D. Restart and Verify

```bash
# Restart service
systemctl restart agent-api

# Check status
systemctl status agent-api

# Monitor logs
journalctl -u agent-api -f

# Health check
curl http://localhost:8000/health
```

#### E. Smoke Testing

```bash
# Quick validation
export TOKEN="production-jwt-token"

# Create test conversation
curl -X POST http://agent-lxc:8000/conversations \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"title": "Production Test"}'

# List conversations
curl http://agent-lxc:8000/conversations \
  -H "Authorization: Bearer $TOKEN"
```

---

## Rollback Procedure

If issues occur, rollback using these steps:

### 1. Revert Migration

```bash
# On agent-lxc container
cd /srv/agent
source venv/bin/activate

# Rollback migration
alembic downgrade -1

# Verify
alembic current
# Should show: 002 (previous)
```

### 2. Deploy Previous Code

```bash
# From admin workstation
cd /path/to/busibox

# Checkout previous version
git checkout <previous-commit>

# Deploy
cd provision/ansible
make agent [INV=inventory/test]
```

### 3. Restart Service

```bash
# On agent-lxc
systemctl restart agent-api
systemctl status agent-api
```

---

## Verification

### Check Database Tables

```bash
# On pg-lxc container
psql -U busibox_user -d busibox

# List tables
\dt

# Should see:
# conversations
# messages
# chat_settings

# Check schema
\d conversations
\d messages
\d chat_settings

# Check indexes
\di
```

### Check API Endpoints

```bash
# From agent-client or curl
curl http://agent-lxc:8000/conversations \
  -H "Authorization: Bearer $TOKEN"

# Should return 200 with empty conversations array
```

### Monitor Logs

```bash
# On agent-lxc
journalctl -u agent-api -f

# Look for:
# - No errors on startup
# - Successful database connection
# - No migration errors
```

---

## Troubleshooting

### Issue: Migration Fails

**Symptoms**: `alembic upgrade head` fails with error

**Solutions**:
1. Check database connectivity:
   ```bash
   psql -U busibox_user -d busibox -h pg-lxc
   ```

2. Check for existing tables (may need manual cleanup):
   ```sql
   SELECT tablename FROM pg_tables WHERE tablename IN ('conversations', 'messages', 'chat_settings');
   ```

3. Review migration logs:
   ```bash
   alembic history -v
   ```

### Issue: Service Won't Start

**Symptoms**: `systemctl start agent-api` fails

**Solutions**:
1. Check logs:
   ```bash
   journalctl -u agent-api -n 100 --no-pager
   ```

2. Check environment variables:
   ```bash
   systemctl cat agent-api
   ```

3. Test manually:
   ```bash
   cd /srv/agent
   source venv/bin/activate
   DATABASE_URL="postgresql://..." python -m uvicorn app.main:app
   ```

### Issue: Tests Fail

**Symptoms**: `pytest` shows failures

**Solutions**:
1. Check database connection in tests
2. Verify test database is clean
3. Check authentication mocks
4. Run individual tests:
   ```bash
   pytest tests/integration/test_api_conversations.py::test_create_conversation -v
   ```

---

## Post-Deployment

### Monitor for 24 Hours

- Check service health every 4 hours
- Monitor error logs
- Track API response times
- Watch database connections

### Update Agent-Client

Once stable in production:
1. Update agent-client to use conversation endpoints
2. Test end-to-end flow
3. Monitor for integration issues

### Documentation Updates

- [ ] Update AI Portal integration docs
- [ ] Update agent-client integration docs
- [ ] Create user guide for chat features

---

## Related Documentation

- [Conversation API Reference](../reference/conversation-api.md)
- [Agent Server Implementation Status](../reference/agent-server-implementation-status.md)
- [Agent Server Testing Guide](../guides/agent-server-testing.md)
- [OpenAPI Specification](../../openapi/agent-api.yaml)

---

## Contact

For deployment issues:
- Check logs: `journalctl -u agent-api -f`
- Review tests: `pytest tests/integration/test_api_conversations.py`
- Consult implementation status doc
- Review conversation API reference









