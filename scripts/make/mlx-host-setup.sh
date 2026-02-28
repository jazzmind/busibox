#!/usr/bin/env bash
#
# mlx-host-setup.sh - Run MLX setup on the macOS host
#
# Execution context: macOS host (NOT inside manager container)
# Called by: Makefile install target after manager container exits
#
# This script runs when install.sh detected LLM_BACKEND=mlx inside the
# manager container and wrote a .mlx-setup-needed marker. The Makefile
# picks up the marker and runs this script directly on the host where
# Apple Silicon, Metal, launchd, and pip are available.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec bash "${SCRIPT_DIR}/install.sh" --mlx-host-setup
