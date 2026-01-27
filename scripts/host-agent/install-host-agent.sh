#!/usr/bin/env bash
#
# Install Busibox Host Agent as a launchd service (macOS)
#
# This script:
#   1. Sets up or uses the MLX virtual environment
#   2. Installs Python dependencies into the venv
#   3. Creates a launchd plist file
#   4. Loads the service to start on login
#
# Usage:
#   install-host-agent.sh           # Install and start
#   install-host-agent.sh --uninstall  # Stop and remove
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# MLX virtual environment path (shared with MLX setup)
MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"

# Service configuration
SERVICE_NAME="com.busibox.host-agent"
PLIST_PATH="${HOME}/Library/LaunchAgents/${SERVICE_NAME}.plist"
LOG_DIR="${HOME}/Library/Logs/Busibox"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info() { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Setup or verify the MLX virtual environment
setup_venv() {
    # Check for Python 3
    if ! command -v python3 &>/dev/null; then
        error "Python 3 is required"
        return 1
    fi
    
    # Create ~/.busibox directory if it doesn't exist
    mkdir -p "${HOME}/.busibox"
    
    # Create virtual environment if it doesn't exist
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        info "Creating MLX virtual environment at ${MLX_VENV_DIR}..."
        python3 -m venv "$MLX_VENV_DIR" || {
            error "Failed to create virtual environment"
            return 1
        }
        success "Virtual environment created"
    else
        info "Using existing virtual environment at ${MLX_VENV_DIR}"
    fi
    
    return 0
}

uninstall() {
    info "Uninstalling Busibox Host Agent..."
    
    # Stop the service if running
    if launchctl list | grep -q "$SERVICE_NAME"; then
        info "Stopping service..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
    fi
    
    # Remove plist
    if [[ -f "$PLIST_PATH" ]]; then
        rm -f "$PLIST_PATH"
        success "Removed $PLIST_PATH"
    fi
    
    success "Host agent uninstalled"
    exit 0
}

install() {
    info "Installing Busibox Host Agent..."
    
    # Check for macOS
    if [[ "$(uname -s)" != "Darwin" ]]; then
        error "This installer is for macOS only"
        exit 1
    fi
    
    # Setup virtual environment
    setup_venv || exit 1
    
    local mlx_pip="${MLX_VENV_DIR}/bin/pip3"
    
    # Install dependencies into venv
    info "Installing Python dependencies into virtual environment..."
    "$mlx_pip" install -q fastapi uvicorn httpx pyyaml || {
        error "Failed to install dependencies"
        exit 1
    }
    success "Dependencies installed"
    
    # Create log directory
    mkdir -p "$LOG_DIR"
    
    # Create LaunchAgents directory if needed
    mkdir -p "${HOME}/Library/LaunchAgents"
    
    # Get the Python path from venv
    PYTHON_PATH="${MLX_VENV_DIR}/bin/python3"
    
    # Read token from env file if available
    HOST_AGENT_TOKEN=""
    HOST_AGENT_PORT="8089"
    for env_name in "dev" "demo" "staging" "prod"; do
        env_file="${REPO_ROOT}/.env.${env_name}"
        if [[ -f "$env_file" ]]; then
            HOST_AGENT_TOKEN=$(grep "^HOST_AGENT_TOKEN=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "")
            HOST_AGENT_PORT=$(grep "^HOST_AGENT_PORT=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "8089")
            if [[ -n "$HOST_AGENT_TOKEN" ]]; then
                break
            fi
        fi
    done
    
    # Create plist file
    info "Creating launchd plist..."
    cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${SERVICE_NAME}</string>
    
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_PATH}</string>
        <string>${SCRIPT_DIR}/host-agent.py</string>
    </array>
    
    <key>WorkingDirectory</key>
    <string>${REPO_ROOT}</string>
    
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOST_AGENT_TOKEN</key>
        <string>${HOST_AGENT_TOKEN}</string>
        <key>HOST_AGENT_PORT</key>
        <string>${HOST_AGENT_PORT}</string>
        <key>HOST_AGENT_HOST</key>
        <string>127.0.0.1</string>
    </dict>
    
    <key>RunAtLoad</key>
    <true/>
    
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/host-agent.log</string>
    
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/host-agent.error.log</string>
    
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF
    
    success "Created $PLIST_PATH"
    
    # Unload if already loaded (ignore errors)
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    
    # Load the service
    info "Loading service..."
    local load_result=0
    local load_output
    load_output=$(launchctl load "$PLIST_PATH" 2>&1) || load_result=$?
    
    if [[ $load_result -eq 0 ]]; then
        success "Service loaded"
    elif [[ $load_result -eq 37 ]] || [[ "$load_output" == *"already loaded"* ]]; then
        # Error 37 or "already loaded" message means service already loaded - not a real error
        warn "Service was already loaded, reloading..."
        launchctl unload "$PLIST_PATH" 2>/dev/null || true
        sleep 1
        if launchctl load "$PLIST_PATH"; then
            success "Service reloaded"
        else
            error "Failed to reload service"
            exit 1
        fi
    elif [[ $load_result -eq 22 ]]; then
        # Error 22 (EINVAL) can happen if plist syntax is wrong
        error "Invalid plist syntax (error 22)"
        echo "Checking plist..."
        plutil -lint "$PLIST_PATH" || true
        exit 1
    else
        error "Failed to load service (error code: $load_result)"
        if [[ -n "$load_output" ]]; then
            echo "Output: $load_output"
        fi
        echo "Try checking: launchctl list | grep busibox"
        echo "Or run manually: ${PYTHON_PATH} ${SCRIPT_DIR}/host-agent.py"
        exit 1
    fi
    
    # Wait for it to start
    sleep 2
    
    # Verify it's running
    if launchctl list | grep -q "$SERVICE_NAME"; then
        success "Host agent is running"
        info "Logs: ${LOG_DIR}/host-agent.log"
        info "Port: ${HOST_AGENT_PORT}"
    else
        warn "Service may not be running - check logs: ${LOG_DIR}/host-agent.error.log"
    fi
    
    success "Installation complete!"
}

# Parse arguments
case "${1:-}" in
    --uninstall|-u)
        uninstall
        ;;
    *)
        install
        ;;
esac
