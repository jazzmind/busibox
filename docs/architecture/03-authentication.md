# Authentication & Authorization

**Created**: 2025-12-09  
**Last Updated**: 2025-12-20  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/01-containers.md`  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `architecture/07-apps.md`

---

## Design Goals

1. **Multi-provider authentication**: Authz service supports authenticating users via EntraID, SAML, other SSO, as well as via email + passkey/TOTP.
2. **Role-based access with OAuth2 scopes**: Roles are assigned to users and contain OAuth2 scopes which control access to services and data. Users can have multiple roles; their effective scopes are the union of all role scopes.
3. **OAuth2 session tokens**: When a user authenticates, they receive an OAuth2 session token that can be passed to any service they can access. Generally done via ai-portal, which is the primary authentication entry point.
4. **Token exchange for service tokens**: ai-portal and downstream services exchange user tokens for API-specific tokens using OAuth2 token exchange (RFC 8693). Session tokens can have long durations while service tokens have shorter TTLs to ensure access/role changes are picked up quickly.
5. **Asymmetric signing (RS256)**: All tokens use asymmetric key signing. Keys are stored in PostgreSQL and published via JWKS.
6. **Row-level security**: Files, embeddings, chunks, summaries, insights and other generated data use PostgreSQL RLS based on roles. Milvus partitions align with role IDs. MinIO object paths are organized by visibility (personal/role) with access validated at the application layer.

---

## Authorization Model

### Two-Dimensional Access Control

Access control has two orthogonal dimensions:

| Dimension | Controlled By | Question Answered |
|-----------|---------------|-------------------|
| **Data Access** | Role membership | "Which data can I see?" |
| **Operations** | OAuth2 scopes | "What can I do with it?" |

### Example: Finance Department

```
┌─────────────────────────────────────────────────────────────┐
│                    FINANCE DOCUMENTS                         │
│              (tagged with Finance roles)                     │
└─────────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌──────────────────────┐     ┌──────────────────────┐
│   Finance Admin      │     │    Finance Team       │
│                      │     │                       │
│ Roles: [finance]     │     │ Roles: [finance]      │
│                      │     │                       │
│ Scopes:              │     │ Scopes:               │
│  • ingest.read       │     │  • ingest.read        │
│  • ingest.write      │     │  • search.read        │
│  • ingest.delete     │     │  • agent.read         │
│  • search.read       │     │                       │
│  • search.write      │     │                       │
│  • search.delete     │     │                       │
│  • agent.read        │     │                       │
│  • agent.write       │     │                       │
│  • agent.delete      │     │                       │
│                      │     │                       │
│ Can: View, edit,     │     │ Can: View finance     │
│      delete finance  │     │      documents only   │
│      documents       │     │                       │
└──────────────────────┘     └──────────────────────┘
```

Both groups have `finance` role membership → they can ACCESS finance-tagged documents.
Only Finance Admin has write/delete scopes → they can MODIFY those documents.

### Standard OAuth2 Scopes

| Scope | Service | Permission |
|-------|---------|------------|
| `ingest.read` | Ingest API | View files, status, metadata |
| `ingest.write` | Ingest API | Upload files, update metadata |
| `ingest.delete` | Ingest API | Delete files and associated data |
| `search.read` | Search API | Execute searches, view results |
| `search.write` | Search API | Modify search configurations |
| `search.delete` | Search API | Delete search artifacts |
| `agent.read` | Agent API | Query agents, view responses |
| `agent.write` | Agent API | Create/modify agents |
| `agent.delete` | Agent API | Delete agents |
| `agent.execute` | Agent API | Execute agent tasks |

---

## AuthZ Service

| Property | Value |
|----------|-------|
| **Containers** | CT 210 (Production), CT 310 (Test) |
| **Code** | `srv/authz` |
| **Port** | 8010 |
| **Issuer** | `busibox-authz` (configurable via `AUTHZ_ISSUER`) |

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/.well-known/jwks.json` | GET | Public JWKS for token validation |
| `/oauth/token` | POST | Issue access tokens (client_credentials, token-exchange) |
| `/authz/audit` | POST | Append audit log entries |
| `/internal/sync/user` | POST | Sync user + roles from ai-portal |
| `/admin/roles` | CRUD | Role management (includes scopes) |
| `/admin/user-roles` | POST/DELETE | User-role bindings |
| `/admin/oauth-clients` | CRUD | OAuth client management |
| `/health/live` | GET | Liveness probe |
| `/health/ready` | GET | Readiness probe |

### Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `AUTHZ_ISSUER` | `busibox-authz` | Token issuer (iss claim) |
| `AUTHZ_ACCESS_TOKEN_TTL` | `900` | Token TTL in seconds (15 min) |
| `AUTHZ_SIGNING_ALG` | `RS256` | Signing algorithm |
| `AUTHZ_RSA_KEY_SIZE` | `2048` | RSA key size |
| `AUTHZ_KEY_ENCRYPTION_PASSPHRASE` | - | Encrypt private keys at rest |
| `AUTHZ_BOOTSTRAP_CLIENT_ID` | - | Bootstrap OAuth client on startup |
| `AUTHZ_BOOTSTRAP_CLIENT_SECRET` | - | Bootstrap client secret |
| `AUTHZ_BOOTSTRAP_ALLOWED_AUDIENCES` | `ingest-api,search-api,agent-api` | Allowed audiences for bootstrap client |
| `AUTHZ_ADMIN_TOKEN` | - | Admin token for management endpoints |
| `POSTGRES_HOST` | - | PostgreSQL host |
| `POSTGRES_DB` | `busibox` | Database name |
| `POSTGRES_USER` | `busibox_user` | Database user |
| `POSTGRES_PASSWORD` | - | Database password |

---

## OAuth2 Token Flows

### Client Credentials Grant
For service-to-service authentication (no user context):

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&
client_id=ai-portal&
client_secret=<secret>&
audience=ingest-api
```

Response:
```json
{
  "access_token": "<jwt>",
  "token_type": "Bearer",
  "expires_in": 900,
  "scope": ""
}
```

### Token Exchange Grant (On-Behalf-Of)
For exchanging user session for service-specific token:

```http
POST /oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:token-exchange&
client_id=ai-portal&
client_secret=<secret>&
audience=ingest-api&
requested_subject=<user-uuid>&
requested_purpose=document-upload
```

Response includes user's role memberships and aggregated scopes.

---

## Access Token Structure

Tokens are signed JWTs (RS256) with the following claims:

```json
{
  "iss": "busibox-authz",
  "sub": "<user-uuid>",
  "aud": "ingest-api",
  "exp": 1703123456,
  "iat": 1703122556,
  "nbf": 1703122556,
  "jti": "<unique-token-id>",
  "typ": "access",
  "scope": "ingest.read ingest.write search.read agent.read",
  "roles": [
    {
      "id": "<role-uuid>",
      "name": "Finance Admin"
    }
  ]
}
```

### Claims Reference

| Claim | Type | Description |
|-------|------|-------------|
| `iss` | string | Token issuer (busibox-authz) |
| `sub` | string | User UUID or client_id (for client_credentials) |
| `aud` | string | Target service (ingest-api, search-api, agent-api) |
| `exp` | int | Expiration timestamp |
| `iat` | int | Issued-at timestamp |
| `nbf` | int | Not-before timestamp |
| `jti` | string | Unique token ID |
| `typ` | string | Token type (access) |
| `scope` | string | Space-delimited OAuth2 scopes (aggregated from all roles) |
| `roles` | array | User's role memberships (for data access filtering) |

**Important**: Scopes are aggregated from all user roles. Roles in the token contain only `id` and `name` (used for RLS/partition filtering), not scopes.

---

## Service-Side Authorization

### Current Implementation Status

| Feature | AuthZ | Ingest | Search | Agent |
|---------|-------|--------|--------|-------|
| JWT validation (RS256) | N/A | ✅ | ✅ | ✅ |
| Audience validation | N/A | ✅ | ✅ | ✅ |
| Role extraction | ✅ | ✅ | ✅ | ✅ |
| Scope extraction | ✅ | ✅ | ✅ | ✅ |
| RLS enforcement (data access) | N/A | ✅ | ✅ | N/A |
| **Scope enforcement (operations)** | N/A | ✅ | ❌ | ❌ |

> **Note**: Scope-based operation authorization is designed but not yet enforced. Currently:
> - **Data access** is enforced via role membership (PostgreSQL RLS, Milvus partitions)
> - **Operation authorization** (scope checks) is not enforced - any authenticated user can perform any operation on data they can access
>
> Helper functions (`require_scope()`, `has_scope()`) exist in `srv/ingest` and `srv/search` but are not called by route handlers.

### Target Authorization Model

When fully implemented, services should perform two checks:

1. **Scope check**: Does the token have the required scope for this operation?
2. **Role check**: Does the user have access to the requested data?

```python
# Example: Deleting a document (target implementation)
async def delete_document(request: Request, doc_id: str):
    # 1. Scope check - can user delete anything?
    require_scope(request, "ingest.delete")  # NOT YET ENFORCED
    
    # 2. Role check - can user access this document?
    # (Handled automatically by RLS - document query returns nothing if no access)
    doc = await get_document(doc_id)  # RLS filters to accessible docs
    if not doc:
        raise HTTPException(404, "Document not found")
    
    # Proceed with deletion
    await delete_document_impl(doc_id)
