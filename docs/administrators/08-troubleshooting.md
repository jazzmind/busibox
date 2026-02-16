---
title: "Troubleshooting"
category: "administrator"
order: 8
description: "Diagnosing and resolving common Busibox issues"
published: true
---

# Troubleshooting

This guide covers common issues and their solutions. Start with the quick diagnostic steps, then drill into specific problem areas.

**Reference**: [GPU Passthrough](../developers/reference/gpu-passthrough.md) | [GitHub Packages Authentication](../developers/reference/github-packages-authentication.md)

## Quick Diagnostics

### 1. Check Service Status

```bash
make manage SERVICE=all ACTION=status
```

This shows which services are running and which are down.

### 2. Check Health Endpoints

```bash
curl http://<authz-ip>:8010/health/live     # AuthZ
curl http://<data-ip>:8002/health            # Data API
curl http://<search-ip>:8003/health          # Search API
curl http://<agent-ip>:8000/health           # Agent API
curl http://<files-ip>:9000/minio/health/live   # MinIO
curl http://<milvus-ip>:9091/healthz         # Milvus
```

### 3. Check Logs

```bash
make manage SERVICE=<service> ACTION=logs
```

On Proxmox, you can also check logs inside containers:

```bash
pct enter <CTID>
journalctl -u <service-name> -n 100 --no-pager
```

## Authentication Issues

### "401 Unauthorized" or "403 Forbidden"

**Cause**: Token validation failure.

**Check**:
- Is the AuthZ service running? `make manage SERVICE=authz ACTION=status`
- Are tokens being exchanged correctly? Check the calling service's logs
- Is the JWT audience correct for the target service?
- Has the token expired? (Default TTL: 15 minutes)

**Fix**:
```bash
# Restart AuthZ
make manage SERVICE=authz ACTION=restart

# If config changed, redeploy
make manage SERVICE=authz ACTION=redeploy
```

### "Password authentication failed"

**Cause**: Secrets weren't injected properly. This almost always means a service was started directly instead of through `make`.

**Fix**:
```bash
# Always use make commands -- they inject secrets from vault
make manage SERVICE=<service> ACTION=redeploy
```

### Users Can't Log In

**Check**:
- Is the Busibox Portal running? `make manage SERVICE=busibox-portal ACTION=status`
- Is AuthZ running? `make manage SERVICE=authz ACTION=status`
- Can the portal reach AuthZ? Check portal logs for connection errors
- Is the JWKS endpoint accessible? `curl http://<authz-ip>:8010/.well-known/jwks.json`

### App Redirects to Localhost

**Cause**: `NEXT_PUBLIC_*` environment variables were not set at build time. Next.js embeds these at build, not runtime.

**Fix**:
```bash
# Redeploy the app (rebuilds with correct env vars)
make manage SERVICE=<app-name> ACTION=redeploy
```

## Document Processing Issues

### Documents Stuck in Processing

**Check**:
1. Is the Data Worker running? `make manage SERVICE=data ACTION=status`
2. Is Redis running? `make manage SERVICE=redis ACTION=status`
3. Check worker logs: `make manage SERVICE=data ACTION=logs`

**Common causes**:
- Worker crashed -- restart it: `make manage SERVICE=data ACTION=restart`
- Redis queue backed up -- check Redis connection
- File format not supported -- check MIME type
- File too large -- default limit is 100 MB

### Extraction Quality Issues

**Try**:
- Switch extraction strategy in Busibox Portal admin (Simple → Marker → ColPali)
- Enable LLM cleanup for OCR artifacts
- For scanned documents, enable ColPali (requires GPU)

### Embeddings Not Generated

**Check**:
- Is the Embedding API running? `make manage SERVICE=embedding ACTION=status`
- Check embedding logs: `make manage SERVICE=embedding ACTION=logs`
- Verify the FastEmbed model is downloaded

## Search Issues

### Search Returns No Results

