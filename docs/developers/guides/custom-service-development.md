---
title: Custom Service Development Guide
category: developer
order: 20
description: How to build and deploy a custom (non-Next.js) service on the busibox platform
published: true
---

# Custom Service Development Guide

**Created**: 2026-03-24
**Last Updated**: 2026-03-24
**Status**: Active
**Category**: Developer Guide

---

This guide walks through creating a custom service -- a non-Next.js application (Python API, Flask app, Go service, multi-container stack) that runs on busibox with full platform integration.

## Prerequisites

- A busibox environment (Docker or Proxmox) with deploy-api running
- A GitHub repository for your service
- Familiarity with Docker and Docker Compose

## Step 1: Create busibox.json

Every busibox application needs a `busibox.json` manifest at its repository root. For custom services, set `appMode: "custom"`:

```json
{
  "id": "inventory-tracker",
  "name": "Inventory Tracker",
  "version": "1.0.0",
  "description": "Warehouse inventory tracking with real-time updates",
  "category": "operations",
  "defaultPath": "/inventory",
  "appMode": "custom",

  "runtime": {
    "type": "docker-compose",
    "composeFile": "docker-compose.yml"
  },

  "services": [
    {
      "name": "api",
      "port": 8000,
      "path": "/inventory/api",
      "stripPath": true,
      "healthEndpoint": "/health"
    },
    {
      "name": "web",
      "port": 3000,
      "path": "/inventory",
      "stripPath": true,
      "healthEndpoint": "/health"
    }
  ],

  "auth": {
    "audience": "inventory-api"
  },

  "repository": {
    "url": "https://github.com/myorg/inventory-tracker"
  },

  "requiredEnvVars": [],
  "optionalEnvVars": ["WAREHOUSE_API_KEY"]
}
```

### Key fields

- **`services`**: Each entry maps a Docker Compose service to a URL path. The `name` must match a service in your `docker-compose.yml`. The `path` is the URL prefix that nginx will proxy to this service.
- **`auth.audience`**: A unique identifier registered with authz for token exchange. Use `kebab-case`, e.g., `inventory-api`.
- **`services[].stripPath`**: When `true` (default), the path prefix is stripped before forwarding. A request to `/inventory/api/items` becomes `/items` at the API service.

## Step 2: Write docker-compose.yml

Your `docker-compose.yml` defines the services that make up your application. Service names must match the `services[].name` values in `busibox.json`.

```yaml
services:
  api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    ports:
      - "8000:8000"
    env_file:
      - .env
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 5s
      retries: 3

  web:
    build:
      context: .
      dockerfile: docker/web.Dockerfile
    ports:
      - "3000:3000"
    env_file:
      - .env
    depends_on:
      api:
        condition: service_healthy

  db:
    image: postgres:15
    volumes:
      - db-data:/var/lib/postgresql/data
    environment:
      POSTGRES_DB: inventory
      POSTGRES_USER: inventory
      POSTGRES_PASSWORD: ${DB_PASSWORD:-changeme}

  worker:
    build:
      context: .
      dockerfile: docker/worker.Dockerfile
    env_file:
      - .env
    depends_on:
      - db
      - api

volumes:
  db-data:
```

Notes:
- Only services listed in `busibox.json` `services[]` get nginx proxy rules. Internal services (db, worker) remain internal.
- Use `env_file: [.env]` to receive busibox-generated environment variables.
- The deploy service connects all containers to `busibox-net` after startup, giving them access to authz, data-api, agent-api, etc.

## Step 3: Add busibox Authentication

### Python (FastAPI)

Install `busibox_common` (automatically copied into `shared/` during deployment):

```dockerfile
COPY shared/ shared/
RUN pip install ./shared
```

Add JWT middleware:

```python
import os
from fastapi import FastAPI
from busibox_common.auth import JWTAuthMiddleware

app = FastAPI()
app.add_middleware(
    JWTAuthMiddleware,
    jwks_url=os.environ["AUTHZ_JWKS_URL"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/items")
async def list_items(request):
    user = request.state.user
    # user.user_id, user.email, user.roles, etc.
    ...
```

### Python (Flask)

```python
import os
from flask import Flask, g, request, jsonify
from busibox_common.auth import parse_jwt_token, extract_user_context

app = Flask(__name__)
JWKS_URL = os.environ["AUTHZ_JWKS_URL"]

@app.before_request
def require_auth():
    if request.path == "/health":
        return
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return jsonify({"error": "Missing token"}), 401
    try:
        claims = parse_jwt_token(token, jwks_url=JWKS_URL)
        g.user = extract_user_context(claims)
    except Exception:
        return jsonify({"error": "Invalid token"}), 401
```

### Other Languages

For Go, Rust, or other languages, validate JWTs directly using your language's JWT library:

1. Fetch the JWKS from `AUTHZ_JWKS_URL` (cache with TTL)
2. Validate the JWT signature against the JWKS keys
3. Verify `aud` matches your `auth.audience`
4. Verify `exp` is in the future
5. Extract user claims from the token payload

