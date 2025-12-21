---
title: JWT Authentication Architecture
created: 2025-11-24
updated: 2025-11-24
status: proposed
category: architecture
---

# JWT Authentication Architecture

## Problem

Current authentication uses a simple `X-User-Id` header that:
- ❌ Is forgeable (any service can set any user ID)
- ❌ Only contains user ID (no role, permissions, or metadata)
- ❌ Doesn't scale (need to add headers for each new field)
- ❌ No expiration or security

## Solution: JWT-Based SSO

Use JSON Web Tokens (JWT) signed by the authentication service (better-auth on AI Portal).

### JWT Structure

```json
{
  "sub": "user-uuid",           // Subject (user ID)
  "email": "user@example.com",
  "name": "John Doe",
  "role": "admin",              // Role: admin, user, guest
  "permissions": ["read", "write", "delete"],
  "iat": 1700000000,            // Issued at
  "exp": 1700003600,            // Expires (1 hour)
  "iss": "ai-portal",           // Issuer
  "aud": ["ingest-api", "agent-api"]  // Audience
}
```

### Flow

```
User → AI Portal (better-auth) → JWT → Ingest/Agent Services
  1. User logs in via AI Portal
  2. better-auth issues JWT
  3. AI Portal includes JWT in Authorization header
  4. Services verify JWT signature
  5. Services extract user info from JWT
```

### Benefits

- ✅ **Secure**: Cryptographically signed, can't be forged
- ✅ **Stateless**: No database lookup needed
- ✅ **Scalable**: Add any fields to JWT payload
- ✅ **Standard**: Industry-standard OAuth 2.0 / OpenID Connect
- ✅ **Expiration**: Tokens expire automatically
- ✅ **Auditable**: Contains issuer, audience, timestamps

## Implementation Plan

### Phase 1: AI Portal (JWT Issuer)

**File**: `ai-portal/src/lib/jwt.ts`

```typescript
import { SignJWT, jwtVerify } from 'jose';

const JWT_SECRET = new TextEncoder().encode(
  process.env.JWT_SECRET || process.env.BETTER_AUTH_SECRET
);

export async function createServiceJWT(user: {
  id: string;
  email: string;
  name: string;
  role: string;
}) {
  return await new SignJWT({
    sub: user.id,
    email: user.email,
    name: user.name,
    role: user.role,
    permissions: getRolePermissions(user.role),
  })
    .setProtectedHeader({ alg: 'HS256' })
    .setIssuedAt()
    .setIssuer('ai-portal')
    .setAudience(['ingest-api', 'agent-api'])
    .setExpirationTime('1h')
    .sign(JWT_SECRET);
}

function getRolePermissions(role: string): string[] {
  const permissions = {
    admin: ['read', 'write', 'delete', 'admin'],
    user: ['read', 'write'],
    guest: ['read'],
  };
  return permissions[role as keyof typeof permissions] || [];
}
```

**Update API Routes**: Replace `X-User-Id` with `Authorization: Bearer <jwt>`

```typescript
// Before
headers: {
  'X-User-Id': user.id,
}

// After
const jwt = await createServiceJWT(user);
headers: {
  'Authorization': `Bearer ${jwt}`,
}
```

### Phase 2: Ingest Service (JWT Verifier)

**File**: `srv/ingest/src/api/middleware/jwt_auth.py`

```python
from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import jwt
import structlog

logger = structlog.get_logger()

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to validate JWT tokens from AI Portal."""
    
    def __init__(self, app, jwt_secret: str):
        super().__init__(app)
        self.jwt_secret = jwt_secret
    
    async def dispatch(self, request: Request, call_next):
        # Skip auth for health endpoints
        if request.url.path.startswith("/health") or request.url.path == "/":
            return await call_next(request)
        
        # Extract JWT from Authorization header
        auth_header = request.headers.get("Authorization")
        
        if not auth_header or not auth_header.startswith("Bearer "):
            logger.warning("Missing or invalid Authorization header")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Missing Authorization header"}
            )
        
        token = auth_header[7:]  # Remove "Bearer " prefix
        
        try:
            # Verify and decode JWT
            payload = jwt.decode(
                token,
                self.jwt_secret,
                algorithms=["HS256"],
                audience=["ingest-api"],
                issuer="ai-portal"
            )
            
            # Attach user info to request state
            request.state.user_id = payload["sub"]
            request.state.user_email = payload.get("email")
            request.state.user_role = payload.get("role", "user")
            request.state.user_permissions = payload.get("permissions", [])
            
            logger.info(
                "Authenticated request",
                user_id=request.state.user_id,
                role=request.state.user_role,
                path=request.url.path
            )
            
        except jwt.ExpiredSignatureError:
            logger.warning("Expired JWT token")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Token expired"}
            )
        except jwt.InvalidTokenError as e:
            logger.warning("Invalid JWT token", error=str(e))
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "Invalid token"}
            )
        
        response = await call_next(request)
        return response
```

