# Deploy Chunking Fixes

## What Was Fixed

### Issue 1: Chunks Exceeding Milvus Limit (CRITICAL)
**Error**: `MilvusException: length of varchar field text exceeds max length, row number: 0, length: 206440, max length: 65535`

**Root Cause**: Chunker was creating 200KB+ chunks instead of ~3KB chunks. This happened when documents had very long paragraphs or poor paragraph detection.

**Fix**: Added hard limit enforcement at multiple levels:
- Check before creating each chunk (60KB safety margin)
- Truncate with warning if limit exceeded
- Applied to all chunking paths (semantic, simple, final chunks)

### Issue 2: No Semantic Extraction
**Problem**: Documents were being chunked as plain text without preserving structure (headings, lists, sections).

**Fix**: Markdown conversion is already implemented and working. The issue was that very long chunks were failing before we could see the markdown formatting.

## Files Changed

1. **srv/ingest/src/processors/chunker.py**
   - Added Milvus limit checks in `_chunk_simple()` loop
   - Added Milvus limit checks in `_chunk_simple()` final chunk
   - Added Milvus limit checks in `_chunk_semantic()` loop
   - Added Milvus limit checks in `_chunk_semantic()` final chunk
   - All checks include truncation with warning logging

2. **srv/ingest/tests/test_chunker.py** (NEW)
   - 15+ test classes
   - 40+ test cases
   - Validates all chunking scenarios
   - Tests Milvus limit enforcement
   - Tests semantic structure preservation

3. **CHUNKING_IMPLEMENTATION.md** (NEW)
   - Complete documentation
   - Usage examples
   - Configuration guide
   - Performance benchmarks

## Deployment Steps

### Step 1: Verify Current State

```bash
# SSH to Proxmox host
ssh root@proxmox-host

# Check ingest worker status
pct enter 206
systemctl status ingest-worker
journalctl -u ingest-worker -n 50 --no-pager
exit
```

### Step 2: Deploy Updated Code

```bash
# From your admin workstation
cd /path/to/busibox/provision/ansible

# Deploy ingest worker with updated chunker
ansible-playbook -i inventory/production/hosts.yml site.yml --tags ingest_worker

# This will:
# - Copy updated chunker.py to /srv/ingest/src/processors/
# - Restart ingest-worker service
# - Verify service is running
```

### Step 3: Run Tests (Optional but Recommended)

```bash
# SSH to ingest container
ssh root@10.96.200.30  # ingest-lxc IP

# Run chunker tests using the convenience script
ingest-test chunker

# Or run all tests
ingest-test

# Or run with coverage
ingest-test coverage

# Expected output: All tests passing
# If any tests fail, review the output and fix before proceeding
```

The deployment now includes:
- Test files copied to `/srv/ingest/tests/`
- Pytest configuration in `/srv/ingest/pytest.ini`
- Convenience script `/usr/local/bin/ingest-test` for running tests

### Step 4: Test with Real Document

```bash
# Upload a test document via AI Portal
# Monitor the logs in real-time

# On Proxmox host:
pct enter 206
journalctl -u ingest-worker -f

# Look for:
# - "Text chunked" log with chunk_count
# - No "Chunk exceeds Milvus limit" warnings (or very few)
# - No MilvusException errors
# - Successful completion
```

### Step 5: Verify in Database

```bash
# SSH to PostgreSQL container
ssh root@10.96.200.27  # pg-lxc IP

# Connect to database
sudo -u postgres psql busibox

# Check document status
SELECT 
    file_id, 
    original_filename, 
    ingestion_status, 
    chunk_count,
    error_message
FROM files
ORDER BY created_at DESC
LIMIT 10;

# Verify chunks were created
SELECT 
    f.original_filename,
    COUNT(c.id) as chunk_count,
    AVG(LENGTH(c.text)) as avg_chunk_length,
    MAX(LENGTH(c.text)) as max_chunk_length
FROM files f
LEFT JOIN chunks c ON c.file_id = f.file_id
WHERE f.ingestion_status = 'completed'
GROUP BY f.file_id, f.original_filename
ORDER BY f.created_at DESC
LIMIT 10;

# Expected:
# - chunk_count > 0
# - avg_chunk_length: 2000-4000 chars
# - max_chunk_length: < 65535 chars
```

### Step 6: Verify in Milvus

```bash
# SSH to Milvus container
ssh root@10.96.200.28  # milvus-lxc IP

# Check vector count
curl -X POST "http://localhost:19530/v1/vector/collections/busibox_vectors/query" \
  -H "Content-Type: application/json" \
  -d '{
    "output_fields": ["file_id", "chunk_index", "modality"],
    "limit": 10
  }'

# Should return vectors without errors
```

## Rollback Plan (If Needed)

If deployment causes issues:

