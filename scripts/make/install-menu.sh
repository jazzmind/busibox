#!/usr/bin/env bash
#
# install-menu.sh - Install menu with options for existing installs
#
# This script provides the same install options as the launcher menu:
# - Fresh install if no existing install
# - Continue/Full/Clean options if existing install detected
#
# Called by:
#   - make install (without SERVICE=)
#   - launcher.sh handle_install()
#
# Usage:
#   install-menu.sh                # Interactive menu (q = quit)
#   install-menu.sh --from-launcher # Called from launcher (b = back, clears screen)
#   install-menu.sh --direct       # Skip menu, go directly to install.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries (profiles.sh is sourced by state.sh automatically)
source "${SCRIPT_DIR}/../lib/ui.sh"
source "${SCRIPT_DIR}/../lib/profiles.sh"
source "${SCRIPT_DIR}/../lib/state.sh"

# Source backend common library
source "${SCRIPT_DIR}/../lib/backends/common.sh"

# Initialize profiles
profile_init

# Active profile info
_active_profile=$(profile_get_active)

# Flags
DIRECT_MODE=false
FROM_LAUNCHER=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --direct)
            DIRECT_MODE=true
            shift
            ;;
        --from-launcher)
            FROM_LAUNCHER=true
            shift
            ;;
        *)
            # Pass through to install.sh
            break
            ;;
    esac
done

# =============================================================================
# DETECTION FUNCTIONS
# =============================================================================

# Check if Docker is available
check_docker_available() {
    command -v docker &>/dev/null && docker info &>/dev/null
}

# Check if Proxmox is available
check_proxmox_available() {
    command -v pct &>/dev/null
}

# Check if K8s (kubectl + kubeconfig) is available
check_k8s_available() {
    if ! command -v kubectl &>/dev/null; then
        return 1
    fi
    # Check profile kubeconfig first
    if [[ -n "${_active_profile:-}" ]]; then
        local kc
        kc=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
        if [[ -n "$kc" && -f "$kc" ]]; then
            return 0
        fi
    fi
    # Fallback
    [[ -f "${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml" ]]
}

# Detect installation status for current environment
# Returns: not_installed, partial, installed
detect_installation_status() {
    local env backend
    
    # Use profile if available
    if [[ -n "${_active_profile:-}" ]]; then
        env=$(profile_get "$_active_profile" "environment")
        backend=$(profile_get "$_active_profile" "backend")
    else
        env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
        
        if [[ -z "$env" ]]; then
            echo "not_installed"
            return
        fi
        
        local env_upper
        env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
        backend=$(get_state "BACKEND_${env_upper}" 2>/dev/null || echo "")
        
        if [[ "$env" == "development" ]]; then
            backend="docker"
        fi
    fi
    
    if [[ -z "$env" || -z "$backend" ]]; then
        echo "not_installed"
        return
    fi
    
    # Check if core services are running
    case "$backend" in
        docker)
            if ! check_docker_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            local prefix
            case "$env" in
                development) prefix="dev" ;;
                staging) prefix="staging" ;;
                production) prefix="prod" ;;
                demo) prefix="demo" ;;
                *) prefix="dev" ;;
            esac
            
            # Check for core services that must be running
            local core_services=("postgres" "authz-api" "core-apps")
            local running=0
            local total=${#core_services[@]}
            
            for service in "${core_services[@]}"; do
                if docker ps --format '{{.Names}}' 2>/dev/null | grep -q "^${prefix}-${service}$"; then
                    ((running++))
                fi
            done
            
            # If all core services are running, system is installed
            if [[ $running -eq $total ]]; then
                echo "installed"
            elif [[ $running -gt 0 ]]; then
                echo "partial"
            else
                echo "not_installed"
            fi
            ;;
        proxmox)
            if ! check_proxmox_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            local base_ctid
            case "$env" in
                production) base_ctid=200 ;;
                staging) base_ctid=300 ;;
                *) base_ctid=300 ;;
            esac
            
            # Check if proxy container is running
            local status
            status=$(pct status "$base_ctid" 2>/dev/null | awk '{print $2}')
            if [[ "$status" == "running" ]]; then
                echo "installed"
            else
                echo "not_installed"
            fi
            ;;
        k8s)
            if ! check_k8s_available 2>/dev/null; then
                echo "not_installed"
                return
            fi
            
            # Get kubeconfig from profile or fallback
            local kubeconfig=""
            if [[ -n "${_active_profile:-}" ]]; then
                kubeconfig=$(profile_get_kubeconfig "$_active_profile" 2>/dev/null)
            fi
            kubeconfig="${kubeconfig:-${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml}"
            
            # Check if core pods are running in the busibox namespace
            local running_pods
            running_pods=$(KUBECONFIG="$kubeconfig" \
                kubectl get pods -n busibox --field-selector=status.phase=Running \
                --no-headers 2>/dev/null | wc -l | tr -d ' ')
            
            if [[ "$running_pods" -ge 3 ]]; then
                echo "installed"
            elif [[ "$running_pods" -gt 0 ]]; then
                echo "partial"
            else
                echo "not_installed"
            fi
            ;;
        *)
            echo "not_installed"
            ;;
    esac
}

