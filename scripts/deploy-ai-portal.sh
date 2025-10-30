#!/usr/bin/env bash
#
# Deploy AI Portal to Test Environment
#
# This script deploys the ai-portal Next.js application to the test apps container
# and configures Nginx reverse proxy with SSL.
#
# Prerequisites:
#   - LXC containers created (proxy, apps, pg, litellm, ollama, vllm)
#   - SSL certificate uploaded (see scripts/upload-ssl-cert.sh)
#   - Ansible vault password (if using encrypted secrets)
#
# Usage:
#   bash deploy-ai-portal.sh [--skip-ssl] [--skip-db] [--skip-app]
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }

# Parse arguments
SKIP_SSL=false
SKIP_DB=false
SKIP_APP=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-ssl)
            SKIP_SSL=true
            shift
            ;;
        --skip-db)
            SKIP_DB=true
            shift
            ;;
        --skip-app)
            SKIP_APP=true
            shift
            ;;
        *)
            error "Unknown option: $1"
            exit 1
            ;;
    esac
done

echo "=========================================="
echo "AI Portal Deployment - Test Environment"
echo "=========================================="
echo

# Check if we're on the Proxmox host
if ! command -v pct &> /dev/null; then
    error "This script must be run on the Proxmox host"
    exit 1
fi

# Check if containers exist
info "Checking container status..."
REQUIRED_CONTAINERS=(
    "300:TEST-proxy-lxc"
    "301:TEST-apps-lxc"
    "303:TEST-pg-lxc"
    "307:TEST-litellm-lxc"
)

for container in "${REQUIRED_CONTAINERS[@]}"; do
    CTID="${container%%:*}"
    NAME="${container##*:}"
    
    if ! pct status "$CTID" &>/dev/null; then
        error "Container $NAME (ID: $CTID) not found"
        echo "  Run: bash provision/pct/create_lxc_base.sh test"
        exit 1
    fi
    
    STATUS=$(pct status "$CTID" | awk '{print $2}')
    if [ "$STATUS" != "running" ]; then
        warn "Container $NAME is not running, starting..."
        systemctl start "pve-container@$CTID"
        sleep 3
    fi
    
    success "Container $NAME is running"
done

echo

# Step 1: Deploy PostgreSQL database
if [ "$SKIP_DB" = false ]; then
    info "Step 1: Deploying PostgreSQL database..."
    cd provision/ansible
    
    if ansible-playbook -i inventory/test/hosts.yml site.yml \
        --limit pg \
        --tags postgres; then
        success "PostgreSQL deployed"
    else
        error "PostgreSQL deployment failed"
        exit 1
    fi
    
    cd "$SCRIPT_DIR"
    echo
else
    info "Step 1: Skipping PostgreSQL deployment (--skip-db)"
    echo
fi

# Step 2: Deploy ai-portal application
if [ "$SKIP_APP" = false ]; then
    info "Step 2: Deploying ai-portal application..."
    cd provision/ansible
    
    if ansible-playbook -i inventory/test/hosts.yml site.yml \
        --limit apps \
        --tags nextjs; then
        success "ai-portal deployed"
    else
        error "ai-portal deployment failed"
        exit 1
    fi
    
    cd "$SCRIPT_DIR"
    
    # Wait for app to be healthy
    info "Waiting for ai-portal to be ready..."
    APPS_IP="10.96.201.201"
    MAX_RETRIES=30
    RETRY=0
    
    while [ $RETRY -lt $MAX_RETRIES ]; do
        if curl -sf "http://$APPS_IP:3000/api/health" > /dev/null 2>&1; then
            success "ai-portal is healthy"
            break
        fi
        
        RETRY=$((RETRY + 1))
        if [ $RETRY -eq $MAX_RETRIES ]; then
            error "ai-portal health check failed after $MAX_RETRIES attempts"
            echo "  Check logs: pct exec 301 -- pm2 logs ai-portal"
            exit 1
        fi
        
        sleep 2
    done
    
    echo
else
    info "Step 2: Skipping ai-portal deployment (--skip-app)"
    echo
fi

# Step 3: Configure Nginx reverse proxy with SSL
if [ "$SKIP_SSL" = false ]; then
    info "Step 3: Configuring Nginx reverse proxy..."
    
    # Check if SSL certificates are uploaded
    VAULT_FILE="provision/ansible/roles/secrets/vars/vault.yml"
    if [ ! -f "$VAULT_FILE" ]; then
        warn "SSL certificates not found in vault"
        echo "  Upload certificates with: bash scripts/upload-ssl-cert.sh test.ai.jaycashman.com cert.crt cert.key chain.crt"
        echo "  Or use self-signed certificates for testing"
        read -p "Use self-signed certificates? (yes/no): " USE_SELFSIGNED
        
        if [ "$USE_SELFSIGNED" = "yes" ]; then
            # Update inventory to use selfsigned mode
            PROXY_VARS="provision/ansible/inventory/test/group_vars/proxy.yml"
            if grep -q "ssl_mode:" "$PROXY_VARS"; then
                sed -i.bak 's/ssl_mode: "provisioned"/ssl_mode: "selfsigned"/' "$PROXY_VARS"
                info "Updated SSL mode to selfsigned"
            fi
        else
            error "SSL certificates required for deployment"
            exit 1
        fi
    fi
    
    cd provision/ansible
    
    if ansible-playbook -i inventory/test/hosts.yml site.yml \
        --limit proxy \
        --tags nginx; then
        success "Nginx configured"
    else
        error "Nginx configuration failed"
        exit 1
    fi
    
    cd "$SCRIPT_DIR"
    echo
else
    info "Step 3: Skipping Nginx deployment (--skip-ssl)"
    echo
fi

# Final status check
echo "=========================================="
echo "Deployment Summary"
echo "=========================================="
echo

info "Service Status:"
echo "  PostgreSQL:  $(pct exec 303 -- systemctl is-active postgresql || echo 'not running')"
echo "  ai-portal:   $(pct exec 301 -- su - appuser -c 'pm2 describe ai-portal' &>/dev/null && echo 'running' || echo 'not running')"
echo "  Nginx:       $(pct exec 300 -- systemctl is-active nginx || echo 'not running')"
echo "  LiteLLM:     $(pct exec 307 -- systemctl is-active litellm || echo 'not running')"

echo

info "Access URLs:"
echo "  Internal:  http://10.96.201.201:3000"
echo "  External:  https://test.ai.jaycashman.com"

echo

info "Useful commands:"
echo "  View app logs:     pct exec 301 -- su - appuser -c 'pm2 logs ai-portal'"
echo "  View nginx logs:   pct exec 300 -- tail -f /var/log/nginx/access.log"
echo "  Restart app:       pct exec 301 -- su - appuser -c 'pm2 restart ai-portal'"
echo "  Check SSL:         curl -v https://test.ai.jaycashman.com"

echo

success "AI Portal deployment complete!"

