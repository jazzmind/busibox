---
title: "AuthZ API Reference"
category: "developer"
order: 52
description: "AuthZ REST API - token exchange, admin endpoints"
published: true
---

# AuthZ API Reference

## Token Endpoint

### POST /oauth/token

Exchange a token for an audience-bound access token.

**Grants supported**:
- `client_credentials` — Service-to-service
- `urn:ietf:params:oauth:grant-type:token-exchange` — User token exchange (RFC 8693)

**Token exchange request**:
```json
{
  "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
  "client_id": "ai-portal",
  "client_secret": "<secret>",
  "subject_token": "<session-jwt>",
  "audience": "data-api",
  "requested_token_type": "urn:ietf:params:oauth:token-type:jwt"
}
```

**Response**: `{"access_token": "...", "token_type": "Bearer", "expires_in": 900}`

See [02-architecture](02-architecture.md) for full token exchange flow and configuration.

## JWKS

### GET /.well-known/jwks.json

Public keys for JWT verification. Services use this to validate tokens without database lookups.

## Admin Endpoints

Admin endpoints require `admin.read` or `admin.write` scope. See AuthZ source for full API.

## Health

### GET /health/live

Liveness probe.
