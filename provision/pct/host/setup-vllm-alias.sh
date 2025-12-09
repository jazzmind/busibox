#!/bin/bash
#
# Setup vLLM IP Aliasing for Test Environment
#
# EXECUTION CONTEXT: Proxmox host (as root)
# PURPOSE: Configure production vLLM containers to also respond to test IP addresses
#
# This allows the test environment to use production vLLM/ColPali without
# deploying separate GPU containers, saving resources.
#
# Usage:
#   bash setup-vllm-alias.sh enable   # Enable test->production aliasing
#   bash setup-vllm-alias.sh disable  # Remove aliases
#   bash setup-vllm-alias.sh status   # Show current status
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

# IP Configuration
# Production IPs (10.96.200.x)
PROD_VLLM_IP="10.96.200.208"
PROD_LITELLM_IP="10.96.200.207"
PROD_OLLAMA_IP="10.96.200.209"

# Test IPs (10.96.201.x)
TEST_VLLM_IP="10.96.201.208"
TEST_LITELLM_IP="10.96.201.207"
TEST_OLLAMA_IP="10.96.201.209"

# Container IDs
PROD_VLLM_CTID="208"
PROD_OLLAMA_CTID="209"

# Test container IDs (production ID + 100)
TEST_VLLM_CTID="218"
TEST_OLLAMA_CTID="220"

# Network interface inside container (usually eth0)
CONTAINER_IFACE="eth0"

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Check if running on Proxmox
check_proxmox() {
    if ! command -v pct &>/dev/null; then
        error "This script must run on a Proxmox host"
        exit 1
    fi
}

# Check if container exists
container_exists() {
    local ctid="$1"
    pct config "$ctid" &>/dev/null
}

# Stop a container if running
stop_container() {
    local ctid="$1"
    local name="$2"
    
    if ! container_exists "$ctid"; then
        info "Container $ctid ($name) does not exist, skipping"
        return 0
    fi
    
    local status=$(pct status "$ctid" 2>/dev/null | awk '{print $2}')
    
    if [ "$status" = "running" ]; then
        info "Stopping container $ctid ($name) to avoid IP conflict..."
        pct stop "$ctid" --timeout 30 2>/dev/null || {
            warn "Failed to gracefully stop $ctid, forcing shutdown..."
            pct stop "$ctid" --force 2>/dev/null || {
                error "Failed to stop container $ctid"
                return 1
            }
        }
        success "  Stopped container $ctid ($name)"
    else
        info "Container $ctid ($name) is not running (status: $status)"
    fi
    
    return 0
}

# Start a container
start_container() {
    local ctid="$1"
    local name="$2"
    
    if ! container_exists "$ctid"; then
        info "Container $ctid ($name) does not exist, skipping"
        return 0
    fi
    
    local status=$(pct status "$ctid" 2>/dev/null | awk '{print $2}')
    
    if [ "$status" != "running" ]; then
        info "Starting container $ctid ($name)..."
        pct start "$ctid" 2>/dev/null || {
            error "Failed to start container $ctid"
            return 1
        }
        success "  Started container $ctid ($name)"
    else
        info "Container $ctid ($name) is already running"
    fi
    
    return 0
}

# Check if container exists and is running
check_container() {
    local ctid="$1"
    if ! pct status "$ctid" &>/dev/null; then
        return 1
    fi
    local status=$(pct status "$ctid" | awk '{print $2}')
    [ "$status" = "running" ]
}

# Add IP alias to container
add_ip_alias() {
    local ctid="$1"
    local alias_ip="$2"
    local name="$3"
    
    if ! check_container "$ctid"; then
        warn "Container $ctid ($name) is not running, skipping"
        return 0
    fi
    
    info "Adding IP alias $alias_ip to container $ctid ($name)..."
    
    # Check if alias already exists
    if pct exec "$ctid" -- ip addr show "$CONTAINER_IFACE" 2>/dev/null | grep -q "$alias_ip"; then
        info "  IP alias $alias_ip already exists on $name"
        return 0
    fi
    
    # Add the IP alias
    pct exec "$ctid" -- ip addr add "$alias_ip/21" dev "$CONTAINER_IFACE" 2>/dev/null || {
        error "Failed to add IP alias $alias_ip to container $ctid"
        return 1
    }
    
    success "  Added IP alias $alias_ip to $name (container $ctid)"
}

