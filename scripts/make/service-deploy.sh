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
set -Eeo pipefail

# Trap ERR to print debug info when a command fails under set -e
trap 'rc=$?; echo "[ERROR] service-deploy.sh failed at line ${LINENO}: ${BASH_COMMAND} (exit ${rc})" >&2' ERR

# Make exports ENV= into the environment. Perl taint mode (used by pct)
# considers ENV and BASH_ENV insecure. Unset them early.
unset ENV BASH_ENV 2>/dev/null || true

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source libraries
source "${REPO_ROOT}/scripts/lib/ui.sh"
source "${REPO_ROOT}/scripts/lib/prereqs.sh"
source "${REPO_ROOT}/scripts/lib/profiles.sh"
source "${REPO_ROOT}/scripts/lib/state.sh"
source "${REPO_ROOT}/scripts/lib/vault.sh"

# Initialize profiles
profile_init

# Active profile info.
# When BUSIBOX_ENV / BUSIBOX_BACKEND are passed explicitly (e.g. by the CLI
# install worker), they take precedence over the profiles.json active profile.
# This prevents multi-instance CLI races where two processes overwrite "active".
if [[ -n "${BUSIBOX_ENV:-}" && -n "${BUSIBOX_BACKEND:-}" ]]; then
    _active_profile=""
else
    _active_profile=$(profile_get_active)
    if [[ -n "$_active_profile" ]]; then
        export BUSIBOX_ENV=$(profile_get "$_active_profile" "environment")
    fi
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
            agent|agent-api) echo "agent_api" ;;
            data|ingest|data-api|data-worker) echo "data" ;;
            search|search-api) echo "apis_search" ;;
            deploy|deploy-api) echo "deploy_api" ;;
            config|config-api) echo "config_api" ;;
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

            # Custom services (Docker Compose stacks)
            custom-services) echo "custom_services" ;;

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
            config|config-api) echo "config" ;;
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

            # Custom services (Docker Compose stacks)
            custom-services) echo "custom_services" ;;

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

    # Load LLM_BACKEND from state if not already set (needed for llm/all group expansion)
    if [[ -z "${LLM_BACKEND:-}" ]] && type get_state &>/dev/null; then
        LLM_BACKEND="$(get_state "LLM_BACKEND" "" 2>/dev/null || echo "")"
        export LLM_BACKEND
    fi

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
                expanded="${expanded} authz embedding data search agent deploy config bridge docs"
                ;;
            llm)
                if [[ "${LLM_BACKEND:-}" == "vllm" ]]; then
                    expanded="${expanded} vllm litellm"
                else
                    expanded="${expanded} litellm"
                fi
                ;;
            frontend)
                expanded="${expanded} core-apps nginx"
                ;;
            all)
                if [[ "${LLM_BACKEND:-}" == "vllm" ]]; then
                    expanded="${expanded} postgres redis minio milvus neo4j authz embedding data search agent deploy config bridge docs vllm litellm core-apps nginx"
                else
                    expanded="${expanded} postgres redis minio milvus neo4j authz embedding data search agent deploy config bridge docs litellm core-apps nginx"
                fi
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
    # Prefer explicit env var (set by CLI install worker for multi-instance safety)
    if [[ -n "${BUSIBOX_ENV:-}" ]]; then
        echo "$BUSIBOX_ENV"
        return
    fi

    # Fallback to active profile
    if [[ -n "$_active_profile" ]]; then
        profile_get "$_active_profile" "environment"
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

    # Prefer explicit env var (set by CLI install worker for multi-instance safety)
    if [[ -n "${BUSIBOX_BACKEND:-}" ]]; then
        backend="$BUSIBOX_BACKEND"
    fi

    # Fallback to active profile
    if [[ -z "$backend" && -n "$_active_profile" ]]; then
        backend=$(profile_get "$_active_profile" "backend")
    fi

    # Fallback to state file
    if [[ -z "$backend" ]]; then
        backend=$(get_backend "$env" 2>/dev/null || echo "")
    fi
    
    if [[ -z "$backend" ]]; then
        case "$env" in
            development) backend="docker" ;;
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
        development) echo "dev" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Map a service to its expected Proxmox container ID (production base).
