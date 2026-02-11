# Local Busibox Development Environment

> Run the complete Busibox stack locally using Docker for rapid development and testing.

## Overview

The local Docker environment provides:

- **Backend Services** (Docker): PostgreSQL, Redis, Milvus, MinIO, LiteLLM, and all Python APIs
- **Frontend Apps** (Local): Next.js apps run locally with `npm run dev` for fast hot-reload
- **Nginx Proxy**: Routes all traffic through `https://localhost` with proper SSL

This "hybrid" approach gives you the best of both worlds:
- Full backend infrastructure in containers (no manual setup)
- Fast frontend development with instant hot-reload

## Prerequisites

### Required

- **Docker Desktop** with Docker Compose v2
- **Node.js 20+** and npm
- **Python 3.11+** (for running tests)

### Recommended

- **mkcert** - Creates locally-trusted SSL certificates (no browser warnings)
  ```bash
  # macOS
  brew install mkcert
  mkcert -install  # Trust the local CA (requires sudo)
  ```

## Quick Start

```bash
# 1. Clone and enter the repo
cd busibox

# 2. Copy environment file
cp env.local.example .env.local

# 3. Build and start backend services
make docker-build   # First time only (takes ~10 min for ML dependencies)
make docker-up      # Start all services

# 4. Run frontend apps locally (in separate terminals)
cd ../ai-portal && npm install && npm run dev
cd ../agent-manager && npm install && npm run dev

# 5. Access the app
open https://localhost/portal
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        https://localhost                             │
├─────────────────────────────────────────────────────────────────────┤
│                           NGINX (Docker)                             │
│   /portal/* → host:3000    /agents/* → host:3001                    │
│   /api/authz/* → authz:8010   /api/ingest/* → ingest:8002           │
│   /api/search/* → search:8003  /api/agent/* → agent:8000            │
└─────────────────────────────────────────────────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐      ┌─────────────────────────────────────────┐
│   LOCAL (npm run)   │      │              DOCKER NETWORK              │
├─────────────────────┤      ├─────────────────────────────────────────┤
│ ai-portal    :3000  │      │ authz-api   :8010  Python FastAPI       │
│ agent-manager:3001  │      │ ingest-api  :8002  Python FastAPI       │
│                     │      │ search-api  :8003  Python FastAPI       │
│                     │      │ agent-api   :8000  Python FastAPI       │
│                     │      │ litellm     :4000  LLM Gateway          │
│                     │      ├─────────────────────────────────────────┤
│                     │      │ postgres    :5432  Database             │
│                     │      │ redis       :6379  Queue/Cache          │
│                     │      │ milvus      :19530 Vector DB            │
│                     │      │ minio       :9000  Object Storage       │
└─────────────────────┘      └─────────────────────────────────────────┘
           │
           ▼ (Apple Silicon only)
┌─────────────────────┐
│   HOST SERVICES     │
├─────────────────────┤
│ host-agent   :8089  │  ← Controls MLX from Docker
│ mlx-lm       :8080  │  ← Local LLM inference
└─────────────────────┘
```

### Apple Silicon (MLX) Support

On Apple Silicon Macs, local LLM inference uses MLX instead of vLLM:

- **host-agent** (`localhost:8089`) - Lightweight FastAPI service running on the host
- **mlx-lm** (`localhost:8080`) - MLX-LM server for inference

The host-agent allows Docker containers (like deploy-api) to control MLX, which requires
direct access to Apple Silicon hardware. During `make install`, a tiny test model (~300MB)
is downloaded to verify MLX works. Larger models can be downloaded via the AI Portal.

```bash
# Manual host-agent control
scripts/host-agent/install-host-agent.sh     # Install as launchd service
scripts/host-agent/install-host-agent.sh -u  # Uninstall

# Manual MLX control
scripts/llm/start-mlx-server.sh              # Start with default model
scripts/llm/start-mlx-server.sh --stop       # Stop server
scripts/llm/start-mlx-server.sh --status     # Check status
```

## Configuration

### Environment Variables

Edit `.env.local` to configure:

```bash
# Database
POSTGRES_USER=busibox_user
POSTGRES_PASSWORD=devpassword

# LLM API Keys (at least one required for AI features)
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
LITELLM_API_KEY=sk-local-dev-key

# MinIO (S3-compatible storage)
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# AuthZ
AUTHZ_ADMIN_TOKEN=local-admin-token
AUTHZ_MASTER_KEY=local-master-key-change-in-production
```

### SSL Certificates

SSL certificates are auto-generated on first `make docker-build`:

```bash
# Manual generation (if needed)
bash scripts/setup/generate-local-ssl.sh

# With mkcert installed (recommended - no browser warnings)
brew install mkcert
mkcert -install
rm ssl/localhost.crt ssl/localhost.key
bash scripts/setup/generate-local-ssl.sh
```

## Common Commands

### Docker Management

