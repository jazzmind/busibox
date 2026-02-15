#!/usr/bin/env bash
# Setup Busibox MCP Core Developer Server for Cursor
# Run from busibox root: make mcp  (builds all) or: cd tools/mcp-core-dev && bash setup.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "Building mcp-shared..."
(cd "$REPO_ROOT/tools/mcp-shared" && npm install && npm run build)
echo "Building mcp-core-dev..."
(cd "$SCRIPT_DIR" && npm install && npm run build)

echo ""
echo "Add to Cursor MCP settings:"
echo '  "busibox-core-dev": { "command": "node", "args": ["'$SCRIPT_DIR/dist/index.js'"] }'
echo ""
