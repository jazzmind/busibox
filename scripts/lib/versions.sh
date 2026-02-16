#!/usr/bin/env bash
#
# Busibox Version Management Library
#
# Provides version tracking and GitHub API integration for managing
# deployed versions of busibox and related repositories.
#
# Usage: source "$(dirname "$0")/lib/versions.sh"
#
# Dependencies:
#   - jq (for JSON parsing)
#   - curl
#   - scripts/lib/state.sh
#   - scripts/lib/github.sh (for token management)

# Get script directory
_VERSIONS_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source dependencies
source "${_VERSIONS_SCRIPT_DIR}/state.sh" 2>/dev/null || true

# Colors (define if not already defined)
_V_RED="${RED:-\033[0;31m}"
_V_GREEN="${GREEN:-\033[0;32m}"
_V_YELLOW="${YELLOW:-\033[1;33m}"
_V_CYAN="${CYAN:-\033[0;36m}"
_V_DIM="${DIM:-\033[2m}"
_V_BOLD="${BOLD:-\033[1m}"
_V_NC="${NC:-\033[0m}"

# =============================================================================
# Repository Configuration
# =============================================================================

# Repositories we track
declare -A TRACKED_REPOS=(
    ["busibox"]="jazzmind/busibox"
    ["busibox-portal"]="jazzmind/busibox-portal"
    ["busibox-agents"]="jazzmind/busibox-agents"
    ["busibox-app"]="jazzmind/busibox-app"
)

# Default branches for each repo
declare -A DEFAULT_BRANCHES=(
    ["busibox"]="main"
    ["busibox-portal"]="main"
    ["busibox-agents"]="main"
    ["busibox-app"]="main"
)

# =============================================================================
# Helper Functions
# =============================================================================

# Check if jq is available
_check_jq() {
    if ! command -v jq &>/dev/null; then
        echo -e "${_V_RED}[ERROR]${_V_NC} jq is required for version management"
        echo "  Install with: brew install jq (macOS) or apt install jq (Linux)"
        return 1
    fi
    return 0
}

# Get GitHub token from environment or state
_get_github_token() {
    if [[ -n "${GITHUB_AUTH_TOKEN:-}" ]]; then
        echo "$GITHUB_AUTH_TOKEN"
        return 0
    fi
    
    # Try to get from state
    local token
    token=$(get_state "GITHUB_AUTH_TOKEN" "" 2>/dev/null)
    if [[ -n "$token" ]]; then
        echo "$token"
        return 0
    fi
    
    return 1
}

# Make authenticated GitHub API request
# Usage: _github_api "repos/jazzmind/busibox/releases"
_github_api() {
    local endpoint="$1"
    local token
    token=$(_get_github_token)
    
    local auth_header=""
    if [[ -n "$token" ]]; then
        auth_header="-H \"Authorization: token ${token}\""
    fi
    
    eval curl -s $auth_header \
        -H "Accept: application/vnd.github.v3+json" \
        "https://api.github.com/${endpoint}" 2>/dev/null
}

# =============================================================================
# GitHub API Functions
# =============================================================================

# Get releases for a repository
# Usage: get_github_releases "busibox" [limit]
# Returns: JSON array of releases
get_github_releases() {
    local repo_key="$1"
    local limit="${2:-5}"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]]; then
        echo "[]"
        return 1
    fi
    
    _github_api "repos/${repo}/releases?per_page=${limit}"
}

# Get latest release for a repository
# Usage: get_latest_release "busibox"
# Returns: Release tag (e.g., "v1.2.3") or empty
get_latest_release() {
    local repo_key="$1"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]]; then
        return 1
    fi
    
    _check_jq || return 1
    
    local response
    response=$(_github_api "repos/${repo}/releases/latest")
    
    if [[ -z "$response" ]] || echo "$response" | jq -e '.message' &>/dev/null; then
        # No releases or error
        return 1
    fi
    
    echo "$response" | jq -r '.tag_name // empty'
}

# Get branches for a repository
# Usage: get_github_branches "busibox" [limit]
# Returns: JSON array of branches
get_github_branches() {
    local repo_key="$1"
    local limit="${2:-10}"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]]; then
        echo "[]"
        return 1
    fi
    
    _github_api "repos/${repo}/branches?per_page=${limit}"
}

