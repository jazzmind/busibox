# Troubleshooting: Ingest Worker Not Processing Files

**Created**: 2025-11-17  
**Updated**: 2025-11-17  
**Status**: Active  
**Category**: Troubleshooting

## Problem

Files uploaded to the ingest service stay stuck in "queued" status until manually reprocessed with the "reprocess" button.

## Root Cause

The ingest-worker service is not running or not processing jobs from the Redis Streams queue.

## Quick Fix

### On Proxmox Host

```bash
# Check if worker is running
ssh root@10.96.200.206 'systemctl status ingest-worker'

# If not running, start it
ssh root@10.96.200.206 'systemctl start ingest-worker'

# Enable it to start on boot
ssh root@10.96.200.206 'systemctl enable ingest-worker'

# Check logs
ssh root@10.96.200.206 'journalctl -u ingest-worker -f'
```

### Full Deployment (Recommended)

```bash
cd /root/busibox/provision/ansible

# Redeploy ingest service (ensures worker is properly configured)
make ingest

# Verify both services are running
ssh root@10.96.200.206 'systemctl status ingest-api ingest-worker'
```

## Diagnosis

### 1. Check Worker Status

```bash
# SSH to ingest container
ssh root@10.96.200.206

# Check service status
systemctl status ingest-worker

# Should show:
#   Active: active (running)
#   Main PID: [number]
```

### 2. Check Worker Logs

```bash
ssh root@10.96.200.206 'journalctl -u ingest-worker -n 100 --no-pager'

# Look for:
#   "Worker started" - Good, worker is running
#   "Connecting to services" - Good, worker is connecting
#   "All services connected" - Good, worker is ready
#   Error messages - Need to fix the error
```

### 3. Check Redis Queue

```bash
ssh root@10.96.200.206

# Connect to Redis
redis-cli

# Check stream length
XLEN jobs:ingestion
# Should show number of pending jobs

# Check consumer groups
XINFO GROUPS jobs:ingestion
# Should show "workers" group

# Check pending messages
XPENDING jobs:ingestion workers
# Shows messages waiting to be processed

# Exit Redis
exit
```

### 4. Check Both Services

```bash
ssh root@10.96.200.206

# Check both API and worker
systemctl status ingest-api
systemctl status ingest-worker

# Both should be active (running)
```

## Common Issues

### Issue 1: Worker Service Not Started

**Symptoms**:
- Files stay in "queued" status
- `systemctl status ingest-worker` shows "inactive (dead)"
- No worker logs

**Solution**:
```bash
ssh root@10.96.200.206

# Start worker
systemctl start ingest-worker

# Enable on boot
systemctl enable ingest-worker

# Verify
systemctl status ingest-worker
```

### Issue 2: Worker Crashing on Startup

**Symptoms**:
- Worker starts but immediately stops
- Logs show errors like "Failed to connect" or "Module not found"

**Check logs**:
```bash
ssh root@10.96.200.206 'journalctl -u ingest-worker -n 50 --no-pager'
```

**Common causes**:
1. **Missing dependencies**: Redeploy service
   ```bash
   make ingest
   ```

2. **Wrong environment variables**: Check `/srv/ingest/.env`
   ```bash
   ssh root@10.96.200.206 'cat /srv/ingest/.env'
   ```

3. **Cannot connect to Redis**: Check Redis is running
   ```bash
   ssh root@10.96.200.206 'systemctl status redis-server'
   ```

4. **Cannot connect to other services**: Check network
   ```bash
   # Test connections
   ssh root@10.96.200.206 'nc -zv 10.96.200.203 5432'  # PostgreSQL
   ssh root@10.96.200.206 'nc -zv 10.96.200.27 19530'  # Milvus
   ssh root@10.96.200.206 'nc -zv 10.96.200.28 9000'   # MinIO
   ```

### Issue 3: Worker Running But Not Processing

**Symptoms**:
- Worker is active
- Jobs stay in queue
- Worker logs show "Worker started" but no job processing

**Check Redis connection**:
```bash
ssh root@10.96.200.206

# Check worker is connected to Redis
journalctl -u ingest-worker | grep -i redis

# Check Redis stream
redis-cli XLEN jobs:ingestion

# Check consumer group exists
redis-cli XINFO GROUPS jobs:ingestion
```

**Solution**:
```bash
# Restart worker to reconnect
systemctl restart ingest-worker

# Watch logs
journalctl -u ingest-worker -f
```

### Issue 4: Redis Not Running

