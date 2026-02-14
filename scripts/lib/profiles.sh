#!/usr/bin/env bash
#
# Busibox Deployment Profile Management Library
#
# Manages multiple deployment profiles (e.g., docker local dev, k8s production,
# proxmox staging) from a single workstation. Each profile has its own state,
# env file, and vault configuration.
#
# Profile Identity: {environment}/{backend}/{label}
#   e.g., development/docker/local, production/k8s/rackspace-spot
#
# Storage: .busibox/profiles.json + .busibox/profiles/{id}/
#
# Usage:
#   source scripts/lib/profiles.sh
#   profile_init
#   profile_list
#   profile_set_active "rackspace-spot"
#

# Get repository root
_PROFILES_REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
_PROFILES_DIR="${_PROFILES_REPO_ROOT}/.busibox"
_PROFILES_JSON="${_PROFILES_DIR}/profiles.json"
_PROFILES_DATA_DIR="${_PROFILES_DIR}/profiles"

# ============================================================================
# JSON Helpers (uses python3, which is guaranteed available)
# ============================================================================

# Read a key from profiles.json
# Usage: _profile_json_get ".active"
# Usage: _profile_json_get ".profiles.\"local-dev\".environment"
_profile_json_get() {
    local jq_path="$1"
    python3 -c "
import json, sys
try:
    with open('${_PROFILES_JSON}') as f:
        data = json.load(f)
    # Navigate the path
    keys = [k.strip('\"') for k in '${jq_path}'.lstrip('.').split('.') if k]
    val = data
    for k in keys:
        val = val[k]
    if isinstance(val, (dict, list)):
        print(json.dumps(val))
    elif val is None:
        print('')
    else:
        print(val)
except (KeyError, TypeError, FileNotFoundError):
    print('')
    sys.exit(0)
" 2>/dev/null
}

