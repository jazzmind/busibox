---
title: "Authenticated Testing Guide"
category: "developer"
order: 1.5
description: "How to write authenticated integration tests using busibox_common.testing"
published: true
---

# Authenticated Testing Guide

**Created**: 2026-03-10
**Status**: Active
**Category**: Guide
**Related Docs**:
- `developers/01-testing.md` -- general testing guide
- `developers/02-security.md` -- security architecture

## Overview

Every Busibox service uses **real JWT tokens** for integration tests -- no
mocked auth.  The shared `busibox_common.testing` package handles the entire
flow:

1. Create a test user via the authz magic-link login flow
2. Exchange the session JWT for a service-scoped access token
3. Include `X-Test-Mode: true` to route DB queries to the test database

All of this is encapsulated in `AuthTestClient`.  You should never implement
your own token acquisition logic in a service's test suite.

## Architecture

```
┌──────────────┐  magic link   ┌────────────┐  token exchange  ┌──────────────┐
│ AuthTestClient│─────────────▶│  authz-api │────────────────▶│ service-api  │
│ (test code)  │  X-Test-Mode │ test_authz │  service JWT     │ test_{db}    │
└──────────────┘              └────────────┘                  └──────────────┘
```

**Test databases** (all owned by `busibox_test_user`):

| Service       | Test Database   |
|---------------|-----------------|
| AuthZ         | `test_authz`    |
| Data / Search | `test_files`    |
| Agent         | `test_agent`    |
| Config        | `test_config`   |

These are created by `config/init-databases.sql` (Docker) or the Ansible
`postgres` role (Proxmox), and bootstrapped with schemas and a test user by
`scripts/docker/bootstrap-test-databases.py`.

## Quick Start: Adding Tests to a Service

### 1. Set up `conftest.py`

Create `srv/<service>/tests/conftest.py`:

```python
"""
Pytest configuration for <service> tests.
Uses real JWT tokens from authz via busibox_common.testing.
"""
import os
from pathlib import Path

# CRITICAL: Load env files BEFORE importing app code
from busibox_common.testing.environment import load_env_files, create_service_auth_fixture
load_env_files(Path(__file__).parent.parent)

# Enable the failed-test filter plugin
pytest_plugins = ["busibox_common.testing.pytest_failed_filter"]

# Import shared fixtures so pytest discovers them
from busibox_common.testing.auth import auth_client  # noqa: F401

# Create an autouse fixture that sets AUTHZ_AUDIENCE=<service>-api
set_auth_env = create_service_auth_fixture("<service>")
```

### 2. Write an integration test

```python
import pytest
import httpx


SERVICE_URL = os.getenv("SERVICE_URL", "http://localhost:8080")


@pytest.mark.asyncio
async def test_authenticated_endpoint(auth_client):
    """Verify the /items endpoint requires and accepts valid auth."""
    headers = auth_client.get_auth_header(audience="<service>-api")

    async with httpx.AsyncClient(base_url=SERVICE_URL) as client:
        resp = await client.get("/items", headers=headers, timeout=10.0)
        assert resp.status_code == 200
```

### 3. Run the test

```bash
# Docker (recommended for local dev)
make test-docker SERVICE=<service>

# Or target a specific file
make test-docker SERVICE=<service> ARGS="tests/integration/test_items.py"
```

## API Reference

### `AuthTestClient`

The main entry point.  Created once per test session by the `auth_client`
fixture.

```python
from busibox_common.testing.auth import AuthTestClient

client = AuthTestClient(
    authz_url="http://localhost:8010",   # auto-detected from AUTHZ_JWKS_URL
    test_user_id="00000000-...-000001",  # auto-detected from TEST_USER_ID
    test_user_email="test@test.example.com",
)
```

#### Token methods

| Method | Returns | Description |
|--------|---------|-------------|
| `get_token(audience)` | `str` | Service-scoped JWT access token |
| `get_auth_header(audience)` | `dict` | `{"Authorization": "Bearer ...", "X-Test-Mode": "true"}` |
| `get_token_with_scopes(scopes, audience)` | `str` | Token with specific scopes |
| `get_token_without_scopes(audience)` | `str` | Token with empty scopes (for 403 testing) |

#### Role management

| Method | Description |
|--------|-------------|
| `add_role_to_user(name)` | Grant a role (creates it if missing) |
| `remove_role_from_user(name)` | Revoke a role |
| `create_role(name, scopes)` | Create a test role with scopes |
| `delete_role(role_id)` | Delete a role |
| `get_user_roles()` | List roles on test user |
| `clear_user_roles()` | Remove all roles |

#### Context managers

```python
# Temporarily add a role
with auth_client.with_role("analyst"):
    resp = client.get("/data", headers=auth_client.get_auth_header("data-api"))
    assert resp.status_code == 200

# Temporarily add multiple roles
with auth_client.with_roles(["analyst", "editor"]):
    ...

# Ensure test user has NO roles (for 403 testing)
with auth_client.with_clean_user():
    resp = client.get("/data", headers=auth_client.get_auth_header("data-api"))
    assert resp.status_code == 403
```