# Get the latest commit on a branch
# Usage: get_branch_head "busibox" "main"
# Returns: Commit SHA (short)
get_branch_head() {
    local repo_key="$1"
    local branch="${2:-${DEFAULT_BRANCHES[$repo_key]:-main}}"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]]; then
        return 1
    fi
    
    _check_jq || return 1
    
    local response
    response=$(_github_api "repos/${repo}/branches/${branch}")
    
    if [[ -z "$response" ]] || echo "$response" | jq -e '.message' &>/dev/null; then
        return 1
    fi
    
    local sha
    sha=$(echo "$response" | jq -r '.commit.sha // empty')
    
    if [[ -n "$sha" ]]; then
        echo "${sha:0:7}"
    fi
}

# Get commit count between two refs
# Usage: get_commits_behind "busibox" "abc1234" "def5678"
# Returns: Number of commits behind, or -1 on error
get_commits_behind() {
    local repo_key="$1"
    local base="$2"
    local head="$3"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]] || [[ -z "$base" ]] || [[ -z "$head" ]]; then
        echo "-1"
        return 1
    fi
    
    _check_jq || { echo "-1"; return 1; }
    
    local response
    response=$(_github_api "repos/${repo}/compare/${base}...${head}")
    
    if [[ -z "$response" ]] || echo "$response" | jq -e '.message' &>/dev/null; then
        echo "-1"
        return 1
    fi
    
    echo "$response" | jq -r '.ahead_by // -1'
}

# Get release info (tag + commit)
# Usage: get_release_info "busibox" "v1.2.3"
# Returns: "v1.2.3:abc1234" (tag:short_sha)
get_release_info() {
    local repo_key="$1"
    local tag="$2"
    local repo="${TRACKED_REPOS[$repo_key]}"
    
    if [[ -z "$repo" ]] || [[ -z "$tag" ]]; then
        return 1
    fi
    
    _check_jq || return 1
    
    # Get tag info to find the commit
    local response
    response=$(_github_api "repos/${repo}/git/refs/tags/${tag}")
    
    if [[ -z "$response" ]] || echo "$response" | jq -e '.message' &>/dev/null; then
        return 1
    fi
    
    local sha
    sha=$(echo "$response" | jq -r '.object.sha // empty')
    
    if [[ -n "$sha" ]]; then
        echo "${tag}:${sha:0:7}"
    fi
}

# =============================================================================
# State Management Functions
# =============================================================================

# Save deployed version for a repository
# Usage: save_deployed_version "busibox" "branch" "main" "abc1234"
# Usage: save_deployed_version "busibox-portal" "release" "v1.2.3" "def5678"
save_deployed_version() {
    local repo_key="$1"
    local version_type="$2"  # "branch" or "release"
    local ref="$3"           # branch name or tag
    local commit="$4"        # short commit SHA
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    
    # Normalize repo key to uppercase for state keys
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    
    set_state "${key_prefix}_TYPE" "$version_type"
    set_state "${key_prefix}_REF" "$ref"
    set_state "${key_prefix}_COMMIT" "$commit"
    set_state "${key_prefix}_TIME" "$timestamp"
}

# Get deployed version for a repository
# Usage: info=$(get_deployed_version "busibox")
# Returns: "type:ref:commit" or empty if not tracked
get_deployed_version() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    
    local version_type ref commit
    version_type=$(get_state "${key_prefix}_TYPE" "")
    ref=$(get_state "${key_prefix}_REF" "")
    commit=$(get_state "${key_prefix}_COMMIT" "")
    
    if [[ -n "$version_type" ]] && [[ -n "$ref" ]]; then
        echo "${version_type}:${ref}:${commit}"
    fi
}

# Get deployed version time
# Usage: time=$(get_deployed_version_time "busibox")
get_deployed_version_time() {
    local repo_key="$1"
    local key_prefix="DEPLOYED_$(echo "$repo_key" | tr '[:lower:]-' '[:upper:]_')"
    get_state "${key_prefix}_TIME" ""
}

# Parse deployed version string
# Usage: parse_deployed_version "branch:main:abc1234" "type|ref|commit"
parse_deployed_version() {
    local version_str="$1"
    local field="$2"
    
    case "$field" in
        type)   echo "$version_str" | cut -d: -f1 ;;
        ref)    echo "$version_str" | cut -d: -f2 ;;
        commit) echo "$version_str" | cut -d: -f3 ;;
        *)      echo "$version_str" ;;
    esac
}

# =============================================================================
# Update Status Functions
# =============================================================================

