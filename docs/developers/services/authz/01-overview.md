---
title: "AuthZ Service Overview"
category: "developer"
order: 50
description: "Authentication and authorization service - OAuth2 token exchange, RBAC, JWKS"
published: true
---

# AuthZ Service Overview

The AuthZ service (`srv/authz`) is the central authentication and authorization authority for Busibox. It issues RS256 JWTs, manages RBAC, and provides OAuth2 token exchange (RFC 8693) for downstream service access.

## Key Capabilities

- **Session JWTs** — Passkey, TOTP, or magic link authentication
- **Token exchange** — Exchange session token for audience-bound access tokens (e.g., `data-api`, `search-api`, `agent-api`)
- **RBAC** — Roles, permissions, app bindings
- **JWKS** — `GET /.well-known/jwks.json` for token validation
- **Envelope encryption** — KEK/DEK for sensitive data

## Documentation

| Doc | Content |
|-----|---------|
| [02-architecture](02-architecture.md) | OAuth2 token exchange, RBAC, key management |
| [03-api](03-api.md) | Token endpoint, admin endpoints |
| [04-testing](04-testing.md) | Bootstrap test credentials, test setup |

## Quick Reference

- **Base URL**: `http://authz-lxc:8010`
- **Token endpoint**: `POST /oauth/token`
- **JWKS**: `GET /.well-known/jwks.json`
