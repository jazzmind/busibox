# Busibox Host Agent

A lightweight FastAPI service that runs on the host machine to enable Docker containers to control MLX-LM, which requires direct access to Apple Silicon hardware.

## Why?

MLX requires direct access to Apple Silicon's unified memory and Neural Engine, which is not available inside Docker containers. The host-agent acts as a bridge, allowing deploy-api (running in Docker) to start/stop/manage MLX-LM.

## Quick Start

```bash
# Install and start as launchd service
bash install-host-agent.sh

# Check status
curl http://localhost:8089/health

# Uninstall
bash install-host-agent.sh --uninstall
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/mlx/status` | GET | Get MLX server status |
| `/mlx/start` | POST | Start MLX (SSE stream) |
| `/mlx/stop` | POST | Stop MLX server |
| `/mlx/models` | GET | List available models by tier |
| `/mlx/models/download` | POST | Download a model (SSE stream) |
| `/models/cached` | GET | List cached models |

## Authentication

Set `HOST_AGENT_TOKEN` environment variable for authentication. Without it, the service runs in dev mode (no auth).

## Manual Usage

```bash
# Run directly (for development)
python3 host-agent.py

# Start MLX via API
curl -X POST http://localhost:8089/mlx/start \
  -H "Content-Type: application/json" \
  -d '{"model_type": "agent"}'

# Check MLX status
curl http://localhost:8089/mlx/status

# Stop MLX
curl -X POST http://localhost:8089/mlx/stop
```

## Files

- `host-agent.py` - FastAPI service
- `requirements.txt` - Python dependencies
- `install-host-agent.sh` - launchd installer
- `README.md` - This file

## Logs

```bash
tail -f ~/Library/Logs/Busibox/host-agent.log
tail -f ~/Library/Logs/Busibox/host-agent.error.log
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_AGENT_HOST` | `127.0.0.1` | Host to bind to |
| `HOST_AGENT_PORT` | `8089` | Port to listen on |
| `HOST_AGENT_TOKEN` | (none) | Auth token |
| `MLX_PORT` | `8080` | MLX-LM server port |

## Documentation

See `docs/guides/mlx-host-agent.md` for full documentation.