# Write profiles.json from stdin or argument
# Usage: echo '{}' | _profile_json_write
# Usage: _profile_json_write "$json_string"
_profile_json_write() {
    local json_data="${1:-$(cat)}"
    python3 -c "
import json, sys
data = json.loads(sys.argv[1])
with open('${_PROFILES_JSON}', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$json_data"
}

# Set a value in profiles.json
# Usage: _profile_json_set "active" "local-dev"
# Usage: _profile_json_set "profiles.local-dev.environment" "development"
_profile_json_set() {
    local key_path="$1"
    local value="$2"
    python3 -c "
import json, sys
key_path = sys.argv[1]
value = sys.argv[2]
with open('${_PROFILES_JSON}') as f:
    data = json.load(f)
keys = key_path.split('.')
obj = data
for k in keys[:-1]:
    if k not in obj:
        obj[k] = {}
    obj = obj[k]
# Try to parse as JSON for complex types, otherwise use string
try:
    parsed = json.loads(value)
    obj[keys[-1]] = parsed
except (json.JSONDecodeError, ValueError):
    obj[keys[-1]] = value
with open('${_PROFILES_JSON}', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$key_path" "$value"
}

# Delete a key from profiles.json
# Usage: _profile_json_delete "profiles.old-profile"
_profile_json_delete() {
    local key_path="$1"
    python3 -c "
import json, sys
key_path = sys.argv[1]
with open('${_PROFILES_JSON}') as f:
    data = json.load(f)
keys = key_path.split('.')
obj = data
for k in keys[:-1]:
    obj = obj[k]
del obj[keys[-1]]
with open('${_PROFILES_JSON}', 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
" "$key_path"
}

# List all profile IDs
# Usage: ids=($(profile_list_ids))
_profile_list_ids() {
    python3 -c "
import json
try:
    with open('${_PROFILES_JSON}') as f:
        data = json.load(f)
    for pid in data.get('profiles', {}):
        print(pid)
except FileNotFoundError:
    pass
" 2>/dev/null
}

# Get profile count
_profile_count() {
    python3 -c "
import json
try:
    with open('${_PROFILES_JSON}') as f:
        data = json.load(f)
    print(len(data.get('profiles', {})))
except FileNotFoundError:
    print('0')
" 2>/dev/null
}

# ============================================================================
# Profile Initialization
# ============================================================================

# Initialize the profiles directory and JSON file
# Automatically migrates from legacy .busibox-state-* files if needed
profile_init() {
    # Already initialized?
    if [[ -f "$_PROFILES_JSON" ]]; then
        return 0
    fi

    # Create directory structure
    mkdir -p "$_PROFILES_DATA_DIR"

    # Check for legacy state files to migrate
    local has_legacy=false
    for f in "${_PROFILES_REPO_ROOT}"/.busibox-state-*; do
        if [[ -f "$f" ]]; then
            has_legacy=true
            break
        fi
    done

    if [[ "$has_legacy" == "true" ]]; then
        _profile_migrate_legacy
    else
        # Fresh install - create empty profiles.json
        _profile_json_write '{"active": "", "profiles": {}}'
    fi
}

# ============================================================================
# Profile CRUD
# ============================================================================

# Create a new profile
# Usage: profile_create "production" "k8s" "rackspace-spot" [vault_prefix] [kubeconfig]
# Returns: profile ID (slug)
profile_create() {
    local environment="$1"
    local backend="$2"
    local label="$3"
    local vault_prefix="${4:-}"
    local kubeconfig="${5:-}"

    if [[ -z "$environment" || -z "$backend" || -z "$label" ]]; then
        echo "Usage: profile_create <environment> <backend> <label> [vault_prefix] [kubeconfig]" >&2
        return 1
    fi

    # Generate profile ID (slug from label, sanitized)
    local profile_id
    profile_id=$(echo "$label" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')

    # Check for duplicate
    local existing
    existing=$(_profile_json_get ".profiles.\"${profile_id}\".environment")
    if [[ -n "$existing" ]]; then
        echo "Profile '${profile_id}' already exists" >&2
        return 1
    fi

    # Default vault prefix from environment
    if [[ -z "$vault_prefix" ]]; then
        case "$environment" in
            development) vault_prefix="dev" ;;
            demo) vault_prefix="demo" ;;
            staging) vault_prefix="staging" ;;
            production) vault_prefix="prod" ;;
            *) vault_prefix="dev" ;;
        esac
    fi

    # Create profile directory
    local profile_dir="${_PROFILES_DATA_DIR}/${profile_id}"
    mkdir -p "$profile_dir"

    # Create initial state file
    cat > "${profile_dir}/state" << EOF
# Busibox State File - Profile: ${profile_id}
# ${environment}/${backend}/${label}
# This file is auto-generated. Do not edit manually unless you know what you're doing.

# Current environment: development, demo, staging, production
ENVIRONMENT=${environment}

# Backend type per environment: docker, proxmox, or k8s
BACKEND_DEVELOPMENT=docker
BACKEND_DEMO=docker
BACKEND_STAGING=
BACKEND_PRODUCTION=
BACKEND_$(echo "$environment" | tr '[:lower:]' '[:upper:]')=${backend}

# Installation status: not_installed, installed, configured, deployed, healthy
INSTALL_STATUS=not_installed

# Last command for re-run feature
LAST_COMMAND=
LAST_COMMAND_TIME=

# Comma-separated list of deployed services
SERVICES_DEPLOYED=

# Platform and LLM backend
PLATFORM=${backend}
LLM_BACKEND=
EOF

    # Build profile JSON object
    local created
    created=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    local profile_json="{\"environment\": \"${environment}\", \"backend\": \"${backend}\", \"label\": \"${label}\", \"created\": \"${created}\", \"vault_prefix\": \"${vault_prefix}\""
    if [[ -n "$kubeconfig" ]]; then
        profile_json="${profile_json}, \"kubeconfig\": \"${kubeconfig}\""
    fi
    profile_json="${profile_json}}"

    # Add to profiles.json
    _profile_json_set "profiles.${profile_id}" "$profile_json"

    echo "$profile_id"
}

