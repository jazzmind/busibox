# Agent Server

FastAPI-based agent orchestration service for Busibox.

## Quick Start

### Database Setup

1. Run migrations:
```bash
cd srv/agent
alembic upgrade head
```

2. Seed built-in agents:
```bash
python scripts/seed_builtin_agents.py
```

This creates the default built-in agents:
- **chat**: General purpose assistant
- **research**: Research agent with search and document analysis
- **document-analyst**: Document analysis specialist  
- **web-researcher**: Web search specialist

### Running the Server

```bash
uvicorn app.main:app --reload --port 8001
```

## Built-in Agents

Built-in agents are system-provided agents available to all users. They are:
- Marked with `is_builtin=True` in the database
- Visible to all authenticated users
- Cannot be edited or deleted by users
- Defined in `scripts/seed_builtin_agents.py`

To add new built-in agents:
1. Update `BUILTIN_AGENTS` in `scripts/seed_builtin_agents.py`
2. Run the seed script
3. Restart the agent-server to load them into the registry

## Personal Agents

Users can create personal agents via the Agent Manager UI:
- Marked with `is_builtin=False`
- Only visible to the creating user
- Can be edited and deleted by the owner
- Support custom models, instructions, and tool configurations

## Architecture

- **Agent Definitions**: Stored in PostgreSQL (`agent_definitions` table)
- **Agent Registry**: In-memory cache of hydrated PydanticAI agents
- **Dynamic Loading**: Agents loaded from database on startup and refreshed on demand
- **Tool Registry**: Validated set of available tools (search, rag, ingest)

## Environment Variables

```bash
# Database
DATABASE_URL=postgresql+asyncpg://agent_server:password@localhost:5432/agent_server

# LiteLLM
LITELLM_BASE_URL=http://10.96.200.207:4000/v1
LITELLM_API_KEY=your-api-key

# Auth
AUTH_JWKS_URL=http://10.96.200.210:8010/.well-known/jwks.json
AUTH_ISSUER=busibox-authz
AUTH_AUDIENCE=agent-server
```

## API Endpoints

- `GET /agents` - List all agents (built-in + personal)
- `GET /agents/{id}` - Get agent details
- `POST /agents/definitions` - Create personal agent
- `PUT /agents/{id}` - Update personal agent
- `DELETE /agents/{id}` - Delete personal agent
- `POST /runs` - Execute an agent
- `GET /streams/runs/{id}` - Stream agent execution

See `openapi/agent-api.yaml` for full API documentation.
