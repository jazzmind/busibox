---
title: Custom Service Hosting
category: architecture
order: 12
description: Hosting non-Next.js services (Python, Go, Docker Compose stacks) in busibox with full platform integration
published: true
---

# Custom Service Hosting

**Created**: 2026-03-24
**Last Updated**: 2026-03-24
**Status**: Active
**Category**: Architecture
**Related Docs**:
- `architecture/07-apps.md`
- `architecture/03-authentication.md`
- `architecture/01-containers.md`

---

## Overview

Custom service hosting extends busibox beyond Next.js applications to support arbitrary service stacks -- Python APIs, Flask/Django apps, Go services, multi-container Docker Compose projects, and anything else with a `docker-compose.yml`. Each custom service gets its own isolated Docker Compose project on the busibox network, with full access to platform services (authz, data-api, agent-api, search-api) and automatic nginx proxy configuration.

## Architecture

```
Deploy API (BusiboxManifest)
├── appMode: "frontend"  →  container_executor  →  user-apps (node:20-slim)
├── appMode: "prisma"    →  container_executor  →  user-apps + provisioned Postgres DB
└── appMode: "custom"    →  custom_service_executor
                              ├── Docker: compose project on busibox-net
                              └── LXC: SSH → remote docker compose
```

Custom services differ from user-apps in several ways:

| Aspect | User Apps (frontend/prisma) | Custom Services |
|--------|---------------------------|-----------------|
| **Runtime** | Shared `node:20-slim` container | Own Docker Compose stack |
| **Processes** | Single Node.js process | Multiple containers (API, DB, worker, etc.) |
| **Language** | JavaScript/TypeScript only | Any language |
| **Database** | Optional managed Postgres | Bring your own (PostGIS, MongoDB, etc.) |
| **Proxy** | Single location block | Multiple location blocks per service endpoint |

### Container Topology

```
busibox infrastructure
├── core-apps (portal, agents, admin, chat...)
├── user-apps (Next.js apps)
├── custom: myservice
│   ├── myservice-api (port 8000)
│   ├── myservice-web (port 5050)
│   ├── myservice-db (PostGIS)
│   └── myservice-worker (background)
└── custom: another-service (...)
```

## Manifest

Custom services declare `appMode: "custom"` in their `busibox.json` with additional fields for runtime, service endpoints, and authentication.

```json
{
  "id": "myservice",
  "name": "My Service",
  "version": "1.0.0",
  "defaultPath": "/myservice",
  "appMode": "custom",
  "runtime": {
    "type": "docker-compose",
    "composeFile": "docker-compose.yml",
    "buildContext": "."
  },
  "services": [
    {
      "name": "web",
      "port": 5050,
      "path": "/myservice",
      "stripPath": true,
      "healthEndpoint": "/health"
    },
    {
      "name": "api",
      "port": 8000,
      "path": "/myservice/api",
      "stripPath": true,
      "healthEndpoint": "/api/health"
    }
  ],
  "auth": {
    "audience": "myservice-api",
    "scopes": []
  }
}
```

### Manifest Fields

**Standard fields** (`id`, `name`, `version`, `defaultPath`, `repository`, `category`, etc.) work the same as for frontend/prisma apps.

**Custom-specific fields**:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `appMode` | `"custom"` | Yes | Enables the custom service executor |
| `runtime.type` | `"docker-compose"` or `"lxc"` | No (default: `docker-compose`) | Container orchestration method |
| `runtime.composeFile` | string | No (default: `docker-compose.yml`) | Path to compose file relative to repo root |
| `runtime.buildContext` | string | No (default: `.`) | Docker build context |
| `services` | array | Yes | Service endpoints to expose via nginx |
| `services[].name` | string | Yes | Docker Compose service name |
| `services[].port` | number | Yes | Port the service listens on inside its container |
| `services[].path` | string | Yes | URL path prefix for nginx proxy |
| `services[].stripPath` | boolean | No (default: `true`) | Strip the path prefix before forwarding |
| `services[].healthEndpoint` | string | No (default: `/health`) | Health check endpoint |
| `auth.audience` | string | Yes | Token audience for authz registration |
| `auth.scopes` | string[] | No (default: `[]`) | OAuth2 scopes for the service |

**Optional for custom apps**: `defaultPort`, `healthEndpoint`, `buildCommand`, `startCommand` -- these apply to Next.js apps and are ignored for custom services.

## Deployment Pipeline

### Docker Backend

1. **Clone/update repository** from GitHub to `/srv/custom-services/{app_id}/`
2. **Copy `busibox_common`** shared Python library into `shared/` inside the build context
3. **Generate `.env`** with busibox service endpoints, authz URLs, and any configured secrets
4. **Build** via `docker compose -p {prefix}-custom-{app_id} build`
5. **Start** via `docker compose -p {prefix}-custom-{app_id} up -d`
6. **Connect** all containers to `busibox-net` for platform service access
7. **Health check** each service endpoint
8. **Configure nginx** with location blocks for each service endpoint
9. **Register audience** in authz for token exchange
10. **Register** in the custom services registry

### LXC Backend (Proxmox)

