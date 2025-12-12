# Deployment Checklist: Agent-Server API Enhancements

**Feature**: 006-agent-client-specs  
**Target**: Test environment first, then production  
**Date**: 2025-12-11

---

## Pre-Deployment Checklist

### 1. Code Review
- [X] All commits reviewed and approved
- [X] 8 commits on branch `006-agent-client-specs`
- [X] No merge conflicts with main branch
- [ ] Branch merged to main (or deploy from feature branch)

### 2. Dependencies
- [X] `structlog>=24.1.0` added to pyproject.toml
- [X] `croniter>=2.0.1` added to pyproject.toml
- [ ] Dependencies installed on target server

### 3. Database Migration
- [X] Migration script created: `20251211_0000_002_agent_enhancements.py`
- [ ] Migration reviewed for correctness
- [ ] Backup of database taken
- [ ] Migration applied: `alembic upgrade head`
- [ ] Migration verified: Check new columns exist

### 4. Configuration
- [ ] Environment variables set (if any new ones)
- [ ] LiteLLM endpoint configured for dispatcher
- [ ] Redis connection configured (optional for caching)

---

## Deployment Steps

### Step 1: Backup Database
```bash
ssh root@<test-agent-ip>
pg_dump -U busibox_user -d busibox > /tmp/busibox_backup_$(date +%Y%m%d_%H%M%S).sql
```

### Step 2: Deploy Code
```bash
# From admin workstation
cd /path/to/busibox/provision/ansible
make deploy-agent INV=inventory/test

# Or manually on agent-lxc:
ssh root@<test-agent-ip>
cd /srv/agent
git fetch origin
git checkout 006-agent-client-specs
git pull origin 006-agent-client-specs
```

### Step 3: Install Dependencies
```bash
ssh root@<test-agent-ip>
cd /srv/agent
source venv/bin/activate
pip install structlog croniter
# Or: pip install -e .
```

### Step 4: Apply Migration
```bash
ssh root@<test-agent-ip>
cd /srv/agent
source venv/bin/activate
alembic upgrade head
```

**Verify Migration**:
```sql
-- Connect to database
psql -U busibox_user -d busibox

-- Check new columns exist
\d agent_definitions
-- Should see: is_builtin, created_by

\d tool_definitions
-- Should see: is_builtin, created_by

\d run_records
-- Should see: definition_snapshot, parent_run_id, resume_from_step, workflow_state

-- Check new table exists
\d dispatcher_decision_log

-- Check indexes
\di | grep idx_agent_definitions_builtin_created
\di | grep idx_dispatcher_log
```

### Step 5: Restart Service
```bash
ssh root@<test-agent-ip>
systemctl restart agent-api
# Or if using PM2:
pm2 restart agent-api

# Check status
systemctl status agent-api
# Or: pm2 status
```

### Step 6: Verify Health
```bash
# Check health endpoint
curl http://<test-agent-ip>:8000/health

# Expected response:
# {"status": "healthy", "service": "agent-server", ...}
```

---

## Post-Deployment Testing

### Test 1: Personal Agent Management (US1)

**Create Personal Agent**:
```bash
curl -X POST http://<test-agent-ip>:8000/agents/definitions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test-personal-agent",
    "display_name": "Test Personal Agent",
    "model": "anthropic:claude-3-5-sonnet",
    "instructions": "You are a test assistant",
    "tools": {"names": []},
    "scopes": []
  }'
```

**Expected**: 201 Created with `is_builtin: false`, `created_by: <user_id>`

**List Agents**:
```bash
curl -X GET http://<test-agent-ip>:8000/agents \
  -H "Authorization: Bearer <token>"
```

**Expected**: See personal agent in list

**Test with Different User**:
```bash
curl -X GET http://<test-agent-ip>:8000/agents \
  -H "Authorization: Bearer <different-token>"
```

**Expected**: Should NOT see other user's personal agent

### Test 2: Intelligent Query Routing (US2)

**Route Document Query**:
```bash
curl -X POST http://<test-agent-ip>:8000/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does our Q4 report say about revenue?",
    "available_tools": ["doc_search", "web_search"],
    "available_agents": [],
    "attachments": [],
    "user_settings": {
      "enabled_tools": ["doc_search", "web_search"],
      "enabled_agents": []
    }
  }'
```

**Expected**:
- 200 OK
- `selected_tools: ["doc_search"]`
- `confidence > 0.7`
- `reasoning` explains why doc_search selected

**Route Web Query**:
```bash
curl -X POST http://<test-agent-ip>:8000/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the weather today?",
    "available_tools": ["doc_search", "web_search"],
    "available_agents": [],
    "user_settings": {
      "enabled_tools": ["doc_search", "web_search"],
      "enabled_agents": []
    }
  }'
```

**Expected**:
- `selected_tools: ["web_search"]`
- `confidence > 0.7`

**Test No Tools Available**:
```bash
curl -X POST http://<test-agent-ip>:8000/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Help me analyze this data",
    "available_tools": [],
    "available_agents": [],
    "user_settings": {
      "enabled_tools": [],
      "enabled_agents": []
    }
  }'
```

**Expected**:
- `selected_tools: []`
- `confidence: 0.0`
- `reasoning` explains no tools available

### Test 3: Tool CRUD (US3)

