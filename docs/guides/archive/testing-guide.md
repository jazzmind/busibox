# Busibox Testing Guide

**Created**: 2025-01-24
**Last Updated**: 2025-01-24
**Status**: Active
**Category**: Guide
**Related Docs**:
- [MCP Server Usage](mcp-server-usage.md)
- [Makefile](../../provision/ansible/Makefile)
- [Testing Reference](../testing/)

## Overview

This guide explains how to test Busibox services using the enhanced Makefile targets and interactive test menu system.

## Prerequisites

- SSH access to Proxmox host
- Ansible configured with inventory
- Services deployed to test or production environment
- Sample test documents (for extraction tests)

## Quick Start

### Interactive Test Menu (Recommended)

The easiest way to run tests is using the interactive menu:

```bash
cd provision/ansible
make test-menu
```

This displays a menu with options for:
1. Service tests (ingest, search, agent, apps)
2. Extraction strategy tests
3. Coverage reports
4. Verification checks
5. All tests

Simply enter the number for the test you want to run.

### Direct Make Targets

You can also run tests directly using make targets:

```bash
# Service tests
make test-ingest         # Test ingest service
make test-search         # Test search service
make test-agent          # Test agent service
make test-apps           # Test applications

# All service tests
make test-all
```

## Service Tests

### Ingest Service Tests

**Basic Tests** (unit tests only):
```bash
make test-ingest
```

This runs:
- Chunking tests
- Text extraction tests
- Basic document processing tests

**All Tests** (including integration):
```bash
make test-ingest-all
```

This includes:
- All basic tests
- Integration tests with real services
- End-to-end document processing

**With Coverage**:
```bash
make test-ingest-coverage
```

Generates HTML coverage report at `/srv/ingest/htmlcov/index.html` on ingest-lxc.

**View Coverage**:
```bash
ssh root@<ingest-ip>
cd /srv/ingest
python -m http.server 8080 --directory htmlcov
# Open http://<ingest-ip>:8080 in browser
```

### Search Service Tests

**Basic Tests** (unit tests only):
```bash
make test-search
# or
make test-search-unit
```

This runs:
- Vector search tests
- Keyword search tests
- Hybrid search tests

**Integration Tests**:
```bash
make test-search-integration
```

This requires:
- Milvus running
- PostgreSQL running
- Embedding service running

**With Coverage**:
```bash
make test-search-coverage
```

Generates HTML coverage report at `/opt/search/htmlcov/index.html` on milvus-lxc.

### Agent Service Tests

```bash
make test-agent
```

Tests the agent API endpoints and functionality.

### Application Tests

```bash
make test-apps
```

Tests Next.js applications on apps-lxc.

## Extraction Strategy Tests

These tests evaluate different PDF extraction methods using sample documents.

### Prerequisites

Sample documents must be in `busibox/samples/docs/` directory. The Makefile automatically copies them to the test container.

### Simple Extraction

Tests basic PDF extraction without LLM cleanup:

```bash
make test-extraction-simple
```

**What it tests**:
- PDF to markdown conversion
- Basic text extraction
- Document structure preservation

**Fast**: No LLM calls, runs quickly.

### LLM-Enhanced Extraction

Tests PDF extraction with LLM cleanup:

```bash
make test-extraction-llm
```

**What it tests**:
- PDF to markdown conversion
- LLM-based text cleanup
- Improved formatting

**Requirements**:
- LiteLLM service running on litellm-lxc
- Model configured in LiteLLM

**Slower**: Makes LLM API calls.

### Marker Extraction

Tests Marker-based PDF extraction (GPU-accelerated):

```bash
make test-extraction-marker
```

**What it tests**:
- Advanced PDF parsing
- Layout analysis
- Table and figure extraction

**Requirements**:
- Marker installed on ingest-lxc
- 3.2GB model cache
- Significant CPU/GPU resources

**Slowest**: Downloads models on first run, resource-intensive.

### ColPali Visual Extraction

Tests ColPali visual document embeddings:

```bash
make test-extraction-colpali
```

**What it tests**:
- Visual document understanding
- Page-level embeddings
- Image-based search

**Requirements**:
- ColPali service running on vllm-lxc
- GPU required

## Verification Tests

### Health Checks

Verify all services are healthy:

```bash
make verify-health
```

Checks:
- PostgreSQL connection
- MinIO health endpoint
- Milvus health endpoint
- Agent API health endpoint (if deployed)

### Smoke Tests

Run basic database smoke tests:

```bash
make verify-smoke
```

Checks:
- Database schema exists
- Tables are accessible
- Migrations are applied

### All Verification

Run all verification checks:

```bash
make verify
```

Runs both health checks and smoke tests.

## Coverage Reports

### Generating Coverage

Run tests with coverage:

```bash
make test-ingest-coverage
make test-search-coverage
```

### Viewing Coverage

**Ingest Service**:
```bash
ssh root@<ingest-ip>
cd /srv/ingest
python -m http.server 8080 --directory htmlcov
```

Open `http://<ingest-ip>:8080` in browser.

**Search Service**:
```bash
ssh root@<milvus-ip>
cd /opt/search
python -m http.server 8080 --directory htmlcov
```

Open `http://<milvus-ip>:8080` in browser.

## Test Environments

### Testing on Test Environment

All test commands default to the production inventory. To test on the test environment:

