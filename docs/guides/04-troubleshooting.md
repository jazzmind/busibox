# Troubleshooting Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `guides/troubleshooting/deployment-debug.md`  
- `guides/troubleshooting/ingest-worker-not-processing.md`  
- `guides/02-deployment.md`

## Quick Checks
- Confirm containers are running: `pct status <CTID>`.
- Health endpoints:
  - Ingest: `curl http://10.96.200.206:8000/health`
  - Search: `curl http://10.96.200.204:8003/health`
  - AuthZ: `curl http://10.96.200.210:8010/health/live`
- Logs inside container: `journalctl -u <service> -n 100 --no-pager`.

## Common Issues
- **401/403 on upload/search**
  - JWT audience/issuer mismatch; check `JWT_AUDIENCE` per service.
  - Legacy `X-User-Id` disallowed if `ALLOW_LEGACY_AUTH=false`.
  - Missing role permissions (`create` for shared upload, `read` for search).
- **Ingestion stuck in queued**
  - Redis not reachable (`REDIS_HOST`), or consumer group missing.
  - Worker errors: check `srv/ingest` worker logs; Milvus/MinIO connectivity.
  - Unsupported MIME type or oversized upload (defaults 100 MB).
- **Search returns empty**
  - Partitions missing: ensure ingest wrote to `personal_*` or `role_*`.
  - Milvus collection name mismatch between ingest/search.
  - Reranker errors: disable `ENABLE_RERANKING` to isolate.
- **MinIO/Storage failures**
  - Validate creds match Ansible provisioning.
  - Check bucket existence (`documents` by default).

## When to Redeploy
- Config drift: re-run `make <role>` from `provision/ansible`.
- Container corruption: recreate via `create_lxc_base.sh` then redeploy services.

## Escalation Artifacts
- Include: service logs, JWT used (with claims), sample request payload, container statuses, and output from health checks.
