#!/usr/bin/env bash
# =============================================================================
# Generate LiteLLM Configuration from Template
# =============================================================================
#
# Reads the LiteLLM demo config template and substitutes model names
# based on detected system configuration.
#
# Usage:
#   ./generate-litellm-config.sh
#
# Output:
#   Creates config/litellm-demo.yaml with appropriate model names
#
# =============================================================================

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source system detection and get models
source "${SCRIPT_DIR}/detect-system.sh"
eval "$(${SCRIPT_DIR}/get-models.sh all)"

TEMPLATE="${REPO_ROOT}/config/litellm-demo.yaml.template"
OUTPUT="${REPO_ROOT}/config/litellm-demo.yaml"

if [[ ! -f "$TEMPLATE" ]]; then
    echo "ERROR: Template not found: $TEMPLATE" >&2
    exit 1
fi

# Generate config from template
sed -e "s|{{DEMO_RAM_GB}}|${DEMO_RAM_GB}|g" \
    -e "s|{{DEMO_TIER}}|${DEMO_TIER}|g" \
    -e "s|{{DEMO_BACKEND}}|${DEMO_BACKEND}|g" \
    -e "s|{{DEMO_MODEL_FAST}}|${DEMO_MODEL_FAST}|g" \
    -e "s|{{DEMO_MODEL_AGENT}}|${DEMO_MODEL_AGENT}|g" \
    -e "s|{{DEMO_MODEL_FRONTIER}}|${DEMO_MODEL_FRONTIER}|g" \
    "$TEMPLATE" \
    > "$OUTPUT"

echo "Generated ${OUTPUT} for ${DEMO_TIER} tier (${DEMO_RAM_GB}GB RAM)"
echo "  Fast:     ${DEMO_MODEL_FAST}"
echo "  Agent:    ${DEMO_MODEL_AGENT}"
echo "  Frontier: ${DEMO_MODEL_FRONTIER}"
