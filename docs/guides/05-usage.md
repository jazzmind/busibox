---
title: "Using Busibox Services"
category: "user"
order: 2
description: "Learn how to use Busibox APIs, upload documents, search, and interact with agents"
published: true
---

# Usage Guide (APIs & Flows)

**Created**: 2025-12-09  
**Last Updated**: 2026-01-15  
**Status**: Active  
**Category**: Guide  
**Related Docs**:  
- `architecture/04-ingestion.md`  
- `architecture/05-search.md`  
- `guides/04-troubleshooting.md`  
- `guides/using-busibox-app-service-clients.md`  
- `guides/oauth2-token-exchange-implementation.md`

## Using Busibox Services

### From Applications (Recommended)

Use the `busibox-app` library for type-safe, authenticated service calls:

**Client-side (React)**:
```typescript
import { uploadChatAttachment, agentChat, useAuthzTokenManager } from 'busibox-app';

const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });

// Upload file
const result = await uploadChatAttachment(file, { tokenManager });

// Chat with agent
const response = await agentChat('Hello', { tokenManager });
```

**Server-side (API routes)**:
```typescript
import { uploadChatAttachment } from 'busibox-app';
import { exchangeDownstreamAccessToken, syncUserToAuthz } from '@/lib/authz-client';

async function getAuthzToken(userId: string, audience: string, scopes: string[]) {
  await syncUserToAuthz(userId);
  const result = await exchangeDownstreamAccessToken({ userId, audience, scopes });
  return result.accessToken;
}

const result = await uploadChatAttachment(file, { userId, getAuthzToken });
```

See `guides/using-busibox-app-service-clients.md` for complete documentation.

### Direct API Calls

For direct service integration or debugging:

#### Upload a Document (Ingest API)
1. Obtain JWT with roles (audience `ingest-api`).
2. Upload (apps should proxy; direct example shown for internal network):
   ```bash
   curl -X POST http://10.96.200.206:8000/upload \
     -H "Authorization: Bearer <JWT>" \
     -F "file=@sample.pdf" \
     -F 'visibility=personal'
   ```
3. Response includes `fileId` and initial `status`.

#### Track Status
- SSE stream (internal, proxied by apps):
  ```bash
  curl http://10.96.200.206:8000/status/<fileId> \
    -H "Authorization: Bearer <JWT>"
  ```
- Expect stages: `queued → parsing → chunking → embedding → indexing → completed`.

#### Search Documents (Search API)
```bash
curl -X POST http://10.96.200.204:8003/search \
  -H "Authorization: Bearer <JWT-with-read-roles>" \
  -H "Content-Type: application/json" \
  -d '{ "query": "safety policy", "mode": "hybrid", "limit": 10 }'
```
- Results limited to partitions derived from your roles and personal docs.

#### Retrieve Markdown/Text
- From ingest: `GET /files/{fileId}/markdown` (Authorization required).

#### Agent Operations
```bash
# Chat with agent
curl -X POST http://10.96.200.207:4111/api/chat \
  -H "Authorization: Bearer <JWT>" \
  -H "Content-Type: application/json" \
  -d '{ "message": "Hello", "agentId": "default" }'
```

## Authentication & Authorization

### Zero Trust Architecture

Busibox uses a Zero Trust authentication model where AuthZ is the sole authentication authority:

1. **User authenticates with AuthZ** (passkey, TOTP, or magic link) → receives RS256 session JWT
2. **App exchanges session JWT** for audience-bound access tokens (no client credentials needed for user operations)
3. **Services validate tokens cryptographically** via JWKS (no database lookups required)

See `docs/architecture/03-authentication.md` for complete details.

### OAuth2 Token Exchange

Busibox uses OAuth2 token exchange (RFC 8693) for service authentication:

1. **App authenticates user** via AuthZ (session JWT stored in `busibox-session` cookie)
2. **App requests service token** from AuthZ:
   ```bash
   curl -X POST http://10.96.200.210:8010/oauth/token \
     -H "Content-Type: application/json" \
     -d '{
       "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
       "client_id": "ai-portal",
       "client_secret": "<secret>",
       "audience": "ingest-api",
       "requested_subject": "<user-id>",
       "scope": "ingest.write"
     }'
   ```
3. **AuthZ returns JWT** with user roles and permissions
4. **App calls service** with JWT in `Authorization: Bearer` header
5. **Service validates JWT** via JWKS endpoint

See `guides/oauth2-token-exchange-implementation.md` for architecture details.

### JWKS Validation

Services validate tokens using AuthZ's public keys:

```bash
# Get public keys
curl http://10.96.200.210:8010/.well-known/jwks.json
```

## Apps Usage Notes
- Apps (AI Portal, Agent Client) proxy all backend calls; they should:
  - Mint AuthZ tokens, then call ingest/search with `Authorization: Bearer`.
  - Proxy SSE status to the browser.
  - Map container IPs from `provision/pct/vars.env` into app env vars.
- Use `busibox-app` library for consistent authentication and error handling.

## Operational Safety
- Keep ingest/search/agent internal; do not expose their ports publicly.
- Use JWTs rather than legacy `X-User-Id` headers.
- Validate MIME types before upload; unsupported types are rejected.
- Short-lived tokens (15 min) reduce exposure window.
- Audience-bound tokens prevent cross-service reuse.

## Related Documentation
- **Service clients**: `guides/using-busibox-app-service-clients.md`
- **OAuth2 implementation**: `guides/oauth2-token-exchange-implementation.md`
- **Library architecture**: `guides/busibox-app-library-architecture.md`
- **API specifications**: `openapi/` directory
