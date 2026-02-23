#!/usr/bin/env bash
# =============================================================================
# GitHub Authentication Library
# =============================================================================
#
# Provides GitHub token validation and management for Busibox.
# Used by install.sh, menu.sh, and Makefile targets.
#
# Usage: source "$(dirname "$0")/lib/github.sh"
#
# Functions:
#   validate_github_token <token>     - Full validation with all checks
#   validate_github_repo_access <token> <repo> - Check single repo access
#   ensure_github_token               - Interactive prompt if token missing/invalid
#   get_github_token                  - Get token from state or env
#
# =============================================================================

# Source dependencies
_GITHUB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${_GITHUB_SCRIPT_DIR}/state.sh" 2>/dev/null || true
source "${_GITHUB_SCRIPT_DIR}/vault.sh" 2>/dev/null || true

# Colors (define if not already defined by ui.sh)
RED="${RED:-\033[0;31m}"
GREEN="${GREEN:-\033[0;32m}"
YELLOW="${YELLOW:-\033[0;33m}"
CYAN="${CYAN:-\033[0;36m}"
BOLD="${BOLD:-\033[1m}"
NC="${NC:-\033[0m}"

# Logging functions (define if not already defined)
if ! command -v info &>/dev/null; then
    info() { echo -e "${CYAN}[INFO]${NC} $1"; }
    success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
    warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
    error() { echo -e "${RED}[ERROR]${NC} $1"; }
fi

# =============================================================================
# Required Repositories
# =============================================================================

# Repositories that the GitHub token must have access to
GITHUB_REQUIRED_REPOS=(
    "jazzmind/busibox-frontend"
    "jazzmind/busibox-app"
)

# =============================================================================
# Validation Functions
# =============================================================================

# Validate GitHub token has access to a specific repo
# Usage: validate_github_repo_access <token> <repo>
# Returns: 0 if access, 1 if no access
validate_github_repo_access() {
    local token="$1"
    local repo="$2"
    
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: token ${token}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/repos/${repo}" 2>/dev/null)
    
    [[ "$response" == "200" ]]
}

# Validate GitHub token has package read access (for npm)
# Usage: validate_github_package_access <token>
# Returns: 0 if access, 1 if no access
validate_github_package_access() {
    local token="$1"
    
    local response
    response=$(curl -s -o /dev/null -w "%{http_code}" \
        -H "Authorization: token ${token}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/user/packages?package_type=npm" 2>/dev/null)
    
    # 200 = has access, 401/403 = no access
    [[ "$response" == "200" ]]
}