**Create Custom Tool**:
```bash
curl -X POST http://<test-agent-ip>:8000/agents/tools \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "test_custom_tool",
    "description": "Test custom tool",
    "schema": {"input": {"type": "object"}},
    "entrypoint": "app.tools.test:custom_tool",
    "scopes": []
  }'
```

**Expected**: 201 Created with `is_builtin: false`, `created_by: <user_id>`

**Get Tool**:
```bash
TOOL_ID=<id-from-create-response>
curl -X GET http://<test-agent-ip>:8000/agents/tools/$TOOL_ID \
  -H "Authorization: Bearer <token>"
```

**Expected**: 200 OK with tool details

**Update Tool**:
```bash
curl -X PUT http://<test-agent-ip>:8000/agents/tools/$TOOL_ID \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description"
  }'
```

**Expected**: 200 OK with `version: 2`

**Delete Tool**:
```bash
curl -X DELETE http://<test-agent-ip>:8000/agents/tools/$TOOL_ID \
  -H "Authorization: Bearer <token>"
```

**Expected**: 204 No Content

**Verify Soft Delete**:
```bash
curl -X GET http://<test-agent-ip>:8000/agents/tools/$TOOL_ID \
  -H "Authorization: Bearer <token>"
```

**Expected**: 404 Not Found

### Test 4: Built-in Resource Protection

**Try to Delete Built-in Tool**:
```bash
# First, get a built-in tool ID
curl -X GET http://<test-agent-ip>:8000/agents/tools \
  -H "Authorization: Bearer <token>" | jq '.[] | select(.is_builtin == true) | .id' | head -1

BUILTIN_TOOL_ID=<id-from-above>
curl -X DELETE http://<test-agent-ip>:8000/agents/tools/$BUILTIN_TOOL_ID \
  -H "Authorization: Bearer <token>"
```

**Expected**: 403 Forbidden with message about built-in resources

**Try to Update Built-in Tool**:
```bash
curl -X PUT http://<test-agent-ip>:8000/agents/tools/$BUILTIN_TOOL_ID \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Trying to update built-in"
  }'
```

**Expected**: 403 Forbidden

### Test 5: Decision Logging

**Check Dispatcher Logs**:
```sql
-- Connect to database
psql -U busibox_user -d busibox

-- Check decision logs created
SELECT 
  id, 
  query_text, 
  selected_tools, 
  confidence, 
  user_id, 
  timestamp 
FROM dispatcher_decision_log 
ORDER BY timestamp DESC 
LIMIT 5;
```

**Expected**: See recent routing decisions logged

### Test 6: Version Isolation

**Create and Run Agent**:
```bash
# Create agent
curl -X POST http://<test-agent-ip>:8000/agents/definitions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "version-test-agent",
    "model": "anthropic:claude-3-5-sonnet",
    "instructions": "Test agent",
    "tools": {"names": ["search"]},
    "scopes": ["search.read"]
  }'

AGENT_ID=<id-from-response>

# Run agent
curl -X POST http://<test-agent-ip>:8000/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "'$AGENT_ID'",
    "input": {"prompt": "test query"}
  }'

RUN_ID=<id-from-response>
```

**Check Snapshot Created**:
```sql
-- Connect to database
psql -U busibox_user -d busibox

-- Check definition_snapshot populated
SELECT 
  id, 
  agent_id, 
  definition_snapshot IS NOT NULL as has_snapshot,
  jsonb_pretty(definition_snapshot) as snapshot
FROM run_records 
WHERE id = '<run-id>';
```

**Expected**: `has_snapshot: true`, snapshot contains agent, tools, workflow

---

## Rollback Plan

If deployment fails:

### 1. Rollback Code
```bash
ssh root@<test-agent-ip>
cd /srv/agent
git checkout main  # or previous working branch
systemctl restart agent-api
```

### 2. Rollback Database
```bash
# Downgrade migration
cd /srv/agent
source venv/bin/activate
alembic downgrade -1

# Or restore from backup
psql -U busibox_user -d busibox < /tmp/busibox_backup_YYYYMMDD_HHMMSS.sql
```

### 3. Verify Rollback
```bash
curl http://<test-agent-ip>:8000/health
```

---

## Success Criteria

- [ ] All new endpoints return expected status codes
- [ ] Personal agents isolated by user
- [ ] Dispatcher routes queries correctly (>80% accuracy on test queries)
- [ ] CRUD operations work for tools, workflows, evaluators
- [ ] Built-in resources cannot be modified (403)
- [ ] Decision logs being created
- [ ] Version isolation snapshots being captured
- [ ] No errors in application logs
- [ ] Service remains stable for 1 hour

---

## Monitoring After Deployment

### Logs to Watch
```bash
# Application logs
ssh root@<test-agent-ip>
journalctl -u agent-api -f

# Or PM2 logs
pm2 logs agent-api --lines 100
```

### Database Queries to Monitor
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

### Performance Metrics
- Dispatcher response time (should be <2s)
- Database query performance
- Memory usage
- CPU usage

---

## Known Issues

1. **Redis Caching**: Not wired up yet, dispatcher will work but without caching
2. **Auth Mocking**: Tests may need auth adjustments
3. **ScheduledRun Model**: Not yet created, workflow delete can't check schedules

---

## Next Steps After Successful Deployment

1. Run full test suite on test server
2. Monitor for 24 hours
3. Deploy to production
4. Implement US4 (Schedule Management) if needed
5. Implement Phase 8 (Polish) for production hardening
