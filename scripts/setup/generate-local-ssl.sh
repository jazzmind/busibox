#!/usr/bin/env bash
# =============================================================================
# Generate SSL Certificates for Local Development
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Create SSL certificates for local nginx using mkcert (preferred)
#          or fallback to self-signed if mkcert isn't available
#
# Usage:
#   bash scripts/setup/generate-local-ssl.sh
#
# Output:
#   - ssl/localhost.crt - SSL certificate
#   - ssl/localhost.key - SSL private key
#
# mkcert creates locally-trusted certificates (no browser warnings)
# Install mkcert: brew install mkcert (macOS) or see https://github.com/FiloSottile/mkcert
#
# Note: For production, use Let's Encrypt with a real domain
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SSL_DIR="${REPO_ROOT}/ssl"

echo "=== Generating SSL Certificates for Local Development ==="
echo ""

# Create SSL directory if it doesn't exist
mkdir -p "${SSL_DIR}"

# Check if certificates already exist
if [[ -f "${SSL_DIR}/localhost.crt" ]] && [[ -f "${SSL_DIR}/localhost.key" ]]; then
    echo "Certificates already exist at ${SSL_DIR}/"
    echo "  - localhost.crt"
    echo "  - localhost.key"
    echo ""
    echo "To regenerate, delete the existing files first:"
    echo "  rm ${SSL_DIR}/localhost.crt ${SSL_DIR}/localhost.key"
    exit 0
fi

# Try mkcert first (creates locally-trusted certificates - no browser warnings)
if command -v mkcert &> /dev/null; then
    echo "Using mkcert to generate locally-trusted certificates..."
    echo ""
    
    # Install local CA if not already done
    mkcert -install 2>/dev/null || true
    
    # Generate certificate
    cd "${SSL_DIR}"
    mkcert -cert-file localhost.crt -key-file localhost.key localhost 127.0.0.1 ::1
    
    echo ""
    echo "=== SSL Certificates Generated (mkcert - locally trusted) ==="
    echo ""
    echo "Certificate: ${SSL_DIR}/localhost.crt"
    echo "Private Key: ${SSL_DIR}/localhost.key"
    echo ""
    echo "These certificates are trusted by your system - no browser warnings!"
    echo ""
else
    echo "mkcert not found - falling back to self-signed certificate"
    echo ""
    echo "To avoid browser warnings, install mkcert:"
    echo "  macOS:  brew install mkcert"
    echo "  Linux:  See https://github.com/FiloSottile/mkcert#installation"
    echo ""
    echo "Generating self-signed certificate..."
    echo ""
    
    # Generate self-signed certificate
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
        -keyout "${SSL_DIR}/localhost.key" \
        -out "${SSL_DIR}/localhost.crt" \
        -subj "/C=US/ST=Local/L=Development/O=Busibox/OU=Dev/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1"
    
    echo ""
    echo "=== SSL Certificates Generated (self-signed) ==="
    echo ""
    echo "Certificate: ${SSL_DIR}/localhost.crt"
    echo "Private Key: ${SSL_DIR}/localhost.key"
    echo ""
    echo "WARNING: Self-signed certificate - browsers will show security warnings."
    echo ""
    echo "To trust the certificate on macOS:"
    echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ${SSL_DIR}/localhost.crt"
    echo ""
    echo "Or install mkcert for a better experience:"
    echo "  brew install mkcert && rm ${SSL_DIR}/localhost.* && bash $0"
fi

echo "Restart nginx to apply: make docker-restart SERVICE=nginx"