# Remove IP alias from container
remove_ip_alias() {
    local ctid="$1"
    local alias_ip="$2"
    local name="$3"
    
    if ! check_container "$ctid"; then
        warn "Container $ctid ($name) is not running, skipping"
        return 0
    fi
    
    info "Removing IP alias $alias_ip from container $ctid ($name)..."
    
    # Check if alias exists
    if ! pct exec "$ctid" -- ip addr show "$CONTAINER_IFACE" 2>/dev/null | grep -q "$alias_ip"; then
        info "  IP alias $alias_ip doesn't exist on $name"
        return 0
    fi
    
    # Remove the IP alias
    pct exec "$ctid" -- ip addr del "$alias_ip/21" dev "$CONTAINER_IFACE" 2>/dev/null || {
        warn "Failed to remove IP alias $alias_ip from container $ctid (may already be removed)"
    }
    
    success "  Removed IP alias $alias_ip from $name (container $ctid)"
}

# Enable aliasing
enable_alias() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║       Enabling vLLM IP Aliasing (Test -> Production)       ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    info "This will configure production vLLM containers to respond to test IPs"
    echo ""
    echo "  Production vLLM ($PROD_VLLM_IP) will also respond to: $TEST_VLLM_IP"
    echo "  Production Ollama ($PROD_OLLAMA_IP) will also respond to: $TEST_OLLAMA_IP"
    echo ""
    
    # Step 1: Stop test containers to avoid IP conflicts
    info "Step 1: Stopping test containers (if running) to avoid IP conflicts..."
    echo ""
    
    stop_container "$TEST_VLLM_CTID" "Test vLLM" || {
        error "Failed to stop test vLLM container - IP conflict may occur"
        return 1
    }
    
    if container_exists "$TEST_OLLAMA_CTID"; then
        stop_container "$TEST_OLLAMA_CTID" "Test Ollama" || {
            warn "Failed to stop test Ollama container"
        }
    fi
    
    echo ""
    
    # Step 2: Add IP aliases to production containers
    info "Step 2: Adding IP aliases to production containers..."
    echo ""
    
    add_ip_alias "$PROD_VLLM_CTID" "$TEST_VLLM_IP" "vLLM"
    
    # Ollama is optional
    if check_container "$PROD_OLLAMA_CTID"; then
        add_ip_alias "$PROD_OLLAMA_CTID" "$TEST_OLLAMA_IP" "Ollama"
    else
        info "Production Ollama container not running, skipping"
    fi
    
    echo ""
    success "vLLM IP aliasing enabled!"
    echo ""
    info "Test environment will now use production vLLM services"
    info "Deploy test LiteLLM to complete the setup: make litellm INV=inventory/test"
    echo ""
    warn "Note: Test vLLM container has been stopped to avoid IP conflict"
    info "To use dedicated test vLLM, run: $0 disable"
}

# Disable aliasing
disable_alias() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║      Disabling vLLM IP Aliasing (Test -> Production)       ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    # Step 1: Remove IP aliases from production containers
    info "Step 1: Removing IP aliases from production containers..."
    echo ""
    
    remove_ip_alias "$PROD_VLLM_CTID" "$TEST_VLLM_IP" "vLLM"
    
    if check_container "$PROD_OLLAMA_CTID"; then
        remove_ip_alias "$PROD_OLLAMA_CTID" "$TEST_OLLAMA_IP" "Ollama"
    fi
    
    echo ""
    
    # Step 2: Optionally start test containers
    info "Step 2: Test containers can now be started (no IP conflict)"
    echo ""
    
    # Check if we're running interactively
    if [ -t 0 ]; then
        # Interactive - ask user
        if container_exists "$TEST_VLLM_CTID"; then
            echo -n "  Start test vLLM container ($TEST_VLLM_CTID)? [y/N]: "
            read -r start_vllm
            if [ "$start_vllm" = "y" ] || [ "$start_vllm" = "Y" ]; then
                start_container "$TEST_VLLM_CTID" "Test vLLM"
            fi
        fi
        
        if container_exists "$TEST_OLLAMA_CTID"; then
            echo -n "  Start test Ollama container ($TEST_OLLAMA_CTID)? [y/N]: "
            read -r start_ollama
            if [ "$start_ollama" = "y" ] || [ "$start_ollama" = "Y" ]; then
                start_container "$TEST_OLLAMA_CTID" "Test Ollama"
            fi
        fi
    else
        # Non-interactive - don't auto-start
        info "Non-interactive mode: test containers not auto-started"
        info "To start manually: pct start $TEST_VLLM_CTID"
    fi
    
    echo ""
    success "vLLM IP aliasing disabled!"
    echo ""
    info "To deploy isolated test vLLM: make vllm INV=inventory/test"
}