# Check if a repository needs updates
# Usage: status=$(check_repo_updates "busibox")
# Returns: "up_to_date", "behind:N" (N commits), "new_release:TAG", or "unknown"
check_repo_updates() {
    local repo_key="$1"
    
    local deployed
    deployed=$(get_deployed_version "$repo_key")
    
    if [[ -z "$deployed" ]]; then
        echo "unknown"
        return
    fi
    
    local version_type ref commit
    version_type=$(parse_deployed_version "$deployed" "type")
    ref=$(parse_deployed_version "$deployed" "ref")
    commit=$(parse_deployed_version "$deployed" "commit")
    
    if [[ "$version_type" == "branch" ]]; then
        # Check if branch has new commits
        local head
        head=$(get_branch_head "$repo_key" "$ref")
        
        if [[ -z "$head" ]]; then
            echo "unknown"
            return
        fi
        
        if [[ "$head" == "$commit" ]]; then
            echo "up_to_date"
            return
        fi
        
        # Count commits behind
        local behind
        behind=$(get_commits_behind "$repo_key" "$commit" "$head")
        
        if [[ "$behind" == "-1" ]]; then
            echo "behind:?"
        elif [[ "$behind" == "0" ]]; then
            echo "up_to_date"
        else
            echo "behind:${behind}"
        fi
        
    elif [[ "$version_type" == "release" ]]; then
        # Check if there's a newer release
        local latest
        latest=$(get_latest_release "$repo_key")
        
        if [[ -z "$latest" ]]; then
            echo "unknown"
            return
        fi
        
        if [[ "$latest" == "$ref" ]]; then
            echo "up_to_date"
        else
            echo "new_release:${latest}"
        fi
    else
        echo "unknown"
    fi
}

# Get update summary for all repositories
# Usage: get_update_summary
# Outputs formatted summary to stdout
get_update_summary() {
    _check_jq || return 1
    
    echo ""
    echo -e "  ${_V_BOLD}Repository          Deployed             Available            Status${_V_NC}"
    echo -e "  ${_V_DIM}─────────────────────────────────────────────────────────────────────────${_V_NC}"
    
    local has_updates=false
    
    for repo_key in "${!TRACKED_REPOS[@]}"; do
        local deployed
        deployed=$(get_deployed_version "$repo_key")
        
        local deployed_display="(not tracked)"
        local available_display="-"
        local status_display="-"
        local status_color="$_V_DIM"
        
        if [[ -n "$deployed" ]]; then
            local version_type ref commit
            version_type=$(parse_deployed_version "$deployed" "type")
            ref=$(parse_deployed_version "$deployed" "ref")
            commit=$(parse_deployed_version "$deployed" "commit")
            
            if [[ "$version_type" == "branch" ]]; then
                deployed_display="${ref}@${commit}"
            else
                deployed_display="${ref}"
            fi
            
            # Check for updates
            local status
            status=$(check_repo_updates "$repo_key")
            
            case "$status" in
                up_to_date)
                    if [[ "$version_type" == "branch" ]]; then
                        available_display="${ref}@${commit}"
                    else
                        available_display="${ref}"
                    fi
                    status_display="✓ Up to date"
                    status_color="$_V_GREEN"
                    ;;
                behind:*)
                    local behind_count="${status#behind:}"
                    local head
                    head=$(get_branch_head "$repo_key" "$ref")
                    available_display="${ref}@${head:-???}"
                    status_display="⚠ ${behind_count} commits behind"
                    status_color="$_V_YELLOW"
                    has_updates=true
                    ;;
                new_release:*)
                    local new_tag="${status#new_release:}"
                    available_display="${new_tag}"
                    status_display="⚠ New release"
                    status_color="$_V_YELLOW"
                    has_updates=true
                    ;;
                unknown)
                    available_display="(checking...)"
                    status_display="? Unknown"
                    status_color="$_V_DIM"
                    ;;
            esac
        fi
        
        printf "  %-17s %-20s %-20s %b%s%b\n" \
            "$repo_key" \
            "$deployed_display" \
            "$available_display" \
            "$status_color" "$status_display" "$_V_NC"
    done
    
    echo ""
    
    if [[ "$has_updates" == true ]]; then
        return 1  # Updates available
    fi
    return 0  # All up to date
}

# =============================================================================
# Release Selection Functions
# =============================================================================