#### Cleanup

`auth_client.cleanup()` is called automatically at session end by the
fixture.  It removes all roles added during the session and clears the
cached session JWT.

### `DatabasePool` / `RLSEnabledPool`

For tests that need direct database access:

```python
from busibox_common.testing.database import RLSEnabledPool

pool = RLSEnabledPool(database="test_files")
await pool.initialize()
pool.set_rls_context(user_id="...", role_ids=["..."])

async with pool.acquire() as conn:
    rows = await conn.fetch("SELECT * FROM data_files WHERE ...")

await pool.close()
```

Session-scoped fixtures `db_pool`, `db_conn`, and `rls_pool` are provided
for convenience.

### Environment helpers

```python
from busibox_common.testing.environment import (
    load_env_files,               # Load .env.local / .env before app imports
    create_service_auth_fixture,  # Factory for autouse AUTHZ_AUDIENCE fixture
    get_test_doc_repo_path,       # Path to busibox-testdocs repo
)
from busibox_common.testing.fixtures import (
    require_env,       # Fail test if env var missing
    get_env,           # Get env var with default
    get_authz_base_url,# Extract authz URL from JWKS URL
)
```

## How Test Database Routing Works

Each Busibox service has a **DatabaseRouter** (from `busibox_common.test_mode`)
that inspects the `X-Test-Mode: true` request header at runtime:

```
Request with X-Test-Mode: true
        │
        ▼
DatabaseRouter.get_pool(request)
        │
        ├── header present + test mode enabled ──▶ test_pool (test_files, etc.)
        │
        └── otherwise ──▶ prod_pool (files, agent, etc.)
```

This means:

- Production data is **never** touched by tests
- The same running service handles both prod and test traffic
- Test isolation is per-request, not per-process

Environment variables that enable this:

| Variable | Value | Purpose |
|----------|-------|---------|
| `AUTHZ_TEST_MODE_ENABLED` | `true` | Enable test-mode routing in authz |
| `DATA_TEST_MODE_ENABLED` | `true` | Enable in data-api |
| `TEST_DB_NAME` | `test_authz` | Test database name |
| `TEST_DB_USER` | `busibox_test_user` | Test database user |
| `TEST_DB_PASSWORD` | `testpassword` | Test database password |

## When to Use Mocks vs Real Auth

| Scenario | Approach |
|----------|----------|
| **Integration test** (hits an API endpoint) | Always use `auth_client.get_auth_header()` |
| **Unit test** (pure function, no I/O) | No auth needed |
| **Unit test** (needs a Principal object but no network) | Use a `mock_principal` fixture, clearly labelled |
| **Scope/role enforcement test** | Use `auth_client.with_role()` / `with_clean_user()` |
| **Negative auth test** (401, 403) | Use `auth_client.get_token_without_scopes()` or no header |

**Rule of thumb**: if the code under test makes an HTTP call or touches a
database, use real auth.

## Package Structure

```
srv/shared/
├── busibox_common/          # pip-installable package
│   ├── __init__.py
│   ├── auth.py              # Runtime JWT/RLS helpers
│   ├── db.py                # DatabaseInitializer, SchemaManager
│   ├── llm.py               # LiteLLM client
│   ├── pool.py              # AsyncPG pool manager
│   ├── test_mode.py         # DatabaseRouter, TestModeConfig
│   └── testing/             # <-- TEST UTILITIES LIVE HERE
│       ├── __init__.py      # Re-exports everything
│       ├── auth.py          # AuthTestClient, auth_client fixture
│       ├── database.py      # DatabasePool, RLSEnabledPool
│       ├── environment.py   # load_env_files, create_service_auth_fixture
│       ├── fixtures.py      # require_env, get_authz_base_url
│       ├── clients.py       # create_async_client, create_sync_client
│       └── pytest_failed_filter.py
└── testing/                 # Backward-compat shims (re-exports from above)
```

Install for tests:

```bash
pip install busibox-common[testing]
```

Or, when running inside Docker / Ansible-deployed containers, the package is
available via PYTHONPATH since `busibox_common/` is copied alongside the
service source code.

## Troubleshooting

### "AUTHZ_JWKS_URL not configured"

The `AuthTestClient` needs to know where authz lives.  Make sure your
`.env.local` or the test runner sets `AUTHZ_JWKS_URL`:

```bash
AUTHZ_JWKS_URL=http://localhost:8010/.well-known/jwks.json
```

### "Failed to initiate login for test user"

The authz service must be running and `test.example.com` must be in
the allowed email domains.  Run `make test-db-init` to bootstrap domains.

### "Password authentication failed"

You're connecting to the **production** database instead of the test
database.  Make sure `X-Test-Mode: true` is in your request headers
and the service has `*_TEST_MODE_ENABLED=true`.

### "Too many connections"

Use the session-scoped `db_pool` / `rls_pool` fixtures instead of
creating per-test connections.