**Check**:
1. Are documents fully processed? (Status should be "completed")
2. Is Milvus running? `make manage SERVICE=milvus ACTION=status`
3. Is the Search API running? `make manage SERVICE=search ACTION=status`
4. Are Milvus partitions created? Check search logs

**Common causes**:
- Documents still processing -- wait for completion
- Wrong visibility settings -- personal docs only visible to uploader
- Milvus collection not initialized -- redeploy search: `make manage SERVICE=search ACTION=redeploy`

### Search Quality Issues

**Try**:
- Enable reranking (`ENABLE_RERANKING=true`) for better relevance
- Adjust chunk sizes if results are too fragmented or too broad
- Check that the embedding model matches between ingestion and search

## Agent Issues

### Agent Not Responding

**Check**:
1. Is the Agent API running? `make manage SERVICE=agent ACTION=status`
2. Is LiteLLM running? `make manage SERVICE=litellm ACTION=status`
3. Check agent logs: `make manage SERVICE=agent ACTION=logs`

**Common causes**:
- LLM provider is down or rate-limited
- Model not available in LiteLLM configuration
- Token exchange failing (check AuthZ)

### Slow Agent Responses

**Possible causes**:
- Local model is slow on current hardware -- consider using a cloud model
- Large context window -- many documents retrieved
- Reranking adding latency -- disable if not needed
- Network issues between services

## Infrastructure Issues

### PostgreSQL

```bash
# Check status
make manage SERVICE=postgres ACTION=status

# Check logs
make manage SERVICE=postgres ACTION=logs

# On Proxmox, enter container
pct enter <pg-ctid>
systemctl status postgresql
```

**Common issues**:
- Disk full -- check storage usage
- Too many connections -- check connection pooling
- Slow queries -- check for missing indexes

### Milvus

```bash
# Check status
make manage SERVICE=milvus ACTION=status

# Health check
curl http://<milvus-ip>:9091/healthz
```

**Note**: Milvus may run in Docker even on Proxmox. Check with:
```bash
docker ps --filter 'name=milvus-standalone'
```

### MinIO

```bash
# Check status
make manage SERVICE=minio ACTION=status

# Health check
curl http://<files-ip>:9000/minio/health/live

# Web console
# http://<files-ip>:9001
```

### Redis

```bash
make manage SERVICE=redis ACTION=status
make manage SERVICE=redis ACTION=logs
```

## App Issues

### App Not Loading

**Check**:
1. Is the app process running? `make manage SERVICE=<app> ACTION=status`
2. Is nginx routing correctly? `make manage SERVICE=nginx ACTION=status`
3. Check app logs: `make manage SERVICE=<app> ACTION=logs`

**Fix**:
```bash
# Restart the app
make manage SERVICE=<app> ACTION=restart

# Full redeploy if restart doesn't help
make manage SERVICE=<app> ACTION=redeploy
```

### App Build Failures

**Common causes**:
- Missing dependencies -- check npm install output in logs
- Node.js version mismatch -- verify Node version on container
- Environment variables missing -- check `apps.yml` configuration

**Fix**:
```bash
# Full redeploy (clean build)
make manage SERVICE=<app> ACTION=redeploy
```

## When to Redeploy vs Restart

| Situation | Action |
|-----------|--------|
| Service crashed | `restart` |
| Changed environment variables | `install` (re-injects secrets) |
| Updated code | `redeploy` |
| Rotated secrets | `install` |
| Configuration drift | `install` |
| Container corruption | Recreate container, then `install` |

## Nuclear Options

If a service is completely broken:

```bash
# Full redeploy with fresh secrets
make install SERVICE=<service>
```

If a container is corrupted (Proxmox only):

```bash
# On Proxmox host -- recreate the container
cd /root/busibox/provision/pct
bash create_lxc_base.sh production

# Then redeploy
make install SERVICE=<service>
```

## Getting Help

1. Check this troubleshooting guide
2. Review service logs: `make manage SERVICE=<service> ACTION=logs`
3. Check the [developer documentation](../developers/) for architecture details
4. Search the docs: use the documentation API or browse `docs/`
