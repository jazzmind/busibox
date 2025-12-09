# Applications Layer

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/03-authentication.md`  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`

## Service Placement
- **Container**: `apps-lxc` (CT 201)
- **Role**: Hosts user-facing Next.js apps (e.g., AI Portal, Agent Client) behind `proxy-lxc`.
- **Ports**: Next.js internal `3000`; exposed via proxy `80/443`.

## Responsibilities
- Provide UI for uploads, search, admin/deployment views.
- Proxy internal calls to:
  - Ingest API (`/upload`, `/status`, `/files`, `/search`).
  - Search API (`/search`) for retrieval when not using ingest’s endpoint.
  - AuthZ service to mint service JWTs for downstream calls.
- Maintain user sessions and attach JWTs/role claims to backend requests.

## Integration Boundaries
- Apps do **not** expose ingest or search publicly; all backend calls stay on the internal network.
- SSE for ingestion status is proxied: browser connects to app endpoint, which forwards to ingest `/status/{fileId}`.
- Role data originates from the app’s identity provider and is forwarded via JWTs to backend services.

## Deployment Notes
- Provisioning and deploy automation live under `provision/ansible` (see `make deploy-apps`, `make deploy-ai-portal` in CLAUDE.md).
- Environment variables for app endpoints should match container IPs in `provision/pct/vars.env` (e.g., `NEXT_PUBLIC_INGEST_API_URL=http://10.96.200.206:8000`).
- Keep proxy rules aligned so only apps are internet-facing; backend containers remain internal.