# Get list of recent releases for selection
# Usage: releases=$(get_release_options "busibox")
# Returns: Newline-separated list of tags
get_release_options() {
    local repo_key="$1"
    local limit="${2:-5}"
    
    _check_jq || return 1
    
    local response
    response=$(get_github_releases "$repo_key" "$limit")
    
    if [[ -z "$response" ]] || [[ "$response" == "[]" ]]; then
        return 1
    fi
    
    echo "$response" | jq -r '.[].tag_name'
}

# Get list of branches for selection
# Usage: branches=$(get_branch_options "busibox")
# Returns: Newline-separated list of branch names
get_branch_options() {
    local repo_key="$1"
    local limit="${2:-10}"
    
    _check_jq || return 1
    
    local response
    response=$(get_github_branches "$repo_key" "$limit")
    
    if [[ -z "$response" ]] || [[ "$response" == "[]" ]]; then
        return 1
    fi
    
    echo "$response" | jq -r '.[].name'
}

# =============================================================================
# Local Git Functions (for repos that are cloned)
# =============================================================================

# Get current commit from a local git repo
# Usage: commit=$(get_local_commit "/path/to/repo")
get_local_commit() {
    local repo_path="$1"
    
    if [[ ! -d "$repo_path/.git" ]]; then
        return 1
    fi
    
    cd "$repo_path" && git rev-parse --short HEAD 2>/dev/null
}

# Get current branch from a local git repo
# Usage: branch=$(get_local_branch "/path/to/repo")
get_local_branch() {
    local repo_path="$1"
    
    if [[ ! -d "$repo_path/.git" ]]; then
        return 1
    fi
    
    cd "$repo_path" && git rev-parse --abbrev-ref HEAD 2>/dev/null
}

# Get current tag if on a release
# Usage: tag=$(get_local_tag "/path/to/repo")
get_local_tag() {
    local repo_path="$1"
    
    if [[ ! -d "$repo_path/.git" ]]; then
        return 1
    fi
    
    cd "$repo_path" && git describe --tags --exact-match 2>/dev/null || true
}

# Detect version info from a local repository
# Usage: info=$(detect_local_version "/path/to/repo")
# Returns: "branch:main:abc1234" or "release:v1.2.3:abc1234"
detect_local_version() {
    local repo_path="$1"
    
    if [[ ! -d "$repo_path/.git" ]]; then
        return 1
    fi
    
    local commit branch tag
    commit=$(get_local_commit "$repo_path")
    tag=$(get_local_tag "$repo_path")
    
    if [[ -n "$tag" ]]; then
        echo "release:${tag}:${commit}"
    else
        branch=$(get_local_branch "$repo_path")
        echo "branch:${branch}:${commit}"
    fi
}

# =============================================================================
# CLI Interface (when run directly)
# =============================================================================

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    case "${1:-}" in
        releases)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 releases <repo>"
                echo "Repos: ${!TRACKED_REPOS[*]}"
                exit 1
            fi
            get_release_options "$2"
            ;;
        branches)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 branches <repo>"
                exit 1
            fi
            get_branch_options "$2"
            ;;
        head)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 head <repo> [branch]"
                exit 1
            fi
            get_branch_head "$2" "${3:-}"
            ;;
        latest)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 latest <repo>"
                exit 1
            fi
            get_latest_release "$2"
            ;;
        status)
            get_update_summary
            ;;
        check)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 check <repo>"
                exit 1
            fi
            check_repo_updates "$2"
            ;;
        deployed)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 deployed <repo>"
                exit 1
            fi
            get_deployed_version "$2"
            ;;
        detect)
            if [[ -z "${2:-}" ]]; then
                echo "Usage: $0 detect <path>"
                exit 1
            fi
            detect_local_version "$2"
            ;;
        *)
            echo "Busibox Version Manager"
            echo ""
            echo "Usage: $0 <command> [args]"
            echo ""
            echo "Commands:"
            echo "  releases <repo>        List recent releases"
            echo "  branches <repo>        List branches"
            echo "  head <repo> [branch]   Get latest commit on branch"
            echo "  latest <repo>          Get latest release tag"
            echo "  status                 Show update status for all repos"
            echo "  check <repo>           Check if repo needs updates"
            echo "  deployed <repo>        Get deployed version from state"
            echo "  detect <path>          Detect version from local git repo"
            echo ""
            echo "Tracked repositories:"
            for key in "${!TRACKED_REPOS[@]}"; do
                echo "  $key -> ${TRACKED_REPOS[$key]}"
            done
            exit 1
            ;;
    esac
fi
