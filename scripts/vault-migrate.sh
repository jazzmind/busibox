#!/usr/bin/env bash
#
# Vault Migration Script
#
# Migrates from single vault.yml to environment-specific vault files:
#   - vault.dev.yml    (development)
#   - vault.staging.yml (staging)  
#   - vault.prod.yml   (production)
#   - vault.demo.yml   (demo)
#
# Each environment vault uses its own password file:
#   - ~/.busibox-vault-pass-dev
#   - ~/.busibox-vault-pass-staging
#   - ~/.busibox-vault-pass-prod
#   - ~/.busibox-vault-pass-demo
#
# Usage:
#   bash scripts/vault-migrate.sh           # Interactive migration
#   bash scripts/vault-migrate.sh --status  # Show current vault status
#   bash scripts/vault-migrate.sh --create prod  # Create vault.prod.yml
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VAULT_DIR="${REPO_ROOT}/provision/ansible/roles/secrets/vars"
VAULT_EXAMPLE="${VAULT_DIR}/vault.example.yml"
LEGACY_VAULT="${VAULT_DIR}/vault.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Show current vault status
show_status() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                           VAULT STATUS                                        ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    # Check vault files
    echo -e "${CYAN}Vault Files:${NC}"
    for prefix in dev staging prod demo; do
        local vault_file="${VAULT_DIR}/vault.${prefix}.yml"
        local pass_file="$HOME/.busibox-vault-pass-${prefix}"
        
        if [[ -f "$vault_file" ]]; then
            local encrypted="no"
            if head -1 "$vault_file" 2>/dev/null | grep -q '^\$ANSIBLE_VAULT'; then
                encrypted="yes"
            fi
            
            local can_decrypt="?"
            if [[ -f "$pass_file" ]]; then
                if ansible-vault view "$vault_file" --vault-password-file="$pass_file" &>/dev/null; then
                    can_decrypt="✓"
                else
                    can_decrypt="✗"
                fi
            fi
            
            printf "  %-20s ${GREEN}exists${NC}  encrypted: %-3s  password: " "vault.${prefix}.yml" "$encrypted"
            if [[ -f "$pass_file" ]]; then
                echo -e "${GREEN}found${NC} (decrypt: $can_decrypt)"
            else
                echo -e "${YELLOW}missing${NC}"
            fi
        else
            printf "  %-20s ${YELLOW}missing${NC}\n" "vault.${prefix}.yml"
        fi
    done
    
    # Check legacy vault
    echo ""
    echo -e "${CYAN}Legacy Vault:${NC}"
    if [[ -f "$LEGACY_VAULT" ]]; then
        local encrypted="no"
        if head -1 "$LEGACY_VAULT" 2>/dev/null | grep -q '^\$ANSIBLE_VAULT'; then
            encrypted="yes"
        fi
        
        # Try to find a password that works
        local working_pass=""
        local can_decrypt="✗"
        
        # Try ~/.vault_pass first
        if [[ -f "$HOME/.vault_pass" ]]; then
            if ansible-vault view "$LEGACY_VAULT" --vault-password-file="$HOME/.vault_pass" &>/dev/null; then
                working_pass="~/.vault_pass"
                can_decrypt="✓"
            fi
        fi
        
        # Try environment-specific files
        if [[ -z "$working_pass" ]]; then
            for prefix in prod staging dev demo; do
                local try_pass="$HOME/.busibox-vault-pass-${prefix}"
                if [[ -f "$try_pass" ]]; then
                    if ansible-vault view "$LEGACY_VAULT" --vault-password-file="$try_pass" &>/dev/null; then
                        working_pass="~/.busibox-vault-pass-${prefix}"
                        can_decrypt="✓"
                        break
                    fi
                fi
            done
        fi
        
        printf "  %-20s ${GREEN}exists${NC}  encrypted: %-3s  " "vault.yml" "$encrypted"
        if [[ -n "$working_pass" ]]; then
            echo -e "password: ${GREEN}${working_pass}${NC} (decrypt: $can_decrypt)"
        elif [[ -f "$HOME/.vault_pass" ]]; then
            echo -e "password: ${YELLOW}~/.vault_pass${NC} (decrypt: ✗ wrong password)"
        else
            echo -e "password: ${YELLOW}not found${NC}"
        fi
    else
        printf "  %-20s ${YELLOW}missing${NC}\n" "vault.yml"
    fi
    
    # Show password files
    echo ""
    echo -e "${CYAN}Password Files:${NC}"
    for prefix in prod staging dev demo; do
        local pass_file="$HOME/.busibox-vault-pass-${prefix}"
        if [[ -f "$pass_file" ]]; then
            printf "  %-35s ${GREEN}exists${NC}\n" "~/.busibox-vault-pass-${prefix}"
        fi
    done
    if [[ -f "$HOME/.vault_pass" ]]; then
        printf "  %-35s ${GREEN}exists${NC}\n" "~/.vault_pass"
    fi
    
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════════════${NC}"
}

