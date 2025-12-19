# Insights API Security Implementation

**Status**: ✅ Complete  
**Date**: 2025-12-16  
**Related**: [insights-migration-completed.md](./insights-migration-completed.md)

## Overview

The insights API has been implemented with proper authentication and authorization following agent-api security patterns.

## Security Model

### Authentication

**All insights endpoints require Bearer token authentication:**

```python
from app.auth.dependencies import get_principal
from app.schemas.auth import Principal

@router.post("/insights")
async def insert_insights(
    insert_request: InsertInsightsRequest,
    principal: Principal = Depends(get_principal),  # ✅ Strict auth
    service: InsightsService = Depends(get_insights_service),
):
    user_id = principal.sub  # Extract user ID from validated JWT
    # ...
```

**No X-User-Id fallback** - Unlike search-api, agent-api uses strict JWT validation only.

### Authorization

**User isolation enforced at multiple levels:**

1. **API Level**: Each endpoint validates user can only access their own data
   ```python
   # Users can only search their own insights
   if search_request.user_id != principal.sub:
       raise HTTPException(status_code=403, detail="Cannot search insights for other users")
   ```

2. **Database Level**: Milvus queries filter by userId
   ```python
   expr=f'userId == "{user_id}"'  # Only return this user's insights
   ```

3. **Service Level**: All operations scoped to authenticated user
   ```python
   service.delete_user_insights(user_id)  # Can only delete own insights
   ```

## Endpoints and Security

| Endpoint | Method | Auth Required | Authorization Check |
|----------|--------|---------------|---------------------|
| `/insights/init` | POST | ✅ Bearer | None (admin operation) |
| `/insights` | POST | ✅ Bearer | Validates userId in insights matches token |
| `/insights/search` | POST | ✅ Bearer | Validates userId in request matches token |
| `/insights/conversation/{id}` | DELETE | ✅ Bearer | Only deletes user's own insights |
| `/insights/user/{id}` | DELETE | ✅ Bearer | Validates {id} matches token user |
| `/insights/stats/{id}` | GET | ✅ Bearer | Validates {id} matches token user |
| `/insights/flush` | POST | ✅ Bearer | None (collection operation) |

## Token Flow

### Production Flow

```
User → AI Portal → AuthZ Service → JWT Token → Agent API → Insights
```

1. User logs into AI Portal
2. AI Portal requests token from AuthZ service
3. AuthZ service issues JWT with user claims
4. AI Portal includes JWT in requests to Agent API
5. Agent API validates JWT and extracts user ID
6. Insights service filters data by user ID

### Test Flow

For testing, you need a valid JWT token from the authz service:

```bash
# Get token (requires proper client setup)
curl -X POST http://authz-lxc:8010/oauth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials" \
  -d "client_id=<client-id>" \
  -d "client_secret=<client-secret>" \
  -d "audience=busibox-services"

# Use token
curl -X POST http://agent-lxc:8000/insights \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"insights": [...]}'
```

## Comparison with Search-API

### Search-API (Old - Less Secure)

```python
# Accepts X-User-Id header as fallback
async def get_current_user_id(
    authorization: str = Header(None),
    x_user_id: str = Header(None),  # ⚠️ Security risk
):
    if x_user_id:
        return x_user_id  # No validation!
```

**Issues:**
- Anyone can set X-User-Id header
- No validation of user identity
- Can impersonate any user

### Agent-API (New - Secure)

```python
# Only accepts validated Bearer tokens
async def get_principal(
    authorization: str = Header(...),  # Required
) -> Principal:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401)
    token = authorization.split(" ", 1)[1]
    principal = await validate_bearer(token)  # ✅ Validates JWT
    return principal
```

**Benefits:**
- JWT validation via authz service
- Cryptographic verification
- Cannot be spoofed
- Includes user claims and permissions

## Data Isolation

### Milvus Level

All queries include user filter:

```python
results = self.collection.search(
    data=[query_embedding],
    anns_field="embedding",
    param=search_params,
    limit=limit,
    expr=f'userId == "{user_id}"',  # ✅ Filter by user
    output_fields=["id", "userId", "content", "conversationId", "analyzedAt"],
)
```

### Application Level

Double-check in results:

```python
# Filter by userId (double-check in case expr didn't work)
if result_user_id != user_id:
    continue  # Skip results for other users
```

## Testing

### Unit Tests

Test authentication and authorization:

```python
def test_insert_insights_requires_auth():
    """Test that insert requires Bearer token."""
    response = client.post("/insights", json={"insights": [...]})
    assert response.status_code == 401

def test_search_insights_user_isolation():
    """Test that users can only search their own insights."""
    response = client.post(
        "/insights/search",
        json={"query": "test", "userId": "other-user"},
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    assert response.status_code == 403  # Forbidden
```

### Integration Tests

Test with real JWT tokens from authz service.

## Security Checklist

- [x] All endpoints require Bearer token authentication
- [x] JWT validation via authz service
- [x] User ID extracted from validated token
- [x] Authorization checks prevent cross-user access
- [x] Milvus queries filtered by userId
- [x] No X-User-Id fallback in production
- [x] Error messages don't leak user information
- [x] Consistent with agent-api security model

## Migration Impact

### Backward Compatibility

**Breaking change for search-api clients:**
- Old: Could use X-User-Id header
- New: Must use Bearer token

**Migration path:**
1. Update busibox-app to get Bearer tokens
2. Update AI Portal to pass tokens
3. Deploy agent-api with insights
4. Remove search-api insights

### Client Updates Required

All clients must:
1. Obtain JWT from authz service
2. Include `Authorization: Bearer <token>` header
3. Remove X-User-Id header usage

## Future Enhancements

### Potential Improvements

1. **Role-based access**:
   - Admin role can view all insights
   - Support for shared insights (team access)

2. **Scope-based permissions**:
   - `insights.read` - Read own insights
   - `insights.write` - Write own insights
   - `insights.admin` - Manage all insights

3. **Audit logging**:
   - Log all insight access
   - Track who accessed what insights
   - Compliance requirements

## Related Documentation

- [insights-migration-completed.md](./insights-migration-completed.md) - Implementation details
- [insights-testing-guide.md](./insights-testing-guide.md) - Testing procedures
- `openapi/agent-api.yaml` - API specification
- `srv/agent/app/auth/dependencies.py` - Authentication implementation

## Notes

- Security model matches agent-api patterns
- No backward compatibility with X-User-Id
- All clients must use proper JWT tokens
- User isolation enforced at multiple levels
- Production-ready security implementation



