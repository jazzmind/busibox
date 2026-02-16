#!/usr/bin/env bash
#
# Start/stop/status for MLX media servers (image, transcribe, voice)
#
# Usage:
#   start-mlx-media-servers.sh start
#   start-mlx-media-servers.sh stop
#   start-mlx-media-servers.sh status
#
# Optional environment overrides:
#   TRANSCRIBE_MODEL_PATH (default: mlx-community/whisper-large-v3-mlx)
#   VOICE_MODEL_PATH      (default: mlx-community/Kokoro-82M-bf16)
#   IMAGE_CONFIG_NAME     (default: flux-schnell)
#   TRANSCRIBE_PORT       (default: 8081)
#   VOICE_PORT            (default: 8082)
#   IMAGE_PORT            (default: 8083)
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
IMAGE_CONFIG_NAME="${IMAGE_CONFIG_NAME:-flux-schnell}"

TRANSCRIBE_PID_FILE="/tmp/mlx-openai-transcribe.pid"
VOICE_PID_FILE="/tmp/mlx-openai-voice.pid"
IMAGE_PID_FILE="/tmp/mlx-openai-image.pid"

TRANSCRIBE_LOG_FILE="/tmp/mlx-openai-transcribe.log"
VOICE_LOG_FILE="/tmp/mlx-openai-voice.log"
IMAGE_LOG_FILE="/tmp/mlx-openai-image.log"

setup_venv() {
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment..."
        python3 -m venv "$MLX_VENV_DIR"
    fi
}

ensure_mlx_openai_server() {
    setup_venv
    if [[ ! -x "$MLX_OPENAI_SERVER_BIN" ]]; then
        info "Installing mlx-openai-server in virtual environment..."
        "$MLX_PIP_BIN" install -q mlx-openai-server
    fi
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
    local health_url="http://localhost:${port}/v1/models"
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
    if is_running "$TRANSCRIBE_PID_FILE"; then
        warn "Transcribe server already running (PID: $(cat "$TRANSCRIBE_PID_FILE"))"
        return 0
    fi

    info "Starting transcribe server on port ${TRANSCRIBE_PORT}..."
    nohup "$MLX_OPENAI_SERVER_BIN" launch \
        --model-type whisper \
        --model-path "$TRANSCRIBE_MODEL_PATH" \
        --host 0.0.0.0 \
        --port "$TRANSCRIBE_PORT" \
        > "$TRANSCRIBE_LOG_FILE" 2>&1 &

    echo $! > "$TRANSCRIBE_PID_FILE"
}

start_voice() {
    if is_running "$VOICE_PID_FILE"; then
        warn "Voice server already running (PID: $(cat "$VOICE_PID_FILE"))"
        return 0
    fi

    info "Starting voice server on port ${VOICE_PORT}..."
    nohup "$MLX_OPENAI_SERVER_BIN" launch \
        --model-type tts \
        --model-path "$VOICE_MODEL_PATH" \
        --host 0.0.0.0 \
        --port "$VOICE_PORT" \
        > "$VOICE_LOG_FILE" 2>&1 &

    echo $! > "$VOICE_PID_FILE"
}

start_image() {
    if is_running "$IMAGE_PID_FILE"; then
        warn "Image server already running (PID: $(cat "$IMAGE_PID_FILE"))"
        return 0
    fi

    info "Starting image server on port ${IMAGE_PORT}..."
    nohup "$MLX_OPENAI_SERVER_BIN" launch \
        --model-type image-generation \
        --config-name "$IMAGE_CONFIG_NAME" \
        --host 0.0.0.0 \
        --port "$IMAGE_PORT" \
        > "$IMAGE_LOG_FILE" 2>&1 &

    echo $! > "$IMAGE_PID_FILE"
}

status_one() {
    local name="$1"
    local pid_file="$2"
    local port="$3"
    local log_file="$4"

    if is_running "$pid_file"; then
        local pid
        pid=$(cat "$pid_file")
        echo "  ${name}: running (PID: ${pid}, port: ${port}, log: ${log_file})"
        if curl -sf "http://localhost:${port}/v1/models" &>/dev/null; then
            echo "    health: healthy"
        else
            echo "    health: not responding"
        fi
    else
        echo "  ${name}: stopped"
    fi
}

start_all() {
    ensure_mlx_openai_server
    start_transcribe
    start_voice
    start_image

    wait_ready "Transcribe" "$TRANSCRIBE_PORT"
    wait_ready "Voice" "$VOICE_PORT"
    wait_ready "Image" "$IMAGE_PORT"
}

stop_all() {
    stop_one "Image" "$IMAGE_PID_FILE"
    stop_one "Voice" "$VOICE_PID_FILE"
    stop_one "Transcribe" "$TRANSCRIBE_PID_FILE"
}

status_all() {
    echo "MLX Media Servers:"
    status_one "Transcribe" "$TRANSCRIBE_PID_FILE" "$TRANSCRIBE_PORT" "$TRANSCRIBE_LOG_FILE"
    status_one "Voice" "$VOICE_PID_FILE" "$VOICE_PORT" "$VOICE_LOG_FILE"
    status_one "Image" "$IMAGE_PID_FILE" "$IMAGE_PORT" "$IMAGE_LOG_FILE"
}

main() {
    if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
        error "MLX media servers require Apple Silicon (Darwin arm64)"
        exit 1
    fi

    local action="${1:-start}"
    case "$action" in
        start) start_all ;;
        stop) stop_all ;;
        status) status_all ;;
        *)
            error "Unknown action: ${action}"
            echo "Usage: $0 {start|stop|status}"
            exit 1
            ;;
    esac
}

main "$@"
