# Busibox Testing Documentation

This directory contains all testing documentation for the Busibox platform.

## Start Here

**[📘 Master Testing Guide](master-guide.md)** - Complete overview of the testing framework

## Testing Guides

### Infrastructure & Deployment

- **[Testing Strategy](testing-strategy.md)** - Infrastructure provisioning test framework
  - Container creation and configuration
  - Ansible deployment testing
  - Idempotency verification
  - Test environment management

- **[Makefile Test Targets](makefile-test-targets.md)** - Command reference for running tests from host
  - Make target usage
  - Environment selection
  - Test execution workflows
  - Troubleshooting

### Service-Specific Testing

- **[Search API Testing](search-api-testing.md)** - Search service testing guide
  - Hybrid search validation
  - Integration testing
  - Performance benchmarks

- **[ColPali Testing](colpali-testing.md)** - Visual embeddings testing
  - ColPali service validation
  - Image encoding tests
  - Performance metrics

- **[Environment Selection](environment-selection.md)** - Running tests in different environments
  - Production vs test environments
  - Automatic IP detection
  - Environment-aware make targets
  - CI/CD integration

## Test Locations

### Infrastructure Tests

```
scripts/
├── test-infrastructure.sh    # Main infrastructure test suite
├── test-llm-containers.sh    # LLM container connectivity
├── test-colpali.sh          # ColPali service testing
└── test-vllm-embedding.sh   # vLLM embedding testing
```

### Service Tests

```
srv/
├── ingest/tests/            # Ingest service tests
│   ├── README.md            # Test documentation
│   ├── test_chunker.py      # Chunking tests (23+)
│   ├── test_multi_flow.py   # Multi-flow tests (40+)
│   ├── test_colpali.py      # ColPali tests (30+)
│   ├── api/                 # API tests
│   └── integration/         # Integration tests
│
├── search/tests/            # Search service tests
│   ├── README.md            # Test documentation
│   ├── unit/                # Unit tests
│   │   ├── test_milvus_search.py
│   │   ├── test_highlighter.py
│   │   └── test_reranker.py
│   └── integration/         # Integration tests
│       └── test_search_api.py
│
├── agent/tests/             # Agent service tests
│   ├── unit/
│   └── integration/
│
└── apps/*/tests/            # App tests
```

## Quick Start

### Run All Tests from Host

```bash
cd /root/busibox/provision/ansible

# All service tests
make test-all

# Individual services
make test-ingest
make test-search

# Health checks
make verify
```

### Infrastructure Testing

```bash
cd /root/busibox

# Full infrastructure test
bash scripts/test-infrastructure.sh full
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

## Test Environments

| Environment | Container IDs | IP Range | Database |
|-------------|---------------|----------|----------|
| Production | 200-209 | 10.96.200.x | busibox |
| Test | 300-309 | 10.96.201.x | busibox_test |

## Coverage Reports

- **Ingest**: `ssh root@10.96.200.206` → `/srv/ingest/htmlcov/index.html`
- **Search**: `ssh root@10.96.200.204` → `/opt/search/htmlcov/index.html`

## Related Documentation

### Reference Documentation

- **[Ingest Test Runner](../reference/ingest-test-runner.md)** - `ingest-test` command reference
- **[PDF Test Suite](../reference/pdf-test-suite.md)** - PDF processing tests

### Deployment Guides

- **[Test Environment](../deployment/test-environment.md)** - Test environment setup
- **[Deployment Guides](../deployment/)** - Service deployment procedures

### Architecture

- **[Architecture](../architecture/architecture.md)** - System design
- **[Testing Strategy](../architecture/testing-strategy.md)** - Original testing architecture

## Contributing

When adding new tests:

1. Place service tests in `srv/<service>/tests/`
2. Document tests in service README
3. Add make targets in `provision/ansible/Makefile`
4. Update this documentation
5. Include in CI/CD pipeline

## Support

For testing issues:

1. Check **[Master Guide Troubleshooting](master-guide.md#troubleshooting)**
2. Review service-specific test documentation
3. Check logs: `journalctl -u <service> -n 50`
4. Verify health: `make verify-health`

---

**Last Updated**: 2025-11-17

