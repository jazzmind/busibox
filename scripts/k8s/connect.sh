#!/usr/bin/env bash
# =============================================================================
# Busibox K8s Connect - Local access to K8s Busibox Portal
# =============================================================================
#
# Execution Context: Admin workstation (macOS/Linux)
# Purpose: Establish a local HTTPS tunnel to the K8s cluster, generating
#          SSL certificates and configuring /etc/hosts for seamless access.
#
# What it does:
#   1. Generates SSL cert for the configured domain (mkcert or self-signed)
#   2. Creates a K8s TLS Secret with the cert
#   3. Patches nginx ConfigMap to serve HTTPS
#   4. Restarts nginx pod to pick up changes
#   5. Adds /etc/hosts entry (requires sudo)
#   6. Starts kubectl port-forward in background
#   7. Opens browser to https://<domain>/portal
#
# Usage:
#   bash scripts/k8s/connect.sh                    # Connect with defaults
#   bash scripts/k8s/connect.sh --domain my.local  # Custom domain
#   bash scripts/k8s/connect.sh --port 8443        # Custom local port
#   bash scripts/k8s/connect.sh --disconnect       # Tear down tunnel
#   bash scripts/k8s/connect.sh --status           # Check connection status
#
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library if available
if [[ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    info() { echo "[INFO] $*"; }
    success() { echo "[OK] $*"; }
    error() { echo "[ERROR] $*" >&2; }
    warn() { echo "[WARN] $*"; }
fi

# ============================================================================
# Configuration
# ============================================================================

DOMAIN="${DOMAIN:-busibox.local}"
LOCAL_PORT="${LOCAL_PORT:-443}"
NAMESPACE="busibox"
SSL_DIR="${REPO_ROOT}/ssl/k8s"
PID_FILE="${REPO_ROOT}/.k8s-connect.pid"
KUBECONFIG_DEFAULT="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
KUBECONFIG="${KUBECONFIG:-${KUBECONFIG_DEFAULT}}"
TLS_SECRET_NAME="nginx-tls"

# kubectl with kubeconfig
kctl() {
    kubectl --kubeconfig="$KUBECONFIG" "$@"
}

# ============================================================================
# Argument Parsing
# ============================================================================

DO_CONNECT=true
DO_DISCONNECT=false
DO_STATUS=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)
            DOMAIN="$2"
            shift 2
            ;;
        --port)
            LOCAL_PORT="$2"
            shift 2
            ;;
        --disconnect|--down|--stop)
            DO_CONNECT=false
            DO_DISCONNECT=true
            shift
            ;;
        --status)
            DO_CONNECT=false
            DO_STATUS=true
            shift
            ;;
        --kubeconfig)
            KUBECONFIG="$2"
            shift 2
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --domain DOMAIN   Domain to use (default: busibox.local)"
            echo "  --port PORT       Local port to forward (default: 443)"
            echo "  --disconnect      Tear down the tunnel"
            echo "  --status          Check connection status"
            echo "  --kubeconfig PATH Path to kubeconfig"
            echo ""
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Status
# ============================================================================

check_status() {
    echo ""
    info "=== K8s Connect Status ==="
    echo ""

    # Check PID file
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "  Tunnel:  ACTIVE (PID $pid)"
        else
            echo "  Tunnel:  STALE (PID $pid not running)"
        fi
    else
        echo "  Tunnel:  NOT CONNECTED"
    fi

    # Check /etc/hosts
    if grep -q "$DOMAIN" /etc/hosts 2>/dev/null; then
        echo "  Hosts:   ${DOMAIN} -> 127.0.0.1 (configured)"
    else
        echo "  Hosts:   ${DOMAIN} not in /etc/hosts"
    fi

    # Check TLS secret
    if kctl get secret "$TLS_SECRET_NAME" -n "$NAMESPACE" &>/dev/null; then
        echo "  TLS:     Secret '${TLS_SECRET_NAME}' exists in K8s"
    else
        echo "  TLS:     No TLS secret in K8s"
    fi

    # Check local cert
    if [[ -f "${SSL_DIR}/${DOMAIN}.crt" ]]; then
        local expiry
        expiry=$(openssl x509 -enddate -noout -in "${SSL_DIR}/${DOMAIN}.crt" 2>/dev/null | cut -d= -f2)
        echo "  Cert:    ${SSL_DIR}/${DOMAIN}.crt (expires: ${expiry})"
    else
        echo "  Cert:    Not generated"
    fi

    # Try to reach the service
    if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        if curl -sk "https://${DOMAIN}:${LOCAL_PORT}/health" --connect-timeout 3 &>/dev/null; then
            echo "  Access:  https://${DOMAIN}:${LOCAL_PORT}/portal (reachable)"
        else
            echo "  Access:  https://${DOMAIN}:${LOCAL_PORT}/portal (not responding)"
        fi
    fi

    echo ""
}

