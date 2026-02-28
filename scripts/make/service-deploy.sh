#!/usr/bin/env bash
#
# Busibox Service Deployment
# ==========================
#
# Deploy specific service(s) automatically detecting
# the current environment and backend (Docker/Proxmox/K8s).
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
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/vault.sh"

# Initialize profiles
profile_init

# Active profile info
_active_profile=$(profile_get_active)

# Set BUSIBOX_ENV from active profile so state.sh and backends pick it up
if [[ -n "$_active_profile" ]]; then
    export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
fi

# ============================================================================
# Configuration
# ============================================================================

# Map service names to Ansible tags
# Tags differ between docker.yml (simple names) and site.yml (prefixed names).
# Pass optional second arg "proxmox" to get site.yml tags; defaults to docker tags.
get_ansible_tag() {
    local service="$1"
    local backend="${2:-docker}"

    if [[ "$backend" == "proxmox" ]]; then
        # site.yml tags - MUST match PLAY-level tags (not just role tags)
        # because Ansible skips the entire play if the play tag doesn't match.
        case "$service" in
            # Infrastructure (play tags are core_* prefixed)
            postgres|pg) echo "core_database" ;;
            redis) echo "redis" ;;
            minio|files) echo "core_storage" ;;
            milvus|etcd) echo "core_vectorstore" ;;
            neo4j|graph) echo "core_graph" ;;

            # APIs
            authz|authz-api) echo "authz" ;;
            agent|agent-api) echo "apis_agent" ;;
            data|ingest|data-api|data-worker) echo "data" ;;
            search|search-api) echo "apis_search" ;;
            deploy|deploy-api) echo "deploy_api" ;;
            bridge|bridge-api) echo "bridge" ;;
            docs|docs-api) echo "docs_api" ;;
            embedding|embedding-api) echo "embedding" ;;

            # LLM (play tags are llm_* prefixed)
            litellm) echo "llm_litellm" ;;
            vllm) echo "llm_vllm" ;;

            # Frontend
            core-apps|apps|busibox-portal|busibox-admin|busibox-agents|busibox-chat|busibox-appbuilder|busibox-media|busibox-documents) echo "apps_frontend" ;;
            nginx|proxy) echo "core_nginx" ;;

            # User apps
            user-apps) echo "user_apps" ;;

            # Unknown
            *) echo "" ;;
        esac
    else
        # docker.yml uses simple tag names
        case "$service" in
            # Infrastructure
            postgres|pg) echo "postgres" ;;
            redis) echo "redis" ;;
            minio|files) echo "minio" ;;
            milvus|etcd) echo "milvus" ;;
            neo4j|graph) echo "neo4j" ;;

            # APIs
            authz|authz-api) echo "authz" ;;
            agent|agent-api) echo "agent" ;;
            data|ingest|data-api|data-worker) echo "data" ;;
            search|search-api) echo "search" ;;
            deploy|deploy-api) echo "deploy" ;;
            bridge|bridge-api) echo "bridge" ;;
            docs|docs-api) echo "docs" ;;
            embedding|embedding-api) echo "embedding" ;;

            # LLM
            litellm) echo "litellm" ;;
            vllm) echo "vllm" ;;

            # Frontend
            core-apps|apps|busibox-portal|busibox-admin|busibox-agents|busibox-chat|busibox-appbuilder|busibox-media|busibox-documents) echo "core-apps" ;;
            nginx|proxy) echo "nginx" ;;

            # User apps
            user-apps) echo "user-apps" ;;

            # Unknown
            *) echo "" ;;
        esac
    fi
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
        # Order matters! Dependencies must come before dependents.
        case "$svc" in
            infrastructure|infra)
                expanded="${expanded} postgres redis minio milvus neo4j"
                ;;
            apis)
                expanded="${expanded} authz embedding data search agent deploy bridge docs"
                ;;
            llm)
                expanded="${expanded} litellm"
                ;;
            frontend)
                expanded="${expanded} core-apps nginx"
                ;;
            all)
                expanded="${expanded} postgres redis minio milvus neo4j authz embedding data search agent deploy bridge docs litellm core-apps nginx"
                ;;
            *)
                expanded="${expanded} ${svc}"
                ;;
        esac
    done
    
    # Remove duplicates while preserving order, and trim extra spaces
    echo "$expanded" | tr ' ' '\n' | awk '!seen[$0]++' | tr '\n' ' ' | xargs
}

# ============================================================================
# Functions
# ============================================================================

# Get the current environment (profile-aware)
get_current_env() {
    # Prefer active profile
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
        return
    fi

    # Fallback to BUSIBOX_ENV
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi

    # Fallback to state file
    local env
    env=$(get_state "ENVIRONMENT" 2>/dev/null || echo "")
    
    if [[ -z "$env" ]]; then
        env="development"
    fi
    
    echo "$env"
}

