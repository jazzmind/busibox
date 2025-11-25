#!/bin/bash
# Interactive configuration menu for Busibox
# Run with: make configure

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Vault password file (optional)
VAULT_PASS_FILE=~/.vault_pass
if [ -f "$VAULT_PASS_FILE" ]; then
    VAULT_FLAGS="--vault-password-file $VAULT_PASS_FILE"
else
    VAULT_FLAGS="--ask-vault-pass"
fi

show_header() {
    echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║     Busibox Configuration Menu         ║${NC}"
    echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
    echo ""
}

# ========================================================================
# Environment Selection
# ========================================================================

select_environment() {
    if [[ -z "${INV:-}" ]]; then
        show_header
        echo "Select environment:"
        echo "  1) Test"
        echo "  2) Production"
        echo ""
        read -p "Choice [1-2]: " env_choice
        echo ""
        
        case "$env_choice" in
            1)
                INV="inventory/test"
                ENV_NAME="TEST"
                ;;
            2)
                INV="inventory/production"
                ENV_NAME="PRODUCTION"
                ;;
            *)
                echo -e "${YELLOW}Invalid choice, defaulting to test${NC}"
                INV="inventory/test"
                ENV_NAME="TEST"
                ;;
        esac
    else
        if [[ "$INV" == *"test"* ]]; then
            ENV_NAME="TEST"
        else
            ENV_NAME="PRODUCTION"
        fi
    fi
    
    export INV
    export ENV_NAME
}

# ========================================================================
# Verification Functions
# ========================================================================

verify_ansible_connectivity() {
    echo -e "${CYAN}Testing Ansible connectivity to all hosts...${NC}"
    echo ""
    
    if ansible -i "$INV" all -m ping 2>/dev/null; then
        echo ""
        echo -e "${GREEN}✓ All hosts reachable${NC}"
        return 0
    else
        echo ""
        echo -e "${RED}✗ Some hosts unreachable${NC}"
        return 1
    fi
}

verify_vault_access() {
    echo -e "${CYAN}Testing vault access...${NC}"
    
    # Check if vault password file exists
    if [ -f "$VAULT_PASS_FILE" ]; then
        echo -e "${GREEN}✓ Vault password file found: $VAULT_PASS_FILE${NC}"
        
        # Try to view a vault-encrypted variable
        if ansible-vault view roles/secrets/vars/vault.yml $VAULT_FLAGS > /dev/null 2>&1; then
            echo -e "${GREEN}✓ Vault decryption successful${NC}"
            return 0
        else
            echo -e "${RED}✗ Vault decryption failed${NC}"
            return 1
        fi
    else
        echo -e "${YELLOW}⚠ Vault password file not found at $VAULT_PASS_FILE${NC}"
        echo "  You will be prompted for vault password during operations"
        return 0
    fi
}

verify_inventory_vars() {
    echo -e "${CYAN}Verifying inventory variables for $ENV_NAME...${NC}"
    echo ""
    
    local errors=0
    
    # Check key variables exist
    local required_vars=(
        "network_base_octets"
        "pg_ip"
        "milvus_ip"
        "ingest_ip"
        "agent_ip"
        "apps_ip"
        "files_ip"
        "proxy_ip"
    )
    
    for var in "${required_vars[@]}"; do
        if ansible -i "$INV" localhost -m debug -a "var=$var" 2>/dev/null | grep -q "VARIABLE IS NOT DEFINED"; then
            echo -e "${RED}✗ Missing variable: $var${NC}"
            ((errors++))
        else
            local value=$(ansible -i "$INV" localhost -m debug -a "var=$var" 2>/dev/null | grep "$var" | head -1)
            echo -e "${GREEN}✓ $var defined${NC}"
        fi
    done
    
    echo ""
    if [ $errors -gt 0 ]; then
        echo -e "${RED}Found $errors missing variable(s)${NC}"
        return 1
    else
        echo -e "${GREEN}All required variables defined${NC}"
        return 0
    fi
}

verify_service_health() {
    echo -e "${CYAN}Checking service health...${NC}"
    echo ""
    
    local all_healthy=true
    
    # Get IPs from inventory
    local pg_ip=$(ansible -i "$INV" localhost -m debug -a "var=pg_ip" 2>/dev/null | grep "pg_ip" | awk -F'"' '{print $2}')
    local milvus_ip=$(ansible -i "$INV" localhost -m debug -a "var=milvus_ip" 2>/dev/null | grep "milvus_ip" | awk -F'"' '{print $2}')
    local files_ip=$(ansible -i "$INV" localhost -m debug -a "var=files_ip" 2>/dev/null | grep "files_ip" | awk -F'"' '{print $2}')
    local ingest_ip=$(ansible -i "$INV" localhost -m debug -a "var=ingest_ip" 2>/dev/null | grep "ingest_ip" | awk -F'"' '{print $2}')
    local agent_ip=$(ansible -i "$INV" localhost -m debug -a "var=agent_ip" 2>/dev/null | grep "agent_ip" | awk -F'"' '{print $2}')
    
    # PostgreSQL
    echo -n "  PostgreSQL ($pg_ip): "
    if ssh root@$pg_ip 'systemctl is-active postgresql' > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${RED}✗ Not running${NC}"
        all_healthy=false
    fi
    
    # Milvus
    echo -n "  Milvus ($milvus_ip): "
    if curl -sf "http://$milvus_ip:9091/healthz" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # MinIO
    echo -n "  MinIO ($files_ip): "
    if curl -sf "http://$files_ip:9000/minio/health/live" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # Ingest API
    echo -n "  Ingest API ($ingest_ip): "
    if curl -sf "http://$ingest_ip:8000/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    # Search API
    echo -n "  Search API ($milvus_ip): "
    if curl -sf "http://$milvus_ip:8001/health" > /dev/null 2>&1; then
        echo -e "${GREEN}✓ Running${NC}"
    else
        echo -e "${YELLOW}⚠ Not responding (may not be deployed)${NC}"
    fi
    
    echo ""
    if $all_healthy; then
        echo -e "${GREEN}Core services healthy${NC}"
        return 0
    else
        echo -e "${YELLOW}Some services may need attention${NC}"
        return 1
    fi
}

