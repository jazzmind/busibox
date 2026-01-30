#!/usr/bin/env bash
# =============================================================================
# Check Prerequisites for Busibox Demo
# =============================================================================
#
# Validates that all required tools and resources are available before
# running the demo.
#
# Usage:
#   ./check-prereqs.sh
#
# Exit codes:
#   0 - All prerequisites met
#   1 - Missing prerequisites
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

source "${SCRIPT_DIR}/progress.sh"
source "${SCRIPT_DIR}/detect-system.sh"

ERRORS=0

echo ""
echo "Checking prerequisites for Busibox Demo..."
echo ""

# =============================================================================
# Docker
# =============================================================================

echo -n "  Docker: "
if command -v docker &>/dev/null; then
    if docker info &>/dev/null; then
        echo -e "${GREEN}OK${NC} ($(docker --version | cut -d' ' -f3 | tr -d ','))"
    else
        echo -e "${RED}NOT RUNNING${NC}"
        error "Docker is installed but not running. Start Docker Desktop."
        ERRORS=$((ERRORS + 1))
    fi
else
    echo -e "${RED}NOT FOUND${NC}"
    error "Docker is required. Install Docker Desktop from https://docker.com"
    ERRORS=$((ERRORS + 1))
fi

# =============================================================================
# Docker Compose
# =============================================================================

echo -n "  Docker Compose: "
if docker compose version &>/dev/null; then
    echo -e "${GREEN}OK${NC} ($(docker compose version --short))"
else
    echo -e "${RED}NOT FOUND${NC}"
    error "Docker Compose is required. It should be included with Docker Desktop."
    ERRORS=$((ERRORS + 1))
fi

# =============================================================================
# Python (for MLX on Apple Silicon)
# =============================================================================

if [[ "$DEMO_BACKEND" == "mlx" ]]; then
    echo -n "  Python 3.11+: "
    if command -v python3 &>/dev/null; then
        PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
        PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
        if [[ $PY_MAJOR -ge 3 && $PY_MINOR -ge 11 ]]; then
            echo -e "${GREEN}OK${NC} ($PY_VERSION)"
        else
            echo -e "${YELLOW}WARNING${NC} ($PY_VERSION - recommend 3.11+)"
        fi
    else
        echo -e "${RED}NOT FOUND${NC}"
        error "Python 3.11+ is required for MLX on Apple Silicon."
        ERRORS=$((ERRORS + 1))
    fi
    
    echo -n "  pip: "
    if command -v pip3 &>/dev/null; then
        echo -e "${GREEN}OK${NC}"
    else
        echo -e "${RED}NOT FOUND${NC}"
        error "pip is required. Install with: python3 -m ensurepip"
        ERRORS=$((ERRORS + 1))
    fi
fi

# =============================================================================
# Git
# =============================================================================

echo -n "  Git: "
if command -v git &>/dev/null; then
    echo -e "${GREEN}OK${NC} ($(git --version | cut -d' ' -f3))"
else
    echo -e "${RED}NOT FOUND${NC}"
    error "Git is required."
    ERRORS=$((ERRORS + 1))
fi

# =============================================================================
# GitHub CLI (for warmup)
# =============================================================================

echo -n "  GitHub CLI: "
if command -v gh &>/dev/null; then
    if gh auth status &>/dev/null 2>&1; then
        echo -e "${GREEN}OK${NC} (authenticated)"
    else
        echo -e "${YELLOW}NOT AUTHENTICATED${NC}"
        warn "Run 'gh auth login' before 'make demo-warmup'"
    fi
else
    echo -e "${YELLOW}NOT FOUND${NC}"
    warn "GitHub CLI recommended for warmup. Install from https://cli.github.com"
fi

# =============================================================================
# RAM
# =============================================================================

echo -n "  RAM: "
echo -e "${GREEN}${DEMO_RAM_GB}GB${NC} (${DEMO_TIER} tier)"

if [[ $DEMO_RAM_GB -lt 16 ]]; then
    error "Minimum 16GB RAM required for demo."
    ERRORS=$((ERRORS + 1))
fi

# =============================================================================
# Disk Space
# =============================================================================

echo -n "  Disk space: "
if [[ "$(uname -s)" == "Darwin" ]]; then
    # macOS
    FREE_GB=$(df -g . | tail -1 | awk '{print $4}')
else
    # Linux
    FREE_GB=$(df -BG . | tail -1 | awk '{print $4}' | tr -d 'G')
fi

if [[ $FREE_GB -ge 20 ]]; then
    echo -e "${GREEN}${FREE_GB}GB free${NC}"
else
    echo -e "${YELLOW}${FREE_GB}GB free${NC}"
    warn "Recommend at least 20GB free disk space."
fi

# =============================================================================
# Architecture
# =============================================================================

echo -n "  Architecture: "
if [[ "$DEMO_BACKEND" == "mlx" ]]; then
    echo -e "${GREEN}Apple Silicon (MLX)${NC}"
else
    echo -e "${GREEN}x86_64/Linux (vLLM)${NC}"
    
    # Check for NVIDIA GPU on Linux
    if command -v nvidia-smi &>/dev/null; then
        echo -n "  NVIDIA GPU: "
        GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
        echo -e "${GREEN}OK${NC} ($GPU_NAME)"
    else
        echo -n "  NVIDIA GPU: "
        echo -e "${RED}NOT FOUND${NC}"
        error "NVIDIA GPU with CUDA required for vLLM."
        ERRORS=$((ERRORS + 1))
    fi
fi

# =============================================================================
# Summary
# =============================================================================

echo ""
if [[ $ERRORS -eq 0 ]]; then
    success "All prerequisites met!"
    echo ""
    exit 0
else
    error "$ERRORS prerequisite(s) not met."
    echo ""
    exit 1
fi