# ============================================================================
# Disconnect
# ============================================================================

disconnect() {
    info "Disconnecting K8s tunnel..."

    # Kill port-forward process
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
            # Also kill any child processes (kubectl port-forward spawns children)
            pkill -P "$pid" 2>/dev/null || true
            success "Port-forward stopped (PID $pid)"
        else
            warn "Port-forward process $pid already stopped"
        fi
        rm -f "$PID_FILE"
    else
        # Try to find and kill any kubectl port-forward for our namespace
        pkill -f "kubectl.*port-forward.*${NAMESPACE}.*svc/nginx" 2>/dev/null || true
        warn "No PID file found, killed any matching port-forward processes"
    fi

    echo ""
    info "Tunnel disconnected."
    info "To remove /etc/hosts entry, run:"
    echo "  sudo sed -i '' '/${DOMAIN}/d' /etc/hosts"
    echo ""
}

# ============================================================================
# SSL Certificate Generation
# ============================================================================

generate_ssl_cert() {
    local domain="$1"

    mkdir -p "$SSL_DIR"

    # Check if cert already exists and is valid
    if [[ -f "${SSL_DIR}/${domain}.crt" ]] && [[ -f "${SSL_DIR}/${domain}.key" ]]; then
        # Check expiry (regenerate if < 30 days remaining)
        local expiry_epoch
        expiry_epoch=$(openssl x509 -enddate -noout -in "${SSL_DIR}/${domain}.crt" 2>/dev/null | cut -d= -f2)
        if [[ -n "$expiry_epoch" ]]; then
            local expiry_ts now_ts
            expiry_ts=$(date -j -f "%b %d %T %Y %Z" "$expiry_epoch" "+%s" 2>/dev/null || date -d "$expiry_epoch" "+%s" 2>/dev/null || echo "0")
            now_ts=$(date "+%s")
            local days_left=$(( (expiry_ts - now_ts) / 86400 ))
            if [[ $days_left -gt 30 ]]; then
                info "SSL certificate exists and valid for ${days_left} days"
                return 0
            fi
            warn "SSL certificate expires in ${days_left} days, regenerating..."
        fi
    fi

    # Try mkcert first (creates locally-trusted certs - no browser warnings)
    if command -v mkcert &>/dev/null; then
        info "Generating locally-trusted certificate with mkcert..."

        # Install local CA if not already done
        mkcert -install 2>/dev/null || true

        # Generate cert
        mkcert -cert-file "${SSL_DIR}/${domain}.crt" \
               -key-file "${SSL_DIR}/${domain}.key" \
               "$domain" "*.${domain}" localhost 127.0.0.1 ::1

        success "SSL certificate generated (mkcert - trusted by your browser)"
    else
        info "mkcert not found, generating self-signed certificate..."
        warn "Install mkcert for a better experience: brew install mkcert"

        openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
            -keyout "${SSL_DIR}/${domain}.key" \
            -out "${SSL_DIR}/${domain}.crt" \
            -subj "/C=US/ST=Local/L=Development/O=Busibox/OU=K8s/CN=${domain}" \
            -addext "subjectAltName=DNS:${domain},DNS:*.${domain},DNS:localhost,IP:127.0.0.1" \
            2>/dev/null

        success "SSL certificate generated (self-signed)"
        echo ""
        warn "Your browser will show a security warning with self-signed certs."
        info "To trust it on macOS:"
        echo "  sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ${SSL_DIR}/${domain}.crt"
        echo ""
        info "Or install mkcert for automatic trust: brew install mkcert"
    fi
}