# Get the backend type for the environment (profile-aware)
get_backend_type() {
    local env="$1"
    local backend=""

    # Prefer active profile
    if [[ -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    fi

    # Fallback to state file
    if [[ -z "$backend" ]]; then
        backend=$(get_backend "$env" 2>/dev/null || echo "")
    fi
    
    if [[ -z "$backend" ]]; then
        case "$env" in
            development|demo) backend="docker" ;;
            *) backend="docker" ;;
        esac
    fi
    
    # Normalize to lowercase (profiles may store mixed case)
    echo "$backend" | tr '[:upper:]' '[:lower:]'
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
    tag=$(get_ansible_tag "$service" "$backend")
    
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
    
    # Add vault password file when available (prefer env-specific file)
    if [[ -n "${ANSIBLE_VAULT_PASSWORD_FILE:-}" && -f "${ANSIBLE_VAULT_PASSWORD_FILE}" ]]; then
        cmd="${cmd} --vault-password-file ${ANSIBLE_VAULT_PASSWORD_FILE}"
    elif [[ -n "${VAULT_PASS_FILE:-}" && -f "${VAULT_PASS_FILE}" ]]; then
        cmd="${cmd} --vault-password-file ${VAULT_PASS_FILE}"
    elif [[ -f "$HOME/.vault_pass" ]]; then
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
# K8s Deployment
# ============================================================================

# Map make service names to K8s buildable image names
get_k8s_image_name() {
    local service="$1"
    case "$service" in
        authz|authz-api) echo "authz-api" ;;
        agent|agent-api) echo "agent-api" ;;
        ingest|data|data-api) echo "data-api" ;;
        search|search-api) echo "search-api" ;;
        deploy|deploy-api) echo "deploy-api" ;;
        bridge|bridge-api) echo "bridge-api" ;;
        docs|docs-api) echo "docs-api" ;;
        embedding|embedding-api) echo "embedding-api" ;;
        # Infrastructure and other services use upstream images - no build needed
        *) echo "" ;;
    esac
}

# Deploy service(s) to K8s cluster using in-cluster build server
deploy_service_k8s() {
    local service="$1"
    local env="$2"
    
    local k8s_deploy="${REPO_ROOT}/scripts/k8s/deploy.sh"
    
    if [[ ! -f "$k8s_deploy" ]]; then
        error "K8s deploy script not found: ${k8s_deploy}"
        return 1
    fi
    
    # Check for kubeconfig
    local kubeconfig="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
    if [[ ! -f "$kubeconfig" ]]; then
        error "Kubeconfig not found: ${kubeconfig}"
        error "Place your Rackspace Spot kubeconfig at k8s/kubeconfig-rackspace-spot.yaml"
        return 1
    fi
    
    # Check if this service has a buildable image
    local image_name
    image_name=$(get_k8s_image_name "$service")
    
    if [[ -n "$image_name" ]]; then
        info "Syncing + building ${BOLD}${service}${NC} (image: ${image_name}) on build server..."
        bash "$k8s_deploy" --sync --build --service "$image_name" --kubeconfig "$kubeconfig"
    else
        info "Service ${BOLD}${service}${NC} uses upstream image - skipping build"
    fi
    
    # Apply manifests (idempotent)
    info "Applying K8s manifests..."
    bash "$k8s_deploy" --apply --kubeconfig "$kubeconfig"
    
    success "Service ${service} deployed to K8s"
    return 0
}

# Deploy all services to K8s (optimized: sync+build all at once, apply once)
deploy_all_k8s() {
    local env="$1"
    
    local k8s_deploy="${REPO_ROOT}/scripts/k8s/deploy.sh"
    local kubeconfig="${REPO_ROOT}/k8s/kubeconfig-rackspace-spot.yaml"
    
    if [[ ! -f "$kubeconfig" ]]; then
        error "Kubeconfig not found: ${kubeconfig}"
        return 1
    fi
    
    info "Deploying all services to K8s (sync, build, push, apply)..."
    bash "$k8s_deploy" --all --kubeconfig "$kubeconfig"
    
    return $?
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
        echo "Services: postgres, redis, minio, milvus, neo4j, authz, agent, data,"
        echo "          search, deploy, docs, embedding, litellm, core-apps, proxy, nginx"
        echo "          busibox-portal, busibox-admin, busibox-agents, busibox-chat,"
        echo "          busibox-appbuilder, busibox-media, busibox-documents"
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
    
    # K8s backend: optimize for full-stack deployment
    if [[ "$backend" == "k8s" ]]; then
        # Check if deploying all services - use optimized path
        if [[ "$services_input" == "all" ]]; then
            if deploy_all_k8s "$env"; then
                success "All services deployed to K8s"
            else
                error "K8s deployment failed"
                exit 1
            fi
            return
        fi
        
        # Per-service K8s deployment
        local failed_services=""
        local deployed_services=""
        
        for service in $services; do
            if ! is_valid_service "$service"; then
                error "Unknown service: $service"
                failed_services="${failed_services} ${service}"
                continue
            fi
            
            if deploy_service_k8s "$service" "$env"; then
                deployed_services="${deployed_services} ${service}"
            else
                failed_services="${failed_services} ${service}"
            fi
            echo ""
        done
        
        # Summary
        echo ""
        box_start 70 single "$GREEN"
        box_header "K8S DEPLOYMENT SUMMARY"
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
        return
    fi
    
    # Docker/Proxmox: Ansible-based deployment
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
