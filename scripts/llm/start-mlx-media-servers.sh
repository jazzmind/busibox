#!/usr/bin/env bash
#
# Start/stop/status for MLX media servers (transcribe, voice, image)
#
# Usage:
#   start-mlx-media-servers.sh start          # Start always-on servers (voice only)
#   start-mlx-media-servers.sh start-all      # Start all servers including on-demand
#   start-mlx-media-servers.sh stop           # Stop all servers
#   start-mlx-media-servers.sh status         # Status of all servers
#   start-mlx-media-servers.sh transcribe     # Toggle Whisper STT (start if stopped, stop if running)
#   start-mlx-media-servers.sh image          # Toggle Flux image-gen (start if stopped, stop if running)
#
# Always-on (small, stay resident):
#   Voice/TTS  (8082) - Kokoro-82M ~0.2GB
#
# On-demand (larger, start/stop as needed):
#   Transcribe (8081) - Whisper Large V3 ~3GB
#   Image gen  (8083) - Flux klein-4b Q8 ~4GB
#
# Optional environment overrides:
#   TRANSCRIBE_MODEL_PATH  (default: mlx-community/whisper-large-v3-mlx)
#   VOICE_MODEL_PATH       (default: mlx-community/Kokoro-82M-bf16)
#   IMAGE_MODEL_PATH       (default: black-forest-labs/FLUX.1-schnell, local path for flux-2-klein-4b)
#   IMAGE_CONFIG_NAME      (deprecated alias for IMAGE_MODEL_PATH)
#   TRANSCRIBE_PORT        (default: 8081)
#   VOICE_PORT             (default: 8082)
#   IMAGE_PORT             (default: 8083)
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../lib/ui.sh"

MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"
MLX_OPENAI_SERVER_BIN="${MLX_VENV_DIR}/bin/mlx-openai-server"
MLX_PYTHON_BIN="${MLX_VENV_DIR}/bin/python3"
MLX_PIP_BIN="${MLX_VENV_DIR}/bin/pip3"

TRANSCRIBE_PORT="${TRANSCRIBE_PORT:-8081}"
VOICE_PORT="${VOICE_PORT:-8082}"
IMAGE_PORT="${IMAGE_PORT:-8083}"

TRANSCRIBE_MODEL_PATH="${TRANSCRIBE_MODEL_PATH:-mlx-community/whisper-large-v3-mlx}"
VOICE_MODEL_PATH="${VOICE_MODEL_PATH:-mlx-community/Kokoro-82M-bf16}"
# Keep IMAGE_CONFIG_NAME as a backward-compatible alias.
# Image model path must be a local directory for Flux models — downloaded by mlx-openai-server on first use.
IMAGE_MODEL_PATH="${IMAGE_MODEL_PATH:-${IMAGE_CONFIG_NAME:-}}"
IMAGE_CONFIG_NAME="${IMAGE_CONFIG_NAME:-flux-2-klein-4b}"
IMAGE_QUANTIZE="${IMAGE_QUANTIZE:-8}"

TRANSCRIBE_PID_FILE="/tmp/mlx-openai-transcribe.pid"
VOICE_PID_FILE="/tmp/mlx-openai-voice.pid"
IMAGE_PID_FILE="/tmp/mlx-openai-image.pid"

TRANSCRIBE_LOG_FILE="/tmp/mlx-openai-transcribe.log"
VOICE_LOG_FILE="/tmp/mlx-openai-voice.log"
IMAGE_LOG_FILE="/tmp/mlx-openai-image.log"

setup_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        local python_bin=""
        # Prefer stable interpreter versions for MLX audio dependencies.
        for candidate in python3.12 python3.13 python3; do
            if command -v "$candidate" &>/dev/null; then
                python_bin="$candidate"
                break
            fi
        done

        if [[ -z "$python_bin" ]]; then
            error "No compatible Python interpreter found (expected python3.12, python3.13, or python3)"
            exit 1
        fi

        info "Creating MLX virtual environment with ${python_bin}..."
        "$python_bin" -m venv "$MLX_VENV_DIR"
    fi
}