# Returns empty string for services that don't have a dedicated LXC.
get_service_ctid() {
    local service="$1"
    case "$service" in
        nginx|proxy) echo "200" ;;
        core-apps|apps) echo "201" ;;
        agent|agent-api) echo "202" ;;
        postgres|pg) echo "203" ;;
        milvus) echo "204" ;;
        minio|files) echo "205" ;;
        data|ingest|data-api|data-worker) echo "206" ;;
        litellm) echo "207" ;;
        vllm) echo "208" ;;
        authz|authz-api) echo "210" ;;
        bridge|bridge-api) echo "211" ;;
        user-apps) echo "212" ;;
        neo4j|graph) echo "213" ;;
        custom-services) echo "214" ;;
        *) echo "" ;;
    esac
}

# Ensure a Proxmox LXC container exists before deploying to it.
# Creates just the one missing container via direct pct commands.
ensure_container_exists() {
    local service="$1"
    local env="$2"

    if ! command -v pct &>/dev/null; then
        return 0
    fi

    local base_ctid
    base_ctid=$(get_service_ctid "$service")
    [[ -z "$base_ctid" ]] && return 0

    local ctid="$base_ctid"
    if [[ "$env" == "staging" ]]; then
        ctid=$((base_ctid + 100))
    fi

    if pct status "$ctid" &>/dev/null; then
        return 0
    fi

    warn "Container $ctid for ${service} does not exist - creating..."

    # Read vars directly from vars.env without sourcing into current shell
    local pct_dir="${REPO_ROOT}/provision/pct"
    local vars_file="${pct_dir}/vars.env"
    if [[ ! -f "$vars_file" ]]; then
        error "PCT vars not found: $vars_file"
        return 1
    fi

    # Extract values we need by sourcing in a subshell
    local ct_ip ct_name ct_template ct_storage ct_bridge ct_cidr ct_gw ct_mem ct_cpus ct_disk ct_ssh_key
    eval "$(bash -c "
        source '$vars_file'
        if [[ '$env' == 'staging' ]]; then
            source '${pct_dir}/stage-vars.env'
        fi
        echo \"ct_template=\$TEMPLATE\"
        echo \"ct_storage=\$STORAGE\"
        echo \"ct_bridge=\$BRIDGE\"
        echo \"ct_cidr=\$CIDR\"
        echo \"ct_gw=\$GW\"
        echo \"ct_mem=\$MEM_MB\"
        echo \"ct_cpus=\$CPUS\"
        echo \"ct_disk=\$DISK_GB\"
        echo \"ct_ssh_key=\$SSH_PUBKEY_PATH\"
    ")"

    # Determine container IP and name from the CT ID
    local ip name priv="unpriv"
    local prefix=""
    if [[ "$env" == "staging" ]]; then
        prefix="STAGE-"
        eval "$(bash -c "
            source '${pct_dir}/vars.env'
            source '${pct_dir}/stage-vars.env'
            case $base_ctid in
                200) echo 'ip='\$IP_PROXY_STAGING ;;
                201) echo 'ip='\$IP_CORE_APPS_STAGING ;;
                202) echo 'ip='\$IP_AGENT_STAGING ;;
                203) echo 'ip='\$IP_PG_STAGING ;;
                204) echo 'ip='\$IP_MILVUS_STAGING ;;
                205) echo 'ip='\$IP_FILES_STAGING ;;
                206) echo 'ip='\$IP_DATA_STAGING ;;
                207) echo 'ip='\$IP_LITELLM_STAGING ;;
                208) echo 'ip='\$IP_VLLM_STAGING ;;
                210) echo 'ip='\$IP_AUTHZ_STAGING ;;
                211) echo 'ip='\$IP_BRIDGE_STAGING ;;
                212) echo 'ip='\$IP_USER_APPS_STAGING ;;
                213) echo 'ip='\$IP_NEO4J_STAGING ;;
                214) echo 'ip='\$IP_CUSTOM_SERVICES_STAGING ;;
            esac
        ")"
    else
        eval "$(bash -c "
            source '${pct_dir}/vars.env'
            case $base_ctid in
                200) echo 'ip='\$IP_PROXY ;;
                201) echo 'ip='\$IP_CORE_APPS ;;
                202) echo 'ip='\$IP_AGENT ;;
                203) echo 'ip='\$IP_PG ;;
                204) echo 'ip='\$IP_MILVUS ;;
                205) echo 'ip='\$IP_FILES ;;
                206) echo 'ip='\$IP_DATA ;;
                207) echo 'ip='\$IP_LITELLM ;;
                208) echo 'ip='\$IP_VLLM ;;
                210) echo 'ip='\$IP_AUTHZ ;;
                211) echo 'ip='\$IP_BRIDGE ;;
                212) echo 'ip='\$IP_USER_APPS ;;
                213) echo 'ip='\$IP_NEO4J ;;
                214) echo 'ip='\$IP_CUSTOM_SERVICES ;;
            esac
        ")"
    fi

    case "$base_ctid" in
        200) name="${prefix}proxy-lxc" ;;
        201) name="${prefix}core-apps-lxc" ;;
        202) name="${prefix}agent-lxc" ;;
        203) name="${prefix}pg-lxc" ;;
        204) name="${prefix}milvus-lxc" ;;
        205) name="${prefix}files-lxc" ;;
        206) name="${prefix}data-lxc" ;;
        207) name="${prefix}litellm-lxc" ;;
        208) name="${prefix}vllm-lxc"; priv="priv" ;;
        210) name="${prefix}authz-lxc" ;;
        211) name="${prefix}bridge-lxc" ;;
        212) name="${prefix}user-apps-lxc" ;;
        213) name="${prefix}neo4j-lxc" ;;
        214) name="${prefix}custom-services-lxc" ;;
        *)   error "Unknown container ID: $base_ctid"; return 1 ;;
    esac

    if [[ -z "${ip:-}" ]]; then
        error "Could not resolve IP for container $ctid"
        return 1
    fi

    info "Creating $name ($ctid) at $ip..."

    # Build pct create args — same as create_ct in functions.sh
    local pct_args=(
        "$ctid" "$ct_template"
        -hostname "$name"
        -net0 "name=eth0,bridge=${ct_bridge},ip=${ip}${ct_cidr},gw=${ct_gw}"
        -storage "$ct_storage"
        -memory "$ct_mem" -cores "$ct_cpus"
        -rootfs "${ct_storage}:${ct_disk}"
        -features nesting=1,keyctl=1
        -onboot 1 -start 1
    )
    if [[ "$priv" == "priv" ]]; then
        pct_args+=(-unprivileged 0)
    fi
    if [[ -f "$ct_ssh_key" ]]; then
        pct_args+=(-ssh-public-keys "$ct_ssh_key")
    fi

    if ! pct create "${pct_args[@]}"; then
        # Retry without SSH key if that was the failure
        if [[ -f "$ct_ssh_key" ]]; then
            warn "pct create failed with SSH key, retrying without..."
            pct_args=("${pct_args[@]/-ssh-public-keys/}")
            # Rebuild without ssh key
            pct_args=(
                "$ctid" "$ct_template"
                -hostname "$name"
                -net0 "name=eth0,bridge=${ct_bridge},ip=${ip}${ct_cidr},gw=${ct_gw}"
                -storage "$ct_storage"
                -memory "$ct_mem" -cores "$ct_cpus"
                -rootfs "${ct_storage}:${ct_disk}"
                -features nesting=1,keyctl=1
                -onboot 1 -start 1
            )
            if [[ "$priv" == "priv" ]]; then
                pct_args+=(-unprivileged 0)
            fi
            if ! pct create "${pct_args[@]}"; then
                error "Failed to create container $ctid even without SSH key"
                return 1
            fi
            # Inject SSH key manually after creation
            warn "Injecting SSH key into container manually..."
            pct start "$ctid" 2>/dev/null || true
            sleep 3
            pct exec "$ctid" -- mkdir -p /root/.ssh
            pct exec "$ctid" -- chmod 700 /root/.ssh
            pct exec "$ctid" -- bash -c "cat > /root/.ssh/authorized_keys" < "$ct_ssh_key"
            pct exec "$ctid" -- chmod 600 /root/.ssh/authorized_keys
        else
            error "Failed to create container $ctid"
            return 1
        fi
    fi

    # Post-create setup (same as create_ct in functions.sh)
    echo "lxc.cgroup2.devices.allow: c 10:200 rwm" >> "/etc/pve/lxc/${ctid}.conf"
    echo "lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file" >> "/etc/pve/lxc/${ctid}.conf"

    pct start "$ctid" 2>/dev/null || true
    sleep 3
    pct exec "$ctid" -- bash -lc "apt update && apt install -y curl ca-certificates sudo gnupg ufw" || true

    if pct status "$ctid" &>/dev/null; then
        success "Container $ctid ($name) created and running"
        return 0
    fi

    error "Container $ctid created but not running"
    return 1
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
    # Always force-recreate on Docker so containers pick up fresh vault secrets.
    # Without this, `docker compose up` sees no config change and leaves existing
    # containers running with stale/placeholder env vars.
    local cmd="ansible-playbook -i ${inventory} ${playbook} --tags ${tag}"

    # If this is an individual core sub-app (busibox-*), pass deploy_app so Ansible
    # only deploys that specific app instead of all apps on the core-apps container.
    case "$service" in
        busibox-portal|busibox-admin|busibox-agents|busibox-chat|busibox-appbuilder|busibox-media|busibox-documents)
            cmd="${cmd} -e deploy_app=${service}"
            ;;
    esac

    # Override deployment_environment so Ansible roles load the correct vault file.
    # The inventory may define deployment_environment as "staging" or "prod", but
    # the actual vault file on disk uses the profile's vault_prefix (e.g.
    # "10-96-200-23-production-proxmox" or "prod"). VAULT_PREFIX is set by the
    # CLI/profile system; fall back to the container prefix for backward compat.
    local vault_env="${VAULT_PREFIX:-$prefix}"
    if [[ -n "$vault_env" ]]; then
        cmd="${cmd} -e deployment_environment=${vault_env}"
    fi

    if [[ "$backend" == "docker" ]]; then
        cmd="${cmd} -e docker_force_recreate=true"
        if [[ "${IMAGE_SOURCE:-}" == "ghcr" ]]; then
            cmd="${cmd} -e docker_pull_images=true"
        fi
        # When (re)deploying core-apps, enable all apps so every previously
        # running app comes back up after the container is recreated.
        # Without this, ENABLED_APPS defaults to "portal,admin" (auto_deploy
        # apps only), leaving agents/chat/media/documents/appbuilder stopped.
        if [[ "$service" == "core-apps" ]]; then
            cmd="${cmd} -e enabled_apps=all"
            export ENABLED_APPS="all"
        fi
    fi

    # Forward git commit/branch from the admin workstation so .deploy_version
    # files are accurate even when .git/ is not present on the remote host.
    if [[ -n "${GIT_COMMIT:-}" ]]; then
        cmd="${cmd} -e git_deploy_commit=${GIT_COMMIT}"
    fi
    if [[ -n "${GIT_BRANCH:-}" ]]; then
        cmd="${cmd} -e git_deploy_branch=${GIT_BRANCH}"
    fi
    
    # Vault password: use vault-pass-from-env.sh when ANSIBLE_VAULT_PASSWORD is set.
    # This is the primary mechanism — the CLI injects the env var, and the script echoes it.
    local env_script="${REPO_ROOT}/scripts/lib/vault-pass-from-env.sh"
    if [[ -n "${ANSIBLE_VAULT_PASSWORD:-}" && -f "$env_script" ]]; then
        [[ -x "$env_script" ]] || chmod +x "$env_script"
        cmd="${cmd} --vault-password-file ${env_script}"
    elif [[ -n "${VAULT_PASS_FILE:-}" && -f "${VAULT_PASS_FILE}" ]]; then
        cmd="${cmd} --vault-password-file ${VAULT_PASS_FILE}"
    else
        warn "No vault password available (need ANSIBLE_VAULT_PASSWORD or VAULT_PASS_FILE)"
    fi
    
    # Add environment variables for Docker
    if [[ "$backend" == "docker" ]]; then
        export CONTAINER_PREFIX="$prefix"
        export BUSIBOX_ENV="$env"
        if [[ "$env" == "development" ]]; then
            export DOCKER_DEV_MODE="local-dev"
        else
            export DOCKER_DEV_MODE="github"
        fi
        # Ensure BASE_DOMAIN is set so full_domain resolves correctly for
        # WEBAUTHN_RP_ID, portal URLs, and SSL cert SANs.
        if [[ -z "${BASE_DOMAIN:-}" ]]; then
            local _sd=""
            if [[ -n "$_active_profile" ]]; then
                _sd=$(profile_get "$_active_profile" "site_domain" 2>/dev/null || true)
            fi
            if [[ -z "$_sd" ]]; then
                _sd=$(get_state "SITE_DOMAIN" "" 2>/dev/null || true)
                [[ -z "$_sd" ]] && _sd=$(get_state "BASE_DOMAIN" "localhost" 2>/dev/null || true)
            fi
            export BASE_DOMAIN="${_sd:-localhost}"
        fi
    fi
    
    echo ""
    echo "Running: ${cmd}"
    echo ""
    
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
    
    # Set vault environment: VAULT_PREFIX (per-profile) takes precedence over container prefix
    local vault_prefix="${VAULT_PREFIX:-$prefix}"
    set_vault_environment "$vault_prefix" || true
    
    # Ensure vault access (fatal if it fails)
    if ! ensure_vault_access; then
        error "Cannot access vault - deployment aborted. Secrets would not be injected."
        error "Run 'make install' through the CLI to set up vault access first."
        exit 1
    fi
    
    # Validate vault secrets before deploying (reject placeholders/insecure defaults)
    if ! validate_vault_secrets; then
        error "Vault secrets validation failed - deployment aborted."
        error "Update your vault file and re-encrypt before deploying."
        exit 1
    fi

    # Sync protected config (admin_email, allowed_domains) from env vars into
    # the vault so Ansible renders .env files with the correct values.
    # The TUI passes ADMIN_EMAIL / ALLOWED_DOMAINS in the process environment.
    sync_secrets_to_vault 2>/dev/null || true
    
    # Expand and validate services
    local services
    services=$(expand_services "$services_input")
    
    info "Services to deploy: ${BOLD}${services}${NC}"
    echo ""
    
    # Ansible is required for docker and proxmox backends
    if [[ "$backend" != "k8s" ]]; then
        ensure_ansible quiet || exit 1
    fi
    
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

        # On Proxmox, ensure the target LXC exists before Ansible tries to reach it
        if [[ "$backend" == "proxmox" ]]; then
            if ! ensure_container_exists "$service" "$env"; then
                failed_services="${failed_services} ${service}"
                continue
            fi
        fi
        
        if deploy_service "$service" "$env" "$backend" "$inventory" "$prefix"; then
            deployed_services="${deployed_services} ${service}"

            # After installing custom-services or user-apps for the first time,
            # refresh deploy-api SSH keys so it can reach the new container.
            # Only needed for Proxmox — Docker uses container networking, not SSH.
            if [[ "$backend" == "proxmox" && ("$service" == "custom-services" || "$service" == "user-apps") ]]; then
                info "Refreshing deploy-api SSH keys for ${service}..."
                local ssh_cmd="ansible-playbook -i ${inventory} site.yml --tags deploy_api_ssh"
                local vault_env="${VAULT_PREFIX:-$prefix}"
                if [[ -n "$vault_env" ]]; then
                    ssh_cmd="${ssh_cmd} -e deployment_environment=${vault_env}"
                fi
                local env_script="${REPO_ROOT}/scripts/lib/vault-pass-from-env.sh"
                if [[ -n "${ANSIBLE_VAULT_PASSWORD:-}" && -f "$env_script" ]]; then
                    [[ -x "$env_script" ]] || chmod +x "$env_script"
                    ssh_cmd="${ssh_cmd} --vault-password-file ${env_script}"
                elif [[ -n "${VAULT_PASS_FILE:-}" && -f "${VAULT_PASS_FILE}" ]]; then
                    ssh_cmd="${ssh_cmd} --vault-password-file ${VAULT_PASS_FILE}"
                fi
                cd "${REPO_ROOT}/provision/ansible"
                if eval "$ssh_cmd" 2>/dev/null; then
                    success "Deploy-api SSH keys refreshed for ${service}"
                else
                    warn "Could not refresh deploy-api SSH keys (run 'make install SERVICE=deploy' to fix)"
                fi
            fi
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
