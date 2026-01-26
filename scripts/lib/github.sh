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
    "jazzmind/ai-portal"
    "jazzmind/agent-manager"
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

# Get GitHub token from state file or environment
# Usage: token=$(get_github_token)
# Returns: token string or empty
get_github_token() {
    # First check state file
    local saved_token
    saved_token=$(get_state "GITHUB_AUTH_TOKEN" "" 2>/dev/null)
    
    if [[ -n "$saved_token" ]]; then
        echo "$saved_token"
        return 0
    fi
    
    # Fall back to environment variable
    if [[ -n "${GITHUB_AUTH_TOKEN:-}" ]]; then
        echo "$GITHUB_AUTH_TOKEN"
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
    echo -e "│    - Clone private repositories (ai-portal, agent-manager)                   │"
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
            # Save to state for future use
            set_state "GITHUB_AUTH_TOKEN" "$GITHUB_AUTH_TOKEN" 2>/dev/null || true
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