ensure_mlx_openai_server() {
    setup_venv

    if [[ ! -x "$MLX_OPENAI_SERVER_BIN" ]] || \
       ! "$MLX_PYTHON_BIN" -c "import mlx_audio, tiktoken, misaki, spacy, webrtcvad, numba, outlines" &>/dev/null; then
        info "Installing MLX media dependencies in virtual environment..."
        "$MLX_PIP_BIN" install -q mlx-openai-server "mlx-audio[all]" "setuptools<81"
    fi
}

register_model() {
    local name="$1"
    local port="$2"
    local model_path="$3"
    local register_url="http://127.0.0.1:${port}/v1/models"

    if ! curl -sf -X POST "${register_url}?model_name=${model_path}" &>/dev/null; then
        error "Failed to register ${name} model on port ${port}: ${model_path}"
        return 1
    fi

    success "${name} model registered: ${model_path}"
}

is_running() {
    local pid_file="$1"
    if [[ ! -f "$pid_file" ]]; then
        return 1
    fi
    local pid
    pid=$(cat "$pid_file")
    kill -0 "$pid" 2>/dev/null
}

kill_port_if_busy() {
    local port="$1"
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [[ -n "$pids" ]]; then
        warn "Port ${port} already in use, stopping existing process(es): ${pids}"
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null || true
        sleep 1
        pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
        if [[ -n "$pids" ]]; then
            # shellcheck disable=SC2086
            kill -9 $pids 2>/dev/null || true
        fi
    fi
}

stop_one() {
    local name="$1"
    local pid_file="$2"
    if is_running "$pid_file"; then
        local pid
        pid=$(cat "$pid_file")
        info "Stopping ${name} server (PID: ${pid})..."
        kill "$pid" 2>/dev/null || true
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
        fi
        success "${name} server stopped"
    fi
    rm -f "$pid_file"
}

wait_ready() {
    local name="$1"
    local port="$2"
    local health_url="http://127.0.0.1:${port}/v1/models"
    local attempts=0
    local max_attempts=90

    while [[ $attempts -lt $max_attempts ]]; do
        if curl -sf "$health_url" &>/dev/null; then
            success "${name} server ready at http://localhost:${port}/v1"
            return 0
        fi
        sleep 2
        attempts=$((attempts + 1))
    done

    error "${name} server failed to become ready (port ${port})"
    return 1
}

start_transcribe() {
    kill_port_if_busy "$TRANSCRIBE_PORT"

    if is_running "$TRANSCRIBE_PID_FILE"; then
        warn "Transcribe server already running (PID: $(cat "$TRANSCRIBE_PID_FILE"))"
        return 0
    fi

    info "Starting transcribe server on port ${TRANSCRIBE_PORT} (on-demand, ~3GB)..."
    nohup "$MLX_PYTHON_BIN" -m mlx_audio.server \
        --host 0.0.0.0 \
        --port "$TRANSCRIBE_PORT" \
        --workers 1 \
        > "$TRANSCRIBE_LOG_FILE" 2>&1 &

    echo $! > "$TRANSCRIBE_PID_FILE"
    wait_ready "Transcribe" "$TRANSCRIBE_PORT"
    register_model "Transcribe" "$TRANSCRIBE_PORT" "$TRANSCRIBE_MODEL_PATH"
}

start_voice() {
    kill_port_if_busy "$VOICE_PORT"

    if is_running "$VOICE_PID_FILE"; then
        warn "Voice server already running (PID: $(cat "$VOICE_PID_FILE"))"
        return 0
    fi

    info "Starting voice server on port ${VOICE_PORT} (always-on, ~0.2GB)..."
    nohup "$MLX_PYTHON_BIN" -m mlx_audio.server \
        --host 0.0.0.0 \
        --port "$VOICE_PORT" \
        --workers 1 \
        > "$VOICE_LOG_FILE" 2>&1 &

    echo $! > "$VOICE_PID_FILE"
    wait_ready "Voice" "$VOICE_PORT"
    register_model "Voice" "$VOICE_PORT" "$VOICE_MODEL_PATH"
}

