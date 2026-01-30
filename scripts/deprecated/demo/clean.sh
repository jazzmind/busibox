#!/usr/bin/env bash
# =============================================================================
# Clean Demo Environment
# =============================================================================
#
# Stops all demo services and optionally removes data volumes.
#
# Usage:
#   ./clean.sh           # Stop services, keep data
#   ./clean.sh --all     # Stop services and remove all data
#   make demo-clean      # Same as above
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/progress.sh"

cd "${REPO_ROOT}"

REMOVE_DATA=false
if [[ "${1:-}" == "--all" || "${1:-}" == "-a" ]]; then
    REMOVE_DATA=true
fi

echo ""
echo "Stopping Busibox Demo..."
echo ""

# =============================================================================
# Stop MLX Server (if running)
# =============================================================================

MLX_PID_FILE="/tmp/mlx-lm-server.pid"
if [[ -f "$MLX_PID_FILE" ]]; then
    PID=$(cat "$MLX_PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        info "Stopping MLX-LM server (PID: $PID)..."
        kill "$PID" 2>/dev/null || true
        rm -f "$MLX_PID_FILE"
        success "MLX-LM server stopped"
    else
        rm -f "$MLX_PID_FILE"
    fi
fi

# =============================================================================
# Stop Docker Services
# =============================================================================

info "Stopping Docker services..."

if [[ "$REMOVE_DATA" == true ]]; then
    warn "Removing all data volumes..."
    docker compose -f docker-compose.yml --profile full --profile demo-vllm down -v --remove-orphans
    success "All services stopped and data removed"
else
    docker compose -f docker-compose.yml --profile full --profile demo-vllm down --remove-orphans
    success "All services stopped (data preserved)"
fi

# =============================================================================
# Clean up generated files (optional)
# =============================================================================

if [[ "$REMOVE_DATA" == true ]]; then
    info "Cleaning up generated configuration..."
    rm -f "${REPO_ROOT}/config/litellm-demo.yaml"
    rm -f "/tmp/mlx-lm-server.log"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                       DEMO CLEANED                                   ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
if [[ "$REMOVE_DATA" == true ]]; then
echo -e "${GREEN}║  All services stopped and data volumes removed.                      ║${NC}"
else
echo -e "${GREEN}║  All services stopped. Data volumes preserved.                       ║${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}║  To remove data: make demo-clean ARGS=--all                          ║${NC}"
fi
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}║  To restart:     make demo                                           ║${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
