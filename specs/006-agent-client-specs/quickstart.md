# Quickstart: Agent-Server API Enhancements

**Feature**: 006-agent-client-specs  
**Date**: 2025-12-11  
**Audience**: Developers implementing or testing the agent-server enhancements

## Prerequisites

- Busibox infrastructure provisioned (Proxmox host with LXC containers)
- Agent-server running in agent-lxc (CTID 207)
- PostgreSQL accessible from agent-lxc
- LiteLLM configured with Claude 3.5 Sonnet access
- Python 3.11+ development environment

## Development Setup

### 1. Clone and Enter Agent-Server Directory

```bash
# SSH into agent-lxc container
ssh root@10.96.200.30

# Navigate to agent-server directory
cd /srv/agent
```

### 2. Install Development Dependencies

```bash
# Activate virtual environment (if using venv)
source venv/bin/activate

# Install dev dependencies
pip install -e ".[dev]"

# Install additional dependencies for new features
pip install pydantic-ai structlog croniter
```

### 3. Run Database Migrations

```bash
# Generate migration from models
alembic revision --autogenerate -m "Add agent enhancements"

# Review generated migration in alembic/versions/
# Edit if needed to match data-model.md

# Apply migration
alembic upgrade head
```

### 4. Configure Environment Variables

```bash
# Edit .env file
nano .env

# Add/verify these variables:
DATABASE_URL=postgresql://user:pass@10.96.200.20:5432/busibox
LITELLM_API_BASE=http://10.96.200.30:4000
LITELLM_API_KEY=your-api-key
REDIS_URL=redis://10.96.200.25:6379
LOG_LEVEL=INFO
```

### 5. Run Tests

```bash
# Run all tests
pytest

# Run specific test modules
pytest tests/unit/test_dispatcher.py
pytest tests/integration/test_personal_agents.py

# Run with coverage
pytest --cov=app --cov-report=html
```

### 6. Start Development Server

```bash
# Start FastAPI with auto-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Or use systemd in development mode
systemctl restart agent-api
journalctl -u agent-api -f
```

## Quick Testing

### Test Personal Agent Filtering

```bash
# Create personal agent as User A
curl -X POST http://localhost:8000/agents \
  -H "Authorization: Bearer <user-a-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Research Assistant",
    "instructions": "Help with research tasks",
    "model": "anthropic:claude-3-5-sonnet",
    "tools": {"names": ["doc_search"]}
  }'

# List agents as User A (should see personal agent)
curl -X GET http://localhost:8000/agents \
  -H "Authorization: Bearer <user-a-token>"

# List agents as User B (should NOT see User A's agent)
curl -X GET http://localhost:8000/agents \
  -H "Authorization: Bearer <user-b-token>"
```

### Test Dispatcher Routing

```bash
# Route a document search query
curl -X POST http://localhost:8000/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What does our Q4 report say about revenue?",
    "available_tools": ["doc_search", "web_search"],
    "available_agents": [],
    "user_settings": {
      "enabled_tools": ["doc_search"]
    }
  }'

# Expected response:
# {
#   "routing_decision": {
#     "selected_tools": ["doc_search"],
#     "selected_agents": [],
#     "confidence": 0.95,
#     "reasoning": "Query asks about documents, doc_search is appropriate",
#     "alternatives": [],
#     "requires_disambiguation": false
#   }
# }
```

### Test Tool CRUD Operations

```bash
# Get individual tool
curl -X GET http://localhost:8000/agents/tools/{tool_id} \
  -H "Authorization: Bearer <token>"

# Update custom tool
curl -X PUT http://localhost:8000/agents/tools/{tool_id} \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "Updated description",
    "schema": {...}
  }'

# Try to delete built-in tool (should return 403)
curl -X DELETE http://localhost:8000/agents/tools/{builtin_tool_id} \
  -H "Authorization: Bearer <token>"

# Delete custom tool not in use
curl -X DELETE http://localhost:8000/agents/tools/{custom_tool_id} \
  -H "Authorization: Bearer <token>"
```

### Test Schedule Management

```bash
# Get schedule
curl -X GET http://localhost:8000/runs/schedule/{schedule_id} \
  -H "Authorization: Bearer <token>"

# Update schedule cron expression
curl -X PUT http://localhost:8000/runs/schedule/{schedule_id} \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "cron_expression": "0 10 * * *"
  }'

# Verify next_run_time updated
curl -X GET http://localhost:8000/runs/schedule/{schedule_id} \
  -H "Authorization: Bearer <token>"
```

### Test Workflow Resume

