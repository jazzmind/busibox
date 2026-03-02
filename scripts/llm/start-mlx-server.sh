#!/usr/bin/env bash
#
# Start MLX-LM server for Apple Silicon
#
# Usage:
#   start-mlx-server.sh           # Start with agent model (Outlines enabled by default)
#   start-mlx-server.sh fast      # Start with fast model
#   start-mlx-server.sh --stop    # Stop the server
#   start-mlx-server.sh --status  # Check server status
#
# Environment:
#   MLX_USE_OUTLINES=0   Disable Outlines server (plain mlx_lm.server fallback)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library
source "${SCRIPT_DIR}/../lib/ui.sh"

# Outlines-based server is the default (structured output enforcement).
# Set MLX_USE_OUTLINES=0 to disable.
: "${MLX_USE_OUTLINES:=1}"

# MLX virtual environment (PEP 668 compliance for modern macOS)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Find Python 3.10+ (required by outlines library)
find_python310() {
    for candidate in python3.13 python3.12 python3.11 python3.10; do
        local p
        p=$(command -v "$candidate" 2>/dev/null) && {
            echo "$p"
            return 0
        }
    done
    # Check common Homebrew paths
    for prefix in /opt/homebrew/bin /usr/local/bin; do
        for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
            [[ -x "${prefix}/${candidate}" ]] && {
                local ver
                ver=$("${prefix}/${candidate}" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
                if [[ -n "$ver" && "$ver" -ge 10 ]]; then
                    echo "${prefix}/${candidate}"
                    return 0
                fi
            }
        done
    done
    # Fallback: check if default python3 is 3.10+
    if command -v python3 &>/dev/null; then
        local ver
        ver=$(python3 -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
        if [[ -n "$ver" && "$ver" -ge 10 ]]; then
            echo "python3"
            return 0
        fi
    fi
    return 1
}

# Setup or use the MLX virtual environment
setup_mlx_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        local py
        py=$(find_python310) || {
            error "Python 3.10+ is required for MLX. Install via: brew install python3"
            return 1
        }
        info "Creating MLX virtual environment ($(${py} --version))..."
        "$py" -m venv "$MLX_VENV_DIR"
    else
        # Verify existing venv has Python 3.10+
        local venv_ver
        venv_ver=$("${MLX_VENV_DIR}/bin/python3" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        if [[ "$venv_ver" -lt 10 ]]; then
            warn "Existing MLX venv uses Python 3.${venv_ver} (need 3.10+). Recreating..."
            rm -rf "$MLX_VENV_DIR"
            local py
            py=$(find_python310) || {
                error "Python 3.10+ is required for MLX. Install via: brew install python3"
                return 1
            }
            info "Creating MLX virtual environment ($(${py} --version))..."
            "$py" -m venv "$MLX_VENV_DIR"
        fi
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
PRIMARY_PORT="${MLX_PORT:-8080}"
FAST_PORT="${MLX_FAST_PORT:-18081}"
PRIMARY_PID_FILE="/tmp/mlx-lm-server.pid"
PRIMARY_LOG_FILE="/tmp/mlx-lm-server.log"
FAST_PID_FILE="/tmp/mlx-lm-fast-server.pid"
FAST_LOG_FILE="/tmp/mlx-lm-fast-server.log"

# Get models
eval "$(bash "${SCRIPT_DIR}/get-models.sh" all)"

stop_server_instance() {
    local pid_file="$1"
    local port="$2"
    local label="$3"

    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            info "Stopping MLX-LM ${label} server (PID: ${pid})..."
            # Kill the process group (covers caffeinate + child python)
            kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 -- -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
            fi
            success "MLX-LM ${label} server stopped"
        fi
        rm -f "$pid_file"
    fi

    # Clean up any orphaned server or caffeinate processes on this port
    pkill -f "mlx_lm.server.*--port ${port}" 2>/dev/null || true
    pkill -f "mlx-outlines-server/server.py.*--port ${port}" 2>/dev/null || true

    # Kill any lsof-visible process still holding the port
    local port_pid
    port_pid=$(lsof -ti :"${port}" 2>/dev/null | head -1)
    if [[ -n "$port_pid" ]]; then
        kill "$port_pid" 2>/dev/null || true
        sleep 1
        kill -0 "$port_pid" 2>/dev/null && kill -9 "$port_pid" 2>/dev/null || true
    fi
}

stop_server() {
    stop_server_instance "$PRIMARY_PID_FILE" "$PRIMARY_PORT" "primary"
    stop_server_instance "$FAST_PID_FILE" "$FAST_PORT" "fast"
}

check_status_instance() {
    local pid_file="$1"
    local port="$2"
    local log_file="$3"
    local label="$4"

    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            success "MLX-LM ${label} server running (PID: ${pid})"
            echo "  Port: ${port}"
            echo "  Log: ${log_file}"
            
            # Check if responding
            if curl -sf "http://localhost:${port}/v1/models" &>/dev/null; then
                echo "  Status: Healthy"
            else
                warn "  Status: Not responding"
            fi
            return 0
        fi
    fi

    warn "MLX-LM ${label} server is not running"
    return 1
}

check_status() {
    local status=0
    check_status_instance "$PRIMARY_PID_FILE" "$PRIMARY_PORT" "$PRIMARY_LOG_FILE" "primary" || status=1
    check_status_instance "$FAST_PID_FILE" "$FAST_PORT" "$FAST_LOG_FILE" "fast" || true
    return "$status"
}

start_server_instance() {
    local role="$1"
    local model="$2"
    local port="$3"
    local pid_file="$4"
    local log_file="$5"
    local label="$6"

    # Check if already running
    if [[ -f "$pid_file" ]]; then
        local pid
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            if curl -sf "http://localhost:${port}/v1/models" &>/dev/null; then
                warn "MLX-LM ${label} server already running (PID: ${pid})"
                return 1
            fi

            # Stale/misaligned process (e.g., running on prior port): recycle it.
            warn "MLX-LM ${label} PID exists but port ${port} is unhealthy; restarting"
            kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
            sleep 2
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 -- -"$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$pid_file"
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

    # Install Outlines if requested and not already present
    if [[ "${MLX_USE_OUTLINES:-0}" == "1" ]]; then
        if ! "$mlx_python" -c "import outlines" 2>/dev/null; then
            info "Installing outlines[mlxlm] into virtual environment..."
            "$mlx_pip" install -q "outlines[mlxlm]" uvicorn starlette
        fi
    fi

    # Display banner
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║${NC}           ${BOLD}MLX-LM ${label} Server for Apple Silicon${NC}                   ${CYAN}║${NC}"
    echo -e "${CYAN}╠══════════════════════════════════════════════════════════════════════╣${NC}"
    printf "${CYAN}║${NC}  System: %-60s${CYAN}║${NC}\n" "${LLM_TIER} tier"
    printf "${CYAN}║${NC}  Model:  %-60s${CYAN}║${NC}\n" "${model}"
    printf "${CYAN}║${NC}  Port:   %-60s${CYAN}║${NC}\n" "${port}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    info "Starting MLX-LM ${label} server..."
    echo "  Model: ${model}"
    echo "  Port: ${port}"
    echo "  Log: ${log_file}"
    echo ""
    
    # Start server in background using venv python.
    # caffeinate -di prevents macOS from throttling/killing the Metal-based
    # MLX process when the display sleeps (-d = display, -i = idle).
    # disown detaches the job from bash's job table so dual startup doesn't
    # SIGTERM the first server when starting the second (macOS bash 3.2 quirk).
    if [[ "${MLX_USE_OUTLINES:-0}" == "1" ]]; then
        local outlines_server="${REPO_ROOT}/config/mlx-outlines-server/server.py"
        info "Using Outlines-based server (structured output enforcement enabled)"
        nohup caffeinate -di "$mlx_python" "$outlines_server" \
            --model "$model" \
            --host 0.0.0.0 \
            --port "$port" \
            --trust-remote-code \
            > "$log_file" 2>&1 &
    else
        nohup caffeinate -di "$mlx_python" -m mlx_lm.server \
            --model "$model" \
            --host 0.0.0.0 \
            --port "$port" \
            --trust-remote-code \
            > "$log_file" 2>&1 &
    fi
    
    local server_pid=$!
    echo "$server_pid" > "$pid_file"
    disown "$server_pid" 2>/dev/null || true
    
    info "Server started with PID: ${server_pid}"
    echo ""
    
    # Wait for server to be ready
    info "Waiting for server to be ready..."
    local max_attempts=60
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        if curl -sf "http://localhost:${port}/v1/models" &>/dev/null; then
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
        error "MLX-LM ${label} server failed to start within timeout"
        echo "Check log: ${log_file}"
        return 1
    fi
    
    success "MLX-LM ${label} server ready at http://localhost:${port}/v1"
}

start_server() {
    local role="${1:-agent}"
    local model

    case "$role" in
        fast) model="$LLM_MODEL_FAST" ;;
        agent) model="$LLM_MODEL_AGENT" ;;
        frontier) model="$LLM_MODEL_FRONTIER" ;;
        test) model="${LLM_MODEL_TEST:-$LLM_MODEL_AGENT}" ;;
        default) model="${LLM_MODEL_DEFAULT:-$LLM_MODEL_AGENT}" ;;
        *) model="$LLM_MODEL_AGENT" ;;
    esac

    start_server_instance "$role" "$model" "$PRIMARY_PORT" "$PRIMARY_PID_FILE" "$PRIMARY_LOG_FILE" "primary"
}