```bash
# SSH to ingest container
ssh root@10.96.200.30

# Revert to previous version
cd /srv/ingest
git log --oneline -5  # Find previous commit
git checkout <previous-commit-hash> src/processors/chunker.py

# Restart worker
systemctl restart ingest-worker

# Verify
systemctl status ingest-worker
```

## Expected Results

### Before Fix
```
ERROR: MilvusException: length of varchar field text exceeds max length
- Chunk length: 206,440 chars
- Ingestion: FAILED
- Documents: Cannot be ingested
```

### After Fix
```
INFO: Text chunked successfully
- Chunk count: 25-50 (typical)
- Avg chunk length: 2,000-4,000 chars
- Max chunk length: < 65,000 chars (safety margin)
- Ingestion: SUCCESS
- Documents: Fully searchable
```

## Monitoring

After deployment, monitor these metrics:

### 1. Ingestion Success Rate
```bash
# Check recent ingestions
pct enter 206
journalctl -u ingest-worker --since "1 hour ago" | grep -E "(Job processing (completed|failed)|Chunk exceeds)"
```

**Expected**: 
- High success rate (> 95%)
- Few or no "Chunk exceeds" warnings

### 2. Chunk Size Distribution
```sql
-- In PostgreSQL
SELECT 
    CASE 
        WHEN LENGTH(text) < 1000 THEN '< 1KB'
        WHEN LENGTH(text) < 5000 THEN '1-5KB'
        WHEN LENGTH(text) < 10000 THEN '5-10KB'
        WHEN LENGTH(text) < 50000 THEN '10-50KB'
        ELSE '> 50KB'
    END as size_range,
    COUNT(*) as count
FROM chunks
WHERE created_at > NOW() - INTERVAL '1 day'
GROUP BY size_range
ORDER BY size_range;
```

**Expected**:
- Most chunks in 1-5KB range
- Few chunks > 10KB
- No chunks > 65KB

### 3. Processing Time
```bash
# Check processing times
journalctl -u ingest-worker --since "1 hour ago" | grep "Job processing completed" | tail -20
```

**Expected**:
- Small docs (< 10 pages): < 10s
- Medium docs (10-50 pages): < 30s
- Large docs (> 50 pages): < 60s

## Troubleshooting

### Issue: Tests Fail

**Symptom**: `pytest` shows failing tests

**Solution**:
1. Check which tests are failing
2. Review test output for specific errors
3. Verify spaCy model is installed: `python -m spacy download en_core_web_sm`
4. Check Python dependencies: `pip install -r requirements.txt`

### Issue: Still Getting Milvus Errors

**Symptom**: `MilvusException: length of varchar field text exceeds max length`

**Solution**:
1. Verify deployment completed: `git log -1` in `/srv/ingest`
2. Check if chunker.py was updated: `grep "Milvus limit" /srv/ingest/src/processors/chunker.py`
3. Restart worker: `systemctl restart ingest-worker`
4. Check logs for truncation warnings

### Issue: No Chunks Created

**Symptom**: `chunk_count = 0` in database

**Solution**:
1. Check if document text was extracted: Look for "Text extraction complete" in logs
2. Verify chunker is being called: Look for "Text chunked" in logs
3. Check for errors in chunking: `journalctl -u ingest-worker | grep -i error`

## Success Criteria

✅ **Deployment Successful If**:
1. Ingest worker service is running
2. Test document uploads successfully
3. Chunks are created (chunk_count > 0)
4. All chunk lengths < 65,535 chars
5. No Milvus insertion errors
6. Documents are searchable in AI Portal

## Next Steps

After successful deployment:

1. **Re-enable Marker** (if desired):
   - Edit `inventory/production/group_vars/all/00-main.yml`
   - Set `marker_enabled: true` in ingest env
   - Redeploy: `ansible-playbook ... --tags ingest`

2. **Re-enable ColPali** (if desired):
   - Ensure image scaling is deployed (already in code)
   - Edit `inventory/production/group_vars/all/00-main.yml`
   - Set `colpali_enabled: true` in ingest env
   - Redeploy: `ansible-playbook ... --tags ingest`

3. **Monitor for 24 hours**:
   - Check ingestion success rate
   - Review chunk size distribution
   - Verify no Milvus errors

4. **Run Full Test Suite**:
   ```bash
   # SSH to ingest container
   ssh root@10.96.200.30
   
   # Run tests with coverage
   ingest-test coverage
   
   # View coverage report (if you have a browser on the server)
   # Or copy it to your local machine:
   # scp -r root@10.96.200.30:/srv/ingest/htmlcov ./ingest-coverage
   ```

## Contact

If issues persist after deployment:
- Check logs: `journalctl -u ingest-worker -n 200 --no-pager`
- Review this document's troubleshooting section
- Check CHUNKING_IMPLEMENTATION.md for detailed documentation