```bash
make docker-build                    # Build all backend images
make docker-build SERVICE=agent-api  # Build specific service
make docker-build NO_CACHE=1         # Force rebuild without cache

make docker-up                       # Start all services
make docker-up SERVICE=authz-api     # Start specific service

make docker-down                     # Stop all services
make docker-restart                  # Restart all services
make docker-restart SERVICE=nginx    # Restart specific service

make docker-logs                     # View all logs
make docker-logs SERVICE=agent-api   # View specific service logs

make docker-ps                       # Show service status
make docker-clean                    # Remove containers & volumes (⚠️ deletes data)
```

### Running Tests

```bash
# Test against Docker services
make docker-test SERVICE=authz       # Test authz-api
make docker-test SERVICE=all         # Run all tests

# Local tests
make test-local SERVICE=authz
```

### Database Access

```bash
# Connect to PostgreSQL (use service-specific databases)
docker exec -it local-postgres psql -U busibox_user -d agent_server  # Agent API
docker exec -it local-postgres psql -U busibox_user -d authz         # AuthZ
docker exec -it local-postgres psql -U busibox_user -d files         # Ingest

# Test databases (for pytest isolation)
docker exec -it local-postgres psql -U busibox_test_user -d test_agent_server
docker exec -it local-postgres psql -U busibox_test_user -d test_authz
docker exec -it local-postgres psql -U busibox_test_user -d test_files

# View all databases
docker exec -it local-postgres psql -U postgres -c "\l"

# Connect to specific database
\c agent_server
\c authz
\c files
\c ai_portal
```

**Database Layout:**

| Service | Database | Owner |
|---------|----------|-------|
| Agent API | `agent_server` | `busibox_user` |
| AuthZ | `authz` | `busibox_user` |
| Ingest | `files` | `busibox_user` |
| AI Portal | `ai_portal` | `busibox_user` |
| Tests | `test_*` | `busibox_test_user` |

### Viewing Logs

```bash
# All services
make docker-logs

# Specific service
make docker-logs SERVICE=search-api

# Follow with Docker directly
docker logs -f local-agent-api
```

## Service URLs

### Via Nginx Proxy (Recommended)

| Service | URL |
|---------|-----|
| AI Portal | https://localhost/portal |
| Agent Manager | https://localhost/agents |
| AuthZ API | https://localhost/api/authz |
| Ingest API | https://localhost/api/ingest |
| Search API | https://localhost/api/search |
| Agent API | https://localhost/api/agent |
| LiteLLM | https://localhost/api/llm |

### Direct Access (Development)

| Service | URL |
|---------|-----|
| AI Portal | http://localhost:3000 |
| Agent Manager | http://localhost:3001 |
| AuthZ API | http://localhost:8010 |
| Ingest API | http://localhost:8002 |
| Search API | http://localhost:8003 |
| Agent API | http://localhost:8000 |
| LiteLLM | http://localhost:4000 |
| MinIO Console | http://localhost:9001 |
| Milvus | localhost:19530 |

## Hot Reload

### Python APIs (Docker)

Python services have hot-reload enabled. Edit files in `srv/` and changes apply automatically:

```
srv/authz/src/     → authz-api auto-reloads
srv/data/src/      → ingest-api auto-reloads (srv/ingest was renamed to srv/data)
srv/search/src/    → search-api auto-reloads
srv/agent/app/     → agent-api auto-reloads
```

**Note:** Rebuild required if you change `requirements.txt` or Dockerfile.

### Next.js Apps (Local)

Frontend apps run locally with full hot-reload:

```bash
cd ../ai-portal && npm run dev      # Port 3000
cd ../agent-manager && npm run dev  # Port 3001
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs for the failing service
docker logs local-search-api

# Restart the service
docker restart local-search-api

# Check all service health
make docker-ps
```

### Search API Unhealthy on First Start

The search-api downloads an ML model (~90MB) on first start. Wait 2 minutes, then:

```bash
docker restart local-search-api
```

### SSL Certificate Errors

```bash
# Regenerate certificates
rm ssl/localhost.crt ssl/localhost.key
bash scripts/setup/generate-local-ssl.sh
docker restart local-nginx
```

### Database Connection Issues

```bash
# Check PostgreSQL is running
docker logs local-postgres

# Verify databases exist
docker exec -it local-postgres psql -U postgres -c "\l"

# Recreate databases (⚠️ deletes all data)
make docker-clean
make docker-up
```

### Port Already in Use

```bash
# Find what's using the port
lsof -i :8003

# Stop the conflicting process or change the port in docker-compose.local.yml
```

### Reset Everything

```bash
# Nuclear option - removes all containers, volumes, and data
make docker-down
docker volume rm $(docker volume ls -q | grep busibox)
make docker-build NO_CACHE=1
make docker-up
```

## Full Docker Mode

For testing the complete containerized stack (including frontend apps):

```bash
# Build and start everything in Docker
docker compose --profile full -f docker-compose.local.yml build
docker compose --profile full -f docker-compose.local.yml up -d
```

This is useful for:
- Testing production-like configurations
- CI/CD pipelines
- When you don't want to run Node.js locally

## Next Steps

- [Using Busibox App Service Clients](reference/using-busibox-app-service-clients.md) - Integrate with APIs
- [Testing Guide](../guides/03-testing.md) - Run and write tests
- [Deployment Guide](../guides/02-deployment.md) - Deploy to production

