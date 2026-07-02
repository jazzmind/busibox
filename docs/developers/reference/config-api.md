---
title: "Config API Reference"
category: "developer"
order: 143
description: "Centralised configuration service with tiered access control"
published: true
---

# Config API Reference

> **Updated**: 2026-05-11
> **Status**: Active
> **Category**: Reference

## Overview

The Config API is a dedicated service for centralised configuration management across the Busibox platform. It replaces the previous configuration scattered across deploy-api's config table, data-api documents (portal branding, app registry), and environment variables.

**Port**: 8012
**Source**: `srv/config/`
**Database**: `config` (PostgreSQL, own database)

## Access Tiers

Every config entry has a `tier` that controls who can read it:

| Tier | Auth Required | Description |
|---|---|---|
| `public` | No | Branding, feature flags for public pages |
| `authenticated` | Any valid JWT | App registry, non-secret app settings |
| `app` | JWT + app role binding | App-specific secrets (API keys, etc.) |
| `admin` | Admin JWT | Platform secrets, full CRUD |

## Sensitive Categories and Source Gating

Certain config categories are classified as **sensitive** and require an additional OAuth2 scope beyond the Admin role:

| Category | Contains | Required scope |
|---|---|---|
| `llm-keys` | Cloud provider credentials (Bedrock, OpenAI, Anthropic) | `config.secrets.read` |

The `config.secrets.read` scope is **never** issued to a user directly. It is injected automatically by authz **only** when all of the following are true:

1. The caller is performing an OAuth2 token exchange targeting `config-api`
2. The subject token presented was issued to `agent-api` (i.e., `aud == "agent-api"`)
3. The user has the `Admin` role

This means:
- A logged-in admin user **cannot** directly call `/admin/config/{key}/raw` for LLM keys — their token will be rejected with `403`
- The agent-api **can** read LLM keys by exchanging its user token chain for a config-api token — because the subject token it presents has `aud=agent-api`

This pattern ensures that raw LLM credentials are never accessible outside the agent-api execution path.

## Database Schema

### config_entries

| Column | Type | Description |
|---|---|---|
| `id` | UUID | Primary key |
| `key` | TEXT | Config key name |
| `value` | TEXT | Value (encrypted if `encrypted=true`) |
| `encrypted` | BOOLEAN | Whether the value is encrypted at rest |
| `scope` | TEXT | `platform`, `app`, or `branding` |
| `app_id` | TEXT | NULL for platform, e.g. `market-intel` for app-scoped |
| `tier` | TEXT | `public`, `authenticated`, `app`, or `admin` |
| `category` | TEXT | Grouping (e.g. `smtp`, `api_keys`, `branding`) |
| `description` | TEXT | Human-readable description |
| `created_at` | TIMESTAMPTZ | Creation timestamp |
| `updated_at` | TIMESTAMPTZ | Last update timestamp |

**Unique constraint**: `(key, COALESCE(app_id, ''))` — same key can exist per-app.

### app_registry

Stores registered application definitions (replaces data-api `busibox-portal-app-config` document).

| Column | Type | Description |
|---|---|---|
| `id` | TEXT | App identifier (e.g. `market-intel`) |
| `name` | TEXT | Display name |
| `type` | TEXT | `BUILT_IN`, `LIBRARY`, `EXTERNAL`, `INTERNAL` |
| `sso_audience` | TEXT | SSO audience for token exchange |
| `url` | TEXT | App URL |
| `deployed_path` | TEXT | Path on server |
| `display_order` | INT | Sort order |
| `is_active` | BOOLEAN | Whether app is visible |
| ... | ... | Plus icon, version, health, color fields |

## API Endpoints

### Public (no auth)

```
GET /config/branding        Returns branding config (company name, colors, logo, etc.)
GET /config/public          Returns all tier=public config entries
GET /health/live            Liveness probe
GET /health/ready           Readiness probe
```

### Authenticated (valid JWT)

