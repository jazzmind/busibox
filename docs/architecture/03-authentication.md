# Authentication & Authorization

**Created**: 2025-12-09  
**Last Updated**: 2025-12-09  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `architecture/07-apps.md`

## AuthZ Service (CT 210)
- **Code**: `srv/authz`
- **Port**: `8010`
- **Token issuance**: `POST /authz/token` issues HS256 JWTs with `sub`, `roles`, `aud`, `iss`.
- **Audit**: `POST /authz/audit` writes audit rows to PostgreSQL.
- **Config**: `JWT_SECRET`, `JWT_ISSUER`, `JWT_AUDIENCE`, `AUTHZ_TOKEN_TTL` via `Config`.
- **Audience**: Downstream services (ingest, search, agent) validate `aud`/`iss`.

## Service-side Validation
- **Ingestion API** (`srv/ingest/src/api/middleware/jwt_auth.py`)
  - Validates `Authorization: Bearer <JWT>` (HS256, default issuer `ai-portal`, audience `ingest-api`).
  - Legacy `X-User-Id` is allowed when `ALLOW_LEGACY_AUTH=true`.
  - Extracts document roles with CRUD permissions and sets PostgreSQL session variables for RLS (`app.user_id`, `app.user_role_ids_*`).
- **Search API** (`srv/search/src/api/middleware/jwt_auth.py`)
  - Validates HS256 JWT (default audience `search-api`).
  - Builds Milvus partition list: `personal_{user_id}` + `role_{role_id}` for readable roles.
  - Accepts legacy `x-user-id` during migration.

## Identity & Roles
- JWT payload format expected by ingest/search:
  ```json
  {
    "sub": "<user-uuid>",
    "email": "user@example.com",
    "roles": [
      { "id": "<role-uuid>", "name": "Editors", "permissions": ["read","create","update","delete"] }
    ],
    "aud": "<service-audience>",
    "iss": "ai-portal",
    "typ": "access"
  }
  ```
- CRUD permissions drive:
  - Upload visibility checks (`create` on shared roles).
  - RLS session variables for PostgreSQL policies.
  - Search partition scoping (readable roles).

## RLS Enforcement (PostgreSQL)
- Ingest sets session variables per request; downstream queries filter by user/role.
- Search relies on Milvus partition naming aligned to the same role IDs; metadata remains in PostgreSQL for audit/status.
- AuthZ audit writes include caller context when supplied.

## Trust Boundaries
- Public traffic terminates at `proxy-lxc` → `apps-lxc`.
- Ingest/Search/AuthZ are **internal-only**; apps proxy requests with user JWTs.
- Legacy `X-User-Id` should be disabled once all clients supply JWTs; keep `ALLOW_LEGACY_AUTH=false` for production.
