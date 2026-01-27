#!/usr/bin/env bash
#
# Start MLX-LM server for Apple Silicon
#
# Usage:
#   start-mlx-server.sh           # Start with agent model
#   start-mlx-server.sh fast      # Start with fast model
#   start-mlx-server.sh --stop    # Stop the server
#   start-mlx-server.sh --status  # Check server status
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/../lib/ui.sh"

# MLX virtual environment (PEP 668 compliance for modern macOS)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Setup or use the MLX virtual environment
setup_mlx_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment..."
        python3 -m venv "$MLX_VENV_DIR"
    fi
}

# Get venv python path
get_mlx_python() {
    echo "${MLX_VENV_DIR}/bin/python3"
}

# Get venv pip path
get_mlx_pip() {
    echo "${MLX_VENV_DIR}/bin/pip3"
}

# Server configuration
PORT="${MLX_PORT:-8080}"
PID_FILE="/tmp/mlx-lm-server.pid"
LOG_FILE="/tmp/mlx-lm-server.log"

# Get models
eval "$(bash "${SCRIPT_DIR}/get-models.sh" all)"

stop_server() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            info "Stopping MLX-LM server (PID: ${pid})..."
            kill "$pid" 2>/dev/null || true
            sleep 2
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null || true
            fi
            success "Server stopped"
        fi
        rm -f "$PID_FILE"
    else
        info "No server running"
    fi
}

check_status() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            success "MLX-LM server running (PID: ${pid})"
            echo "  Port: ${PORT}"
            echo "  Log: ${LOG_FILE}"
            
            # Check if responding
            if curl -sf "http://localhost:${PORT}/v1/models" &>/dev/null; then
                echo "  Status: Healthy"
            else
                warn "  Status: Not responding"
            fi
            return 0
        fi
    fi
    
    info "No MLX-LM server running"
    return 1
}

start_server() {
    local role="${1:-agent}"
    local model
    
    case "$role" in
        fast) model="$LLM_MODEL_FAST" ;;
        agent) model="$LLM_MODEL_AGENT" ;;
        frontier) model="$LLM_MODEL_FRONTIER" ;;
        *) model="$LLM_MODEL_AGENT" ;;
    esac
    
    # Check if already running
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            warn "MLX-LM server already running (PID: ${pid})"
            info "Use --stop to stop it first"
            return 1
        fi
        rm -f "$PID_FILE"
    fi
    
    # Setup venv and install mlx-lm if needed
    setup_mlx_venv
    local mlx_python
    local mlx_pip
    mlx_python=$(get_mlx_python)
    mlx_pip=$(get_mlx_pip)
    
    if ! "$mlx_python" -c "import mlx_lm" 2>/dev/null; then
        info "Installing mlx-lm into virtual environment..."
        "$mlx_pip" install -q mlx-lm huggingface_hub
    fi
    
    # Display banner
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}              ${BOLD}MLX-LM Server for Apple Silicon${NC}                      ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════════════════╣${NC}"
    printf "${CYAN}║${NC}  System: %-60s${CYAN}║${NC}\n" "${LLM_TIER} tier"
    printf "${CYAN}║${NC}  Model:  %-60s${CYAN}║${NC}\n" "${model}"
    printf "${CYAN}║${NC}  Port:   %-60s${CYAN}║${NC}\n" "${PORT}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    info "Starting MLX-LM server..."
    echo "  Model: ${model}"
    echo "  Port: ${PORT}"
    echo "  Log: ${LOG_FILE}"
    echo ""
    
    # Start server in background using venv python
    nohup "$mlx_python" -m mlx_lm.server \
        --model "$model" \
        --host 0.0.0.0 \
        --port "$PORT" \
        --trust-remote-code \
        > "$LOG_FILE" 2>&1 &
    
    echo $! > "$PID_FILE"
    
    info "Server started with PID: $(cat $PID_FILE)"
    echo ""
    
    # Wait for server to be ready
    info "Waiting for server to be ready..."
    local max_attempts=60
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "http://localhost:${PORT}/v1/models" &>/dev/null; then
            break
        fi
        sleep 2
        ((attempt++))
        if [[ $((attempt % 10)) -eq 0 ]]; then
            echo -n "."
        fi
    done
    echo ""
    
    if [[ $attempt -ge $max_attempts ]]; then
        error "Server failed to start within timeout"
        echo "Check log: ${LOG_FILE}"
        return 1
    fi
    
    success "MLX-LM server ready at http://localhost:${PORT}/v1"
}

# Main
main() {
    local action="${1:-agent}"
    
    # Check for Apple Silicon
    if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
        error "MLX-LM requires Apple Silicon (M1/M2/M3/M4)"
        info "For NVIDIA GPUs, use vLLM instead"
        exit 1
    fi
    
    case "$action" in
        --stop|-s)
            stop_server
            ;;
        --status)
            check_status
            ;;
        fast|agent|frontier)
            start_server "$action"
            ;;
        *)
            start_server "agent"
            ;;
    esac
}

main "$@"