verify_all() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════${NC}"
    echo -e "${BLUE}  Full Configuration Verification ($ENV_NAME)${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════${NC}"
    echo ""
    
    local errors=0
    
    # 1. Ansible connectivity
    echo -e "${BLUE}[1/4] Ansible Connectivity${NC}"
    echo "────────────────────────────────────────────"
    verify_ansible_connectivity || ((errors++))
    echo ""
    
    # 2. Vault access
    echo -e "${BLUE}[2/4] Vault Access${NC}"
    echo "────────────────────────────────────────────"
    verify_vault_access || ((errors++))
    echo ""
    
    # 3. Inventory variables
    echo -e "${BLUE}[3/4] Inventory Variables${NC}"
    echo "────────────────────────────────────────────"
    verify_inventory_vars || ((errors++))
    echo ""
    
    # 4. Service health
    echo -e "${BLUE}[4/4] Service Health${NC}"
    echo "────────────────────────────────────────────"
    verify_service_health || ((errors++))
    echo ""
    
    # Summary
    echo -e "${BLUE}═══════════════════════════════════════════${NC}"
    if [ $errors -eq 0 ]; then
        echo -e "${GREEN}✓ All verification checks passed!${NC}"
    else
        echo -e "${YELLOW}⚠ $errors check(s) had issues${NC}"
    fi
    echo -e "${BLUE}═══════════════════════════════════════════${NC}"
}

# ========================================================================
# Configuration Functions
# ========================================================================

configure_vault_password() {
    echo -e "${CYAN}Configure Vault Password${NC}"
    echo ""
    
    if [ -f "$VAULT_PASS_FILE" ]; then
        echo "Vault password file already exists at $VAULT_PASS_FILE"
        read -p "Overwrite? [y/N]: " overwrite
        if [[ ! "$overwrite" =~ ^[Yy]$ ]]; then
            return 0
        fi
    fi
    
    echo "Enter vault password (will be saved to $VAULT_PASS_FILE):"
    read -s vault_pass
    echo ""
    
    echo "$vault_pass" > "$VAULT_PASS_FILE"
    chmod 600 "$VAULT_PASS_FILE"
    
    echo -e "${GREEN}✓ Vault password saved${NC}"
}

edit_inventory() {
    local inv_file="$INV/group_vars/all/00-main.yml"
    
    if [ -f "$inv_file" ]; then
        echo "Opening $inv_file in editor..."
        ${EDITOR:-vim} "$inv_file"
    else
        echo -e "${RED}Inventory file not found: $inv_file${NC}"
    fi
}

view_encrypted_secrets() {
    echo -e "${CYAN}Viewing encrypted secrets...${NC}"
    echo ""
    
    ansible-vault view roles/secrets/vars/vault.yml $VAULT_FLAGS
}

edit_encrypted_secrets() {
    echo -e "${CYAN}Editing encrypted secrets...${NC}"
    echo ""
    
    ansible-vault edit roles/secrets/vars/vault.yml $VAULT_FLAGS
}

show_host_ips() {
    echo -e "${CYAN}Host IPs for $ENV_NAME:${NC}"
    echo ""
    
    ansible -i "$INV" localhost -m debug -a "var=hostvars[groups['all'][0]]" 2>/dev/null | grep "_ip" | head -20 || true
    
    echo ""
    echo "Full list from inventory:"
    ansible-inventory -i "$INV" --list 2>/dev/null | grep -A 20 "_meta" | head -30 || ansible -i "$INV" all --list-hosts
}

# ========================================================================
# Main Menu
# ========================================================================

show_config_menu() {
    echo ""
    echo -e "${CYAN}Environment: $ENV_NAME ($INV)${NC}"
    echo ""
    echo "Configuration Options:"
    echo "  1) Verify configuration (recommended first step)"
    echo "  2) Test Ansible connectivity"
    echo "  3) Configure vault password"
    echo "  4) View host IPs"
    echo "  5) Edit inventory variables"
    echo "  6) View encrypted secrets"
    echo "  7) Edit encrypted secrets"
    echo "  8) Change environment"
    echo "  Q) Quit"
    echo ""
    read -p "Choice: " config_choice
    
    case "$config_choice" in
        1)
            verify_all
            ;;
        2)
            verify_ansible_connectivity
            ;;
        3)
            configure_vault_password
            ;;
        4)
            show_host_ips
            ;;
        5)
            edit_inventory
            ;;
        6)
            view_encrypted_secrets
            ;;
        7)
            edit_encrypted_secrets
            ;;
        8)
            unset INV
            unset ENV_NAME
            select_environment
            ;;
        Q|q)
            echo "Exiting..."
            exit 0
            ;;
        *)
            echo -e "${RED}Invalid choice${NC}"
            ;;
    esac
    
    echo ""
    read -p "Press Enter to continue..."
    show_config_menu
}

# ========================================================================
# Main
# ========================================================================

select_environment
show_config_menu