# Delete a profile
# Usage: profile_delete "rackspace-spot"
profile_delete() {
    local profile_id="$1"

    if [[ -z "$profile_id" ]]; then
        echo "Usage: profile_delete <profile_id>" >&2
        return 1
    fi

    # Check it exists
    local existing
    existing=$(_profile_json_get ".profiles.\"${profile_id}\".environment")
    if [[ -z "$existing" ]]; then
        echo "Profile '${profile_id}' not found" >&2
        return 1
    fi

    # If this is the active profile, clear active
    local active
    active=$(profile_get_active)
    if [[ "$active" == "$profile_id" ]]; then
        _profile_json_set "active" ""
    fi

    # Remove from JSON
    _profile_json_delete "profiles.${profile_id}"

    # Remove profile directory
    rm -rf "${_PROFILES_DATA_DIR}/${profile_id}"

    return 0
}

# Get active profile ID
# Usage: active=$(profile_get_active)
profile_get_active() {
    _profile_json_get ".active"
}

# Set active profile
# Usage: profile_set_active "rackspace-spot"
profile_set_active() {
    local profile_id="$1"

    # Verify profile exists
    local existing
    existing=$(_profile_json_get ".profiles.\"${profile_id}\".environment")
    if [[ -z "$existing" ]]; then
        echo "Profile '${profile_id}' not found" >&2
        return 1
    fi

    _profile_json_set "active" "$profile_id"
}

# Get a profile property
# Usage: env=$(profile_get "rackspace-spot" "environment")
# Usage: backend=$(profile_get "rackspace-spot" "backend")
profile_get() {
    local profile_id="$1"
    local key="$2"
    _profile_json_get ".profiles.\"${profile_id}\".${key}"
}

# Get the state file path for a profile
# Usage: state_file=$(profile_get_state_file "rackspace-spot")
profile_get_state_file() {
    local profile_id="${1:-$(profile_get_active)}"
    if [[ -z "$profile_id" ]]; then
        echo "" 
        return 1
    fi
    echo "${_PROFILES_DATA_DIR}/${profile_id}/state"
}

# Get the env file path for a profile
# Usage: env_file=$(profile_get_env_file "rackspace-spot")
profile_get_env_file() {
    local profile_id="${1:-$(profile_get_active)}"
    if [[ -z "$profile_id" ]]; then
        echo ""
        return 1
    fi
    echo "${_PROFILES_DATA_DIR}/${profile_id}/.env"
}

# Get the vault prefix for a profile
# Usage: prefix=$(profile_get_vault_prefix "rackspace-spot")
profile_get_vault_prefix() {
    local profile_id="${1:-$(profile_get_active)}"
    if [[ -z "$profile_id" ]]; then
        echo "dev"
        return 1
    fi
    local prefix
    prefix=$(profile_get "$profile_id" "vault_prefix")
    echo "${prefix:-dev}"
}