# ============================================================================
# K8s TLS Secret
# ============================================================================

create_tls_secret() {
    local domain="$1"

    info "Creating TLS secret in K8s..."

    # Delete existing secret if present
    kctl delete secret "$TLS_SECRET_NAME" -n "$NAMESPACE" 2>/dev/null || true

    # Create new TLS secret
    kctl create secret tls "$TLS_SECRET_NAME" \
        -n "$NAMESPACE" \
        --cert="${SSL_DIR}/${domain}.crt" \
        --key="${SSL_DIR}/${domain}.key"

    success "TLS secret '${TLS_SECRET_NAME}' created in namespace '${NAMESPACE}'"
}

# ============================================================================
# Nginx HTTPS Configuration
# ============================================================================

patch_nginx_for_tls() {
    local domain="$1"

    info "Patching nginx configuration for HTTPS..."

    # Get the current proxy ConfigMap
    local current_config
    current_config=$(kctl get configmap proxy-config -n "$NAMESPACE" -o jsonpath='{.data.nginx\.conf}' 2>/dev/null)

    if [[ -z "$current_config" ]]; then
        error "Could not read proxy-config ConfigMap"
        return 1
    fi

    # Check if HTTPS is already configured
    if echo "$current_config" | grep -q "listen 443 ssl"; then
        info "Nginx already configured for HTTPS, updating..."
    fi

    # Build the HTTPS server block by extracting locations from existing config
    # We extract everything between the first 'server {' and the matching '}'
    local locations
    locations=$(echo "$current_config" | sed -n '/location/,/^        }/p')

    # Create the patched nginx.conf with both HTTP redirect and HTTPS server
    local patched_config
    patched_config=$(cat <<'NGINX_CONF'
worker_processes auto;
error_log /var/log/nginx/error.log warn;
pid /var/run/nginx.pid;

events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    sendfile on;
    keepalive_timeout 65;
    client_max_body_size 500M;

    # Upstream definitions
    upstream authz_api { server authz-api:8010; }
    upstream data_api { server data-api:8002; }
    upstream search_api { server search-api:8003; }
    upstream agent_api { server agent-api:8000; }
    upstream docs_api { server docs-api:8004; }
    upstream bridge_api { server bridge-api:8081; }
    upstream litellm { server litellm:4000; }
    upstream embedding_api { server embedding-api:8005; }
    upstream deploy_api { server deploy-api:8011; }
    upstream ai_portal { server busibox-portal:3000; }
    upstream agent_manager { server busibox-agents:3001; }

    # HTTP -> HTTPS redirect
    server {
        listen 80;
        server_name _;

        # Health check (keep on HTTP for K8s probes)
        location /health {
            return 200 'OK';
            add_header Content-Type text/plain;
        }

        # Redirect everything else to HTTPS
        location / {
            return 301 https://$host$request_uri;
        }
    }

    # HTTPS server
    server {
        listen 443 ssl;
        server_name DOMAIN_PLACEHOLDER _;

        ssl_certificate /etc/nginx/tls/tls.crt;
        ssl_certificate_key /etc/nginx/tls/tls.key;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;
        ssl_prefer_server_ciphers on;

        # Health check
        location /health {
            return 200 'OK';
            add_header Content-Type text/plain;
        }

        # AuthZ API
        location /auth/ {
            proxy_pass http://authz_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Data API
        location /data/ {
            proxy_pass http://data_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Search API
        location /search/ {
            proxy_pass http://search_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Agent API
        location /agent/ {
            proxy_pass http://agent_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            # SSE support
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 300;
        }

        # Docs API
        location /docs/ {
            proxy_pass http://docs_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # Bridge API
        location /bridge/ {
            proxy_pass http://bridge_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # LiteLLM
        location /llm/ {
            proxy_pass http://litellm/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_read_timeout 120;
        }

        # Busibox Portal (core app)
        location /portal/ {
            proxy_pass http://ai_portal/portal/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Agent Manager (core app)
        location /agents/ {
            proxy_pass http://agent_manager/agents/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            # SSE support for agent streaming
            proxy_buffering off;
            proxy_cache off;
            proxy_read_timeout 300;
        }

        # Deploy API
        location /deploy/ {
            proxy_pass http://deploy_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }

        # Embedding API
        location /embedding/ {
            proxy_pass http://embedding_api/;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }

        # Root redirect to portal
        location / {
            return 301 https://$host/portal/;
        }
    }
}
NGINX_CONF
)

    # Replace domain placeholder
    patched_config="${patched_config//DOMAIN_PLACEHOLDER/$domain}"

    # Apply the patched ConfigMap
    # We use kubectl create configmap --dry-run + apply to update
    echo "$patched_config" | kctl create configmap proxy-config \
        -n "$NAMESPACE" \
        --from-file=nginx.conf=/dev/stdin \
        --dry-run=client -o yaml | kctl apply -f -

    success "Proxy ConfigMap patched with HTTPS configuration"
}

# ============================================================================
# Restart Nginx
# ============================================================================

restart_nginx() {
    info "Restarting nginx to pick up TLS cert and HTTPS config..."

    # The nginx Deployment already has an optional TLS volume mount
    # (see k8s/base/frontend/nginx-ingress.yaml) so we just need a restart
    kctl rollout restart deployment/nginx -n "$NAMESPACE"

    info "Waiting for nginx to restart..."
    kctl rollout status deployment/nginx -n "$NAMESPACE" --timeout=60s
    success "Nginx restarted with TLS"
}

# ============================================================================
# /etc/hosts Management
# ============================================================================

configure_hosts() {
    local domain="$1"

    if grep -q "127.0.0.1.*${domain}" /etc/hosts 2>/dev/null; then
        info "/etc/hosts already has entry for ${domain}"
        return 0
    fi

    info "Adding ${domain} to /etc/hosts (requires sudo)..."
    echo ""
    echo "  Will add: 127.0.0.1  ${domain}"
    echo ""

    # Use sudo to add the entry
    if sudo sh -c "echo '127.0.0.1  ${domain}  # busibox k8s connect' >> /etc/hosts"; then
        success "/etc/hosts updated"
    else
        error "Failed to update /etc/hosts"
        echo ""
        echo "Please add manually:"
        echo "  echo '127.0.0.1  ${domain}  # busibox k8s connect' | sudo tee -a /etc/hosts"
        return 1
    fi
}

# ============================================================================
# Port Forward
# ============================================================================

start_port_forward() {
    local port="$1"

    # Kill any existing port-forward
    if [[ -f "$PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        kill "$old_pid" 2>/dev/null || true
        pkill -P "$old_pid" 2>/dev/null || true
        rm -f "$PID_FILE"
    fi

    # Also kill any stale port-forward processes for our namespace
    pkill -f "kubectl.*port-forward.*${NAMESPACE}.*svc/nginx" 2>/dev/null || true
    sleep 1

    info "Starting port-forward (local :${port} -> K8s nginx :443)..."

    # Check if port is in use
    if lsof -i ":${port}" &>/dev/null; then
        local existing_proc
        existing_proc=$(lsof -i ":${port}" -t 2>/dev/null | head -1)
        if [[ -n "$existing_proc" ]]; then
            warn "Port ${port} is already in use by PID ${existing_proc}"
            if [[ "$port" == "443" ]]; then
                info "Trying port 8443 instead..."
                port=8443
                LOCAL_PORT=8443
                if lsof -i ":${port}" &>/dev/null; then
                    error "Port 8443 also in use. Free up a port or specify --port <port>"
                    return 1
                fi
            else
                error "Port ${port} is in use. Specify a different port with --port <port>"
                return 1
            fi
        fi
    fi

    # Port 443 requires sudo on macOS/Linux
    if [[ "$port" -lt 1024 ]]; then
        info "Port ${port} requires elevated privileges..."
        # Use sudo kubectl port-forward in background
        sudo -E kubectl --kubeconfig="$KUBECONFIG" port-forward \
            -n "$NAMESPACE" svc/nginx "${port}:443" \
            --address=127.0.0.1 &>/dev/null &
        local pf_pid=$!
    else
        kubectl --kubeconfig="$KUBECONFIG" port-forward \
            -n "$NAMESPACE" svc/nginx "${port}:443" \
            --address=127.0.0.1 &>/dev/null &
        local pf_pid=$!
    fi

    echo "$pf_pid" > "$PID_FILE"

    # Wait for port-forward to establish
    local retries=0
    while [[ $retries -lt 10 ]]; do
        sleep 1
        if curl -sk "https://127.0.0.1:${port}/health" --connect-timeout 2 &>/dev/null; then
            success "Port-forward established (PID $pf_pid)"
            return 0
        fi
        retries=$((retries + 1))
    done

    # Check if process is still running
    if ! kill -0 "$pf_pid" 2>/dev/null; then
        error "Port-forward process died. Check kubectl connectivity."
        rm -f "$PID_FILE"
        return 1
    fi

    warn "Port-forward started but health check not responding yet (PID $pf_pid)"
    warn "It may take a moment for nginx to be ready"
    return 0
}

# ============================================================================
# Connect (Main Flow)
# ============================================================================

connect() {
    echo ""
    info "=== Busibox K8s Connect ==="
    info "Domain: ${DOMAIN}"
    info "Port:   ${LOCAL_PORT}"
    echo ""

    # Verify kubeconfig exists
    if [[ ! -f "$KUBECONFIG" ]]; then
        error "Kubeconfig not found: ${KUBECONFIG}"
        echo ""
        echo "Place your Rackspace Spot kubeconfig at:"
        echo "  ${KUBECONFIG_DEFAULT}"
        echo ""
        echo "Or specify with --kubeconfig <path>"
        exit 1
    fi

    # Verify cluster connectivity
    info "Verifying cluster connectivity..."
    if ! kctl cluster-info &>/dev/null; then
        error "Cannot connect to K8s cluster. Check your kubeconfig."
        exit 1
    fi
    success "Cluster connected"

    # Verify nginx is running
    if ! kctl get deployment nginx -n "$NAMESPACE" &>/dev/null; then
        error "Nginx deployment not found in namespace '${NAMESPACE}'"
        error "Deploy first with: make k8s-deploy"
        exit 1
    fi

    # Step 1: Generate SSL cert
    info ""
    info "--- Step 1: SSL Certificate ---"
    generate_ssl_cert "$DOMAIN"

    # Step 2: Create K8s TLS secret
    info ""
    info "--- Step 2: K8s TLS Secret ---"
    create_tls_secret "$DOMAIN"

    # Step 3: Patch nginx ConfigMap for HTTPS
    info ""
    info "--- Step 3: Nginx HTTPS Configuration ---"
    patch_nginx_for_tls "$DOMAIN"

    # Step 4: Restart nginx to pick up changes
    info ""
    info "--- Step 4: Restart Nginx ---"
    restart_nginx

    # Step 5: Configure /etc/hosts
    info ""
    info "--- Step 5: /etc/hosts ---"
    configure_hosts "$DOMAIN"

    # Step 6: Start port-forward
    info ""
    info "--- Step 6: Port Forward ---"
    start_port_forward "$LOCAL_PORT"

    # Build the URL
    local url="https://${DOMAIN}"
    if [[ "$LOCAL_PORT" != "443" ]]; then
        url="https://${DOMAIN}:${LOCAL_PORT}"
    fi

    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║                    K8s Connect Ready                        ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║                                                             ║"
    echo "║  Busibox Portal:  ${url}/portal/"
    echo "║  Agents:     ${url}/agents/"
    echo "║                                                             ║"
    echo "║  Disconnect: make disconnect                                ║"
    echo "║  Status:     make k8s-connect-status                        ║"
    echo "║                                                             ║"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""

    # Try to open browser
    if command -v open &>/dev/null; then
        info "Opening browser..."
        open "${url}/portal/" 2>/dev/null || true
    elif command -v xdg-open &>/dev/null; then
        info "Opening browser..."
        xdg-open "${url}/portal/" 2>/dev/null || true
    fi

    success "Connected! The tunnel runs in the background."
    info "Run 'make disconnect' to stop the tunnel."
}

# ============================================================================
# Main
# ============================================================================

if $DO_STATUS; then
    check_status
    exit 0
fi

if $DO_DISCONNECT; then
    disconnect
    exit 0
fi

if $DO_CONNECT; then
    connect
fi