# Show status
show_status() {
    echo ""
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║              vLLM IP Aliasing Status                       ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    local vllm_aliased=false
    local ollama_aliased=false
    
    echo -e "  ${BOLD}Production Containers:${NC}"
    echo ""
    
    # Check Production vLLM
    if check_container "$PROD_VLLM_CTID"; then
        echo -n "  vLLM (CTID $PROD_VLLM_CTID, $PROD_VLLM_IP): "
        if pct exec "$PROD_VLLM_CTID" -- ip addr show "$CONTAINER_IFACE" 2>/dev/null | grep -q "$TEST_VLLM_IP"; then
            echo -e "${GREEN}Running + Aliased${NC} (also responds to $TEST_VLLM_IP)"
            vllm_aliased=true
        else
            echo -e "${GREEN}Running${NC} (no alias)"
        fi
    else
        echo -e "  vLLM (CTID $PROD_VLLM_CTID): ${RED}Not running${NC}"
    fi
    
    # Check Production Ollama
    if check_container "$PROD_OLLAMA_CTID"; then
        echo -n "  Ollama (CTID $PROD_OLLAMA_CTID, $PROD_OLLAMA_IP): "
        if pct exec "$PROD_OLLAMA_CTID" -- ip addr show "$CONTAINER_IFACE" 2>/dev/null | grep -q "$TEST_OLLAMA_IP"; then
            echo -e "${GREEN}Running + Aliased${NC} (also responds to $TEST_OLLAMA_IP)"
            ollama_aliased=true
        else
            echo -e "${GREEN}Running${NC} (no alias)"
        fi
    else
        echo -e "  Ollama (CTID $PROD_OLLAMA_CTID): ${YELLOW}Not running (optional)${NC}"
    fi
    
    echo ""
    echo -e "  ${BOLD}Test Containers:${NC}"
    echo ""
    
    # Check Test vLLM
    if container_exists "$TEST_VLLM_CTID"; then
        local test_vllm_status=$(pct status "$TEST_VLLM_CTID" 2>/dev/null | awk '{print $2}')
        echo -n "  vLLM (CTID $TEST_VLLM_CTID, $TEST_VLLM_IP): "
        if [ "$test_vllm_status" = "running" ]; then
            if $vllm_aliased; then
                echo -e "${RED}Running - IP CONFLICT!${NC}"
            else
                echo -e "${GREEN}Running${NC}"
            fi
        else
            echo -e "${YELLOW}Stopped${NC}"
        fi
    else
        echo -e "  vLLM (CTID $TEST_VLLM_CTID): ${BLUE}Not created${NC}"
    fi
    
    # Check Test Ollama
    if container_exists "$TEST_OLLAMA_CTID"; then
        local test_ollama_status=$(pct status "$TEST_OLLAMA_CTID" 2>/dev/null | awk '{print $2}')
        echo -n "  Ollama (CTID $TEST_OLLAMA_CTID, $TEST_OLLAMA_IP): "
        if [ "$test_ollama_status" = "running" ]; then
            if $ollama_aliased; then
                echo -e "${RED}Running - IP CONFLICT!${NC}"
            else
                echo -e "${GREEN}Running${NC}"
            fi
        else
            echo -e "${YELLOW}Stopped${NC}"
        fi
    else
        echo -e "  Ollama (CTID $TEST_OLLAMA_CTID): ${BLUE}Not created${NC}"
    fi
    
    echo ""
    echo -e "  ${BOLD}Summary:${NC}"
    if $vllm_aliased; then
        success "  Test environment is using production vLLM (aliased)"
    else
        local test_vllm_status=$(pct status "$TEST_VLLM_CTID" 2>/dev/null | awk '{print $2}' || echo "")
        if [ "$test_vllm_status" = "running" ]; then
            info "  Test environment is using dedicated test vLLM"
        else
            warn "  Test environment has no vLLM (neither aliased nor running)"
        fi
    fi
}

# Main
check_proxmox

case "${1:-status}" in
    enable)
        enable_alias
        ;;
    disable)
        disable_alias
        ;;
    status)
        show_status
        ;;
    *)
        echo "Usage: $0 {enable|disable|status}"
        exit 1
        ;;
esac

