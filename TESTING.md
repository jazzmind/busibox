# Busibox Testing Guide

## 📘 Complete Testing Documentation

For comprehensive testing documentation, see:

**[docs/developers/01-testing.md](docs/developers/01-testing.md)** - Complete testing guide with:
- Testing architecture overview
- All test levels (infrastructure, service, integration)
- Running tests from Proxmox host
- Service-specific testing guides
- Environment configuration
- CI/CD integration
- Troubleshooting

## Quick Reference

### Infrastructure Testing

```bash
# On Proxmox host
cd /root/busibox
bash scripts/test-infrastructure.sh full
```

### Service Testing (from Host)

```bash
# On Proxmox host or workstation
cd /root/busibox/provision/ansible

# All tests
make test-all

# Individual services
make test-ingest
make test-search
make test-agent
make test-apps

# Health checks
make verify
```

### Direct Container Testing

```bash
# Ingest tests
ssh root@10.96.200.206
ingest-test

# Search tests
ssh root@10.96.200.204
search-test
```

## Documentation Index

- **[Master Guide](docs/testing/master-guide.md)** - Complete testing guide
- **[Testing Strategy](docs/testing/testing-strategy.md)** - Infrastructure tests
- **[Makefile Targets](docs/testing/makefile-test-targets.md)** - Make commands
- **[Search API Testing](docs/testing/search-api-testing.md)** - Search tests
- **[ColPali Testing](docs/testing/colpali-testing.md)** - ColPali tests

---

## ⚠️ Important: Where to Run Tests

Infrastructure tests **must run on a Proxmox host**, not on your local workstation.

### Why?

The infrastructure tests require:
- **Proxmox VE** with `pct` command for LXC containers
- **LXC storage** (e.g., `local-lvm`)
- **Network access** to create containers on the Proxmox network
- **Ansible** for service provisioning

Service-level tests can be run from the host using make targets (SSH to containers automatically).

---

## Alternative: Test Individual Components

If you don't have a Proxmox host yet, you can test individual components:

### Test Ansible Playbooks (Dry Run)

```bash
# On your Mac (requires Ansible)
cd provision/ansible

# Check syntax
ansible-playbook --syntax-check site.yml

# Dry run (won't actually change anything)
ansible-playbook -i inventory/test-hosts.yml site.yml --check
```

### Test Python Code

```bash
# On your Mac
cd srv/agent

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run linter
pip install flake8
flake8 src/
```

### Test Database Migrations

```bash
# On your Mac (requires PostgreSQL and psql)
# Create test database
createdb busibox_test

# Apply migrations
psql -d busibox_test -f provision/ansible/roles/postgres/files/migrations/001_initial_schema.sql
psql -d busibox_test -f provision/ansible/roles/postgres/files/migrations/002_add_rls_policies.sql

# Verify tables
psql -d busibox_test -c "\dt"

# Test rollback
psql -d busibox_test -f provision/ansible/roles/postgres/files/migrations/002_rollback.sql
psql -d busibox_test -f provision/ansible/roles/postgres/files/migrations/001_rollback.sql
```

### Test Milvus Initialization

```bash
# On your Mac (requires Milvus running)
# Start Milvus with Docker
docker run -d --name milvus-test \
  -p 19530:19530 -p 9091:9091 \
  milvusdb/milvus:v2.6.5 \
  milvus run standalone

# Run initialization script
pip install 'pymilvus>=2.6.7,<2.7.0'
python tools/milvus_init.py

# Cleanup
docker stop milvus-test
docker rm milvus-test
```

---

## CI/CD Testing (Future)

When you set up CI/CD, you can use GitHub Actions with:
- Docker containers for services (PostgreSQL, Milvus, MinIO, Redis)
- Mock Proxmox environment for testing scripts

See `.github/workflows/test.yml` (to be created).

---

## Common Test Issues

### Issue: `storage 'local-lvm' does not exist`

**Cause**: Running test script on non-Proxmox system or wrong storage name

**Solution**: 
1. Run on Proxmox host, OR
2. Update `test-vars.env` with your actual storage name:
   ```bash
   STORAGE=your-storage-name
   ```

### Issue: `pct: command not found`

**Cause**: Running on non-Proxmox system

**Solution**: Copy repository to Proxmox host and run there

### Issue: `ansible: command not found`

**Cause**: Ansible not installed on Proxmox host

**Solution**:
```bash
# On Proxmox host
apt update
apt install -y ansible
```

### Issue: Template not found

**Cause**: Ubuntu LXC template not downloaded

**Solution**:
```bash
# On Proxmox host - download Ubuntu 22.04 template
pveam update
pveam download local ubuntu-22.04-standard_22.04-1_amd64.tar.zst

# Or update test-vars.env with correct path
```

---

## Testing Workflow

```
┌─────────────────────────────────────────────────────┐
│              Development Workstation                 │
│                  (Your Mac)                          │
│                                                      │
│  • Edit code                                        │
│  • Run linters                                      │
│  • Test Python code in isolation                   │
│  • Commit & push to GitHub                         │
│                                                      │
└──────────────────┬──────────────────────────────────┘
                   │
                   │ rsync or git pull
                   ▼
┌─────────────────────────────────────────────────────┐
│              Proxmox Test Host                       │
│                                                      │
│  • Run test-infrastructure.sh                       │
│  • Create test containers (301-307)                 │
│  • Deploy services with Ansible                     │
│  • Run integration tests                            │
│  • Verify health checks                             │
│  • Clean up test environment                        │
│                                                      │
└─────────────────────────────────────────────────────┘
                   │
                   │ If tests pass
                   ▼
┌─────────────────────────────────────────────────────┐
│            Proxmox Production Host                   │
│                                                      │
│  • Deploy to production containers (201-207)        │
│  • Run verification suite                           │
│                                                      │
└─────────────────────────────────────────────────────┘
```

---

## Next Steps

1. **Get Proxmox Access**: Set up a Proxmox host for testing
2. **Run Full Test Suite**: Validate complete infrastructure
3. **Report Issues**: Document any problems found
4. **Iterate**: Fix issues and re-test

See [`docs/testing.md`](docs/testing.md) for comprehensive testing documentation.

