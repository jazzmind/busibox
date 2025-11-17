# Busibox Infrastructure Testing Guide

**Version**: 1.0.0  
**Last Updated**: 2025-10-14  
**Purpose**: Guide for testing infrastructure provisioning and deployment

---

## Overview

The Busibox testing framework provides **isolated, safe testing** of the entire infrastructure provisioning process without affecting production containers. Test containers use:

- **Container IDs**: 301-307 (Production IDs + 100)
- **IP Range**: 10.96.201.24-30 (Different subnet from production)
- **Name Prefix**: TEST- (e.g., TEST-pg-lxc)
- **Database**: busibox_test (separate from production)

This allows you to:
1. Test provisioning on a fresh environment
2. Verify services work correctly
3. Test idempotency (re-running scripts safely)
4. Test incremental updates (adding containers to existing stack)
5. Clean up completely without affecting production

---

## Quick Start

### Run Full Test Suite

```bash
# From project root
bash test-infrastructure.sh full
```

This will:
1. Create test containers (IDs 301-307)
2. Provision all services via Ansible
3. Run health checks
4. Verify database schema
5. Test idempotency
6. Prompt for cleanup

### Manual Testing Workflow

```bash
# 1. Create test containers
bash test-infrastructure.sh provision

# 2. Verify everything works
bash test-infrastructure.sh verify

# 3. Clean up
bash test-infrastructure.sh cleanup
```

---

## Test Commands

### Available Commands

| Command | Description |
|---------|-------------|
| `full` | Run complete test suite (provision → test → cleanup) |
| `provision` | Create and provision test containers |
| `verify` | Run health checks and smoke tests |
| `incremental` | Test adding containers to existing stack |
| `cleanup` | Destroy all test containers |
| `help` | Show help message |

### Command Examples

```bash
# Full automated test
bash test-infrastructure.sh full

# Just provision (no tests)
bash test-infrastructure.sh provision

# Just verification (assumes already provisioned)
bash test-infrastructure.sh verify

# Clean up test environment
bash test-infrastructure.sh cleanup
```

---

## Test Environment Details

### Container Mapping

| Production | Test | Name | IP |
|-----------|------|------|-----|
| 201 | **301** | TEST-openwebui-lxc | 10.96.201.24 |
| 202 | **302** | TEST-apps-lxc | 10.96.201.25 |
| 203 | **303** | TEST-pg-lxc | 10.96.201.26 |
| 204 | **304** | TEST-milvus-lxc | 10.96.201.27 |
| 205 | **305** | TEST-files-lxc | 10.96.201.28 |
| 206 | **306** | TEST-ingest-lxc | 10.96.201.29 |
| 207 | **307** | TEST-agent-lxc | 10.96.201.30 |

### Test Configuration Files

- **`provision/pct/test-vars.env`**: Test environment variables
- **`provision/ansible/inventory/test-hosts.yml`**: Ansible inventory for test
- **`test-infrastructure.sh`**: Main test runner
- **`provision/pct/destroy_test.sh`**: Test cleanup script

---

## Test Scenarios

### 1. Fresh Provisioning Test

**Purpose**: Verify complete infrastructure can be provisioned from scratch

**Steps**:
```bash
# 1. Ensure clean state
bash test-infrastructure.sh cleanup

# 2. Provision fresh
bash test-infrastructure.sh provision

# 3. Verify
bash test-infrastructure.sh verify
```

**Success Criteria**:
- ✓ All 7 containers created (IDs 301-307)
- ✓ All containers running
- ✓ All services responding to health checks
- ✓ Database schema applied (2+ migrations)
- ✓ Database tables created

---

### 2. Idempotency Test

**Purpose**: Verify scripts can be run multiple times without errors

**Steps**:
```bash
# 1. Provision once
bash test-infrastructure.sh provision

# 2. Run again (should be safe)
bash provision/pct/create_lxc_base.sh test

# 3. Run Ansible again
cd provision/ansible
ansible-playbook -i inventory/test-hosts.yml site.yml
```

**Success Criteria**:
- ✓ Container script detects existing containers
- ✓ No duplicate containers created
- ✓ Ansible playbook runs without errors
- ✓ Services remain functional
- ✓ No data loss

---

### 3. Incremental Provisioning Test

**Purpose**: Verify adding one container to existing stack works

**Steps**:
```bash
# 1. Provision full stack
bash test-infrastructure.sh provision

# 2. Manually destroy ONE container (e.g., agent)
pct stop 307
pct destroy 307 --purge

# 3. Re-run container creation
bash provision/pct/create_lxc_base.sh test

# 4. Re-run Ansible provisioning
cd provision/ansible
ansible-playbook -i inventory/test-hosts.yml -l agent site.yml

# 5. Verify services still work
bash test-infrastructure.sh verify
```

**Success Criteria**:
- ✓ Only missing container (307) is recreated
- ✓ Existing containers untouched
- ✓ Ansible provisions only the missing service
- ✓ All health checks pass
- ✓ Inter-service communication works

---

### 4. Service Integration Test

**Purpose**: Verify services can communicate with each other

**Steps**:
```bash
# 1. Provision full stack
bash test-infrastructure.sh provision

# 2. Test database connectivity
psql -h 10.96.201.26 -U busibox_test_user -d busibox_test -c "SELECT 1"

# 3. Test MinIO connectivity
curl http://10.96.201.28:9000/minio/health/live

# 4. Test Milvus connectivity
curl http://10.96.201.27:9091/healthz

# 5. Test Python service can connect to all dependencies
# (requires agent API to be deployed)
curl http://10.96.201.30:8000/health
```

