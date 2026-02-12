#!/usr/bin/env bash
#
# Busibox Service Deployment
# ==========================
#
# Deploy specific service(s) via Ansible, automatically detecting
# the current environment and backend (Docker/Proxmox).
#
# Usage:
#   make install SERVICE=authz
#   make install SERVICE=authz,agent,ingest
#   bash scripts/make/service-deploy.sh authz
#
# This script reads the environment from state file and uses
# Ansible to deploy with proper vault secrets.
#
set -eo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/vault.sh"

# ============================================================================
# Configuration
# ============================================================================

# Map service names to Ansible tags
get_ansible_tag() {
    local service="$1"
    case "$service" in
        # Infrastructure
        postgres|pg) echo "postgres" ;;
        redis) echo "redis" ;;
        minio|files) echo "minio" ;;
        milvus|etcd) echo "milvus" ;;
        
        # APIs
        authz|authz-api) echo "authz" ;;
        agent|agent-api) echo "agent" ;;
        ingest|data-api) echo "data" ;;
        search|search-api) echo "search" ;;
        deploy|deploy-api) echo "deploy" ;;
        bridge|bridge-api) echo "bridge" ;;
        docs|docs-api) echo "docs" ;;
        embedding|embedding-api) echo "embedding" ;;
        
        # LLM
        litellm) echo "litellm" ;;
        vllm) echo "vllm" ;;
        # NOTE: ollama is deprecated - use vLLM instead
        
        # Frontend
        core-apps|apps) echo "core-apps" ;;
        nginx|proxy) echo "nginx" ;;
        
        # User apps
        user-apps) echo "user-apps" ;;
        
        # Unknown
        *) echo "" ;;
    esac
}

# Check if service is valid
is_valid_service() {
    local service="$1"
    local tag
    tag=$(get_ansible_tag "$service")
    [[ -n "$tag" ]]
}

# Expand service groups to individual services
expand_services() {
    local input="$1"
    local expanded=""
    
    # Split by comma
    IFS=',' read -ra services <<< "$input"
    
    for svc in "${services[@]}"; do
        # Trim whitespace
        svc=$(echo "$svc" | xargs)
        
        # Check if it's a group
        case "$svc" in
            infrastructure|infra)
                expanded="${expanded} postgres redis minio milvus"
                ;;
            apis)
                expanded="${expanded} authz agent data search deploy bridge docs embedding"
                ;;
            llm)
                expanded="${expanded} litellm"
                ;;
            frontend)
                expanded="${expanded} core-apps nginx"
                ;;
            all)
                expanded="${expanded} postgres redis minio milvus authz agent data search deploy bridge docs embedding litellm core-apps nginx"
                ;;
            *)
                expanded="${expanded} ${svc}"
                ;;
        esac
    done
    
    # Remove duplicates and extra spaces
    echo "$expanded" | tr ' ' '\n' | sort -u | tr '\n' ' ' | xargs
}

# ============================================================================
# Functions
# ============================================================================

# Get the current environment from state
get_current_env() {
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    
    if [[ -z "$env" ]]; then
        # Try to detect from state file existence
        if [[ -f "${REPO_ROOT}/.busibox-state-prod" ]]; then
            env="production"
        elif [[ -f "${REPO_ROOT}/.busibox-state-staging" ]]; then
            env="staging"
        elif [[ -f "${REPO_ROOT}/.busibox-state-demo" ]]; then
            env="demo"
        else
            env="development"
        fi
    fi
    
    echo "$env"
}

# Get the backend type for the environment
get_backend_type() {
    local env="$1"
    local backend
    backend=$(get_backend "$env" 2>/dev/null || echo "")
    
    if [[ -z "$backend" ]]; then
        # Default based on environment
        case "$env" in
            development|demo) backend="docker" ;;
            staging|production) backend="docker" ;;
            *) backend="docker" ;;
        esac
    fi
    
    echo "$backend"
}

# Map environment to inventory path
get_inventory_path() {
    local env="$1"
    local backend="$2"
    
    if [[ "$backend" == "docker" ]]; then
        echo "inventory/docker"
    else
        case "$env" in
            staging) echo "inventory/staging" ;;
            production) echo "inventory/production" ;;
            *) echo "inventory/docker" ;;
        esac
    fi
}

