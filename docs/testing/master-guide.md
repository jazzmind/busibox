# Busibox Testing Master Guide

**Created**: 2025-11-17  
**Status**: Active  
**Category**: Testing

## Overview

This master guide provides a complete overview of the Busibox testing framework, including infrastructure provisioning tests, service-level tests, and integration tests. All tests can be executed from the Proxmox host using make commands or shell scripts.

---

## Table of Contents

1. [Testing Architecture](#testing-architecture)
2. [Quick Start](#quick-start)
3. [Test Levels](#test-levels)
4. [Running Tests from Host](#running-tests-from-host)
5. [Service-Specific Tests](#service-specific-tests)
6. [Test Environments](#test-environments)
7. [CI/CD Integration](#cicd-integration)
8. [Troubleshooting](#troubleshooting)
9. [Related Documentation](#related-documentation)

---

## Testing Architecture

### Test Levels

Busibox testing is organized in a hierarchy from infrastructure to application:

```
┌─────────────────────────────────────────────────────────┐
│              Level 1: Infrastructure Tests               │
│  • Container creation                                   │
│  • Network configuration                                │
│  • Storage setup                                        │
│  • GPU passthrough                                      │
│  Script: test-infrastructure.sh                         │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│         Level 2: Provisioning & Deployment Tests         │
│  • Ansible playbook execution                           │
│  • Service deployment                                   │
│  • Configuration management                             │
│  • Idempotency verification                             │
│  Location: provision/ansible/Makefile                   │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│              Level 3: Service Health Tests               │
│  • PostgreSQL connectivity                              │
│  • MinIO health checks                                  │
│  • Milvus health checks                                 │
│  • API health endpoints                                 │
│  Commands: make verify-health, make verify-smoke        │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│            Level 4: Service Unit & Integration Tests     │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Ingest Service Tests                             │  │
│  │ • Chunking (23+ tests)                          │  │
│  │ • Multi-flow processing (40+ tests)             │  │
│  │ • ColPali (30+ tests)                           │  │
│  │ • LLM cleanup                                   │  │
│  │ Commands: make test-ingest*                     │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Search Service Tests                             │  │
│  │ • Milvus search (21+ tests)                     │  │
│  │ • Highlighting, reranking                       │  │
│  │ • RRF fusion                                    │  │
│  │ • Integration tests                             │  │
│  │ Commands: make test-search*                     │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Agent Service Tests                              │  │
│  │ • API endpoints                                 │  │
│  │ • Authentication                                │  │
│  │ Commands: make test-agent                       │  │
│  └─────────────────────────────────────────────────┘  │
│  ┌─────────────────────────────────────────────────┐  │
│  │ Apps Service Tests                               │  │
│  │ • AI Portal tests                               │  │
│  │ • UI components                                 │  │
│  │ Commands: make test-apps                        │  │
│  └─────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

### Execution Locations

| Test Type | Execution Location | Command Interface |
|-----------|-------------------|-------------------|
| Infrastructure | Proxmox host | `test-infrastructure.sh` |
| Provisioning | Proxmox host / workstation | `make` targets |
| Health checks | Proxmox host / workstation | `make verify-*` |
| Service tests | Remote (via SSH) | `make test-*` |

---

## Quick Start

### 1. Infrastructure Testing (First-Time Setup)

```bash
# On Proxmox host
cd /root/busibox

# Full infrastructure test
bash scripts/test-infrastructure.sh full
```

**What it does:**
- Creates test containers (300-309)
- Configures GPU passthrough
- Provisions services with Ansible
- Runs health checks
- Verifies database schema
- Tests idempotency
- Offers cleanup

**Duration:** 5-10 minutes

---

### 2. Service Testing (After Deployment)

```bash
# From Proxmox host or workstation
cd /root/busibox/provision/ansible  # or your local path

# Test all services
make test-all

# Or test individually
make test-ingest
make test-search
make test-agent
make test-apps
```

**What it does:**
- SSHs into each service container
- Runs service-specific test suites
- Reports pass/fail status
- Shows test coverage

**Duration:** 2-5 minutes

---

### 3. Quick Validation (Daily)

```bash
# Health checks only
cd provision/ansible
make verify-health

# Single service smoke test
make test-ingest
```

**Duration:** 30 seconds - 1 minute

---

## Test Levels

### Level 1: Infrastructure Tests

**Purpose**: Validate Proxmox container infrastructure

**Location**: `scripts/test-infrastructure.sh`

**Commands**:
```bash
# Full suite
bash scripts/test-infrastructure.sh full

# Just provision
bash scripts/test-infrastructure.sh provision

# Just verify
bash scripts/test-infrastructure.sh verify

# Cleanup
bash scripts/test-infrastructure.sh cleanup
```

**What's tested**:
- Container creation (IDs 300-309)
- Network configuration
- GPU passthrough (if available)
- Ansible connectivity
- Service health endpoints
- Database schema
- Idempotency

**Reference**: [`testing-strategy.md`](testing-strategy.md)

---

### Level 2: Deployment Tests

**Purpose**: Validate Ansible provisioning and deployment

**Location**: `provision/ansible/Makefile`

**Commands**:
```bash
cd provision/ansible

# Deploy and verify
make all
make verify

# Deploy specific service
make ingest
make search
make agent
make apps
```

**What's tested**:
- Ansible playbook execution
- Service configuration
- File deployment
- Systemd service setup
- Inter-service connectivity

**Reference**: [`../deployment/`](../deployment/)

---

### Level 3: Health & Smoke Tests

**Purpose**: Quick validation of running services

**Location**: `provision/ansible/Makefile`

**Commands**:
```bash
cd provision/ansible

# All health checks
make verify-health

# Smoke tests (database, migrations)
make verify-smoke

# Combined
make verify
```

**What's tested**:
- PostgreSQL health
- MinIO health
- Milvus health
- Agent API health
- Database schema
- Migration status

**Duration**: 10-30 seconds

---

### Level 4: Service Unit & Integration Tests

#### Ingest Service Tests

**Purpose**: Validate document ingestion and processing

**Location**: `srv/ingest/tests/`

**Run via Makefile**:
```bash
cd provision/ansible

# Quick chunker tests (default, ~5s)
make test-ingest

# All tests including integration (~60s)
make test-ingest-all

# With coverage report
make test-ingest-coverage

# Target test environment
make test-ingest INV=inventory/test
```

**Run directly in container**:
```bash
ssh root@10.96.200.206  # ingest-lxc

# Default (chunker tests)
ingest-test

# All tests
ingest-test all

# With coverage
ingest-test coverage

# Specific test
ingest-test tests/test_multi_flow.py::TestMultiFlowProcessor -v
```

**Test suites**:
- **Chunking**: 23+ tests (~5s)
  - Token limits
  - Character limits (Milvus 65535)
  - Heading detection
  - List handling
  - Overlap validation
  
- **Multi-flow**: 40+ tests (~30s)
  - SIMPLE strategy
  - MARKER strategy
  - COLPALI strategy
  - Parallel processing
  - Best strategy selection
  
- **ColPali**: 30+ tests (~20s)
  - Service health
  - Image encoding
  - Embedding generation
  - Performance benchmarks

**Reference**: 
- Service tests: `srv/ingest/tests/README.md`
- Test runner: [`ingest-test-runner.md`](../reference/ingest-test-runner.md)
- ColPali guide: [`colpali-testing.md`](colpali-testing.md)

---

#### Search Service Tests

**Purpose**: Validate hybrid search functionality

**Location**: `srv/search/tests/`

**Run via Makefile**:
```bash
cd provision/ansible

# Quick unit tests (default, ~10s)
make test-search

# Unit tests explicitly
make test-search-unit

# Integration tests (~30s)
make test-search-integration

# With coverage
make test-search-coverage
```

**Run directly in container**:
```bash
ssh root@10.96.200.204  # milvus-lxc (search API runs here)

# Default (unit tests)
search-test

# Unit tests
search-test unit

# Integration tests
search-test integration

# With coverage
search-test coverage
```

**Test suites**:
- **Milvus Search**: Unit tests (~10s)
  - Keyword search (BM25)
  - Semantic search (dense vectors)
  - Hybrid search with RRF fusion
  - Document retrieval
  
- **Highlighting**: Unit tests
  - Exact match highlighting
  - Stemming and fuzzy matching
  - Multiple term highlighting
  - Fragment extraction
  
- **Reranking**: Unit tests
  - Cross-encoder scoring
  - Result sorting
  - Top-K selection
  
- **Integration**: Full API tests (~30s)
  - Complete search flow
  - Authentication
  - File filtering
  - Error handling

**Reference**: 
- Service tests: `srv/search/tests/README.md`
- API guide: [`search-api-testing.md`](search-api-testing.md)

---

#### Agent Service Tests

**Purpose**: Validate agent API endpoints

**Location**: `srv/agent/tests/`

**Run via Makefile**:
```bash
cd provision/ansible
make test-agent
```

**Run directly in container**:
```bash
ssh root@10.96.200.202  # agent-lxc
cd /srv/agent && source venv/bin/activate && npm test
```

**Reference**: `srv/agent/tests/` (if implemented)

---

#### Apps Service Tests

**Purpose**: Validate AI Portal and UI components

**Location**: `srv/apps/*/tests/` (future)

**Run via Makefile**:
```bash
cd provision/ansible
make test-apps
```

**Run directly in container**:
```bash
ssh root@10.96.200.201  # apps-lxc
cd /srv/apps/ai-portal && npm test
```

---

## Running Tests from Host

All tests can be executed from the Proxmox host without manually SSHing into containers.

### Prerequisites

```bash
# Ensure you're on the Proxmox host
hostname  # Should show your Proxmox hostname

# Ensure SSH key authentication is configured
ssh-keygen -t ed25519 -f ~/.ssh/busibox_rsa
# Add public key to containers (done automatically by Ansible)

# Navigate to project directory
cd /root/busibox
```

---

### Using Make Targets (Recommended)

The Ansible Makefile provides convenient targets for all testing operations:

```bash
cd /root/busibox/provision/ansible

# ============================================================
# Quick Commands
# ============================================================

# Run all tests
make test-all

# Run ingest tests (fast)
make test-ingest

# Run search tests
make test-search

# Health checks
make verify-health

# Smoke tests
make verify-smoke

# ============================================================
# Environment Selection
# ============================================================

# Test environment
make test-all INV=inventory/test

# Production environment (default)
make test-all INV=inventory/production

# Custom inventory
make test-all INV=inventory/custom

# ============================================================
# Specific Test Types
# ============================================================

# Ingest: Quick chunker tests (~5s)
make test-ingest

# Ingest: All tests including integration (~60s)
make test-ingest-all

# Ingest: With coverage report
make test-ingest-coverage

# Search: Quick unit tests (~10s)
make test-search

# Search: Unit tests explicitly
make test-search-unit

# Search: Integration tests (~30s)
make test-search-integration

# Search: With coverage
make test-search-coverage

# Agent: API tests
make test-agent

# Apps: UI tests
make test-apps

# ============================================================
# Combined Workflows
# ============================================================

# Deploy and test ingest service
make ingest && make test-ingest

# Deploy search and run all tests
make search && make test-search-integration

# Deploy everything and test
make all && make verify && make test-all
```

**Reference**: [`makefile-test-targets.md`](makefile-test-targets.md)

---

### Using Shell Scripts

For infrastructure-level testing:

```bash
cd /root/busibox

# Full infrastructure test suite
bash scripts/test-infrastructure.sh full

# LLM container connectivity
bash scripts/test-llm-containers.sh

# ColPali service
bash scripts/test-colpali.sh test

# vLLM embedding service
bash scripts/test-vllm-embedding.sh test
```

---

## Service-Specific Tests

### Ingest Service

**What it tests**: Document ingestion, chunking, multi-flow processing, ColPali embeddings

**Quick test**:
```bash
# From host
cd provision/ansible
make test-ingest

# From container
ssh root@10.96.200.206
ingest-test
```

**Full test suite**:
```bash
make test-ingest-all
# or
ssh root@10.96.200.206
ingest-test all
```

**Coverage report**:
```bash
make test-ingest-coverage
# View: ssh root@10.96.200.206 and open /srv/ingest/htmlcov/index.html
```

**Test structure**:
- **Unit tests**: `test_chunker.py`, `test_multi_flow.py`, `test_colpali.py`
- **Integration tests**: `tests/integration/`
- **API tests**: `tests/api/`

**Key metrics**:
- Chunker tests: 23+ tests, ~5s
- Multi-flow tests: 40+ tests, ~30s
- ColPali tests: 30+ tests, ~20s
- Integration tests: varies, ~60s

**References**:
- [`colpali-testing.md`](colpali-testing.md)
- `srv/ingest/tests/README.md`
- [`../reference/ingest-test-runner.md`](../reference/ingest-test-runner.md)

---

### Search Service

**What it tests**: Hybrid search, highlighting, reranking, RRF fusion

**Quick test**:
```bash
# From host
cd provision/ansible
make test-search

# From container
ssh root@10.96.200.204
search-test
```

**Integration tests**:
```bash
make test-search-integration
# or
ssh root@10.96.200.204
search-test integration
```

**Coverage report**:
```bash
make test-search-coverage
# View: ssh root@10.96.200.204 and open /opt/search/htmlcov/index.html
```

**Test structure**:
- **Unit tests**: `test_milvus_search.py`, `test_highlighter.py`, `test_reranker.py`
- **Integration tests**: `test_search_api.py`

**Key metrics**:
- Unit tests: 21+ tests, ~10s
- Integration tests: varies, ~30s

**References**:
- [`search-api-testing.md`](search-api-testing.md)
- `srv/search/tests/README.md`

---

### Agent Service

**What it tests**: Agent API endpoints, authentication, webhooks

**Quick test**:
```bash
cd provision/ansible
make test-agent
```

**Test structure**: `srv/agent/tests/` (location)

---

### Apps Service

**What it tests**: AI Portal UI, components, integration

**Quick test**:
```bash
cd provision/ansible
make test-apps
```

**Test structure**: `srv/apps/*/tests/` (location)

---

## Test Environments

Busibox supports multiple testing environments:

### Production Environment

**Container IDs**: 200-209  
**IP Range**: 10.96.200.200-209  
**Database**: busibox

```bash
cd provision/ansible
make test-all  # Uses production by default
# or explicitly
make test-all INV=inventory/production
```

---

### Test Environment

**Container IDs**: 300-309  
**IP Range**: 10.96.201.200-209  
**Database**: busibox_test

```bash
cd provision/ansible
make test-all INV=inventory/test
```

**Create test environment**:
```bash
cd provision/pct
bash create_lxc_base.sh test
```

**Destroy test environment**:
```bash
bash destroy_test.sh
```

---

### Local Development

For local testing without Proxmox:

```bash
# Run unit tests locally
cd srv/ingest
python -m pytest tests/test_chunker.py -v

cd srv/search
python -m pytest tests/unit/ -v
```

---

## CI/CD Integration

### Automated Testing in CI/CD

Example GitHub Actions workflow:

```yaml
name: Test Busibox

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]

jobs:
  test-infrastructure:
    runs-on: self-hosted  # Proxmox runner
    steps:
      - uses: actions/checkout@v3
      
      - name: Run infrastructure tests
        run: |
          cd /root/busibox
          bash scripts/test-infrastructure.sh full
      
      - name: Run service tests
        run: |
          cd /root/busibox/provision/ansible
          make test-all INV=inventory/test

  test-services:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          cd srv/ingest
          pip install -r requirements.txt
          pip install pytest pytest-cov
      
      - name: Run unit tests
        run: |
          cd srv/ingest
          pytest tests/test_chunker.py -v --cov=src
```

---

### Pre-deployment Testing

```bash
#!/bin/bash
# scripts/pre-deploy.sh

set -e

echo "Running pre-deployment tests..."

# 1. Run infrastructure tests on test environment
cd /root/busibox
bash scripts/test-infrastructure.sh full

# 2. Deploy to test environment
cd provision/ansible
make all INV=inventory/test

# 3. Run health checks
make verify-health INV=inventory/test

# 4. Run service tests
make test-all INV=inventory/test

echo "All tests passed! Ready for production deployment."
```

---

## Troubleshooting

### Common Issues

#### 1. SSH Connection Failed

**Symptom**: `ssh: connect to host X.X.X.X port 22: Connection refused`

**Solutions**:
```bash
# Check container is running
pct status <CTID>

# Start container if stopped
pct start <CTID>

# Check SSH service in container
pct exec <CTID> -- systemctl status ssh

# Restart SSH if needed
pct exec <CTID> -- systemctl restart ssh
```

---

#### 2. Test Command Not Found

**Symptom**: `ingest-test: command not found`

**Solutions**:
```bash
# Verify test script is deployed
ssh root@10.96.200.206 'which ingest-test'

# Redeploy if missing
cd provision/ansible
make ingest
```

---

#### 3. Tests Failing After Deployment

**Symptom**: Tests fail with service errors

**Solutions**:
```bash
# Check service logs
ssh root@10.96.200.206 'journalctl -u ingest-worker -n 50'

# Check service status
ssh root@10.96.200.206 'systemctl status ingest-worker'

# Restart service
ssh root@10.96.200.206 'systemctl restart ingest-worker'

# Verify dependencies
ssh root@10.96.200.206 'cd /srv/ingest && source venv/bin/activate && python -m spacy info en_core_web_sm'
```

---

#### 4. Integration Tests Fail

**Symptom**: Integration tests fail but unit tests pass

**Solutions**:
```bash
# Verify all services are running
make verify-health

# Check inter-service connectivity
ssh root@10.96.200.206 'curl http://10.96.200.204:9091/healthz'  # Milvus
ssh root@10.96.200.206 'curl http://10.96.200.203:5432'  # PostgreSQL
ssh root@10.96.200.206 'curl http://10.96.200.205:9000/minio/health/live'  # MinIO

# Deploy missing services
cd provision/ansible
make all
```

---

#### 5. Wrong Environment

**Symptom**: Tests running against wrong environment

**Solutions**:
```bash
# Always specify environment explicitly
make test-ingest INV=inventory/test

# Verify inventory file
cat provision/ansible/inventory/test/hosts.yml

# Check container IPs
pct config <CTID> | grep net0
```

---

### Test Debugging

#### Enable Verbose Output

```bash
# Makefile tests
cd provision/ansible
make test-ingest

# Then SSH and run manually with verbose
ssh root@10.96.200.206
ingest-test -vv -s
```

#### Run Specific Tests

```bash
# Specific test file
ssh root@10.96.200.206
ingest-test tests/test_chunker.py::TestMilvusLimit -v

# Specific test method
ingest-test tests/test_chunker.py::TestMilvusLimit::test_very_long_paragraph -vv -s
```

#### Check Test Logs

```bash
# Container logs
ssh root@10.96.200.206
journalctl -u ingest-worker -n 100 --no-pager

# Test output
cd /srv/ingest
pytest tests/ -v --tb=long
```

---

## Related Documentation

### Testing Guides

- **[Testing Strategy](testing-strategy.md)** - Infrastructure test framework
- **[Makefile Test Targets](makefile-test-targets.md)** - Makefile test commands reference
- **[ColPali Testing](colpali-testing.md)** - ColPali visual embeddings testing
- **[Search API Testing](search-api-testing.md)** - Search API testing guide

### Service Documentation

- **[Ingest Tests](../../srv/ingest/tests/README.md)** - Ingest service test suite
- **[Search Tests](../../srv/search/tests/README.md)** - Search service test suite
- **[Ingest Test Runner](../reference/ingest-test-runner.md)** - `ingest-test` command reference

### Deployment Guides

- **[Deployment Guides](../deployment/)** - Service deployment procedures
- **[Test Environment](../deployment/test-environment.md)** - Test environment setup

### Architecture

- **[Architecture](../architecture/architecture.md)** - System architecture overview

---

## Quick Reference

### Common Commands

```bash
# ============================================================
# Infrastructure Tests
# ============================================================
cd /root/busibox
bash scripts/test-infrastructure.sh full
bash scripts/test-llm-containers.sh

# ============================================================
# Service Tests (from host)
# ============================================================
cd /root/busibox/provision/ansible

# All tests
make test-all

# Individual services
make test-ingest          # Quick (~5s)
make test-ingest-all      # Full (~60s)
make test-ingest-coverage # With coverage
make test-search          # Unit (~10s)
make test-search-integration # Integration (~30s)
make test-agent
make test-apps

# Health checks
make verify-health
make verify-smoke
make verify

# Environment selection
make test-all INV=inventory/test
make test-all INV=inventory/production

# ============================================================
# Service Tests (direct in container)
# ============================================================

# Ingest
ssh root@10.96.200.206
ingest-test                 # Default (chunker)
ingest-test all            # All tests
ingest-test coverage       # With coverage

# Search
ssh root@10.96.200.204
search-test                # Default (unit)
search-test integration    # Integration
search-test coverage       # With coverage

# ============================================================
# Deployment + Testing
# ============================================================
cd provision/ansible

# Deploy and test
make ingest && make test-ingest
make search && make test-search
make all && make verify && make test-all
```

---

## Summary

The Busibox testing framework provides comprehensive testing at all levels:

1. **Infrastructure**: Container creation, networking, GPU passthrough
2. **Provisioning**: Ansible deployment and configuration
3. **Health**: Service health checks and smoke tests
4. **Service**: Unit and integration tests for each service
5. **End-to-end**: Full pipeline validation

All tests can be executed from the Proxmox host using make commands or shell scripts, with support for multiple environments (production, test, local).

**Key principle**: Test frequently, test thoroughly, test from the host.

---

**Last Updated**: 2025-11-17  
**Maintainer**: Busibox Team

