#!/usr/bin/env bash
# =============================================================================
# Generate Self-Signed SSL Certificates for Local Development
# =============================================================================
#
# Execution Context: Admin workstation
# Purpose: Create self-signed SSL certificates for local nginx
#
# Usage:
#   bash scripts/setup/generate-local-ssl.sh
#
# Output:
#   - ssl/localhost.crt - SSL certificate
#   - ssl/localhost.key - SSL private key
#
# Note: For production, use LetsEncrypt instead
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SSL_DIR="${REPO_ROOT}/ssl"

echo "=== Generating Self-Signed SSL Certificates ==="
echo ""

# Create SSL directory if it doesn't exist
mkdir -p "${SSL_DIR}"

# Check if certificates already exist
if [[ -f "${SSL_DIR}/localhost.crt" ]] && [[ -f "${SSL_DIR}/localhost.key" ]]; then
    echo "Certificates already exist at ${SSL_DIR}/"
    echo "To regenerate, delete the existing files first:"
    echo "  rm ${SSL_DIR}/localhost.crt ${SSL_DIR}/localhost.key"
    echo ""
    read -p "Regenerate certificates? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Keeping existing certificates."
        exit 0
    fi
fi

echo "Generating self-signed certificate for localhost..."
echo ""

# Generate private key and certificate
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "${SSL_DIR}/localhost.key" \
    -out "${SSL_DIR}/localhost.crt" \
    -subj "/C=US/ST=Local/L=Development/O=Busibox/OU=Dev/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:*.localhost,IP:127.0.0.1"

echo ""
echo "=== SSL Certificates Generated ==="
echo ""
echo "Certificate: ${SSL_DIR}/localhost.crt"
echo "Private Key: ${SSL_DIR}/localhost.key"
echo ""
echo "To trust the certificate on macOS:"
echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ${SSL_DIR}/localhost.crt"
echo ""
echo "To trust the certificate on Linux:"
echo "  sudo cp ${SSL_DIR}/localhost.crt /usr/local/share/ca-certificates/"
echo "  sudo update-ca-certificates"
echo ""
echo "Restart nginx to apply: docker compose -f docker-compose.local.yml restart nginx"