# Map environment to container prefix
get_container_prefix() {
    local env="$1"
    case "$env" in
        demo) echo "demo" ;;
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Deploy a single service
deploy_service() {
    local service="$1"
    local env="$2"
    local backend="$3"
    local inventory="$4"
    local prefix="$5"
    
    local tag
    tag=$(get_ansible_tag "$service")
    
    info "Deploying ${BOLD}${service}${NC} (tag: ${tag})..."
    
    # Change to ansible directory
    cd "${REPO_ROOT}/provision/ansible"
    
    # Determine playbook based on backend
    local playbook
    if [[ "$backend" == "docker" ]]; then
        playbook="docker.yml"
    else
        playbook="site.yml"
    fi
    
    # Build ansible-playbook command
    local cmd="ansible-playbook -i ${inventory} ${playbook} --tags ${tag}"
    
    # Add vault password file if exists
    if [[ -f "$HOME/.vault_pass" ]]; then
        cmd="${cmd} --vault-password-file $HOME/.vault_pass"
    fi
    
    # Add environment variables for Docker
    if [[ "$backend" == "docker" ]]; then
        export CONTAINER_PREFIX="$prefix"
        export BUSIBOX_ENV="$env"
    fi
    
    # Run the deployment with reduced noise
    echo ""
    echo "Running: ${cmd}"
    echo ""
    
    # Set ANSIBLE_DISPLAY_SKIPPED_HOSTS=no to reduce noise
    export ANSIBLE_DISPLAY_SKIPPED_HOSTS=no
    export ANSIBLE_FORCE_COLOR=1
    
    if eval "$cmd"; then
        success "Service ${service} deployed successfully"
        return 0
    else
        error "Failed to deploy ${service}"
        return 1
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    local services_input="${1:-}"
    
    if [[ -z "$services_input" ]]; then
        error "No service specified"
        echo ""
        echo "Usage: make install SERVICE=<service>[,<service>...]"
        echo ""
        echo "Examples:"
        echo "  make install SERVICE=authz"
        echo "  make install SERVICE=authz,agent,data"
        echo "  make install SERVICE=apis          # All API services"
        echo "  make install SERVICE=infrastructure  # postgres, redis, minio, milvus"
        echo ""
        echo "Services: postgres, redis, minio, milvus, authz, agent, data,"
        echo "          search, deploy, docs, embedding, litellm, core-apps, nginx"
        echo ""
        echo "Groups: infrastructure, apis, llm, frontend, all"
        echo ""
        exit 1
    fi
    
    # Get environment info
    local env backend inventory prefix
    env=$(get_current_env)
    backend=$(get_backend_type "$env")
    inventory=$(get_inventory_path "$env" "$backend")
    prefix=$(get_container_prefix "$env")
    
    echo ""
    box_start 70 single "$CYAN"
    box_header "SERVICE DEPLOYMENT"
    box_empty
    box_line "  Environment: ${BOLD}${env}${NC}"
    box_line "  Backend:     ${BOLD}${backend}${NC}"
    box_line "  Inventory:   ${inventory}"
    box_line "  Prefix:      ${prefix}"
    box_empty
    box_footer
    echo ""
    
    # Set vault environment before accessing vault
    set_vault_environment "$prefix" 2>/dev/null || true
    
    # Ensure vault access
    if ! ensure_vault_access 2>/dev/null; then
        warn "Could not access vault - some secrets may not be available"
    fi
    
    # Expand and validate services
    local services
    services=$(expand_services "$services_input")
    
    info "Services to deploy: ${BOLD}${services}${NC}"
    echo ""
    
    local failed_services=""
    local deployed_services=""
    
    for service in $services; do
        if ! is_valid_service "$service"; then
            error "Unknown service: $service"
            failed_services="${failed_services} ${service}"
            continue
        fi
        
        if deploy_service "$service" "$env" "$backend" "$inventory" "$prefix"; then
            deployed_services="${deployed_services} ${service}"
        else
            failed_services="${failed_services} ${service}"
        fi
        echo ""
    done
    
    # Summary
    echo ""
    box_start 70 single "$GREEN"
    box_header "DEPLOYMENT SUMMARY"
    box_empty
    if [[ -n "$deployed_services" ]]; then
        box_line "  ${GREEN}✓${NC} Deployed:${deployed_services}"
    fi
    if [[ -n "$failed_services" ]]; then
        box_line "  ${RED}✗${NC} Failed:${failed_services}"
    fi
    box_empty
    box_footer
    echo ""
    
    if [[ -n "$failed_services" ]]; then
        exit 1
    fi
}

main "$@"
