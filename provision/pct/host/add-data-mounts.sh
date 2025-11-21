#!/usr/bin/env bash
#
# Add bind mounts for persistent data storage to LXC containers
# This script adds mount points for ZFS datasets created by setup-proxmox-host.sh
#
# NOTE: As of 2025-11-04, data mounts are automatically added during container
# creation by create_lxc_base.sh. This script is kept for:
# - Manual mount management
# - Debugging mount issues
# - Adding mounts to existing containers created before automation
#
# Usage:
#   bash add-data-mounts.sh [test|production]
#
set -e

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine mode
MODE="${1:-production}"

# Load variables from parent directory (provision/pct/)
PCT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
if [[ "$MODE" == "test" ]]; then
    source "${PCT_DIR}/test-vars.env"
    CT_PG="$CT_PG_TEST"
    CT_FILES="$CT_FILES_TEST"
    CT_MILVUS="$CT_MILVUS_TEST"
    CT_VLLM="$CT_VLLM_TEST"
    CT_OLLAMA="$CT_OLLAMA_TEST"
else
    source "${PCT_DIR}/vars.env"
fi

echo "=========================================="
echo "Adding Data Bind Mounts to LXC Containers"
echo "Mode: ${MODE}"
echo "=========================================="
echo ""

# Function to add mount point if not already present
add_mount() {
    local ctid=$1
    local host_path=$2
    local container_path=$3
    local mp_num=$4
    
    local config_file="/etc/pve/lxc/${ctid}.conf"
    
    if [[ ! -f "$config_file" ]]; then
        echo "  ⚠ Container $ctid not found, skipping"
        return
    fi
    
    # Check if mount already exists
    if grep -q "mp${mp_num}:" "$config_file"; then
        echo "  ✓ Mount point mp${mp_num} already configured for container $ctid"
        return
    fi
    
    # Check if host path exists
    if [[ ! -d "$host_path" ]]; then
        echo "  ⚠ Host path $host_path does not exist, skipping"
        echo "    Run: bash provision/pct/setup-proxmox-host.sh first"
        return
    fi
    
    # Add mount point with proper options
    echo "mp${mp_num}: ${host_path},mp=${container_path},backup=0,replicate=0" >> "$config_file"
    echo "  ✓ Added bind mount to container $ctid:"
    echo "    Host: ${host_path} -> Container: ${container_path}"
}

# Add mount for PostgreSQL container
echo "PostgreSQL Container (${CT_PG}):"
add_mount "$CT_PG" "/var/lib/data/postgres" "/var/lib/postgresql/data" "0"
echo ""

# Add mount for MinIO (files) container  
echo "MinIO Container (${CT_FILES}):"
add_mount "$CT_FILES" "/var/lib/data/minio" "/srv/minio/data" "0"
echo ""

# Add mount for Milvus container
echo "Milvus Container (${CT_MILVUS}):"
add_mount "$CT_MILVUS" "/var/lib/data/milvus" "/srv/milvus/data" "0"
echo ""

# Add mount for vLLM container (HuggingFace model cache)
echo "vLLM Container (${CT_VLLM}):"
add_mount "$CT_VLLM" "/var/lib/llm-models/huggingface" "/var/lib/llm-models/huggingface" "0"
echo ""

# Add mount for Ollama container (if not using vLLM only)
# echo "Ollama Container (${CT_OLLAMA}):"
# add_mount "$CT_OLLAMA" "/var/lib/llm-models/ollama" "/var/lib/ollama/models" "0"
# echo ""

echo "=========================================="
echo "Bind Mounts Configuration Complete"
echo "=========================================="
echo ""
echo "⚠ Container restart required for changes to take effect"
echo ""
echo "To apply changes:"
if [[ "$MODE" == "test" ]]; then
    echo "  pct stop ${CT_PG} && pct start ${CT_PG}"
    echo "  pct stop ${CT_FILES} && pct start ${CT_FILES}"
    echo "  pct stop ${CT_MILVUS} && pct start ${CT_MILVUS}"
    echo "  pct stop ${CT_VLLM} && pct start ${CT_VLLM}"
else
    echo "  pct stop ${CT_PG} && pct start ${CT_PG}"
    echo "  pct stop ${CT_FILES} && pct start ${CT_FILES}"
    echo "  pct stop ${CT_MILVUS} && pct start ${CT_MILVUS}"
    echo "  pct stop ${CT_VLLM} && pct start ${CT_VLLM}"
fi
echo ""
echo "Or restart all containers:"
if [[ "$MODE" == "test" ]]; then
    echo "  for ct in ${CT_PG} ${CT_FILES} ${CT_MILVUS} ${CT_VLLM}; do pct stop \$ct; pct start \$ct; done"
else
    echo "  for ct in ${CT_PG} ${CT_FILES} ${CT_MILVUS} ${CT_VLLM}; do pct stop \$ct; pct start \$ct; done"
fi
echo ""

