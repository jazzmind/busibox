# Busibox Agent (Python / Pydantic AI)

FastAPI + Pydantic AI agents/tools/workflows aligned with Busibox services (chat/search/ingest/RAG) with Busibox auth/token forwarding.

## Quick Start

### Prerequisites
- Python 3.11+
- PostgreSQL 15+
- Redis (for scheduler)

### Setup
```bash
pip install uv  # or poetry/pip
uv sync         # installs from pyproject dependencies

cp .env.example .env  # set auth + DB + Redis endpoints

# Run API
uvicorn app.main:app --reload
```

Key env vars (see `app/config/settings.py`):
- `AUTH_CLIENT_ID`, `AUTH_CLIENT_SECRET`, `AUTH_JWKS_URL`, `AUTH_TOKEN_URL`
- `DATABASE_URL`, `REDIS_URL`
- `SEARCH_API_URL`, `INGEST_API_URL`, `RAG_API_URL`

## Components
- `app/` FastAPI app, agents, dynamic loader, token exchange, scheduler, SSE streams.
- `app/db/schema.sql` initial DDL for dynamic defs, runs, tokens, RAG.
- `tests/` smoke test scaffold.

## Auth + Token Exchange
- Validates Busibox JWT via JWKS, issuer, audience.
- Exchanges user token → downstream scoped token via OAuth2 client-credentials (`/auth/exchange`), caches in DB (`token_grants`).

## Dynamic Resources
- CRUD for agents/tools/workflows/evals (`/agents/*`).
- Loader hydrates active agents and registers allowed tool adapters.

## Runs & Scheduling
- `/runs` executes agent with Busibox token forwarding and stores run/output.
- `/streams/runs/{id}` SSE for status/output.
- `/runs/schedule` cron scheduling (APS Scheduler).


