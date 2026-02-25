# Zero Trust Authentication Rules

## Overview

Busibox uses a Zero Trust authentication model where **user identity is cryptographically proven by JWT tokens**, not by service-to-service credentials. All services trust tokens signed by the authz service.

## Core Principles

### 1. NO Client Credentials for User Operations

**NEVER** use client_id/client_secret for operations on behalf of users:

```python
# ❌ WRONG - Don't use client credentials
token_client = TokenExchangeClient(
    client_id="agent-api",
    client_secret="secret123",
)
token = await token_client.get_token_for_service(
    requested_subject=user_id,  # Impersonation!
    target_audience="data-api"
)

# ✅ CORRECT - Use Zero Trust token exchange
token = await exchange_token_zero_trust(
    subject_token=user_token,  # User's actual token
    target_audience="data-api",
    user_id=user_id  # For logging only
)
```

### 2. Token Exchange Flow

Any valid authz-signed token can be exchanged for another audience:

```
User Session JWT (aud=ai-portal)
    ↓ token exchange
Agent API Token (aud=agent-api)
    ↓ token exchange (THIS IS NOW ALLOWED)
Ingest API Token (aud=ingest-api)
```

The security comes from:
1. **Signature verification** - Token must be signed by authz
2. **Expiration check** - Token must not be expired
3. **RBAC from authz DB** - User's scopes/roles come from database, not the incoming token
4. **Session revocation** - Session/delegation tokens are checked for revocation

### 3. Scopes Come from RBAC, Not Tokens

When exchanging tokens, the scopes in the issued token come from the **user's roles in the authz database**, not from the incoming token's scopes:

```python
# In authz token exchange:
roles = await db.get_user_roles(user_id)
all_scopes = set()
for r in roles:
    all_scopes.update(r.get("scopes") or [])
# New token gets aggregated scopes from all user's roles
```

### 4. Pass Tokens Through the Call Chain

Services should store and pass along user tokens for downstream calls:

```python
# In FastAPI dependency
async def get_principal(authorization: str = Header(...)) -> Principal:
    token = authorization.split(" ", 1)[1]
    principal = await validate_bearer(token)
    principal.token = token  # Store for downstream use
    return principal

# In service code
async def call_downstream_service(principal: Principal):
    downstream_token = await exchange_token_zero_trust(
        subject_token=principal.token,
        target_audience="downstream-api",
        user_id=principal.sub
    )
```

## Implementation Patterns

### Python Services (busibox_common)

```python
from app.auth.token_exchange import exchange_token_zero_trust

# Exchange user's token for downstream service
ingest_token = await exchange_token_zero_trust(
    subject_token=principal.token,
    target_audience="ingest-api",
    user_id=principal.sub
)

if ingest_token:
    headers = {"Authorization": f"Bearer {ingest_token}"}
    # Make downstream call
```

### TypeScript Services (busibox-app)

```typescript
import { exchangeTokenZeroTrust } from '@jazzmind/busibox-app/lib/authz';

const result = await exchangeTokenZeroTrust({
  sessionJwt: userToken,
  audience: 'agent-api',
  scopes: ['agents:read'],
});
```

## Token Types

| Type | Audience | Purpose | Revocable |
|------|----------|---------|-----------|
| Session | ai-portal | User browser session | Yes (via session_id) |
| Delegation | ai-portal | Background tasks | Yes (via delegation_id) |
| Access | service-specific | API calls | No (short-lived) |

All types can be used as `subject_token` for token exchange.

## Anti-Patterns to Avoid

### ❌ Service Account Impersonation

```python
# DON'T DO THIS
client = ServiceClient(client_id="agent-api", secret="...")
token = client.get_token_as(user_id)  # Impersonation
```

### ❌ Hardcoded Audience Restrictions

```python
# DON'T DO THIS
if token.audience != "ai-portal":
    raise Error("wrong audience")  # Over-restrictive
```

### ❌ Passing Scopes from Incoming Token

```python
# DON'T DO THIS
new_token = exchange(subject_token, scopes=subject_token.scopes)
# Scopes should come from RBAC, not the incoming token
```

## When to Use Delegation Tokens

For background tasks that run without user presence (e.g., scheduled jobs, webhooks):

1. User explicitly creates a delegation token while authenticated
2. Delegation token has limited scopes and expiration
3. Background job uses delegation token as subject_token for exchanges

```python
# Create delegation token (user must be present)
delegation = await create_delegation_token(
    session_jwt=user_session,
    name="nightly-sync",
    scopes=["ingest:read"],
    expires_in_seconds=86400
)

# Later, in background job
token = await exchange_token_zero_trust(
    subject_token=delegation.token,
    target_audience="ingest-api",
    user_id=delegation.user_id
)
```

## Related Files

- `srv/authz/src/routes/oauth.py` - Token exchange implementation
- `srv/agent/app/auth/token_exchange.py` - Agent service token exchange helper
- `srv/shared/busibox_common/auth.py` - Shared auth utilities
- `busibox-app/src/lib/authz/zero-trust.ts` - TypeScript Zero Trust client