```bash
# Create a workflow that will fail
# (implementation-specific, depends on workflow setup)

# Resume failed workflow
curl -X POST http://localhost:8000/runs/workflow/{failed_run_id}/resume \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "from_step": "step3",
    "override_input": {...}
  }'

# Check new run references parent
curl -X GET http://localhost:8000/runs/{new_run_id} \
  -H "Authorization: Bearer <token>"
```

## Deployment to Test Environment

### 1. Push Changes to GitHub

```bash
# From development machine
git add .
git commit -m "feat: implement agent-server enhancements"
git push origin 006-agent-client-specs
```

### 2. Deploy via Ansible

```bash
# From busibox admin workstation
cd /path/to/busibox/provision/ansible

# Deploy to test environment
make deploy-agent INV=inventory/test

# Or deploy all services
make all INV=inventory/test
```

### 3. Verify Deployment

```bash
# SSH into test agent-lxc
ssh root@<test-agent-ip>

# Check service status
systemctl status agent-api

# Check logs
journalctl -u agent-api -n 100 --no-pager

# Verify migration applied
cd /srv/agent
alembic current

# Test health endpoint
curl http://localhost:8000/health
```

### 4. Run Integration Tests

```bash
# From test agent-lxc
cd /srv/agent
pytest tests/integration/ -v

# Or from admin workstation
cd /path/to/busibox
bash scripts/test-agent-enhancements.sh test
```

## Deployment to Production

### 1. Merge to Main Branch

```bash
# From development machine
git checkout main
git merge 006-agent-client-specs
git push origin main
```

### 2. Deploy via Ansible

```bash
# From busibox admin workstation
cd /path/to/busibox/provision/ansible

# Deploy to production
make deploy-agent

# Or deploy all services
make all
```

### 3. Verify Production Deployment

```bash
# SSH into production agent-lxc
ssh root@10.96.200.30

# Check service status
systemctl status agent-api

# Check logs for errors
journalctl -u agent-api -n 100 --no-pager | grep ERROR

# Verify migration applied
cd /srv/agent
alembic current

# Test health endpoint
curl http://localhost:8000/health
```

### 4. Smoke Tests

```bash
# Test personal agent filtering
curl -X GET https://agent.busibox.local/agents \
  -H "Authorization: Bearer <token>"

# Test dispatcher routing
curl -X POST https://agent.busibox.local/dispatcher/route \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "test query", "available_tools": [], "available_agents": []}'

# Check dispatcher decision logs
ssh root@10.96.200.30
psql -U busibox_user -d busibox -c "SELECT COUNT(*) FROM dispatcher_decision_log;"
```

## Monitoring

### Check Dispatcher Performance

```bash
# SSH into agent-lxc
ssh root@10.96.200.30

# Query dispatcher decision logs
psql -U busibox_user -d busibox <<EOF
SELECT 
  AVG(confidence) as avg_confidence,
  COUNT(*) as total_decisions,
  COUNT(*) FILTER (WHERE confidence < 0.7) as low_confidence_count
FROM dispatcher_decision_log
WHERE timestamp > NOW() - INTERVAL '1 hour';
EOF
```

### Check Version Isolation

```bash
# Verify definition snapshots captured
psql -U busibox_user -d busibox <<EOF
SELECT 
  COUNT(*) as total_runs,
  COUNT(*) FILTER (WHERE definition_snapshot IS NOT NULL) as with_snapshot
FROM run_records
WHERE created_at > NOW() - INTERVAL '1 day';
EOF
```

### Check CRUD Operations

```bash
# Check tool version increments
psql -U busibox_user -d busibox <<EOF
SELECT name, version, updated_at
FROM tool_definitions
WHERE is_active = TRUE
ORDER BY updated_at DESC
LIMIT 10;
EOF
```

### Monitor APScheduler

```bash
# Check scheduled runs
psql -U busibox_user -d busibox <<EOF
SELECT id, cron_expression, next_run_time, is_active
FROM scheduled_runs
WHERE is_active = TRUE
ORDER BY next_run_time
LIMIT 10;
EOF

# Check APScheduler logs
journalctl -u agent-api | grep -i "apscheduler"
```

## Troubleshooting

### Dispatcher Not Routing Correctly

```bash
# Check LiteLLM connection
curl http://localhost:4000/health

# Check dispatcher decision logs
psql -U busibox_user -d busibox -c "
SELECT query_text, selected_tools, confidence, reasoning
FROM dispatcher_decision_log
ORDER BY timestamp DESC
LIMIT 5;"

# Increase log level
export LOG_LEVEL=DEBUG
systemctl restart agent-api
```

### Personal Agents Not Filtering

```bash
# Check is_builtin flag set correctly
psql -U busibox_user -d busibox -c "
SELECT id, name, is_builtin, created_by
FROM agent_definitions
WHERE is_active = TRUE;"

# Check query filtering logic
# (add debug logging to app/api/routes/agents.py)
```

