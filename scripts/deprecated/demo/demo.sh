#!/usr/bin/env bash
# =============================================================================
# Busibox Demo Script
# =============================================================================
#
# Main orchestrator for the Busibox demo. Starts all services and opens
# the browser to the Busibox Portal.
#
# Usage:
#   ./demo.sh
#   make demo
#
# Prerequisites:
#   - Run 'make demo-warmup' first for offline capability
#   - Or run with network access for on-demand downloads
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/progress.sh"
source "${SCRIPT_DIR}/detect-system.sh"
eval "$(${SCRIPT_DIR}/get-models.sh all)"

# Change to repo root for docker compose commands
cd "${REPO_ROOT}"

# Show impressive banner with system info
show_banner "BUSIBOX DEMO" \
    "${DEMO_RAM_GB}GB Unified Memory | ${DEMO_TIER^} Tier | $([[ $DEMO_BACKEND == mlx ]] && echo 'Apple Silicon (MLX)' || echo 'x86/Linux (vLLM)')"

echo ""
echo "Models selected for your system:"
echo "  Fast:     ${DEMO_MODEL_FAST}"
echo "  Agent:    ${DEMO_MODEL_AGENT}"
echo "  Frontier: ${DEMO_MODEL_FRONTIER}"
echo ""
sleep 2

# =============================================================================
# Ensure Configuration
# =============================================================================

# Generate LiteLLM config if needed
if [[ ! -f "${REPO_ROOT}/config/litellm-demo.yaml" ]]; then
    bash "${SCRIPT_DIR}/generate-litellm-config.sh"
fi

# Ensure .env.local exists
if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
    cp "${REPO_ROOT}/config/demo.env" "${REPO_ROOT}/.env.local"
    info "Created .env.local from demo.env"
fi

# =============================================================================
# Start LLM Server
# =============================================================================

if [[ "$DEMO_BACKEND" == "mlx" ]]; then
    show_stage 8 "Starting MLX-LM server" \
        "Local inference on Apple Silicon. Your data never leaves your machine."
    
    bash "${SCRIPT_DIR}/start-mlx-server.sh" &
    
    # Wait for MLX server to be ready
    wait_for_url "http://localhost:8080/v1/models" 120
else
    show_stage 8 "Starting vLLM container" \
        "GPU-accelerated inference. Enterprise-grade throughput."
    
    docker compose -f docker-compose.yml --profile demo-vllm up -d vllm
    wait_for_url "http://localhost:8080/health" 180
fi

# =============================================================================
# Start Infrastructure
# =============================================================================

show_stage 18 "Starting PostgreSQL" \
    "Row-level security on every table. Audit trails built-in."

docker compose -f docker-compose.yml up -d postgres
wait_for_healthy local-postgres 60

show_stage 28 "Starting Milvus vector database" \
    "Enterprise vector search. Find documents by meaning, not keywords."

docker compose -f docker-compose.yml up -d etcd milvus-minio milvus
wait_for_healthy local-milvus 120

show_stage 38 "Starting Redis & MinIO" \
    "Job queues and S3-compatible storage. All local, all secure."

docker compose -f docker-compose.yml up -d redis minio minio-init
wait_for_healthy local-redis 30
wait_for_healthy local-minio 30

# =============================================================================
# Start APIs
# =============================================================================

show_stage 48 "Starting AuthZ service" \
    "OAuth2 + RBAC. Every request authenticated and authorized."

docker compose -f docker-compose.yml up -d authz-api
wait_for_healthy local-authz-api 60

show_stage 56 "Starting Data API" \
    "Automatic PDF extraction, chunking, and semantic embedding."

docker compose -f docker-compose.yml up -d data-api milvus-init
wait_for_healthy local-data-api 180

show_stage 64 "Starting Search API" \
    "Hybrid search: vectors + keywords. Results filtered by permissions."

docker compose -f docker-compose.yml up -d search-api
wait_for_healthy local-search-api 120

show_stage 70 "Starting Data Worker" \
    "Background processing: PDF -> text -> chunks -> vectors"

docker compose -f docker-compose.yml up -d data-worker

show_stage 76 "Starting Agent API" \
    "AI agents with tools. RAG search. Workflow automation."

docker compose -f docker-compose.yml up -d agent-api
wait_for_healthy local-agent-api 60

# =============================================================================
# Start Frontends
# =============================================================================

show_stage 84 "Starting Busibox Portal & Agent Manager" \
    "Beautiful UI. SSO integration. White-label ready."

# Generate SSL if needed
if [[ ! -f "${REPO_ROOT}/ssl/localhost.crt" ]]; then
    bash "${REPO_ROOT}/scripts/setup/generate-local-ssl.sh" || warn "Could not generate SSL"
fi

docker compose -f docker-compose.yml --profile full up -d busibox-portal busibox-agents nginx
wait_for_healthy local-nginx 60

# =============================================================================
# Seed Demo Data
# =============================================================================

show_stage 92 "Ingesting demo documents" \
    "Watch the pipeline: upload -> extract -> chunk -> embed -> search."

if [[ -f "${SCRIPT_DIR}/seed-demo-data.sh" ]]; then
    bash "${SCRIPT_DIR}/seed-demo-data.sh" || warn "Could not seed demo data"
else
    info "No seed script found, skipping demo data"
fi

# =============================================================================
# Complete!
# =============================================================================

show_stage 100 "Demo ready!" \
    "Air-gap capable. Disconnect wifi and everything keeps working."

echo ""
echo "Opening browser..."

# Open browser (works on macOS and Linux)
if [[ "$(uname -s)" == "Darwin" ]]; then
    open "https://localhost/portal" 2>/dev/null || true
else
    xdg-open "https://localhost/portal" 2>/dev/null || true
fi

# Show final dashboard
show_dashboard "${DEMO_TIER}" "${DEMO_RAM_GB}" "${DEMO_MODEL_AGENT}"

# Keep script running to show logs option
echo ""
echo "Press Ctrl+C to exit, or run these commands in another terminal:"
echo ""
echo "  View all logs:    docker compose -f docker-compose.yml logs -f"
echo "  View agent logs:  docker compose -f docker-compose.yml logs -f agent-api"
echo "  Stop demo:        make demo-clean"
echo ""