```
GET /config/apps            List active apps from app_registry
GET /config/apps/{app_id}   Get single app info
GET /config/authenticated   Returns all tier=authenticated entries
```

### App-scoped (JWT with app access)

```
GET /config/app/{app_id}            All config for the app (values masked if encrypted)
GET /config/app/{app_id}/{key}      Specific config value (masked)
GET /config/app/{app_id}/{key}/raw  Raw (decrypted) value — use at runtime for secrets
```

### Admin (Admin JWT)

```
GET    /admin/config                 List all config entries (filterable by category, scope, app_id)
GET    /admin/config/categories      List categories with key counts
GET    /admin/config/export          Export all config; sensitive categories redacted unless config.secrets.read scope present
POST   /admin/config/bulk            Bulk create/update entries
GET    /admin/config/{key}           Get single config (masked)
GET    /admin/config/{key}/raw       Get raw (decrypted) value — requires config.secrets.read for sensitive categories
PUT    /admin/config/{key}           Create or update a config entry
DELETE /admin/config/{key}           Delete a config entry

PUT    /admin/branding               Update branding config entries

GET    /admin/apps                   List all apps (including inactive)
POST   /admin/apps                   Register a new app
PUT    /admin/apps/reorder           Batch update display order
GET    /admin/apps/{app_id}          Get app details
PUT    /admin/apps/{app_id}          Update an app
DELETE /admin/apps/{app_id}          Delete an app
```

> **Sensitive category access**: Endpoints that return raw values for `llm-keys` entries enforce the `config.secrets.read` scope. Admin tokens obtained directly via session token exchange do **not** carry this scope. Only agent-api's token exchange chain injects it. This prevents direct admin API access to raw LLM credentials.

## Token Exchange

Config-api is a registered audience in the Zero Trust token exchange:

```typescript
import { exchangeWithSubjectToken } from '@jazzmind/busibox-app/lib/authz/next-client';

const result = await exchangeWithSubjectToken({
  sessionJwt,
  userId,
  audience: 'config-api',
  purpose: 'config-api',
});
const configToken = result.accessToken;
```

## Frontend Client

The `@jazzmind/busibox-app` package includes a config-api client:

```typescript
import {
  getBranding,
  listApps,
  getAppConfigRaw,
  getConfigApiToken,
} from '@jazzmind/busibox-app/lib/config/client';

// Public (no auth needed)
const branding = await getBranding();

// Authenticated
const token = await getConfigApiToken(userId, sessionJwt);
const apps = await listApps(token);

// App-scoped secret at runtime
const apiKey = await getAppConfigRaw(token, 'market-intel', 'AIS_API_KEY');
```

## Environment Variables

### Config-api service

| Variable | Default | Description |
|---|---|---|
| `CONFIG_PORT` | `8012` | Service port |
| `POSTGRES_HOST` | `postgres` | PostgreSQL host |
| `POSTGRES_DB` | `config` | Database name |
| `AUTHZ_BASE_URL` | `http://authz-api:8010` | Authz service URL (JWKS) |
| `CONFIG_ENCRYPTION_KEY` | (none) | AES key for encrypted values |

### Other services

Add `CONFIG_API_URL=http://config-api:8012` to services that need runtime config access.

## What Stays as Environment Variables

Service routing URLs (`DATA_API_URL`, `AUTHZ_BASE_URL`, etc.), port numbers, and base paths remain as env vars — these are infrastructure routing, not application configuration.

## Migration from Previous System

| Previous Location | New Location |
|---|---|
| deploy-api `config` table (SMTP, OAuth, etc.) | `config_entries` scope=platform, tier=admin |
| `busibox-portal-config` data-api document | `config_entries` scope=branding, tier=public |
| `busibox-portal-app-config` data-api document | `app_registry` table |
| `AIS_API_KEY` env var in market-intel | `config_entries` scope=app, app_id=market-intel, tier=app |
