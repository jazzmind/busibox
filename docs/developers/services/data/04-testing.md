---
title: "Data Service Testing"
category: "developer"
order: 63
description: "How to run and debug data service tests"
published: true
---

# Data Service Testing

## Running Tests

```bash
# On container
make test SERVICE=data INV=staging

# Locally against staging
make test-local SERVICE=data INV=staging

# Full pipeline with worker
make test-local SERVICE=data INV=staging WORKER=1 FAST=0
```

## Test Database

Tests use `test_data` database (owned by `busibox_test_user`). See [architecture/08-tests](../../architecture/08-tests.md) for isolation details.

## Bootstrap

Ensure test credentials are initialized: `make bootstrap-test-creds INV=inventory/staging`