**Symptoms**:
- Worker fails to start
- Logs show "Connection refused" to Redis

**Solution**:
```bash
ssh root@10.96.200.206

# Check Redis status
systemctl status redis-server

# Start if stopped
systemctl start redis-server
systemctl enable redis-server

# Restart worker
systemctl restart ingest-worker
```

### Issue 5: Jobs Stuck in Pending State

**Symptoms**:
- Files are queued but worker doesn't process them
- `XPENDING` shows old messages

**Solution**:
```bash
ssh root@10.96.200.206

# Check pending messages
redis-cli XPENDING jobs:ingestion workers

# If messages are very old (stuck), claim them
redis-cli XCLAIM jobs:ingestion workers consumer1 0 [message-id]

# Or restart worker to pick them up
systemctl restart ingest-worker
```

## Testing After Fix

### 1. Upload a Test File

Use the ai-portal or curl:

```bash
curl -X POST http://10.96.200.206:8000/upload \
  -H "X-User-Id: test-user-id" \
  -F "file=@test.pdf"
```

Should return:
```json
{
  "fileId": "...",
  "status": "queued",
  "message": "File uploaded and queued for processing"
}
```

### 2. Watch Worker Process It

```bash
ssh root@10.96.200.206 'journalctl -u ingest-worker -f'
```

Should see:
```
Processing job: <file-id>
Stage: extraction
Stage: chunking  
Stage: embedding
Stage: completed
```

### 3. Check Status

```bash
curl http://10.96.200.206:8000/files/<file-id> \
  -H "X-User-Id: test-user-id"
```

Status should change from `queued` → `extraction` → `chunking` → `embedding` → `completed`

## Prevention

### Ensure Worker Starts on Boot

```bash
ssh root@10.96.200.206

# Enable worker service
systemctl enable ingest-worker

# Verify it's enabled
systemctl is-enabled ingest-worker
# Should output: enabled
```

### Monitor Worker Health

Add to your monitoring system:

```bash
# Check script (can be run from cron or monitoring tool)
#!/bin/bash
STATUS=$(ssh root@10.96.200.206 'systemctl is-active ingest-worker')
if [ "$STATUS" != "active" ]; then
    echo "ALERT: Ingest worker is not running!"
    # Send alert...
fi
```

### Regular Health Checks

```bash
# Add to cron or monitoring
*/5 * * * * ssh root@10.96.200.206 'systemctl is-active ingest-worker || systemctl start ingest-worker'
```

## Architecture

### How It Works

```
1. User uploads file → AI Portal
2. AI Portal → POST /upload → Ingest API (port 8000)
3. Ingest API:
   - Stores file in MinIO
   - Creates DB record
   - Adds job to Redis Streams (jobs:ingestion)
   - Returns fileId with status="queued"

4. Ingest Worker (separate process):
   - Reads from Redis Streams
   - Downloads file from MinIO
   - Extracts text (Marker/TATR)
   - Chunks text
   - Generates embeddings
   - Stores in Milvus + PostgreSQL
   - Updates status to "completed"
```

### Services

| Service | Container | Port | Purpose |
|---------|-----------|------|---------|
| **ingest-api** | ingest-lxc (206) | 8000 | HTTP API, accepts uploads |
| **ingest-worker** | ingest-lxc (206) | - | Background job processor |
| **redis-server** | ingest-lxc (206) | 6379 | Job queue (Redis Streams) |

Both services run on the same container but are separate systemd services.

## Quick Commands Reference

```bash
# Check status
ssh root@10.96.200.206 'systemctl status ingest-worker'

# Start worker
ssh root@10.96.200.206 'systemctl start ingest-worker'

# Restart worker
ssh root@10.96.200.206 'systemctl restart ingest-worker'

# Enable on boot
ssh root@10.96.200.206 'systemctl enable ingest-worker'

# View logs (live)
ssh root@10.96.200.206 'journalctl -u ingest-worker -f'

# View recent logs
ssh root@10.96.200.206 'journalctl -u ingest-worker -n 100 --no-pager'

# Check Redis queue
ssh root@10.96.200.206 'redis-cli XLEN jobs:ingestion'

# Redeploy service (fixes most issues)
cd /root/busibox/provision/ansible && make ingest
```

## Related Documentation

- **Ingest Service Architecture**: `docs/architecture/ingestion-pipeline.md`
- **Deployment Guide**: `docs/deployment/ingest-service.md`
- **Worker Implementation**: `srv/ingest/src/worker.py`
- **Redis Service**: `srv/ingest/src/api/services/redis.py`