```bash
# Set environment variable
export INV=inventory/test

# Run tests
make test-ingest

# Or specify inline
make test-ingest INV=inventory/test
```

### Container IP Resolution

The Makefile automatically resolves container IPs from the inventory:

```makefile
# Production IPs
INGEST_IP = 10.96.200.206
AGENT_IP = 10.96.200.202
APPS_IP = 10.96.200.201
MILVUS_IP = 10.96.200.204

# Test IPs (when INV=inventory/test)
# Same IPs but from test inventory
```

## Troubleshooting

### Test Samples Not Found

**Error**: `ERROR: Samples directory not found`

**Solution**:
```bash
# Ensure samples exist
ls -la samples/docs/

# Should contain directories like:
# doc01_rfp_project_management/
# doc02_technical_specification/
# etc.
```

### Cannot Resolve Container IP

**Error**: `ERROR: Could not resolve ingest-lxc IP address`

**Solution**:
```bash
# Check inventory
cat provision/ansible/inventory/production/hosts.yml

# Verify container is running
ssh root@<proxmox-host>
pct list
pct status <container-id>
```

### Tests Fail on Test Container

**Error**: Tests fail when running on test environment

**Solution**:
```bash
# Verify test environment is deployed
make all INV=inventory/test

# Check service status
ssh root@<test-container-ip>
systemctl status <service>
journalctl -u <service> -n 50
```

### LLM Tests Fail

**Error**: `Connection refused` or `Model not found`

**Solution**:
```bash
# Check LiteLLM is running
ssh root@<litellm-ip>
systemctl status litellm

# Check model configuration
curl http://<litellm-ip>:4000/models

# Verify ingest can reach LiteLLM
ssh root@<ingest-ip>
curl http://<litellm-ip>:4000/health
```

### Marker Tests Fail

**Error**: `Out of memory` or `Model download failed`

**Solution**:
```bash
# Check available memory
ssh root@<ingest-ip>
free -h

# Check disk space
df -h

# Marker requires:
# - 3.2GB for models
# - 4GB+ RAM recommended
# - May need to increase container memory
```

### ColPali Tests Fail

**Error**: `Service unavailable` or `GPU not found`

**Solution**:
```bash
# Check ColPali service
ssh root@<vllm-ip>
systemctl status colpali

# Check GPU availability
nvidia-smi

# Verify ColPali endpoint
curl http://<vllm-ip>:8003/health
```

## CI/CD Integration

### Running Tests in CI

Example GitHub Actions workflow:

```yaml
name: Test Busibox Services

on: [push, pull_request]

jobs:
  test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v3
      
      - name: Run Tests
        run: |
          cd provision/ansible
          make test-all INV=inventory/test
```

### Pre-Deployment Testing

Always run tests before deploying to production:

```bash
# 1. Deploy to test
make all INV=inventory/test

# 2. Run all tests
make test-all INV=inventory/test

# 3. Verify services
make verify INV=inventory/test

# 4. If all pass, deploy to production
make all
```

## Best Practices

### 1. Use Test Menu for Exploration

When learning or debugging, use the interactive menu:
```bash
make test-menu
```

### 2. Run Specific Tests During Development

When working on a feature, run only relevant tests:
```bash
# Working on ingest chunking
make test-ingest

# Working on search
make test-search-unit
```

### 3. Run Coverage Periodically

Check test coverage regularly:
```bash
make test-ingest-coverage
make test-search-coverage
```

Aim for >80% coverage on critical paths.

### 4. Test on Test Environment First

Always test on the test environment before production:
```bash
make test-ingest INV=inventory/test
```

### 5. Verify After Deployment

After deploying, run verification:
```bash
make verify
```

### 6. Document Test Failures

When tests fail, document the issue:
```bash
# Create troubleshooting doc
vim docs/troubleshooting/test-failure-YYYY-MM-DD.md
```

### 7. Keep Sample Documents Updated

Maintain a diverse set of test documents:
- Different PDF types
- Various layouts
- Different languages
- Edge cases

## Advanced Usage

### Running Specific Test Files

SSH into the container and run pytest directly:

```bash
# Ingest service
ssh root@<ingest-ip>
cd /srv/ingest
source venv/bin/activate
pytest tests/test_chunker.py -v

# Search service
ssh root@<milvus-ip>
cd /opt/search
source venv/bin/activate
pytest tests/test_vector_search.py -v
```

### Running Tests with Specific Markers

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only slow tests
pytest -m slow
```

### Debugging Failed Tests

```bash
# Run with verbose output
pytest -vv

# Run with print statements
pytest -s

# Run with pdb on failure
pytest --pdb

# Run last failed tests only
pytest --lf
```

### Custom Test Configuration

Create a `pytest.ini` in the service directory:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    unit: Unit tests
    integration: Integration tests
    slow: Slow tests
```

## Related Documentation

- [MCP Server Usage](mcp-server-usage.md) - Using MCP to run tests
- [Makefile](../../provision/ansible/Makefile) - All available targets
- [Testing Reference](../testing/) - Detailed test documentation
- [Troubleshooting](../troubleshooting/) - Common issues and solutions

## Next Steps

1. **Run the test menu**: `make test-menu`
2. **Explore test options**: Try different test types
3. **Check coverage**: Run tests with coverage
4. **Document issues**: Add to troubleshooting docs
5. **Automate**: Add tests to CI/CD pipeline

