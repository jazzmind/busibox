#!/usr/bin/env bash
# =============================================================================
# Start MLX-LM Server for Apple Silicon
# =============================================================================
#
# Starts the MLX-LM server with OpenAI-compatible API on the host machine.
# Model is selected based on system RAM tier.
#
# This runs NATIVELY on the host (not in Docker) for optimal Apple Silicon
# performance using unified memory.
#
# Usage:
#   ./start-mlx-server.sh           # Start with detected model
#   ./start-mlx-server.sh --stop    # Stop the server
#
# The server exposes an OpenAI-compatible API at http://localhost:8080/v1
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source system detection and get models
source "${SCRIPT_DIR}/detect-system.sh"
eval "$(${SCRIPT_DIR}/get-models.sh all)"

# MLX virtual environment (PEP 668 compliance for modern macOS)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

PORT=8080
PID_FILE="/tmp/mlx-lm-server.pid"
LOG_FILE="/tmp/mlx-lm-server.log"

# Handle stop command
if [[ "${1:-}" == "--stop" ]]; then
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "Stopping MLX-LM server (PID: $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            echo "Server stopped."
        else
            echo "Server not running (stale PID file)."
            rm -f "$PID_FILE"
        fi
    else
        echo "No PID file found. Server may not be running."
    fi
    exit 0
fi

# Check if already running
if [[ -f "$PID_FILE" ]]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "MLX-LM server already running (PID: $PID)"
        echo "Use '$0 --stop' to stop it first."
        exit 0
    else
        rm -f "$PID_FILE"
    fi
fi

# Setup MLX virtual environment (PEP 668 compliance)
mkdir -p "${HOME}/.busibox"
if [[ ! -d "$MLX_VENV_DIR" ]]; then
    echo "Creating MLX virtual environment..."
    python3 -m venv "$MLX_VENV_DIR"
fi

MLX_PYTHON="${MLX_VENV_DIR}/bin/python3"
MLX_PIP="${MLX_VENV_DIR}/bin/pip3"

# Check if mlx-lm is installed in venv
if ! "$MLX_PYTHON" -c "import mlx_lm" 2>/dev/null; then
    echo "Installing mlx-lm into virtual environment..."
    "$MLX_PIP" install -q mlx-lm huggingface_hub
fi

echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║                    MLX-LM Server for Apple Silicon                   ║"
echo "╠══════════════════════════════════════════════════════════════════════╣"
printf "║  System: %-60s║\n" "${DEMO_RAM_GB}GB RAM, ${DEMO_TIER} tier"
printf "║  Model:  %-60s║\n" "${DEMO_MODEL_AGENT}"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo ""

echo "Starting MLX-LM server..."
echo "  Model: ${DEMO_MODEL_AGENT}"
echo "  Port: ${PORT}"
echo "  Log: ${LOG_FILE}"
echo ""

# Start server in background with agent model (primary model for demo)
nohup "$MLX_PYTHON" -m mlx_lm.server \
    --model "$DEMO_MODEL_AGENT" \
    --host 0.0.0.0 \
    --port "$PORT" \
    --trust-remote-code \
    > "$LOG_FILE" 2>&1 &

# Save PID
echo $! > "$PID_FILE"
echo "Server started with PID: $(cat $PID_FILE)"
echo ""
echo "Waiting for server to be ready..."

# Wait for server to be ready (max 120 seconds)
MAX_WAIT=120
WAITED=0
while ! curl -sf "http://localhost:${PORT}/v1/models" >/dev/null 2>&1; do
    if (( WAITED >= MAX_WAIT )); then
        echo "ERROR: Server failed to start within ${MAX_WAIT} seconds"
        echo "Check log: ${LOG_FILE}"
        exit 1
    fi
    sleep 2
    WAITED=$((WAITED + 2))
    echo -n "."
done

echo ""
echo "MLX-LM server ready at http://localhost:${PORT}/v1"