## Step 4: Call Busibox Platform APIs

Custom services can call other busibox APIs using Zero Trust token exchange. The user's JWT is exchanged for a service-scoped token.

### Python

```python
from busibox_common.auth import exchange_token_zero_trust

async def get_documents(user_token: str):
    data_api_token = exchange_token_zero_trust(
        user_token=user_token,
        target_audience="data-api",
        authz_url=os.environ["AUTHZ_TOKEN_URL"],
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{os.environ['DATA_API_URL']}/data",
            headers={"Authorization": f"Bearer {data_api_token}"},
        )
        return resp.json()
```

### Available Services

| Service | Env Variable | Purpose |
|---------|-------------|---------|
| data-api | `DATA_API_URL` | Structured data storage, CRUD operations |
| agent-api | `AGENT_API_URL` | AI agents, chat, structured output |
| search-api | `SEARCH_API_URL` | Semantic search, web search |
| authz | `AUTHZ_BASE_URL` | User lookup, role management |

## Step 5: Deploy

### Via Admin UI

1. Open the Busibox Admin panel
2. Navigate to Apps
3. Click "Register App"
4. Enter your GitHub repository (owner/repo)
5. The system reads your `busibox.json` and validates it
6. Click "Deploy"

The Admin UI shows custom service details including all service endpoints, their ports and paths, the runtime configuration, and the auth audience.

### Via Deploy API

```bash
curl -X POST https://busibox.local/deploy-api/deploy \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "appId": "inventory-tracker",
    "githubRepoOwner": "myorg",
    "githubRepoName": "inventory-tracker",
    "githubBranch": "main",
    "environment": "development"
  }'
```

## Step 6: Manage

Once deployed, use standard busibox management commands:

```bash
# Check status
make manage SERVICE=inventory-tracker ACTION=status

# View logs
make manage SERVICE=inventory-tracker ACTION=logs

# Restart
make manage SERVICE=inventory-tracker ACTION=restart

# Full rebuild + restart
make manage SERVICE=inventory-tracker ACTION=redeploy

# Stop
make manage SERVICE=inventory-tracker ACTION=stop
```

## Project Structure Example

A typical custom service repository:

```
inventory-tracker/
├── busibox.json              # Busibox manifest
├── docker-compose.yml        # Service orchestration
├── docker/
│   ├── api.Dockerfile        # API service image
│   ├── web.Dockerfile        # Web frontend image
│   └── worker.Dockerfile     # Background worker image
├── api/                      # API source code
│   ├── main.py
│   ├── routes/
│   └── requirements.txt
├── web/                      # Web frontend
│   ├── app.py
│   ├── templates/
│   └── static/
├── worker/                   # Background worker
│   └── tasks.py
└── shared/                   # Auto-populated by deploy service
    └── busibox_common/       # Copied during deployment
```

## Tips

### Docker Compose Networking

- Your services can communicate with each other using Docker Compose service names (e.g., `http://api:8000` from the web service).
- After deployment, all containers are connected to `busibox-net`, so they can reach platform services (e.g., `http://dev-authz-api:8010`).
- Don't bind ports to the host -- nginx handles external routing.

### Health Checks

- Every service listed in `busibox.json` must have a health endpoint.
- The deploy service checks health 10 times with 3-second intervals before reporting failure.
- Health checks run inside the container (via `docker exec` or network call), so use `localhost` in your health endpoint.

### Environment Variables

- The `.env` file is generated automatically during deployment. Don't commit one.
- Add an `env.example` showing required variables for local development.
- Use `requiredEnvVars` in `busibox.json` for variables that must be set.
- Use `optionalEnvVars` for variables with sensible defaults.

### Local Development

For local development without busibox:

```bash
# Create your own .env with service URLs pointing to local or busibox services
cp env.example .env

# Start services
docker compose up -d
```

If your busibox instance is running locally, you can point `AUTHZ_JWKS_URL`, `DATA_API_URL`, etc., to the local busibox services.

## Troubleshooting

### "No custom service found"

The service isn't registered in the deploy-api registry. Deploy it through the Admin UI or Deploy API.

### Health checks failing

1. Verify your health endpoint returns a 200 status
2. Check that the port in `busibox.json` matches the port your service listens on
3. Check container logs: `make manage SERVICE=myservice ACTION=logs`

### Can't reach busibox APIs

1. Verify containers are connected to `busibox-net`: `docker network inspect dev-busibox-net`
2. Check `.env` has the correct service URLs
3. Verify token exchange is working by checking authz logs

### Nginx returning 502

1. The upstream container isn't running or isn't healthy
2. The container name doesn't match the expected pattern (`{prefix}-custom-{app_id}-{service_name}`)
3. Check nginx config: look in `config/nginx-sites/apps/{app_id}.conf`

## Related

- [Custom Service Hosting Architecture](../architecture/12-custom-services.md)
- [Applications Layer](../architecture/07-apps.md)
- [Authentication Architecture](../architecture/03-authentication.md)
- [busibox_common Library](../reference/busibox-common.md)