### Tool/Workflow Delete Conflicts

```bash
# Check what's using a tool
psql -U busibox_user -d busibox -c "
SELECT id, name, tools
FROM agent_definitions
WHERE is_active = TRUE
AND tools::text LIKE '%tool_name%';"

# Check workflow schedules
psql -U busibox_user -d busibox -c "
SELECT id, workflow_id, cron_expression, next_run_time
FROM scheduled_runs
WHERE is_active = TRUE
AND workflow_id = 'workflow-uuid';"
```

### Schedule Updates Not Working

```bash
# Check APScheduler job exists
# (add debug logging to app/services/scheduler.py)

# Verify cron expression valid
python3 -c "from croniter import croniter; print(croniter.is_valid('0 10 * * *'))"

# Check next_run_time calculation
psql -U busibox_user -d busibox -c "
SELECT id, cron_expression, next_run_time, updated_at
FROM scheduled_runs
WHERE id = 'schedule-uuid';"
```

### Migration Failures

```bash
# Check current migration version
cd /srv/agent
alembic current

# Check migration history
alembic history

# Rollback one version
alembic downgrade -1

# Re-apply migration
alembic upgrade head

# Check for errors
journalctl -u agent-api -n 100 | grep -i "alembic\|migration"
```

## Performance Testing

### Load Test Dispatcher

```bash
# Install hey (HTTP load testing tool)
apt-get install hey

# Run load test (1000 requests, 50 concurrent)
hey -n 1000 -c 50 -m POST \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "test", "available_tools": [], "available_agents": []}' \
  http://localhost:8000/dispatcher/route

# Check results for p95 latency (should be <2s)
```

### Load Test CRUD Endpoints

```bash
# Test tool retrieval
hey -n 1000 -c 50 \
  -H "Authorization: Bearer <token>" \
  http://localhost:8000/agents/tools/{tool_id}

# Check results for p95 latency (should be <500ms)
```

### Monitor Database Performance

```bash
# Enable pg_stat_statements
psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

# Check slow queries
psql -U busibox_user -d busibox <<EOF
SELECT 
  query,
  calls,
  mean_exec_time,
  max_exec_time
FROM pg_stat_statements
WHERE query LIKE '%agent_definitions%'
   OR query LIKE '%tool_definitions%'
   OR query LIKE '%dispatcher_decision_log%'
ORDER BY mean_exec_time DESC
LIMIT 10;
EOF
```

## Common Issues

### Issue: Dispatcher returns confidence=0 for all queries

**Cause**: LiteLLM not accessible or Claude API key invalid

**Solution**:
```bash
# Check LiteLLM status
curl http://localhost:4000/health

# Check API key
cat /srv/agent/.env | grep LITELLM_API_KEY

# Restart LiteLLM
systemctl restart litellm
```

### Issue: Personal agents visible to all users

**Cause**: Filtering logic not applied or is_builtin flag not set

**Solution**:
```bash
# Check filtering in code (app/api/routes/agents.py)
# Verify query includes:
# or_(AgentDefinition.is_builtin.is_(True), AgentDefinition.created_by == user_id)

# Set is_builtin for system agents
psql -U busibox_user -d busibox -c "
UPDATE agent_definitions
SET is_builtin = TRUE
WHERE name IN ('system-agent-1', 'system-agent-2');"
```

### Issue: Tool updates not incrementing version

**Cause**: Version increment logic missing in update endpoint

**Solution**:
```bash
# Check code in app/api/routes/tools.py
# Verify: tool.version += 1 before commit

# Manually fix versions if needed
psql -U busibox_user -d busibox -c "
UPDATE tool_definitions
SET version = version + 1
WHERE id = 'tool-uuid';"
```

### Issue: Schedule updates not reflected in APScheduler

**Cause**: Scheduler not updated or transaction rolled back

**Solution**:
```bash
# Check APScheduler logs
journalctl -u agent-api | grep -i "reschedule"

# Verify transaction commit
# (add debug logging before/after scheduler.reschedule_job())

# Manually trigger schedule update
# (restart agent-api to reload all schedules)
systemctl restart agent-api
```

## Additional Resources

- **API Documentation**: See `contracts/openapi.yaml`
- **Data Model**: See `data-model.md`
- **Research Notes**: See `research.md`
- **Feature Spec**: See `spec.md`
- **Busibox Docs**: `/path/to/busibox/docs/`

## Support

For issues or questions:
1. Check logs: `journalctl -u agent-api -f`
2. Check database: `psql -U busibox_user -d busibox`
3. Review implementation plan: `plan.md`
4. Check Busibox troubleshooting: `docs/troubleshooting/`

---

**Last Updated**: 2025-12-11  
**Status**: Ready for implementation