**Success Criteria**:
- ✓ PostgreSQL accepts connections
- ✓ MinIO health check returns 200
- ✓ Milvus health check returns 200
- ✓ Agent API can reach all dependencies
- ✓ Health endpoint reports all services healthy

---

### 5. Database Migration Test

**Purpose**: Verify database migrations apply correctly

**Steps**:
```bash
# 1. Provision fresh (applies migrations)
bash test-infrastructure.sh provision

# 2. Check migrations applied
psql -h 10.96.201.26 -U busibox_test_user -d busibox_test \
  -c "SELECT version, name, applied_at FROM schema_migrations ORDER BY version;"

# 3. Check tables created
psql -h 10.96.201.26 -U busibox_test_user -d busibox_test \
  -c "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"

# 4. Test idempotency - re-run migrations
cd provision/ansible
ansible-playbook -i inventory/test-hosts.yml -l pg site.yml

# 5. Verify migrations still at version 2
psql -h 10.96.201.26 -U busibox_test_user -d busibox_test \
  -c "SELECT COUNT(*) FROM schema_migrations;"
```

**Success Criteria**:
- ✓ Migration 001 applied successfully
- ✓ Migration 002 applied successfully
- ✓ All expected tables exist (users, roles, files, chunks, etc.)
- ✓ Re-running migrations is safe (idempotent)
- ✓ No duplicate migration records

---

## Manual Testing

### Create Test Containers Only

```bash
cd provision/pct
bash create_lxc_base.sh test
```

### Provision Services Only (No Container Creation)

```bash
cd provision/ansible
ansible-playbook -i inventory/test-hosts.yml site.yml
```

### Provision Single Service

```bash
cd provision/ansible

# PostgreSQL only
ansible-playbook -i inventory/test-hosts.yml -l pg site.yml

# MinIO only
ansible-playbook -i inventory/test-hosts.yml -l files site.yml

# Agent API only
ansible-playbook -i inventory/test-hosts.yml -l agent site.yml
```

### Verify Connectivity

```bash
cd provision/ansible

# Ping all test containers
ansible -i inventory/test-hosts.yml all -m ping

# Check disk space
ansible -i inventory/test-hosts.yml all -m shell -a "df -h"

# Check systemd services
ansible -i inventory/test-hosts.yml all -m shell -a "systemctl list-units --type=service --state=running"
```

---

## Cleanup

### Destroy All Test Containers

```bash
# With confirmation prompt
bash provision/pct/destroy_test.sh

# Skip confirmation
bash provision/pct/destroy_test.sh --force

# Via test runner
bash test-infrastructure.sh cleanup
```

### Safety Features

The cleanup script has multiple safety checks:
1. **ID Validation**: Only destroys containers with ID >= 300
2. **Confirmation Prompt**: Requires user confirmation (unless --force)
3. **Name Verification**: Shows container names before destruction
4. **Production Protection**: Cannot destroy production containers (IDs 201-207)

---

## Troubleshooting

### Test containers already exist

```bash
# Cleanup and start fresh
bash test-infrastructure.sh cleanup
bash test-infrastructure.sh provision
```

### Ansible can't connect to test containers

```bash
# Check SSH connectivity
ssh root@10.96.201.26

# Check containers are running
pct status 301
pct status 302
# ... etc

# Start stopped containers
pct start 301
pct start 302
```

### Services not responding

```bash
# Check service status inside container
pct exec 303 -- systemctl status postgresql

# Check logs
pct exec 303 -- journalctl -u postgresql -n 50

# Restart service
pct exec 303 -- systemctl restart postgresql
```

### Database connection errors

```bash
# Check PostgreSQL is listening
pct exec 303 -- netstat -tlnp | grep 5432

# Check pg_hba.conf allows test subnet
pct exec 303 -- cat /etc/postgresql/*/main/pg_hba.conf | grep "10.96.201"

# Test connection from another container
pct exec 307 -- psql -h 10.96.201.26 -U busibox_test_user -d busibox_test -c "SELECT 1"
```

---

## CI/CD Integration

The test framework can be integrated into CI/CD pipelines:

```bash
#!/bin/bash
# ci-test.sh

set -e

# Run full test suite
bash test-infrastructure.sh full

# Check exit code
if [ $? -eq 0 ]; then
  echo "All tests passed!"
  exit 0
else
  echo "Tests failed!"
  exit 1
fi
```

---

## Best Practices

1. **Always test before production**: Run test suite before deploying to production
2. **Clean up after testing**: Don't leave test containers running indefinitely
3. **Test incrementally**: Test each service individually before full stack
4. **Document failures**: If tests fail, document the issue for future reference
5. **Version testing**: Test upgrades by provisioning old version, then upgrading

---

## Future Enhancements

Planned improvements to the testing framework:

- [ ] Automated incremental provisioning test
- [ ] Performance testing (concurrent operations)
- [ ] Fault injection testing (simulate service failures)
- [ ] Backup/restore testing
- [ ] Upgrade testing (old version → new version)
- [ ] Security testing (penetration tests, RBAC verification)
- [ ] Load testing (100+ concurrent users)
- [ ] Data integrity testing (ensure RLS works correctly)

---

## References

- **Architecture**: [`docs/architecture.md`](./architecture.md)
- **Quickstart**: [`QUICKSTART.md`](../QUICKSTART.md)
- **Task List**: [`specs/001-create-an-initial/tasks.md`](../specs/001-create-an-initial/tasks.md)
- **Specification**: [`specs/001-create-an-initial/spec.md`](../specs/001-create-an-initial/spec.md)

---

**Document Version**: 1.0.0  
**Last Review**: 2025-10-14  
**Next Review**: After first production deployment