The same pipeline runs over SSH to the target LXC container:
1. SSH to the custom services host (typically the user-apps LXC container)
2. Clone repo, generate `.env`, and run `docker compose build && up -d` on the remote
3. Configure nginx on the proxy LXC via the nginx configurator

### Project Naming

Docker Compose projects use the naming convention `{CONTAINER_PREFIX}-custom-{app_id}`, e.g., `dev-custom-myservice` or `prod-custom-myservice`. This isolates custom service containers from the main busibox compose project.

## Nginx Configuration

Each service endpoint declared in the manifest generates a separate nginx `location` block:

```nginx
# Auto-generated by Busibox Deploy Service for: myservice

location ^~ /myservice/api/ {
    proxy_pass http://dev-custom-myservice-api:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location ^~ /myservice {
    proxy_pass http://dev-custom-myservice-web:5050;
    rewrite ^/myservice$ / break;
    rewrite ^/myservice(/.*)$ $1 break;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Location blocks are sorted by specificity (longest path first) to ensure more specific routes match before general ones.

## Authentication

Custom services integrate with busibox SSO via `busibox_common`:

### Python Services (FastAPI)

```python
from busibox_common.auth import JWTAuthMiddleware

app = FastAPI()
app.add_middleware(JWTAuthMiddleware, jwks_url=os.environ["AUTHZ_JWKS_URL"])
```

### Python Services (Flask)

```python
from busibox_common.auth import parse_jwt_token, extract_user_context

@app.before_request
def require_auth():
    token = request.headers.get("Authorization", "").removeprefix("Bearer ")
    claims = parse_jwt_token(token, jwks_url=os.environ["AUTHZ_JWKS_URL"])
    g.user = extract_user_context(claims)
```

### Token Exchange

To call other busibox APIs (data-api, agent-api, search-api), use Zero Trust token exchange:

```python
from busibox_common.auth import exchange_token_zero_trust

data_api_token = exchange_token_zero_trust(
    user_token=request_token,
    target_audience="data-api",
    authz_url=os.environ["AUTHZ_TOKEN_URL"],
)
```

## Environment Variables

The deploy service auto-generates a `.env` file for each custom service with:

| Variable | Description |
|----------|-------------|
| `AUTHZ_BASE_URL` | Authz service URL |
| `AUTHZ_JWKS_URL` | JWKS endpoint for JWT validation |
| `AUTHZ_TOKEN_URL` | Token exchange endpoint |
| `AUTHZ_AUDIENCE` | The service's registered audience |
| `DATA_API_URL` | Data API endpoint |
| `AGENT_API_URL` | Agent API endpoint |
| `SEARCH_API_URL` | Search API endpoint |
| `BUSIBOX_PORTAL_URL` | Portal URL (for SSO redirects) |

Plus any `requiredEnvVars`, `optionalEnvVars`, and secrets configured in the deployment.

## Management

Custom services integrate with the standard busibox management commands:

```bash
make manage SERVICE=myservice ACTION=status
make manage SERVICE=myservice ACTION=restart
make manage SERVICE=myservice ACTION=logs
make manage SERVICE=myservice ACTION=stop
make manage SERVICE=myservice ACTION=start
make manage SERVICE=myservice ACTION=redeploy
```

The management scripts auto-detect custom services by checking for their Docker Compose project or `/srv/custom-services/{service}/` directory. Custom service actions delegate to `docker compose` commands on the appropriate project.

### Via Admin UI

Custom services appear in the Busibox Admin UI with their service endpoint details, runtime configuration, and auth audience displayed. Deploy, undeploy, and stop operations are available through the same UI used for frontend/prisma apps.

## Shared Library: busibox_common

Python-based custom services can use the `busibox_common` shared library for:

- **JWT authentication** (middleware, token parsing, JWKS validation)
- **Token exchange** (Zero Trust pattern for service-to-service calls)
- **User context extraction** (roles, organization, permissions)

The library is automatically copied into the custom service's build context at `shared/busibox_common/` during deployment. Services should reference it in their Dockerfile:

```dockerfile
COPY shared/ shared/
RUN pip install ./shared
```

## Implementation Files

| File | Description |
|------|-------------|
| `srv/deploy/src/models.py` | `ServiceEndpoint`, `RuntimeConfig`, `AuthConfig` models |
| `srv/deploy/src/custom_service_executor.py` | Docker and LXC deployment, lifecycle management |
| `srv/deploy/src/nginx_config.py` | Multi-endpoint nginx configuration generation |
| `srv/deploy/src/routes.py` | API route integration and authz audience registration |
| `scripts/lib/backends/common.sh` | Custom service detection (`_is_custom_service`) |
| `scripts/lib/backends/docker.sh` | Docker management actions (`_custom_service_action`) |
| `scripts/lib/backends/proxmox.sh` | Proxmox/LXC management actions |
| `packages/app/src/lib/deploy/manifest-schema.ts` | Frontend Zod validation for custom manifests |
| `apps/admin/src/components/admin/AppForm.tsx` | Admin UI custom service display |

## Related

- [Applications Layer](07-apps.md) -- Standard Next.js app hosting
- [Authentication](03-authentication.md) -- Zero Trust token exchange
- [Containers](01-containers.md) -- LXC container architecture