start_dual_servers() {
    local primary_model="${LLM_MODEL_AGENT}"
    local fast_model="${LLM_MODEL_FAST:-$LLM_MODEL_TEST}"

    if [[ -z "$fast_model" ]]; then
        fast_model="$LLM_MODEL_AGENT"
    fi

    # Start both servers independently so one already-running server
    # does not block starting the other.
    local failed=0
    start_server_instance "agent" "$primary_model" "$PRIMARY_PORT" "$PRIMARY_PID_FILE" "$PRIMARY_LOG_FILE" "primary" || failed=1
    start_server_instance "fast" "$fast_model" "$FAST_PORT" "$FAST_PID_FILE" "$FAST_LOG_FILE" "fast" || failed=1

    # Final health gate: if both are healthy, treat as success.
    if curl -sf "http://localhost:${PRIMARY_PORT}/v1/models" >/dev/null 2>&1 && \
       curl -sf "http://localhost:${FAST_PORT}/v1/models" >/dev/null 2>&1; then
        success "Both MLX-LM primary and fast servers are healthy"
        return 0
    fi

    if [[ $failed -ne 0 ]]; then
        error "Dual MLX startup incomplete: one or more servers failed to start"
    else
        warn "Dual MLX startup finished but one or more health checks failed"
    fi
    return 1
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
        --dual)
            start_dual_servers
            ;;
        fast|agent|frontier|test|default)
            start_server "$action"
            ;;
        *)
            start_server "agent"
            ;;
    esac
}

main "$@"
