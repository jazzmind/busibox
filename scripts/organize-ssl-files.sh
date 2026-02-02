#!/bin/bash
# Organize SSL files to match expected naming convention
# Usage: bash scripts/organize-ssl-files.sh

set -euo pipefail

cd "$(dirname "$0")/.."
SSL_DIR="ssl"

echo "Organizing SSL files in $SSL_DIR/"
echo

# Rename AI.JAYCASHMAN.COM.crt to ai.jaycashman.com.crt
if [[ -f "$SSL_DIR/AI.JAYCASHMAN.COM.crt" ]]; then
    echo "✓ Renaming AI.JAYCASHMAN.COM.crt → ai.jaycashman.com.crt"
    mv "$SSL_DIR/AI.JAYCASHMAN.COM.crt" "$SSL_DIR/ai.jaycashman.com.crt"
fi

# Verify we have the matching key and fullchain
if [[ -f "$SSL_DIR/ai.jaycashman.com.crt" ]] && [[ -f "$SSL_DIR/ai.jaycashman.com.key" ]]; then
    echo "✓ Found ai.jaycashman.com.crt + ai.jaycashman.com.key (wildcard for *.ai.jaycashman.com)"
fi

if [[ -f "$SSL_DIR/ai.jaycashman.com.fullchain.crt" ]]; then
    echo "✓ Found ai.jaycashman.com.fullchain.crt (recommended for nginx)"
fi

# Check localhost certs (for local development)
if [[ -f "$SSL_DIR/localhost.crt" ]] && [[ -f "$SSL_DIR/localhost.key" ]]; then
    echo "✓ Found localhost.crt + localhost.key (for local dev)"
fi

echo
echo "SSL files organized!"
echo
echo "Current SSL certificates:"
ls -lh "$SSL_DIR"/*.{crt,key} 2>/dev/null || echo "No .crt or .key files found"
