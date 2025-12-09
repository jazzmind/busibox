# Usage Guide (APIs & Flows)

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `guides/04-troubleshooting.md`

## Upload a Document (Ingest API)
1. Obtain JWT with roles (audience `ingest-api`).
2. Upload (apps should proxy; direct example shown for internal network):
   ```bash
   curl -X POST http://10.96.200.206:8000/upload \
     -H "Authorization: Bearer <JWT>" \
     -F "file=@sample.pdf" \
     -F 'visibility=personal'
   ```
3. Response includes `fileId` and initial `status`.

## Track Status
- SSE stream (internal, proxied by apps):
  ```bash
  curl http://10.96.200.206:8000/status/<fileId> \
    -H "Authorization: Bearer <JWT>"
  ```
- Expect stages: `queued → parsing → chunking → embedding → indexing → completed`.

## Search Documents (Search API)
```bash
curl -X POST http://10.96.200.204:8003/search \
  -H "Authorization: Bearer <JWT-with-read-roles>" \
  -H "Content-Type: application/json" \
  -d '{ "query": "safety policy", "mode": "hybrid", "limit": 10 }'
```
- Results limited to partitions derived from your roles and personal docs.

## Retrieve Markdown/Text
- From ingest: `GET /files/{fileId}/markdown` (Authorization required).

## Apps Usage Notes
- Apps (AI Portal, Agent Client) proxy all backend calls; they should:
  - Mint AuthZ tokens, then call ingest/search with `Authorization: Bearer`.
  - Proxy SSE status to the browser.
  - Map container IPs from `provision/pct/vars.env` into app env vars.

## Operational Safety
- Keep ingest/search internal; do not expose their ports publicly.
- Use JWTs rather than legacy `X-User-Id` headers.
- Validate MIME types before upload; unsupported types are rejected.
