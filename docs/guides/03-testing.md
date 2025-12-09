# Testing Guide

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `guides/02-deployment.md`  
- `guides/testing/master-guide.md`  
- `guides/testing/TEST_STRATEGY.md`

## Platform Checks (Ansible)
- **Smoke/health**:
  ```bash
  cd provision/ansible
  make verify          # health checks across services
  make verify-health   # service health subset
  make verify-smoke    # DB and container basics
  ```

## Ingestion Service
- Unit/integration (inside repo or container):
  ```bash
  cd srv/ingest
  pip install -r requirements.txt
  pytest tests/api     # API routes incl. upload/status/search
  pytest tests/integration -m "not slow"  # pipeline coverage
  ```
- End-to-end upload + SSE + indexing:
  - Use `tests/integration/test_full_pipeline.py` (requires MinIO, Redis, Milvus running).

## Search Service
- Run search tests:
  ```bash
  cd srv/search
  pip install -r requirements.txt
  pytest tests/integration/test_search_api.py
  ```
- Ensure Milvus is seeded with ingest-produced partitions before running.

## Apps
- App-specific tests live in their repos (AI Portal, Agent Client). Run `npm test`/`npm run test:watch` per repo after wiring env vars to container endpoints.

## What to Verify After Deployment
- Ingest `/health` and Search `/health` return 200.
- Upload via ingest returns `queued` or `completed` for images/videos; status stream reaches `completed`.
- Search returns results limited to the user’s partitions (use different JWTs to confirm access control).
- AuthZ token issuance works and audit rows are written.