# Get backend for current environment (profile-aware)
get_current_backend() {
    local env="$1"
    
    # Use profile if available
    if [[ -n "${_active_profile:-}" ]]; then
        profile_get "$_active_profile" "backend"
        return
    fi
    
    local env_upper
    env_upper=$(echo "$env" | tr '[:lower:]' '[:upper:]')
    
    # Development always uses docker
    if [[ "$env" == "development" ]]; then
        echo "docker"
        return
    fi
    
    get_state "BACKEND_${env_upper}" 2>/dev/null || echo ""
}

# =============================================================================
# UNINSTALL FUNCTION
# =============================================================================

perform_uninstall() {
    local env="$1"
    local backend="$2"
    
    info "Uninstalling Busibox..."
    
    if [[ "$backend" == "docker" ]]; then
        local prefix
        case "$env" in
            development) prefix="dev" ;;
            staging) prefix="staging" ;;
            production) prefix="prod" ;;
            demo) prefix="demo" ;;
            *) prefix="dev" ;;
        esac
        
        info "Stopping and removing Docker containers (prefix: ${prefix})..."
        
        # Stop and remove containers
        docker ps -a --filter "name=${prefix}-" --format '{{.Names}}' | while read -r container; do
            info "Removing container: $container"
            docker stop "$container" 2>/dev/null || true
            docker rm "$container" 2>/dev/null || true
        done
        
        # Remove volumes
        info "Removing Docker volumes..."
        docker volume ls --filter "name=${prefix}" --format '{{.Name}}' | while read -r volume; do
            info "Removing volume: $volume"
            docker volume rm "$volume" 2>/dev/null || true
        done
        
        # Remove network
        info "Removing Docker network..."
        docker network rm "${prefix}-busibox" 2>/dev/null || true
        
        # Remove env file
        local env_file="${REPO_ROOT}/.env.${prefix}"
        if [[ -f "$env_file" ]]; then
            info "Removing env file: $env_file"
            rm -f "$env_file"
        fi
        
        success "Docker uninstall complete"
        
    elif [[ "$backend" == "proxmox" ]]; then
        local base_ctid
        case "$env" in
            production) base_ctid=200 ;;
            staging) base_ctid=300 ;;
            *)
                error "Unknown environment for Proxmox: $env"
                return 1
                ;;
        esac
        
        warn "Proxmox uninstall will stop and destroy containers ${base_ctid}-$((base_ctid+19))"
        read -p "Are you sure? Type 'YES' to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "Cancelled."
            return 1
        fi
        
        for ctid in $(seq "$base_ctid" $((base_ctid+19))); do
            if pct status "$ctid" &>/dev/null; then
                info "Stopping and destroying container $ctid..."
                pct stop "$ctid" 2>/dev/null || true
                pct destroy "$ctid" 2>/dev/null || true
            fi
        done
        
        success "Proxmox uninstall complete"
        
    elif [[ "$backend" == "k8s" ]]; then
        warn "K8s uninstall will delete all Busibox resources from the cluster"
        read -p "Are you sure? Type 'YES' to confirm: " confirm
        if [[ "$confirm" != "YES" ]]; then
            echo "Cancelled."
            return 1
        fi
        
        local kubeconfig="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
        if [[ ! -f "$kubeconfig" ]]; then
            error "Kubeconfig not found: ${kubeconfig}"
            return 1
        fi
        
        info "Deleting K8s resources..."
        
        # Delete kustomized resources first
        local overlay_dir="${REPO_ROOT}/k8s/overlays/rackspace-spot"
        if [[ -d "$overlay_dir" ]]; then
            KUBECONFIG="$kubeconfig" kubectl delete -k "$overlay_dir" --ignore-not-found 2>/dev/null || true
        fi
        
        # Delete secrets
        KUBECONFIG="$kubeconfig" kubectl delete secret busibox-secrets -n busibox --ignore-not-found 2>/dev/null || true
        KUBECONFIG="$kubeconfig" kubectl delete secret ghcr-pull-secret -n busibox --ignore-not-found 2>/dev/null || true
        
        # Delete namespace
        KUBECONFIG="$kubeconfig" kubectl delete namespace busibox --timeout=120s 2>/dev/null || true
        
        success "K8s uninstall complete"
    else
        error "Unknown backend: $backend"
        return 1
    fi
}

# =============================================================================
# MAIN
# =============================================================================

