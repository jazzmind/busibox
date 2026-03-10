---
title: "Testing Guide"
category: "developer"
order: 1
description: "Guide for running tests, test database isolation, and platform verification"
published: true
---

# Testing Guide

**Created**: 2025-12-09  
**Last Updated**: 2026-03-10  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `developers/testing-auth-guide.md` -- authenticated testing with `busibox_common.testing`
- `administrators/02-install.md`  
- `architecture/08-tests.md`

## Test Database Isolation

All tests run against dedicated test databases, separate from production:

| Service       | Test Database   | Owner              |
|---------------|-----------------|---------------------|
| Agent         | `test_agent`    | `busibox_test_user` |
| AuthZ         | `test_authz`    | `busibox_test_user` |
| Data / Search | `test_files`    | `busibox_test_user` |
| Config        | `test_config`   | `busibox_test_user` |

This ensures tests never pollute production data. The `make test` commands
automatically connect to the appropriate test databases.

## Authenticated Testing

All integration tests use **real JWT tokens** obtained from the authz service.
The shared `busibox_common.testing` package provides the `AuthTestClient`
which handles user creation, token exchange, and cleanup.

See **[Authenticated Testing Guide](testing-auth-guide.md)** for:
- How to set up `conftest.py` for a new service
- `AuthTestClient` API reference
- When to use real auth vs mocks
- Test database routing (`X-Test-Mode` header)
- Troubleshooting

## Platform Checks (Ansible)
- **Smoke/health**:
  ```bash
  cd provision/ansible
  make verify          # health checks across services
  make verify-health   # service health subset
  make verify-smoke    # DB and container basics
  ```

## Service Testing

### Ingestion Service
- Unit/integration (inside repo or container):
  ```bash
  cd srv/data
  pip install -r requirements.txt
  pytest tests/api     # API routes incl. upload/status/search
  pytest tests/integration -m "not slow"  # pipeline coverage
  pytest tests/test_pdf_splitting.py -v  # PDF splitting tests (uses real PDFs from busibox-testdocs)
  ```
- End-to-end upload + SSE + indexing:
  - Use `tests/integration/test_full_pipeline.py` (requires MinIO, Redis, Milvus running).
- **PDF Splitting Tests**: Tests validate that large PDFs (>5 pages) are automatically split into 5-page chunks before processing. Uses real PDF files from the `busibox-testdocs` repository.

### Search Service
- Run search tests:
  ```bash
  cd srv/search
  pip install -r requirements.txt
  pytest tests/integration/test_search_api.py
  ```
- Ensure Milvus is seeded with ingest-produced partitions before running.

### Agent Server
- **Quick start**:
  ```bash
  cd srv/agent
  source venv/bin/activate
  make test              # All tests
  make test-unit         # Fast unit tests only
  make test-integration  # Integration tests with DB
  make test-cov          # With coverage report
  ```

- **Deployed testing** (via MCP):
  ```bash
  cd provision/ansible
  make test-agent INV=inventory/staging
  make test-agent-coverage INV=inventory/staging
  ```

- **Test structure**:
  - Unit tests: Auth, tokens, agents, workflows, scoring (117 tests)
  - Integration tests: API endpoints, SSE streaming, RBAC (40+ tests)
  - See `guides/agent-server-testing.md` for complete details

### AuthZ Service
- **Integration tests**:
  ```bash
  cd srv/authz
  source venv/bin/activate
  pytest tests/ -v
  ```
- **Test coverage**:
  - OAuth2 token exchange
  - RBAC management
  - Admin endpoints
  - Client credentials flow

## Bootstrap Test Credentials

Test databases and the bootstrap test user are initialised automatically:

```bash
# Docker development -- run once after first `docker compose up`
make test-db-init

# Or from within the manager container
python scripts/docker/bootstrap-test-databases.py
```

This creates:
- Signing keys in `test_authz`
- Test email domains (`test.example.com`, `busibox.local`)
- Test user `00000000-0000-0000-0000-000000000001`
- Schemas for `test_files` and `test_agent`

For Proxmox/staging/production, the Ansible `postgres` role handles test
database creation when `enable_pytest_databases: true`.

The `AuthTestClient` in `busibox_common.testing` automatically bootstraps
the Admin role for the test user on first use, so no manual credential
setup is needed.

## Apps
- App-specific tests live in their repos (Busibox Portal, Busibox Agents). Run `npm test`/`npm run test:watch` per repo after wiring env vars to container endpoints.

## What to Verify After Deployment
- Ingest `/health` and Search `/health` return 200.
- Upload via ingest returns `queued` or `completed` for images/videos; status stream reaches `completed`.
- Search returns results limited to the user's partitions (use different JWTs to confirm access control).
- AuthZ token issuance works and audit rows are written.
- Agent server responds to `/health` and can execute runs.

## Reference

- [Authenticated Testing Guide](testing-auth-guide.md) — `AuthTestClient` API, conftest patterns, DB routing
- [Python Test Import Gotchas](reference/python-test-import-gotchas.md) — Common import and path issues in pytest
- [Test Environment Containers](reference/test-environment-containers.md) — Staging container IPs, DB names, bootstrap

## Related Documentation
- **Auth testing library**: `srv/shared/busibox_common/testing/` (install: `pip install busibox-common[testing]`)
- **Test strategy**: `guides/testing/TEST_STRATEGY.md`
- **Service testing**: `guides/testing/master-guide.md`
