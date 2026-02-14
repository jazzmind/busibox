---
title: "Search Service Testing"
category: "developer"
order: 73
description: "How to run and debug search service tests"
published: true
---

# Search Service Testing

## Running Tests

```bash
# On container
make test SERVICE=search INV=staging

# Locally against staging
make test-local SERVICE=search INV=staging
```

## Dependencies

Search tests require Milvus and PostgreSQL with test data. See [architecture/08-tests](../../architecture/08-tests.md) for container IPs and test database setup.
