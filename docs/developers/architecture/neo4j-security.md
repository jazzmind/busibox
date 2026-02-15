---
title: Neo4j Graph Database Security Model
category: architecture
order: 12
description: Security architecture for the Neo4j graph database including multi-tenancy, network isolation, and at-rest encryption
published: true
---

# Neo4j Graph Database Security Model

## Overview

Neo4j does not support Row-Level Security (RLS) like PostgreSQL. Instead, Busibox
uses a defense-in-depth approach combining application-level tenant isolation,
network isolation, authentication, and at-rest encryption.

## Multi-Tenancy: Application-Level Filtering

All graph nodes store `owner_id` and `visibility` properties, set during creation
via `upsert_node()`. Every query and mutation method in `GraphService` enforces
tenant isolation:

### Read Operations

All read methods accept `owner_id` and inject a WHERE clause:

```python
WHERE (n.owner_id = $owner_id OR n.visibility = 'shared')
```

Methods with owner filtering:
- `get_graph_visualization()` -- all query branches
- `get_neighbors()`
- `get_document_entities()`
- `find_path()` -- filters start, end, and all intermediate nodes
- `query()` -- callers must include owner filter in Cypher

### Write Operations

All write methods accept optional `owner_id` for tenant validation:

- `create_relationship(owner_id=...)` -- verifies both source and target nodes
  belong to the owner before creating the relationship
- `delete_node(owner_id=...)` -- only deletes if the node belongs to the owner
- `delete_relationships(owner_id=...)` -- only deletes relationships on owned nodes
- `delete_document_graph(owner_id=...)` -- verifies document ownership before
  cascading delete

Internal callers (e.g., `sync_data_document_records`) that have already verified
ownership through PostgreSQL RLS can omit `owner_id` for performance.

### Important: Not Database-Enforced

Unlike PostgreSQL RLS, these filters are application-level. A bug in the
application code could bypass them. This is mitigated by:

1. Consistent patterns across all methods
2. Write operations are typically called from code paths already protected by
   PostgreSQL RLS (document/record CRUD)
3. Network isolation prevents direct database access

## Network Isolation

### Docker

- Neo4j is on the internal `busibox-net` Docker network only
- Ports 7474 (HTTP) and 7687 (Bolt) are NOT exposed to the host in production
- Only the `docker-compose.local-dev.yml` overlay re-exposes ports for development
- Only services within the Docker network can reach Neo4j

### Proxmox (LXC)

- Neo4j runs in a dedicated container on the internal subnet (10.96.x.x)
- Firewall rules restrict access to only the data-lxc container
- No external access to Neo4j ports

## Authentication

- Neo4j requires username/password authentication (`NEO4J_AUTH` environment variable)
- Credentials are managed via Ansible Vault (production) or environment variables (Docker)
- The `GraphService` connects using credentials from environment variables

## At-Rest Encryption

### Recommendation

Use volume-level encryption for Neo4j data volumes:

**Docker**: Use Docker volume encryption or mount an encrypted filesystem:
```yaml
volumes:
  neo4j_data:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: /path/to/encrypted/mount
```

**Proxmox**: Use LUKS/dm-crypt on the LXC container's data partition:
```bash
# On the Proxmox host
cryptsetup luksFormat /dev/zvol/rpool/data/neo4j-data
cryptsetup luksOpen /dev/zvol/rpool/data/neo4j-data neo4j-data
mkfs.ext4 /dev/mapper/neo4j-data
```

This protects against disk theft or backup compromise without impacting
Neo4j query functionality (unlike property-level encryption which would
destroy the ability to search, filter, and aggregate on node properties).

### Why Not Property-Level Encryption

Encrypting individual node properties would:
- Prevent Cypher queries from filtering on encrypted fields
- Eliminate full-text search capability
- Break aggregation queries (count by status, etc.)
- Turn Neo4j into an expensive key-value store
- Require decryption of every node on every read

The graph data is derived from PostgreSQL (the source of truth, protected by RLS).
Volume-level encryption provides at-rest protection without sacrificing functionality.

## Audit Trail

Graph mutations are logged via structlog with context:
- `owner_id` of the requesting user
- `node_id` / `document_id` being modified
- Operation type (create, delete, relationship)
- Blocked operations (ownership check failures)

## Security Checklist

- [x] Application-level owner_id filtering on all read queries
- [x] Application-level owner_id validation on all write operations
- [x] Network isolation (internal Docker network / LXC subnet only)
- [x] Authentication required (username/password)
- [x] No host port exposure in production Docker compose
- [x] Structured logging of all mutations and blocked operations
- [ ] Volume-level encryption (recommended for production)
- [ ] Regular security audit of Cypher queries for injection
