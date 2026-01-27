# MLX Host Agent

**Created**: 2026-01-27  
**Status**: Active  
**Category**: Guides  
**Related Docs**:
- `development/local-busibox.md`
- `architecture/00-overview.md`

## Overview

The MLX Host Agent is a lightweight FastAPI service that enables Docker containers to control MLX-LM, which requires direct access to Apple Silicon hardware.

## Why a Host Agent?

MLX (Apple's machine learning framework) requires direct access to Apple Silicon's unified memory and Neural Engine. This means:

- **MLX cannot run in Docker** - Docker containers don't have access to the Metal GPU
- **deploy-api runs in Docker** - It orchestrates service deployment via Docker Compose
- **Solution: host-agent** - A bridge service running on the host that Docker can call

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Docker Network                              │
├─────────────────────────────────────────────────────────────────────┤
│  AI Portal  ─────►  deploy-api  ─────►  host.docker.internal:8089   │
│  (Browser)          (Container)              (Host Agent)           │
└─────────────────────────────────────────────────────────────────────┘
                                                      │
                                              ┌───────▼───────┐
                                              │   Host (Mac)   │
                                              ├───────────────┤
                                              │ host-agent    │ localhost:8089
                                              │ mlx-lm server │ localhost:8080
                                              │ Apple Silicon │
                                              └───────────────┘
```

## Installation

### Automatic (via make install)

When running `make install` on Apple Silicon, the installer automatically:

1. Creates an isolated Python virtual environment at `~/.busibox/mlx-venv`
2. Installs MLX-LM and huggingface_hub into the virtual environment
3. Downloads a tiny test model (~300MB)
4. Installs host-agent as a launchd service (using the venv Python)
5. Starts host-agent on port 8089

**Note**: The virtual environment is required due to PEP 668, which prevents installing packages directly to the system Python on modern macOS (Homebrew Python).

### Manual Installation

```bash
# Install host-agent
cd scripts/host-agent
bash install-host-agent.sh

# Verify it's running
curl http://localhost:8089/health

# View logs
tail -f ~/Library/Logs/Busibox/host-agent.log
```

### Uninstall

```bash
bash scripts/host-agent/install-host-agent.sh --uninstall
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/mlx/status` | GET | Get MLX server status (running, model, healthy) |
| `/mlx/start` | POST | Start MLX with specified model (SSE stream) |
| `/mlx/stop` | POST | Stop MLX server |
| `/mlx/models` | GET | List available models by tier |
| `/mlx/models/download` | POST | Download a model (SSE stream) |
| `/models/cached` | GET | List cached HuggingFace models |

## Authentication

The host-agent uses a shared secret for authentication:

1. Token is generated during `make install` and saved to `.env.{env}` as `HOST_AGENT_TOKEN`
2. Token is passed to deploy-api via docker-compose environment
3. Requests to host-agent include `Authorization: Bearer {token}` header

**Development mode**: If no token is configured, host-agent runs without authentication.

## Model Tiers

Models are selected based on available RAM:

| Tier | RAM | Test Model | Agent Model | Notes |
|------|-----|------------|-------------|-------|
| test | <16GB | Qwen2.5-0.5B | Qwen2.5-0.5B | ~300MB, for testing only |
| minimal | 16-23GB | Qwen2.5-1.5B | Qwen2.5-3B | ~2GB |
| standard | 24-47GB | Qwen2.5-3B | Qwen2.5-7B | ~4GB |
| enhanced | 48-95GB | Qwen2.5-7B | Qwen2.5-14B | ~8GB |
| professional | 96-127GB | Qwen2.5-14B | Qwen2.5-32B | ~18GB |
| enterprise | 128-255GB | Qwen2.5-32B | Qwen2.5-72B | ~40GB |
| ultra | 256GB+ | Qwen2.5-72B | Qwen3-235B | ~65GB |

Model configuration is in `config/demo-models.yaml`.

## Files

```
scripts/host-agent/
├── host-agent.py           # FastAPI service
├── requirements.txt        # Python dependencies
├── install-host-agent.sh   # launchd installer
└── README.md               # This documentation

~/.busibox/
└── mlx-venv/               # Python virtual environment for MLX
    ├── bin/
    │   ├── python3         # Python interpreter (used by host-agent)
    │   └── pip3            # Package installer
    └── lib/                # Installed packages
```

## Manual MLX Control

You can control MLX directly without the host-agent:

```bash
# Start MLX server
scripts/llm/start-mlx-server.sh              # Default (agent) model
scripts/llm/start-mlx-server.sh fast         # Fast model
scripts/llm/start-mlx-server.sh frontier     # Frontier model

# Stop server
scripts/llm/start-mlx-server.sh --stop

# Check status
scripts/llm/start-mlx-server.sh --status
```

## Troubleshooting

### Host-agent not starting

```bash
# Check if already running
lsof -i :8089

# Check launchd status
launchctl list | grep busibox

# View error logs
cat ~/Library/Logs/Busibox/host-agent.error.log

# Reinstall
bash scripts/host-agent/install-host-agent.sh --uninstall
bash scripts/host-agent/install-host-agent.sh
```

### MLX server not responding

```bash
# Check MLX status via host-agent
curl http://localhost:8089/mlx/status

# Check MLX directly
curl http://localhost:8080/v1/models

# Check logs
cat /tmp/mlx-lm-server.log
```

### Model download stuck

Models are downloaded from HuggingFace. Large models (>10GB) can take a while.

```bash
# Check download progress
ls -la ~/.cache/huggingface/hub/

# Download manually (using the MLX venv)
~/.busibox/mlx-venv/bin/python3 -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/Qwen2.5-7B-Instruct-4bit')"
```

### Docker can't reach host-agent

```bash
# Test from inside a container
docker exec -it dev-deploy-api curl http://host.docker.internal:8089/health

# If that fails, check Docker Desktop settings
# Ensure "host.docker.internal" is enabled (default on Docker Desktop for Mac)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST_AGENT_HOST` | `127.0.0.1` | Host to bind to |
| `HOST_AGENT_PORT` | `8089` | Port to listen on |
| `HOST_AGENT_TOKEN` | (none) | Authentication token |
| `MLX_PORT` | `8080` | Port for MLX-LM server |

## Virtual Environment

MLX packages are installed in an isolated virtual environment to comply with PEP 668 (externally-managed-environment) on modern macOS.

**Location**: `~/.busibox/mlx-venv`

**Contents**:
- `mlx-lm` - Apple's MLX language model library
- `huggingface_hub` - For downloading models
- `fastapi`, `uvicorn`, `httpx`, `pyyaml` - Host-agent dependencies

**Manual activation** (for debugging):
```bash
source ~/.busibox/mlx-venv/bin/activate
python3 -c "import mlx_lm; print('MLX-LM version:', mlx_lm.__version__)"
```

## Integration with Deploy-API

Deploy-API calls host-agent for MLX operations:

```python
# service_routes.py
HOST_AGENT_URL = os.getenv("HOST_AGENT_URL", "http://host.docker.internal:8089")
HOST_AGENT_TOKEN = os.getenv("HOST_AGENT_TOKEN", "")

# Start MLX
response = await client.post(
    f"{HOST_AGENT_URL}/mlx/start",
    json={"model_type": "agent"},
    headers={"Authorization": f"Bearer {HOST_AGENT_TOKEN}"},
)

# Check health
response = await client.get(f"{HOST_AGENT_URL}/mlx/status")
```

## Security Considerations

- Host-agent only binds to `127.0.0.1` (not exposed externally)
- Authentication via shared secret token
- Whitelisted operations only (no arbitrary shell execution)
- Runs as user process (not root)