# Create an environment-specific vault
create_vault() {
    local prefix="$1"
    local vault_file="${VAULT_DIR}/vault.${prefix}.yml"
    local pass_file="$HOME/.busibox-vault-pass-${prefix}"
    
    if [[ -f "$vault_file" ]]; then
        warn "Vault file already exists: vault.${prefix}.yml"
        read -p "Overwrite? (y/N) " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            return 1
        fi
    fi
    
    # Check for source to copy from
    echo ""
    echo -e "${CYAN}Choose source for vault.${prefix}.yml:${NC}"
    echo ""
    
    local sources=()
    local source_labels=()
    
    if [[ -f "$VAULT_EXAMPLE" ]]; then
        sources+=("$VAULT_EXAMPLE")
        source_labels+=("vault.example.yml (template)")
    fi
    
    if [[ -f "$LEGACY_VAULT" ]]; then
        sources+=("$LEGACY_VAULT")
        source_labels+=("vault.yml (legacy)")
    fi
    
    for p in dev staging prod demo; do
        local other_vault="${VAULT_DIR}/vault.${p}.yml"
        if [[ -f "$other_vault" ]] && [[ "$p" != "$prefix" ]]; then
            sources+=("$other_vault")
            source_labels+=("vault.${p}.yml")
        fi
    done
    
    if [[ ${#sources[@]} -eq 0 ]]; then
        error "No source vault files found!"
        return 1
    fi
    
    local i=1
    for label in "${source_labels[@]}"; do
        echo "  $i) $label"
        ((i++))
    done
    echo ""
    
    read -p "Choice [1]: " choice
    choice="${choice:-1}"
    ((choice--))
    
    if [[ $choice -lt 0 ]] || [[ $choice -ge ${#sources[@]} ]]; then
        error "Invalid choice"
        return 1
    fi
    
    local source="${sources[$choice]}"
    
    # Decrypt source if needed
    if head -1 "$source" 2>/dev/null | grep -q '^\$ANSIBLE_VAULT'; then
        info "Source file is encrypted. Decrypting..."
        
        # Find password file for source
        local source_pass=""
        if [[ "$source" == "$LEGACY_VAULT" ]] && [[ -f "$HOME/.vault_pass" ]]; then
            source_pass="$HOME/.vault_pass"
        else
            local source_prefix=$(basename "$source" | sed 's/vault\.\(.*\)\.yml/\1/')
            local try_pass="$HOME/.busibox-vault-pass-${source_prefix}"
            if [[ -f "$try_pass" ]]; then
                source_pass="$try_pass"
            fi
        fi
        
        if [[ -z "$source_pass" ]]; then
            read -sp "Enter password for $source: " source_pass_input
            echo ""
            local tmp_pass=$(mktemp)
            echo "$source_pass_input" > "$tmp_pass"
            chmod 600 "$tmp_pass"
            source_pass="$tmp_pass"
        fi
        
        # Decrypt to temp file
        local tmp_file=$(mktemp)
        if ! ansible-vault view "$source" --vault-password-file="$source_pass" > "$tmp_file"; then
            error "Failed to decrypt source file"
            rm -f "$tmp_file" "$tmp_pass" 2>/dev/null
            return 1
        fi
        
        cp "$tmp_file" "$vault_file"
        rm -f "$tmp_file" "$tmp_pass" 2>/dev/null
    else
        cp "$source" "$vault_file"
    fi
    
    success "Created vault.${prefix}.yml"
    
    # Set up password
    echo ""
    if [[ -f "$pass_file" ]]; then
        info "Password file already exists: $pass_file"
        read -p "Use existing password? (Y/n) " use_existing
        if [[ "$use_existing" =~ ^[Nn]$ ]]; then
            rm -f "$pass_file"
        fi
    fi
    
    if [[ ! -f "$pass_file" ]]; then
        read -sp "Enter new password for vault.${prefix}.yml: " new_pass
        echo ""
        read -sp "Confirm password: " confirm_pass
        echo ""
        
        if [[ "$new_pass" != "$confirm_pass" ]]; then
            error "Passwords don't match"
            return 1
        fi
        
        echo "$new_pass" > "$pass_file"
        chmod 600 "$pass_file"
        success "Created password file: $pass_file"
    fi
    
    # Encrypt vault
    info "Encrypting vault.${prefix}.yml..."
    if ansible-vault encrypt "$vault_file" --vault-password-file="$pass_file"; then
        success "Encrypted vault.${prefix}.yml"
    else
        error "Failed to encrypt vault"
        return 1
    fi
    
    echo ""
    success "Environment vault ready: vault.${prefix}.yml"
    echo ""
    echo -e "  To edit:   ${CYAN}ansible-vault edit $vault_file --vault-password-file=$pass_file${NC}"
    echo -e "  To view:   ${CYAN}ansible-vault view $vault_file --vault-password-file=$pass_file${NC}"
}

# Interactive migration
interactive_migrate() {
    echo ""
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════════════${NC}"
    echo -e "${BOLD}                      VAULT MIGRATION WIZARD                                   ${NC}"
    echo -e "${BOLD}═══════════════════════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "This wizard helps you migrate from a single vault.yml to"
    echo -e "environment-specific vault files (vault.dev.yml, vault.prod.yml, etc.)"
    echo ""
    echo -e "Each environment vault uses its own password, allowing you to:"
    echo -e "  • Have different secrets per environment"
    echo -e "  • Share dev secrets without exposing prod secrets"
    echo -e "  • Run multiple environments on the same machine"
    echo ""
    
    show_status
    
    echo ""
    echo -e "${CYAN}What would you like to do?${NC}"
    echo ""
    echo "  1) Create vault.dev.yml"
    echo "  2) Create vault.staging.yml"
    echo "  3) Create vault.prod.yml"
    echo "  4) Create vault.demo.yml"
    echo "  5) Create all missing vaults"
    echo "  6) Exit"
    echo ""
    
    read -p "Choice [6]: " choice
    choice="${choice:-6}"
    
    case "$choice" in
        1) create_vault "dev" ;;
        2) create_vault "staging" ;;
        3) create_vault "prod" ;;
        4) create_vault "demo" ;;
        5)
            for prefix in dev staging prod demo; do
                local vault_file="${VAULT_DIR}/vault.${prefix}.yml"
                if [[ ! -f "$vault_file" ]]; then
                    echo ""
                    info "Creating vault.${prefix}.yml..."
                    create_vault "$prefix" || true
                fi
            done
            ;;
        6) info "Exiting" ;;
        *) error "Invalid choice" ;;
    esac
}

# Main
case "${1:-}" in
    --status)
        show_status
        ;;
    --create)
        if [[ -z "${2:-}" ]]; then
            error "Usage: $0 --create <prefix>"
            echo "  Prefixes: dev, staging, prod, demo"
            exit 1
        fi
        create_vault "$2"
        ;;
    --help|-h)
        echo "Vault Migration Script"
        echo ""
        echo "Usage:"
        echo "  $0              Interactive migration wizard"
        echo "  $0 --status     Show current vault status"
        echo "  $0 --create ENV Create vault for environment (dev/staging/prod/demo)"
        echo ""
        ;;
    *)
        interactive_migrate
        ;;
esac