main() {
    # If direct mode, skip menu and go to install.sh
    if [[ "$DIRECT_MODE" == true ]]; then
        exec bash "${SCRIPT_DIR}/install.sh" "$@"
    fi
    
    local install_status
    install_status=$(detect_installation_status)
    
    # Get current environment info (profile-aware)
    local env backend
    if [[ -n "${_active_profile:-}" ]]; then
        env=$(profile_get "$_active_profile" "environment")
        backend=$(profile_get "$_active_profile" "backend")
    else
        env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
        backend=$(get_current_backend "$env")
    fi
    
    if [[ "$install_status" == "installed" || "$install_status" == "partial" ]]; then
        # Show install options submenu
        if [[ "$FROM_LAUNCHER" == true ]]; then
            clear
        fi
        echo ""
        box_start 70 double "$CYAN"
        box_header "INSTALL OPTIONS"
        box_empty
        if [[ -n "${_active_profile:-}" ]]; then
            local display
            display=$(profile_get_display "$_active_profile")
            box_line "  ${CYAN}Profile:${NC} $_active_profile ($display)"
            box_empty
            box_separator
            box_empty
        elif [[ -n "$env" ]]; then
            box_line "  ${CYAN}Environment:${NC} $env ($backend)"
            box_empty
            box_separator
            box_empty
        fi
        box_line "  ${BOLD}1)${NC} Continue Install - pick up from where we last failed"
        box_line "  ${BOLD}2)${NC} Full Install - redeploy all services, keep config & data"
        box_line "  ${BOLD}3)${NC} Clean Install - clear everything and start fresh"
        if [[ "$backend" == "proxmox" ]]; then
        box_line "  ${BOLD}4)${NC} Rebuild Containers - recreate LXCs, ${GREEN}preserve data${NC}"
        fi
        box_empty
        box_line "  ${BOLD}e)${NC} New Environment - install to a different environment/backend"
        box_empty
        if [[ "$FROM_LAUNCHER" == true ]]; then
            box_line "  ${DIM}b = back${NC}"
        else
            box_line "  ${DIM}q = quit${NC}"
        fi
        box_empty
        box_footer
        echo ""
        
        read -n 1 -s -r -p "Select option: " choice
        echo ""
        
        case "$choice" in
            1)
                # Continue - resume from current install phase
                if [[ -n "$env" ]]; then
                    exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" "$@"
                else
                    exec bash "${SCRIPT_DIR}/install.sh" "$@"
                fi
                ;;
            2)
                # Full Install - reset install phase but keep config
                echo ""
                printf "${YELLOW}This will redeploy all services but preserve your configuration and data.${NC}\n"
                read -p "Continue? [y/N]: " confirm
                if [[ "$confirm" == "y" || "$confirm" == "Y" ]]; then
                    # Reset install phase to force full redeploy
                    set_state "INSTALL_PHASE" "wizard_complete"
                    # Clear container creation flags to force service redeploy
                    if [[ "$backend" == "proxmox" ]]; then
                        # Keep LXC containers but force service redeploy
                        set_state "LXC_CONTAINERS_CREATED_${env^^}" "true"
                    fi
                    if [[ -n "$env" ]]; then
                        exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" --full-install "$@"
                    else
                        exec bash "${SCRIPT_DIR}/install.sh" --full-install "$@"
                    fi
                else
                    echo "Cancelled."
                    exit 0
                fi
                ;;
            3)
                # Clean Install - full uninstall then fresh install
                echo ""
                printf "${RED}Warning: This will remove ALL containers, data, and configuration.${NC}\n"
                read -p "Type 'CLEAN' to confirm: " confirm
                if [[ "$confirm" == "CLEAN" ]]; then
                    perform_uninstall "$env" "$backend"
                    # Clear state file for this environment
                    local state_prefix
                    case "$env" in
                        development) state_prefix="dev" ;;
                        staging) state_prefix="staging" ;;
                        production) state_prefix="prod" ;;
                        demo) state_prefix="demo" ;;
                        *) state_prefix="dev" ;;
                    esac
                    local state_file="${REPO_ROOT}/.busibox-state-${state_prefix}"
                    if [[ -f "$state_file" ]]; then
                        info "Removing state file: $state_file"
                        rm -f "$state_file"
                    fi
                    echo ""
                    info "Starting fresh install..."
                    sleep 1
                    exec bash "${SCRIPT_DIR}/install.sh" "$@"
                else
                    echo "Cancelled."
                    exit 0
                fi
                ;;
            4)
                # Rebuild Containers (Proxmox only) - recreate LXCs, preserve data
                if [[ "$backend" != "proxmox" ]]; then
                    echo "Option only available for Proxmox backends."
                    sleep 1
                else
                    echo ""
                    local rebuild_script="${REPO_ROOT}/provision/pct/containers/rebuild-staging.sh"
                    local rebuild_single="${REPO_ROOT}/provision/pct/containers/rebuild-container.sh"
                    local rebuild_mode="staging"
                    if [[ "$env" == "production" ]]; then
                        rebuild_mode="production"
                    fi

                    box_start 70 double "$CYAN"
                    box_header "REBUILD CONTAINERS (PRESERVE DATA)"
                    box_empty
                    box_line "  ${CYAN}Environment:${NC} ${env} (${rebuild_mode})"
                    box_empty
                    box_line "  Stateful data on host bind mounts is ${GREEN}preserved${NC}:"
                    box_line "    PostgreSQL, Milvus, MinIO, Neo4j, Redis"
                    box_empty
                    box_separator
                    box_empty
                    box_line "  ${BOLD}a)${NC} Rebuild ALL containers"
                    box_line "  ${BOLD}s)${NC} Rebuild a single container"
                    box_empty
                    box_line "  ${DIM}b = back${NC}"
                    box_empty
                    box_footer
                    echo ""

                    read -n 1 -s -r -p "Select option: " rebuild_choice
                    echo ""

                    case "$rebuild_choice" in
                        a|A)
                            if [[ "$env" == "staging" && -f "$rebuild_script" ]]; then
                                echo ""
                                info "Running staging rebuild dry-run..."
                                echo ""
                                bash "$rebuild_script"
                                echo ""
                                printf "${YELLOW}Proceed with rebuild? This will destroy and recreate all staging LXCs.${NC}\n"
                                printf "${GREEN}Stateful data on host mounts will be preserved.${NC}\n"
                                read -p "Type 'REBUILD' to confirm: " confirm_rebuild
                                if [[ "$confirm_rebuild" == "REBUILD" ]]; then
                                    echo ""
                                    bash "$rebuild_script" --confirm
                                    echo ""
                                    info "Now redeploy services:"
                                    echo "  make install SERVICE=all INV=inventory/staging"
                                    echo ""
                                    read -n 1 -s -r -p "Press any key to continue..."
                                else
                                    echo "Cancelled."
                                fi
                            elif [[ "$env" == "production" ]]; then
                                echo ""
                                warn "Production full rebuild is not automated."
                                echo "Use the single-container rebuild option instead."
                                read -n 1 -s -r -p "Press any key to continue..."
                            else
                                echo ""
                                error "Rebuild script not found: ${rebuild_script}"
                                read -n 1 -s -r -p "Press any key to continue..."
                            fi
                            ;;
                        s|S)
                            echo ""
                            echo "Available containers:"
                            echo "  proxy-lxc, core-apps-lxc, user-apps-lxc, agent-lxc, authz-lxc"
                            echo "  pg-lxc, milvus-lxc, files-lxc, neo4j-lxc"
                            echo "  data-lxc, litellm-lxc, bridge-lxc, vllm-lxc, ollama-lxc"
                            echo ""
                            read -p "Container name: " container_name
                            if [[ -n "$container_name" ]]; then
                                echo ""
                                info "Running rebuild dry-run for ${container_name}..."
                                echo ""
                                bash "$rebuild_single" "$container_name" "$rebuild_mode"
                                echo ""
                                printf "${YELLOW}Proceed with rebuild?${NC}\n"
                                printf "${GREEN}Stateful data on host mounts will be preserved.${NC}\n"
                                read -p "Type 'REBUILD' to confirm: " confirm_rebuild
                                if [[ "$confirm_rebuild" == "REBUILD" ]]; then
                                    echo ""
                                    bash "$rebuild_single" "$container_name" "$rebuild_mode" --confirm
                                    read -n 1 -s -r -p "Press any key to continue..."
                                else
                                    echo "Cancelled."
                                fi
                            fi
                            ;;
                        b|B) ;;
                    esac
                fi
                ;;
            e|E)
                # New Environment - launch the full wizard without any pre-set environment/backend
                echo ""
                info "Starting fresh install wizard (choose a new environment and backend)..."
                sleep 1
                exec bash "${SCRIPT_DIR}/install.sh" "$@"
                ;;
            b|B)
                if [[ "$FROM_LAUNCHER" == true ]]; then
                    # Return to launcher menu (exit 2 signals "back", not "install done")
                    exit 2
                else
                    echo "Exiting."
                    exit 0
                fi
                ;;
            q|Q)
                echo "Exiting."
                exit 0
                ;;
            *)
                echo "Invalid option."
                ;;
        esac
    else
        # Fresh install - go directly to install wizard
        if [[ -z "$env" ]]; then
            exec bash "${SCRIPT_DIR}/install.sh" "$@"
        else
            exec bash "${SCRIPT_DIR}/install.sh" --env "$env" --backend "$backend" "$@"
        fi
    fi
}

main "$@"