start_image() {
    kill_port_if_busy "$IMAGE_PORT"

    if is_running "$IMAGE_PID_FILE"; then
        warn "Image server already running (PID: $(cat "$IMAGE_PID_FILE"))"
        return 0
    fi

    info "Starting image generation server on port ${IMAGE_PORT} (on-demand, ~4GB)..."

    local launch_args=(
        launch
        --model-type image-generation
        --config-name "$IMAGE_CONFIG_NAME"
        --quantize "$IMAGE_QUANTIZE"
        --host 0.0.0.0
        --port "$IMAGE_PORT"
    )
    # If a local model path is provided, add --model-path
    if [[ -n "$IMAGE_MODEL_PATH" ]]; then
        launch_args+=(--model-path "$IMAGE_MODEL_PATH")
    fi

    nohup "$MLX_OPENAI_SERVER_BIN" "${launch_args[@]}" \
        > "$IMAGE_LOG_FILE" 2>&1 &

    echo $! > "$IMAGE_PID_FILE"
    wait_ready "Image" "$IMAGE_PORT"
}

toggle_transcribe() {
    if is_running "$TRANSCRIBE_PID_FILE"; then
        stop_one "Transcribe" "$TRANSCRIBE_PID_FILE"
    else
        ensure_mlx_openai_server
        start_transcribe
    fi
}

toggle_image() {
    if is_running "$IMAGE_PID_FILE"; then
        stop_one "Image" "$IMAGE_PID_FILE"
    else
        ensure_mlx_openai_server
        start_image
    fi
}

status_one() {
    local name="$1"
    local pid_file="$2"
    local port="$3"
    local log_file="$4"
    local kind="$5"

    if is_running "$pid_file"; then
        local pid
        pid=$(cat "$pid_file")
        echo "  ${name} [${kind}]: running (PID: ${pid}, port: ${port}, log: ${log_file})"
        if curl -sf "http://127.0.0.1:${port}/v1/models" &>/dev/null; then
            echo "    health: healthy"
        else
            echo "    health: not responding"
        fi
    else
        echo "  ${name} [${kind}]: stopped"
    fi
}

# Start always-on servers only (voice/TTS)
start_always_on() {
    ensure_mlx_openai_server
    start_voice
}

# Start all servers including on-demand (for demos / full dev session)
start_everything() {
    ensure_mlx_openai_server
    start_voice
    start_transcribe
    start_image
}

stop_all() {
    stop_one "Image" "$IMAGE_PID_FILE"
    stop_one "Transcribe" "$TRANSCRIBE_PID_FILE"
    stop_one "Voice" "$VOICE_PID_FILE"
}

status_all() {
    echo "MLX Media Servers:"
    status_one "Voice/TTS"  "$VOICE_PID_FILE"      "$VOICE_PORT"      "$VOICE_LOG_FILE"      "always-on"
    status_one "Transcribe" "$TRANSCRIBE_PID_FILE"  "$TRANSCRIBE_PORT" "$TRANSCRIBE_LOG_FILE"  "on-demand"
    status_one "Image gen"  "$IMAGE_PID_FILE"       "$IMAGE_PORT"      "$IMAGE_LOG_FILE"       "on-demand"
}

main() {
    if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
        error "MLX media servers require Apple Silicon (Darwin arm64)"
        exit 1
    fi

    local action="${1:-start}"
    case "$action" in
        start)      start_always_on ;;
        start-all)  start_everything ;;
        stop)       stop_all ;;
        status)     status_all ;;
        transcribe) toggle_transcribe ;;
        image)      toggle_image ;;
        *)
            error "Unknown action: ${action}"
            echo "Usage: $0 {start|start-all|stop|status|transcribe|image}"
            exit 1
            ;;
    esac
}

main "$@"
