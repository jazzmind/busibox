#!/bin/sh
# =============================================================================
# Busibox Proxy Entrypoint
# =============================================================================
#
# Execution Context:
# - Runs inside Docker proxy container
# - Ensures local TLS certificates exist for nginx
# - Starts nginx in foreground
#
# =============================================================================

set -eu

SSL_DIR="/etc/nginx/ssl"
CERT_FILE="${SSL_DIR}/localhost.crt"
KEY_FILE="${SSL_DIR}/localhost.key"

mkdir -p "${SSL_DIR}"

if [ ! -f "${CERT_FILE}" ] || [ ! -f "${KEY_FILE}" ]; then
  echo "[proxy-entrypoint] Generating self-signed certificate for localhost"
  openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -subj "/C=US/ST=CA/L=San Francisco/O=Busibox/CN=localhost"
fi

echo "[proxy-entrypoint] Validating nginx configuration"
nginx -t

echo "[proxy-entrypoint] Starting nginx"
exec nginx -g 'daemon off;'