# Full GitHub token validation
# Usage: validate_github_token <token> [--quiet]
# Returns: 0 if valid, 1 if invalid
validate_github_token() {
    local token="$1"
    local quiet="${2:-}"
    local errors=0
    
    [[ "$quiet" != "--quiet" ]] && info "Validating GitHub token..."
    
    # Check if token is valid at all
    local user_response
    user_response=$(curl -s -w "\n%{http_code}" \
        -H "Authorization: token ${token}" \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/user" 2>/dev/null)
    
    local http_code
    http_code=$(echo "$user_response" | tail -1)
    local body
    body=$(echo "$user_response" | sed '$d')
    
    if [[ "$http_code" != "200" ]]; then
        [[ "$quiet" != "--quiet" ]] && error "Invalid GitHub token (HTTP $http_code)"
        return 1
    fi
    
    local username
    username=$(echo "$body" | grep -o '"login": *"[^"]*"' | head -1 | sed 's/"login": *"\([^"]*\)"/\1/')
    [[ "$quiet" != "--quiet" ]] && info "Token belongs to: ${username}"
    
    # Check required repos
    [[ "$quiet" != "--quiet" ]] && echo "" && info "Checking repository access..."
    
    for repo in "${GITHUB_REQUIRED_REPOS[@]}"; do
        if validate_github_repo_access "$token" "$repo"; then
            [[ "$quiet" != "--quiet" ]] && echo -e "  ${GREEN}✓${NC} ${repo}"
        else
            [[ "$quiet" != "--quiet" ]] && echo -e "  ${RED}✗${NC} ${repo} - ${RED}No access${NC}"
            ((errors++))
        fi
    done
    
    # Check package read access
    [[ "$quiet" != "--quiet" ]] && echo "" && info "Checking npm package access..."
    if validate_github_package_access "$token"; then
        [[ "$quiet" != "--quiet" ]] && echo -e "  ${GREEN}✓${NC} GitHub Packages (npm)"
    else
        [[ "$quiet" != "--quiet" ]] && echo -e "  ${RED}✗${NC} GitHub Packages - ${RED}No read:packages scope${NC}"
        ((errors++))
    fi
    
    [[ "$quiet" != "--quiet" ]] && echo ""
    
    if [[ $errors -gt 0 ]]; then
        [[ "$quiet" != "--quiet" ]] && {
            error "Token is missing required permissions"
            echo ""
            echo "Your token needs these scopes:"
            echo "  • repo (for private repository access)"
            echo "  • read:packages (for npm package access)"
            echo ""
            echo "Create a new token at: https://github.com/settings/tokens/new"
            echo "Select scopes: repo, read:packages"
        }
        return 1
    fi
    
    [[ "$quiet" != "--quiet" ]] && success "GitHub token validated successfully"
    return 0
}

# =============================================================================
# Token Retrieval
# =============================================================================

# Get GitHub token from vault, environment, or state file (legacy)
# Usage: token=$(get_github_token)
# Returns: token string or empty
get_github_token() {
    # First check environment variable (may be set by install.sh from vault)
    if [[ -n "${GITHUB_AUTH_TOKEN:-}" ]]; then
        echo "$GITHUB_AUTH_TOKEN"
        return 0
    fi
    
    # Try to read from vault (source of truth for secrets)
    if command -v get_vault_secret &>/dev/null; then
        # Initialize vault access first - this sets VAULT_FILE correctly
        local env_prefix
        env_prefix=$(_get_env_prefix 2>/dev/null || echo "dev")
        
        # Set up vault environment (this sets VAULT_FILE for the correct environment)
        if command -v set_vault_environment &>/dev/null; then
            set_vault_environment "$env_prefix" >/dev/null 2>&1 || true
        fi
        if command -v ensure_vault_access &>/dev/null; then
            ensure_vault_access >/dev/null 2>&1 || true
        fi
        
        # Now check if the vault file exists (VAULT_FILE is set by set_vault_environment)
        if [[ -f "${VAULT_FILE:-}" ]]; then
            local vault_token
            vault_token=$(get_vault_secret "secrets.github.personal_access_token" 2>/dev/null || echo "")
            if [[ -n "$vault_token" ]] && [[ "$vault_token" != "null" ]] && [[ "$vault_token" != "CHANGE_ME"* ]]; then
                echo "$vault_token"
                return 0
            fi
        fi
    fi
    
    # Legacy: check state file (for backwards compatibility during migration)
    local saved_token
    saved_token=$(get_state "GITHUB_AUTH_TOKEN" "" 2>/dev/null)
    
    if [[ -n "$saved_token" ]]; then
        echo "$saved_token"
        return 0
    fi
    
    # No token found
    return 1
}

# =============================================================================
# Interactive Token Management
# =============================================================================

# Ensure a valid GitHub token is available
# Will prompt user if token is missing or invalid
# Usage: ensure_github_token
# Returns: 0 if valid token available, 1 if user cancelled or failed
# Side effects: Sets GITHUB_AUTH_TOKEN environment variable
ensure_github_token() {
    # Check if we have a saved token
    local saved_token
    saved_token=$(get_github_token)
    
    if [[ -n "$saved_token" ]]; then
        if validate_github_token "$saved_token" --quiet; then
            export GITHUB_AUTH_TOKEN="$saved_token"
            return 0
        else
            warn "Saved GitHub token is no longer valid"
        fi
    fi
    
    # Need to prompt for token
    echo ""
    echo -e "┌─ ${BOLD}GITHUB AUTHENTICATION${NC} ──────────────────────────────────────────────────────┐"
    echo -e "│                                                                              │"
    echo -e "│  A GitHub Personal Access Token is required to:                              │"
    echo -e "│    - Clone private repositories (busibox-frontend monorepo)                  │"
    echo -e "│    - Download npm packages from GitHub Packages (@jazzmind/busibox-app)      │"
    echo -e "│                                                                              │"
    echo -e "│  Required scopes: ${BOLD}repo${NC}, ${BOLD}read:packages${NC}                                        │"
    echo -e "│                                                                              │"
    echo -e "│  Create token: ${CYAN}https://github.com/settings/tokens/new${NC}                        │"
    echo -e "│                                                                              │"
    echo -e "└──────────────────────────────────────────────────────────────────────────────┘"
    echo ""
    
    local max_attempts=3
    local attempt=0
    
    while [[ $attempt -lt $max_attempts ]]; do
        echo ""
        read -sp "  GitHub Personal Access Token: " input_token
        echo ""
        
        if [[ -z "$input_token" ]]; then
            warn "Token cannot be empty"
            ((attempt++))
            continue
        fi
        
        if validate_github_token "$input_token"; then
            export GITHUB_AUTH_TOKEN="$input_token"
            # Token will be saved to vault via sync_secrets_to_vault (not state file)
            return 0
        fi
        
        ((attempt++))
        if [[ $attempt -lt $max_attempts ]]; then
            echo ""
            warn "Please try again ($((max_attempts - attempt)) attempts remaining)"
        fi
    done
    
    error "Failed to provide valid GitHub token after $max_attempts attempts"
    return 1
}

# =============================================================================
# Release/Tag Fetching
# =============================================================================

# Fetch recent releases and branches for a GitHub repo
# Usage: fetch_github_refs <repo> [max_results]
# Output: One ref per line in format "TYPE|NAME|DATE|DESCRIPTION"
#   TYPE = release, tag, or branch
# Returns: 0 if successful, 1 if failed
fetch_github_refs() {
    local repo="$1"
    local max="${2:-10}"
    local token
    token=$(get_github_token 2>/dev/null || echo "")

    local auth_header=""
    if [[ -n "$token" ]]; then
        auth_header="-H \"Authorization: token ${token}\""
    fi

    local refs=()

    # Fetch releases (includes tag name, date, title)
    local releases_json
    releases_json=$(eval curl -s \
        $auth_header \
        -H '"Accept: application/vnd.github.v3+json"' \
        "\"https://api.github.com/repos/${repo}/releases?per_page=${max}\"" 2>/dev/null)

    if [[ -n "$releases_json" ]] && echo "$releases_json" | grep -q '"tag_name"'; then
        while IFS= read -r line; do
            refs+=("$line")
        done < <(echo "$releases_json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for r in data:
        tag = r.get('tag_name', '')
        name = r.get('name', tag)
        date = r.get('published_at', '')[:10]
        pre = ' (pre-release)' if r.get('prerelease') else ''
        print(f'release|{tag}|{date}|{name}{pre}')
except: pass
" 2>/dev/null)
    fi

    # Fetch recent branches (just main + a few recent ones)
    local branches_json
    branches_json=$(eval curl -s \
        $auth_header \
        -H '"Accept: application/vnd.github.v3+json"' \
        "\"https://api.github.com/repos/${repo}/branches?per_page=10\"" 2>/dev/null)

    if [[ -n "$branches_json" ]] && echo "$branches_json" | grep -q '"name"'; then
        while IFS= read -r line; do
            refs+=("$line")
        done < <(echo "$branches_json" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for b in data:
        name = b.get('name', '')
        print(f'branch|{name}||')
except: pass
" 2>/dev/null)
    fi

    # Output results
    for ref in "${refs[@]}"; do
        echo "$ref"
    done

    [[ ${#refs[@]} -gt 0 ]]
}

# Interactive release/branch selector for a GitHub repo
# Usage: selected_ref=$(select_github_ref <repo> [default_ref])
# Returns the selected ref string (tag or branch name) on stdout.
# All interactive display goes to /dev/tty so it works inside $().
select_github_ref() {
    local repo="$1"
    local default_ref="${2:-main}"

    echo "" >/dev/tty
    echo -e "${CYAN}[INFO]${NC} Fetching releases and branches for ${BOLD}${repo}${NC}..." >/dev/tty

    local refs=()

    while IFS= read -r line; do
        refs+=("$line")
    done < <(fetch_github_refs "$repo" 10 2>/dev/null)

    if [[ ${#refs[@]} -eq 0 ]]; then
        echo -e "${YELLOW}[WARN]${NC} Could not fetch refs from GitHub (no token or API error)" >/dev/tty
        echo "" >/dev/tty
        read -p "  Enter branch or tag to deploy [${default_ref}]: " manual_ref </dev/tty
        echo "${manual_ref:-$default_ref}"
        return
    fi

    # Display releases
    local has_releases=false
    local idx=1
    local display_entries=()

    echo "" >/dev/tty
    printf "  ${BOLD}Releases:${NC}\n" >/dev/tty
    for ref in "${refs[@]}"; do
        IFS='|' read -r type name date desc <<< "$ref"
        if [[ "$type" == "release" ]]; then
            has_releases=true
            display_entries+=("$name")
            if [[ -n "$desc" && "$desc" != "$name" ]]; then
                printf "    ${BOLD}%2d)${NC} %-20s ${DIM}%s - %s${NC}\n" "$idx" "$name" "$date" "$desc" >/dev/tty
            else
                printf "    ${BOLD}%2d)${NC} %-20s ${DIM}%s${NC}\n" "$idx" "$name" "$date" >/dev/tty
            fi
            ((idx++))
        fi
    done

    if ! $has_releases; then
        printf "    ${DIM}(no releases found)${NC}\n" >/dev/tty
    fi

    # Display branches
    echo "" >/dev/tty
    printf "  ${BOLD}Branches:${NC}\n" >/dev/tty
    for ref in "${refs[@]}"; do
        IFS='|' read -r type name date desc <<< "$ref"
        if [[ "$type" == "branch" ]]; then
            display_entries+=("$name")
            local main_marker=""
            if [[ "$name" == "main" || "$name" == "master" ]]; then
                main_marker=" ${DIM}(default)${NC}"
            fi
            printf "    ${BOLD}%2d)${NC} %s%s\n" "$idx" "$name" "$main_marker" >/dev/tty
            ((idx++))
        fi
    done

    echo "" >/dev/tty
    printf "  ${BOLD} c)${NC} Custom (enter manually)\n" >/dev/tty
    echo "" >/dev/tty

    read -p "  Select ref [default: ${default_ref}]: " choice </dev/tty

    if [[ -z "$choice" ]]; then
        echo "$default_ref"
        return
    fi

    if [[ "$choice" == "c" || "$choice" == "C" ]]; then
        read -p "  Enter branch or tag: " manual_ref </dev/tty
        echo "${manual_ref:-$default_ref}"
        return
    fi

    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#display_entries[@]} )); then
        echo "${display_entries[$((choice-1))]}"
        return
    fi

    # If they typed a ref name directly, use it
    echo "${choice}"
}

# =============================================================================
# Quick Check (for Makefile)
# =============================================================================

# Check if GitHub token is available and valid (non-interactive)
# Usage: check_github_token
# Returns: 0 if valid, 1 if missing/invalid
# Output: Error message if invalid
check_github_token() {
    local token
    token=$(get_github_token)
    
    # Get current environment for better error messages
    local env_prefix
    env_prefix=$(_get_env_prefix 2>/dev/null || echo "dev")
    local env_file=".env.${env_prefix}"
    
    if [[ -z "$token" ]]; then
        error "No GitHub token found"
        echo ""
        echo "Set GITHUB_AUTH_TOKEN in one of these ways:"
        echo "  1. Run 'make install' or 'make demo' to go through guided setup"
        echo "  2. Add GITHUB_AUTH_TOKEN to your ${env_file} file"
        echo "  3. Export GITHUB_AUTH_TOKEN in your shell"
        echo ""
        echo "Create a token at: https://github.com/settings/tokens/new"
        echo "Required scopes: repo, read:packages"
        return 1
    fi
    
    if ! validate_github_token "$token" --quiet; then
        error "GitHub token is invalid or missing required permissions"
        echo ""
        echo "Run 'make install' to update your token, or:"
        echo "  1. Create a new token at: https://github.com/settings/tokens/new"
        echo "  2. Required scopes: repo, read:packages"
        echo "  3. Update GITHUB_AUTH_TOKEN in your ${env_file} file"
        return 1
    fi
    
    return 0
}

# =============================================================================
# Command-line Interface
# =============================================================================

# If script is run directly (not sourced), provide CLI
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        validate)
            if [[ -n "${2:-}" ]]; then
                validate_github_token "$2"
            else
                token=$(get_github_token)
                if [[ -n "$token" ]]; then
                    validate_github_token "$token"
                else
                    error "No token provided and none found in state/environment"
                    exit 1
                fi
            fi
            ;;
        check)
            check_github_token
            exit $?
            ;;
        ensure)
            ensure_github_token
            exit $?
            ;;
        get)
            token=$(get_github_token)
            if [[ -n "$token" ]]; then
                echo "$token"
            else
                exit 1
            fi
            ;;
        *)
            echo "Usage: $0 <command> [args]"
            echo ""
            echo "Commands:"
            echo "  validate [token]  - Validate a GitHub token"
            echo "  check             - Check if saved token is valid (non-interactive)"
            echo "  ensure            - Ensure valid token, prompt if needed"
            echo "  get               - Output saved token (for use in scripts)"
            exit 1
            ;;
    esac
fi
