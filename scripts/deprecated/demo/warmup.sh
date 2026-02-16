#!/usr/bin/env bash
# =============================================================================
# Busibox Demo Warmup Script
# =============================================================================
#
# Pre-downloads everything needed for offline demo:
# - Private GitHub repositories
# - LLM models (MLX or vLLM depending on architecture)
# - Docker images
# - Embedding models
#
# Usage:
#   ./warmup.sh
#   make demo-warmup
#
# After warmup, the demo can run completely offline.
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

source "${SCRIPT_DIR}/progress.sh"
source "${SCRIPT_DIR}/detect-system.sh"
eval "$(${SCRIPT_DIR}/get-models.sh all)"

show_banner "BUSIBOX DEMO WARMUP" \
    "${DEMO_RAM_GB}GB RAM detected - using ${DEMO_TIER} tier (${DEMO_BACKEND})"

echo ""
echo "Models to download:"
echo "  Fast:     ${DEMO_MODEL_FAST}"
echo "  Agent:    ${DEMO_MODEL_AGENT}"
echo "  Frontier: ${DEMO_MODEL_FRONTIER}"
echo ""
sleep 2

# =============================================================================
# 1. Check GitHub Authentication
# =============================================================================

show_stage 5 "Checking GitHub authentication" \
    "Required for cloning private repositories"

if ! gh auth status &>/dev/null 2>&1; then
    error "GitHub authentication required."
    echo ""
    echo "Please run: gh auth login"
    echo ""
    exit 1
fi
success "GitHub authenticated"

# =============================================================================
# 2. Clone Private Repositories
# =============================================================================

show_stage 12 "Cloning busibox-portal" \
    "Next.js frontend with SSO and document management"

if [[ -d "${REPO_ROOT}/../busibox-portal" ]]; then
    info "busibox-portal exists, pulling latest..."
    (cd "${REPO_ROOT}/../busibox-portal" && git pull --quiet) || warn "Could not pull busibox-portal"
else
    git clone --depth 1 git@github.com:jazzmind/busibox-portal.git "${REPO_ROOT}/../busibox-portal"
fi

show_stage 20 "Cloning busibox-agents" \
    "Agent configuration and workflow management UI"

if [[ -d "${REPO_ROOT}/../busibox-agents" ]]; then
    info "busibox-agents exists, pulling latest..."
    (cd "${REPO_ROOT}/../busibox-agents" && git pull --quiet) || warn "Could not pull busibox-agents"
else
    git clone --depth 1 git@github.com:jazzmind/busibox-agents.git "${REPO_ROOT}/../busibox-agents"
fi

show_stage 28 "Cloning busibox-testdocs" \
    "Sample documents for demo ingestion"

if [[ -d "${REPO_ROOT}/../busibox-testdocs" ]]; then
    info "busibox-testdocs exists, pulling latest..."
    (cd "${REPO_ROOT}/../busibox-testdocs" && git pull --quiet) || warn "Could not pull busibox-testdocs"
else
    git clone --depth 1 git@github.com:jazzmind/busibox-testdocs.git "${REPO_ROOT}/../busibox-testdocs"
fi

# =============================================================================
# 3. Download LLM Models
# =============================================================================

if [[ "$DEMO_BACKEND" == "mlx" ]]; then
    # MLX on Apple Silicon - use virtual environment (PEP 668 compliance)
    MLX_VENV_DIR="${HOME}/.busibox/mlx-venv"
    
    show_stage 33 "Setting up MLX virtual environment" \
        "Creating isolated Python environment for MLX packages"
    
    mkdir -p "${HOME}/.busibox"
    if [[ ! -d "$MLX_VENV_DIR" ]]; then
        python3 -m venv "$MLX_VENV_DIR"
    fi
    
    MLX_PYTHON="${MLX_VENV_DIR}/bin/python3"
    MLX_PIP="${MLX_VENV_DIR}/bin/pip3"
    
    show_stage 35 "Installing MLX-LM" \
        "Apple's native ML framework for optimal Silicon performance"
    
    "$MLX_PIP" install -q mlx-lm huggingface_hub pyyaml
    
    show_stage 42 "Downloading fast model" \
        "Quick responses for simple tasks: ${DEMO_MODEL_FAST}"
    
    "$MLX_PYTHON" -c "from mlx_lm import load; load('${DEMO_MODEL_FAST}')" || \
        warn "Could not download fast model - will try at runtime"
    
    show_stage 52 "Downloading agent model" \
        "Primary model for agent reasoning and tool use: ${DEMO_MODEL_AGENT}"
    
    "$MLX_PYTHON" -c "from mlx_lm import load; load('${DEMO_MODEL_AGENT}')" || \
        error "Could not download agent model"
    
    show_stage 62 "Downloading frontier model" \
        "Best quality for complex analysis: ${DEMO_MODEL_FRONTIER}"
    
    "$MLX_PYTHON" -c "from mlx_lm import load; load('${DEMO_MODEL_FRONTIER}')" || \
        warn "Could not download frontier model - will try at runtime"