# Get the kubeconfig path for a profile (K8s only)
# Usage: kc=$(profile_get_kubeconfig "rackspace-spot")
profile_get_kubeconfig() {
    local profile_id="${1:-$(profile_get_active)}"
    if [[ -z "$profile_id" ]]; then
        echo ""
        return 1
    fi
    local kc
    kc=$(profile_get "$profile_id" "kubeconfig")
    if [[ -n "$kc" ]]; then
        # Resolve relative paths against repo root
        if [[ "$kc" != /* ]]; then
            echo "${_PROFILES_REPO_ROOT}/${kc}"
        else
            echo "$kc"
        fi
    fi
}

# ============================================================================
# Profile Display
# ============================================================================

# Get display string for a profile: "environment/backend/label"
# Usage: display=$(profile_get_display "rackspace-spot")
profile_get_display() {
    local profile_id="$1"
    local env backend label
    env=$(profile_get "$profile_id" "environment")
    backend=$(profile_get "$profile_id" "backend")
    label=$(profile_get "$profile_id" "label")
    echo "${env}/${backend}/${label}"
}

# List all profiles with formatted output
# Usage: profile_list
profile_list() {
    local active
    active=$(profile_get_active)
    local ids
    ids=$(_profile_list_ids)

    if [[ -z "$ids" ]]; then
        echo "  (no profiles configured)"
        return 0
    fi

    local idx=1
    while IFS= read -r pid; do
        local display marker
        display=$(profile_get_display "$pid")
        if [[ "$pid" == "$active" ]]; then
            marker="*"
        else
            marker=" "
        fi
        printf "  %s %d) %-22s %s\n" "$marker" "$idx" "$pid" "$display"
        ((idx++))
    done <<< "$ids"
}

# Get profile ID by index (1-based)
# Usage: id=$(profile_get_by_index 2)
profile_get_by_index() {
    local target_idx="$1"
    local ids
    ids=$(_profile_list_ids)
    local idx=1
    while IFS= read -r pid; do
        if [[ "$idx" -eq "$target_idx" ]]; then
            echo "$pid"
            return 0
        fi
        ((idx++))
    done <<< "$ids"
    return 1
}

# Get profile count
profile_count() {
    _profile_count
}

# ============================================================================
# Legacy Migration
# ============================================================================

# Migrate from .busibox-state-* files to new profile system
_profile_migrate_legacy() {
    local simple_state="${_PROFILES_REPO_ROOT}/.busibox-state"

    # Read last env for setting active profile
    local last_env=""
    if [[ -f "$simple_state" ]]; then
        last_env=$(grep "^LAST_ENV=" "$simple_state" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'")
    fi

    # Start with empty profiles
    _profile_json_write '{"active": "", "profiles": {}}'

    local first_profile=""

    # Scan for legacy state files
    for state_file in "${_PROFILES_REPO_ROOT}"/.busibox-state-*; do
        [[ -f "$state_file" ]] || continue

        local prefix
        prefix=$(basename "$state_file" | sed 's/^\.busibox-state-//')

        # Read environment and backend from legacy file
        local env backend platform
        env=$(grep "^ENVIRONMENT=" "$state_file" 2>/dev/null | cut -d'=' -f2)
        platform=$(grep "^PLATFORM=" "$state_file" 2>/dev/null | cut -d'=' -f2)

        # Determine backend from state file
        if [[ -n "$platform" ]]; then
            backend="$platform"
        else
            # Try to read BACKEND_* keys
            local env_upper
            env_upper=$(echo "${env:-development}" | tr '[:lower:]' '[:upper:]')
            backend=$(grep "^BACKEND_${env_upper}=" "$state_file" 2>/dev/null | cut -d'=' -f2)
            backend="${backend:-docker}"
        fi

        # Default environment from prefix
        if [[ -z "$env" ]]; then
            case "$prefix" in
                dev) env="development" ;;
                demo) env="demo" ;;
                staging) env="staging" ;;
                prod) env="production" ;;
                *) env="development" ;;
            esac
        fi

        # Generate label from prefix and backend
        local label
        case "$prefix" in
            dev) label="local" ;;
            demo) label="demo" ;;
            staging) label="staging" ;;
            prod)
                if [[ "$backend" == "k8s" ]]; then
                    # Check for kubeconfig to determine label
                    local kc_files
                    kc_files=$(ls "${_PROFILES_REPO_ROOT}"/k8s/kubeconfig-*.yaml 2>/dev/null | head -1)
                    if [[ -n "$kc_files" ]]; then
                        label=$(basename "$kc_files" | sed 's/^kubeconfig-//; s/\.yaml$//')
                    else
                        label="production"
                    fi
                else
                    label="production"
                fi
                ;;
            *) label="$prefix" ;;
        esac

        # Generate profile ID
        local profile_id
        profile_id=$(echo "$label" | tr '[:upper:]' '[:lower:]' | tr ' ' '-' | tr -cd 'a-z0-9-')

        # Handle duplicate IDs
        if [[ -d "${_PROFILES_DATA_DIR}/${profile_id}" ]]; then
            profile_id="${profile_id}-${prefix}"
        fi

        # Create profile directory and copy state
        local profile_dir="${_PROFILES_DATA_DIR}/${profile_id}"
        mkdir -p "$profile_dir"
        cp "$state_file" "${profile_dir}/state"

        # Copy env file if it exists
        local env_file="${_PROFILES_REPO_ROOT}/.env.${prefix}"
        if [[ -f "$env_file" ]]; then
            cp "$env_file" "${profile_dir}/.env"
        fi

        # Determine kubeconfig for K8s profiles
        local kubeconfig=""
        if [[ "$backend" == "k8s" ]]; then
            # Look for kubeconfig files
            for kc in "${_PROFILES_REPO_ROOT}"/k8s/kubeconfig-*.yaml; do
                if [[ -f "$kc" ]]; then
                    kubeconfig=$(echo "$kc" | sed "s|^${_PROFILES_REPO_ROOT}/||")
                    break
                fi
            done
        fi

        # Build profile JSON
        local created
        created=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
        local profile_json="{\"environment\": \"${env}\", \"backend\": \"${backend}\", \"label\": \"${label}\", \"created\": \"${created}\", \"vault_prefix\": \"${prefix}\""
        if [[ -n "$kubeconfig" ]]; then
            profile_json="${profile_json}, \"kubeconfig\": \"${kubeconfig}\""
        fi
        profile_json="${profile_json}}"

        _profile_json_set "profiles.${profile_id}" "$profile_json"

        # Track first profile for fallback active
        if [[ -z "$first_profile" ]]; then
            first_profile="$profile_id"
        fi

        # Set active based on last_env
        local last_env_prefix=""
        case "$last_env" in
            development) last_env_prefix="dev" ;;
            demo) last_env_prefix="demo" ;;
            staging) last_env_prefix="staging" ;;
            production) last_env_prefix="prod" ;;
        esac
        if [[ "$prefix" == "$last_env_prefix" ]]; then
            _profile_json_set "active" "$profile_id"
        fi
    done

    # If no active was set, use the first profile
    local active
    active=$(profile_get_active)
    if [[ -z "$active" && -n "$first_profile" ]]; then
        _profile_json_set "active" "$first_profile"
    fi
}

# ============================================================================
# Environment Helpers (for compatibility with existing scripts)
# ============================================================================

# Get the environment prefix for the active profile (dev, staging, prod, demo)
# This replaces _get_env_prefix() in state.sh
profile_get_env_prefix() {
    local profile_id="${1:-$(profile_get_active)}"
    if [[ -z "$profile_id" ]]; then
        echo "dev"
        return
    fi
    local env
    env=$(profile_get "$profile_id" "environment")
    case "$env" in
        development) echo "dev" ;;
        demo) echo "demo" ;;
        staging) echo "staging" ;;
        production) echo "prod" ;;
        *) echo "dev" ;;
    esac
}

# Get the container prefix for Docker (matches Makefile CONTAINER_PREFIX)
profile_get_container_prefix() {
    profile_get_env_prefix "$@"
}

# Export profile info as environment variables (for child processes)
# Usage: eval "$(profile_export_env)"
profile_export_env() {
    local profile_id
    profile_id=$(profile_get_active)
    if [[ -z "$profile_id" ]]; then
        echo "# No active profile"
        return
    fi
    local env backend label vault_prefix
    env=$(profile_get "$profile_id" "environment")
    backend=$(profile_get "$profile_id" "backend")
    label=$(profile_get "$profile_id" "label")
    vault_prefix=$(profile_get_vault_prefix "$profile_id")

    echo "export BUSIBOX_PROFILE=\"${profile_id}\""
    echo "export BUSIBOX_ENV=\"${env}\""
    echo "export BUSIBOX_BACKEND=\"${backend}\""
    echo "export BUSIBOX_PROFILE_LABEL=\"${label}\""
    echo "export BUSIBOX_VAULT_PREFIX=\"${vault_prefix}\""
}