```

### Token Validation

Downstream services validate tokens by:
1. Fetching JWKS from `http://authz:8010/.well-known/jwks.json`
2. Verifying RS256 signature using the key matching the `kid` header
3. Validating `iss`, `aud`, `exp` claims
4. Extracting `scope` (available but not enforced)
5. Extracting `roles` for data access filtering

### Ingestion API (`srv/ingest`)
- Validates JWT (RS256, audience `ingest-api`)
- **Scope enforcement**: Routes require appropriate scopes (`ingest.read`, `ingest.write`, `ingest.delete`)
- Uses `roles[].id` for PostgreSQL RLS session variables
- Documents are tagged with role IDs at upload time
- MinIO storage paths organized by visibility: `personal/{user_id}/...` or `role/{role_id}/...`

### Search API (`srv/search`)
- Validates JWT (RS256, audience `search-api`)
- Uses `roles[].id` for Milvus partition filtering: `personal_{user_id}` + `role_{role_id}`
- Scope utilities defined: `require_scope()`, `has_scope()` (not yet enforced)

### Agent API (`srv/agent`)
- Validates JWT (RS256, audience `agent-api`)
- Scopes stored in token grants for downstream service calls
- Token exchange with authz for service-specific tokens

---

## RLS Enforcement

### PostgreSQL
Session variables set per request enable row-level security:
- `app.user_id` - Current user UUID
- `app.user_role_ids` - JSON array of role IDs user has membership in

RLS policies filter data based on role membership, not scopes. Scopes control operations; role membership controls data visibility.

### Milvus
Partition naming aligns with role IDs:
- `personal_{user_id}` - User's personal partition
- `role_{role_id}` - Shared role partitions

### MinIO
Object storage paths organized by visibility for logical isolation:
- `personal/{user_id}/{file_id}/` - Personal documents
- `role/{role_id}/{file_id}/` - Shared documents (stored under primary role)

Access control is enforced at the application layer (Ingest API validates user access via role membership before serving files). MinIO itself uses a service account with broad access.

### Envelope Encryption (At-Rest Protection)

Files are encrypted at rest using envelope encryption:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      ENVELOPE ENCRYPTION HIERARCHY                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Master Key (from AUTHZ_MASTER_KEY env var)                         │
│       │                                                              │
│       ├── encrypts → Role KEK (role-finance)                        │
│       │                   │                                          │
│       │                   └── wraps → DEK for file-123              │
│       │                                     │                        │
│       │                                     └── encrypts → content   │
│       │                                                              │
│       ├── encrypts → Role KEK (role-legal)                          │
│       │                   │                                          │
│       │                   └── wraps → DEK for file-123 (same DEK)   │
│       │                                                              │
│       └── encrypts → User KEK (user-abc)                            │
│                           │                                          │
│                           └── wraps → DEK for file-456              │
│                                             │                        │
│                                             └── encrypts → content   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

**Key Hierarchy:**
1. **Master Key**: Derived from `AUTHZ_MASTER_KEY` environment variable using PBKDF2
2. **KEKs (Key Encryption Keys)**: One per role or user, encrypted with master key, stored in PostgreSQL
3. **DEKs (Data Encryption Keys)**: One per file, wrapped (encrypted) with authorized KEKs

**Benefits:**
- Even with MinIO admin access, files are unreadable without the master key
- Revoking role access = deleting that role's wrapped DEK (file remains encrypted, role can't decrypt)
- Key rotation at role level doesn't require re-encrypting all files

**AuthZ Keystore Endpoints:**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/keystore/kek` | POST | Create KEK for role/user |
| `/keystore/kek/ensure-for-role/{role_id}` | POST | Ensure KEK exists (idempotent) |
| `/keystore/kek/rotate` | POST | Rotate a KEK |
| `/keystore/encrypt` | POST | Encrypt content with envelope encryption |
| `/keystore/decrypt` | POST | Decrypt content |
| `/keystore/file/{file_id}/add-role/{role_id}` | POST | Grant role access to file |
| `/keystore/file/{file_id}/remove-role/{role_id}` | DELETE | Revoke role access |

---

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                      PUBLIC INTERNET                         │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTPS
┌─────────────────────▼───────────────────────────────────────┐
│                   proxy-lxc (nginx)                          │
│              TLS termination, rate limiting                  │
└─────────────────────┬───────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────┐
│                   apps-lxc (ai-portal)                       │
│          User authentication, session management            │
│          Token exchange with authz for service calls        │
└─────────────────────┬───────────────────────────────────────┘
                      │ Internal Network (JWT)
        ┌─────────────┼─────────────────┐
        ▼             ▼                 ▼
┌───────────┐  ┌─────────────┐  ┌─────────────┐
│ authz-lxc │  │ ingest-lxc  │  │ search-lxc  │
│   :8010   │  │    :8020    │  │    :8030    │
│           │  │             │  │             │
│ JWKS+Token│  │    Role     │  │    Role     │
│  issuance │  │ enforcement │  │ enforcement │
└───────────┘  └─────────────┘  └─────────────┘
```