else
    # vLLM on x86/Linux
    show_stage 38 "Pulling vLLM image" \
        "GPU-accelerated inference engine"
    
    docker pull vllm/vllm-openai:latest
    
    show_stage 50 "Pre-caching HuggingFace models" \
        "Downloading models for offline use"
    
    # Note: This caches models in Docker volume
    for model in "$DEMO_MODEL_FAST" "$DEMO_MODEL_AGENT" "$DEMO_MODEL_FRONTIER"; do
        info "Caching: $model"
        docker run --rm \
            -v busibox-local_vllm_cache:/root/.cache/huggingface \
            vllm/vllm-openai:latest \
            python -c "from huggingface_hub import snapshot_download; snapshot_download('${model}')" \
            || warn "Could not cache $model"
    done
fi

# =============================================================================
# 4. Generate LiteLLM Configuration
# =============================================================================

show_stage 68 "Generating LiteLLM configuration" \
    "Routing layer connecting services to local models"

bash "${SCRIPT_DIR}/generate-litellm-config.sh"

# =============================================================================
# 5. Copy Demo Environment
# =============================================================================

show_stage 72 "Setting up demo environment" \
    "Zero-config environment with demo defaults"

if [[ ! -f "${REPO_ROOT}/.env.local" ]]; then
    cp "${REPO_ROOT}/config/demo.env" "${REPO_ROOT}/.env.local"
    info "Created .env.local from demo.env"
else
    info ".env.local already exists, skipping"
fi

# =============================================================================
# 6. Build Docker Images
# =============================================================================

show_stage 78 "Building Docker images" \
    "Python APIs, Next.js frontends, and infrastructure"

cd "${REPO_ROOT}"
docker compose -f docker-compose.yml build --quiet

# =============================================================================
# 7. Pull Infrastructure Images
# =============================================================================

show_stage 88 "Pulling infrastructure images" \
    "PostgreSQL, Redis, Milvus, MinIO - all local, all secure"

docker compose -f docker-compose.yml pull --quiet postgres redis minio

# Milvus components
docker compose -f docker-compose.yml pull --quiet etcd milvus-minio milvus || true

# =============================================================================
# 8. Cache Embedding Models
# =============================================================================

show_stage 95 "Caching embedding models" \
    "FastEmbed for efficient document vectorization"

# Start postgres temporarily if needed
docker compose -f docker-compose.yml up -d postgres
sleep 5

# Run data-api briefly to cache models
docker compose -f docker-compose.yml run --rm data-api \
    python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" 2>/dev/null || \
    warn "Could not pre-cache embedding model - will download at runtime"

# Stop postgres
docker compose -f docker-compose.yml down

# =============================================================================
# Complete
# =============================================================================

show_stage 100 "Warmup complete!" \
    "All models cached. Ready for offline demo."

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                      WARMUP COMPLETE                                 ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
printf "${GREEN}║  Tier: %-63s║${NC}\n" "${DEMO_TIER} (${DEMO_RAM_GB}GB RAM)"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}║  Models downloaded:                                                  ║${NC}"
printf "${GREEN}║    Fast:     %-56s║${NC}\n" "${DEMO_MODEL_FAST}"
printf "${GREEN}║    Agent:    %-56s║${NC}\n" "${DEMO_MODEL_AGENT}"
printf "${GREEN}║    Frontier: %-56s║${NC}\n" "${DEMO_MODEL_FRONTIER}"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}║  You can now disconnect wifi and run:                                ║${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}║    ${BOLD}make demo${NC}${GREEN}                                                         ║${NC}"
echo -e "${GREEN}║                                                                      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════════╝${NC}"
echo ""