**Update main.py**:

```python
from api.middleware.jwt_auth import JWTAuthMiddleware
from shared.config import Config

config = Config()

# Replace AuthMiddleware with JWTAuthMiddleware
app.add_middleware(
    JWTAuthMiddleware,
    jwt_secret=config.jwt_secret
)
```

### Phase 3: Role-Based Access Control

**Add permission checks to routes**:

```python
def require_permission(permission: str):
    """Decorator to require specific permission."""
    def decorator(func):
        async def wrapper(request: Request, *args, **kwargs):
            permissions = getattr(request.state, "user_permissions", [])
            if permission not in permissions:
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"error": f"Missing required permission: {permission}"}
                )
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator

# Usage
@router.delete("/{fileId}")
@require_permission("delete")
async def delete_file(fileId: str, request: Request):
    # Only users with "delete" permission can access
    ...
```

### Phase 4: Agent Service

Same JWT verification middleware for agent-api.

## Migration Strategy

### Step 1: Dual Support (Backward Compatible)

Support both `X-User-Id` and `Authorization` headers during transition:

```python
# Check for JWT first, fall back to X-User-Id
auth_header = request.headers.get("Authorization")
if auth_header and auth_header.startswith("Bearer "):
    # Use JWT auth (new)
    ...
else:
    # Fall back to X-User-Id (legacy)
    user_id = request.headers.get("X-User-Id")
    if not user_id:
        return 401
    request.state.user_id = user_id
    request.state.user_role = "user"  # Default role
```

### Step 2: Update AI Portal

Deploy AI Portal with JWT generation.

### Step 3: Update Services

Deploy ingest/agent services with JWT verification.

### Step 4: Remove Legacy

After verification, remove `X-User-Id` support.

## Configuration

### Environment Variables

**AI Portal**:
```bash
JWT_SECRET=<shared-secret-key>  # Same across all services
JWT_EXPIRATION=3600             # 1 hour
```

**Ingest/Agent Services**:
```bash
JWT_SECRET=<same-shared-secret-key>
JWT_ISSUER=ai-portal
JWT_AUDIENCE=ingest-api  # or agent-api
```

### Ansible Vault

Store `JWT_SECRET` in Ansible vault:

```yaml
# provision/ansible/roles/secrets/vars/vault.yml
jwt_secret: !vault |
  $ANSIBLE_VAULT;1.1;AES256
  ...
```

## Security Considerations

1. **Secret Rotation**: Implement JWT secret rotation strategy
2. **Token Refresh**: Implement refresh tokens for long-lived sessions
3. **Revocation**: Implement token blacklist for logout/revocation
4. **HTTPS Only**: Ensure all services use HTTPS in production
5. **Short Expiration**: Keep token expiration short (1 hour)

## Testing

### Unit Tests

```python
def test_jwt_auth_valid_token():
    """Test that valid JWT grants access"""
    token = create_test_jwt(user_id="test-user", role="admin")
    response = client.get("/files/123", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

def test_jwt_auth_expired_token():
    """Test that expired JWT is rejected"""
    token = create_expired_jwt()
    response = client.get("/files/123", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401

def test_jwt_auth_invalid_signature():
    """Test that tampered JWT is rejected"""
    token = create_jwt_with_wrong_signature()
    response = client.get("/files/123", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401

def test_rbac_admin_can_delete():
    """Test that admin role can delete"""
    token = create_test_jwt(role="admin")
    response = client.delete("/files/123", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

def test_rbac_user_cannot_delete():
    """Test that user role cannot delete"""
    token = create_test_jwt(role="user")
    response = client.delete("/files/123", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 403
```

## Future Enhancements

1. **OAuth 2.0 Flows**: Support authorization code flow for third-party apps
2. **Refresh Tokens**: Long-lived refresh tokens for mobile apps
3. **Scopes**: Fine-grained scopes beyond roles
4. **Multi-Tenancy**: Add tenant/organization to JWT
5. **Audit Logging**: Log all JWT verifications for security audits

## References

- [RFC 7519 - JSON Web Token (JWT)](https://tools.ietf.org/html/rfc7519)
- [RFC 6749 - OAuth 2.0](https://tools.ietf.org/html/rfc6749)
- [OpenID Connect Core 1.0](https://openid.net/specs/openid-connect-core-1_0.html)