- **Public traffic** terminates at proxy-lxc → apps-lxc
- **Ingest/Search/AuthZ** are internal-only; apps proxy requests with user JWTs
- **Service-to-service** calls use client_credentials tokens

---

## Audit Logging

All token issuance and sensitive operations are logged:

```json
{
  "actor_id": "<user-uuid>",
  "action": "oauth.token.issued",
  "resource_type": "oauth_token",
  "details": {
    "grant_type": "token-exchange",
    "client_id": "ai-portal",
    "audience": "ingest-api",
    "scope": "ingest.read ingest.write search.read"
  }
}
```

---

## Database Schema

```sql
-- OAuth clients (service accounts)
CREATE TABLE authz_oauth_clients (
  client_id TEXT PRIMARY KEY,
  client_secret_hash TEXT NOT NULL,
  allowed_audiences TEXT[] NOT NULL DEFAULT '{}',
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Signing keys (JWKS)
CREATE TABLE authz_signing_keys (
  kid TEXT PRIMARY KEY,
  alg TEXT NOT NULL,
  private_key_pem BYTEA NOT NULL,
  public_jwk JSONB NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Roles with OAuth2 scopes
CREATE TABLE authz_roles (
  id UUID PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  scopes TEXT[] NOT NULL DEFAULT '{}',  -- OAuth2 scopes for this role
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Users (synced from ai-portal)
CREATE TABLE authz_users (
  user_id UUID PRIMARY KEY,
  email TEXT NOT NULL,
  status TEXT,
  idp_provider TEXT,
  idp_tenant_id TEXT,
  idp_object_id TEXT,
  idp_roles JSONB NOT NULL DEFAULT '[]',
  idp_groups JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User-role bindings
CREATE TABLE authz_user_roles (
  user_id UUID NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
  role_id UUID NOT NULL REFERENCES authz_roles(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, role_id)
);

-- Audit log
CREATE TABLE audit_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  actor_id UUID NOT NULL,
  action TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id UUID,
  details JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Key Encryption Keys (KEKs) for envelope encryption
CREATE TABLE authz_key_encryption_keys (
  kek_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_type TEXT NOT NULL CHECK (owner_type IN ('role', 'user', 'system')),
  owner_id UUID NULL,  -- NULL for system-level keys
  encrypted_key BYTEA NOT NULL,  -- KEK encrypted with master key
  key_algorithm TEXT NOT NULL DEFAULT 'AES-256-GCM',
  key_version INTEGER NOT NULL DEFAULT 1,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  rotated_at TIMESTAMPTZ NULL,
  UNIQUE (owner_type, owner_id, key_version)
);

-- Wrapped Data Encryption Keys (DEKs)
CREATE TABLE authz_wrapped_data_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  file_id UUID NOT NULL,  -- Reference to the encrypted file
  kek_id UUID NOT NULL REFERENCES authz_key_encryption_keys(kek_id) ON DELETE CASCADE,
  wrapped_dek BYTEA NOT NULL,  -- DEK encrypted with the KEK
  dek_algorithm TEXT NOT NULL DEFAULT 'AES-256-GCM',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (file_id, kek_id)
);
```

---

## Migration from Permissions to Scopes

### Breaking Changes

The `permissions` field in role claims is replaced by aggregated `scope` in the token:

**Before (deprecated):**
```json
{
  "roles": [
    { "id": "...", "name": "Finance Admin", "permissions": ["read", "create", "update", "delete"] }
  ]
}
```

**After:**
```json
{
  "scope": "ingest.read ingest.write ingest.delete search.read search.write search.delete",
  "roles": [
    { "id": "...", "name": "Finance Admin" }
  ]
}
```

### Migration Steps

1. Add `scopes` column to `authz_roles` table
2. Update token generation to aggregate scopes from user roles
3. Update downstream services to check `scope` claim instead of `roles[].permissions`
4. Remove `permissions` from role claims in tokens
