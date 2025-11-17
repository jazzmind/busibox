#!/usr/bin/env bash
#
# update-vault.sh - Update Ansible vault with missing secrets
#
# EXECUTION CONTEXT: Admin workstation
# DIRECTORY: scripts/
# RUN FROM: Repository root
#
# DESCRIPTION:
#   Compares the encrypted vault.yml with vault.example.yml and prompts
#   for missing secrets or values that still have CHANGE_ME placeholders.
#   Automatically handles Jinja2 templates without prompting.
#
# USAGE:
#   bash scripts/update-vault.sh
#   bash scripts/update-vault.sh --vault-password-file ~/.vault_pass
#
# REQUIREMENTS:
#   - ansible-vault command
#   - yq (YAML processor) - install with: brew install yq
#   - Vault password (prompted or via --vault-password-file)
#
# EXAMPLES:
#   # Interactive mode
#   bash scripts/update-vault.sh
#
#   # Using password file
#   bash scripts/update-vault.sh --vault-password-file ~/.vault_pass
#
# EXIT CODES:
#   0 - Success
#   1 - General error
#   2 - Missing dependencies
#   3 - Vault decryption failed
#
# AUTHOR: Busibox Team
# CREATED: 2025-11-06

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VAULT_FILE="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.yml"
EXAMPLE_FILE="${REPO_ROOT}/provision/ansible/roles/secrets/vars/vault.example.yml"
TEMP_DIR=""

# Vault password options
VAULT_PASSWORD_ARGS=""

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --vault-password-file)
      VAULT_PASSWORD_ARGS="--vault-password-file $2"
      shift 2
      ;;
    -h|--help)
      grep "^#" "$0" | grep -v "#!/" | sed 's/^# //' | sed 's/^#//'
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      exit 1
      ;;
  esac
done

# Function: Print colored message
log_info() {
  echo -e "${BLUE}[INFO]${NC} $*"
}

log_success() {
  echo -e "${GREEN}[SUCCESS]${NC} $*"
}

log_warn() {
  echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
  echo -e "${RED}[ERROR]${NC} $*"
}

# Function: Cleanup temporary files
cleanup() {
  if [[ -n "${TEMP_DIR}" && -d "${TEMP_DIR}" ]]; then
    log_info "Cleaning up temporary files..."
    rm -rf "${TEMP_DIR}"
  fi
}

trap cleanup EXIT

# Function: Check dependencies
check_dependencies() {
  local missing_deps=()
  
  if ! command -v ansible-vault &> /dev/null; then
    missing_deps+=("ansible-vault")
  fi
  
  if ! command -v yq &> /dev/null; then
    missing_deps+=("yq")
  fi
  
  if ! command -v python3 &> /dev/null; then
    missing_deps+=("python3")
  fi
  
  if [[ ${#missing_deps[@]} -gt 0 ]]; then
    log_error "Missing required dependencies: ${missing_deps[*]}"
    log_info "Install with:"
    for dep in "${missing_deps[@]}"; do
      case $dep in
        yq)
          echo "  brew install yq"
          ;;
        ansible-vault)
          echo "  pip3 install ansible"
          ;;
        python3)
          echo "  brew install python3"
          ;;
      esac
    done
    return 2
  fi
  
  return 0
}

# Function: Check if value is a Jinja2 template
is_jinja_template() {
  local value="$1"
  [[ "$value" =~ \{\{.*\}\} ]]
}

# Function: Check if value needs to be changed
needs_change() {
  local value="$1"
  [[ "$value" =~ ^CHANGE_ME ]]
}

# Function: Get YAML value
get_yaml_value() {
  local file="$1"
  local key="$2"
  yq eval "$key" "$file" 2>/dev/null || echo "null"
}

# Function: Set YAML value
set_yaml_value() {
  local file="$1"
  local key="$2"
  local value="$3"
  
  # Escape special characters for yq
  # Use yq to properly set the value with correct quoting
  yq eval -i "${key} = \"${value}\"" "$file"
}

# Function: Prompt for value
prompt_for_value() {
  local key="$1"
  local current_value="$2"
  local description="$3"
  local value=""
  
  echo ""
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  echo -e "${YELLOW}Key:${NC} $key"
  if [[ -n "$description" ]]; then
    echo -e "${YELLOW}Description:${NC} $description"
  fi
  if [[ "$current_value" != "null" && ! "$current_value" =~ ^CHANGE_ME ]]; then
    echo -e "${YELLOW}Current value:${NC} $current_value"
  fi
  echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  
  read -rp "Enter value (or press Enter to skip): " value
  
  if [[ -z "$value" ]]; then
    if [[ "$current_value" != "null" && ! "$current_value" =~ ^CHANGE_ME ]]; then
      echo "$current_value"
    else
      echo "null"
    fi
  else
    echo "$value"
  fi
}

# Function: Extract keys from YAML file recursively
extract_keys() {
  local file="$1"
  local prefix="${2:-}"
  
  # Use yq to get all keys with their paths
  yq eval '.. | select(tag != "!!map" and tag != "!!seq") | (path | join("."))' "$file" | sort -u
}

# Function: Get description for key from example file
get_key_description() {
  local key="$1"
  local line_num
  
  # Find the line number where this key is defined
  line_num=$(yq eval --unwrapScalar "path(${key}) | @sh" "$EXAMPLE_FILE" 2>/dev/null | head -1 || echo "")
  
  if [[ -z "$line_num" ]]; then
    echo ""
    return
  fi
  
  # Look for comments above this line
  local desc=""
  local search_line=$((line_num - 1))
  while [[ $search_line -gt 0 ]]; do
    local line
    line=$(sed -n "${search_line}p" "$EXAMPLE_FILE")
    if [[ "$line" =~ ^[[:space:]]*# ]]; then
      local comment
      comment=$(echo "$line" | sed 's/^[[:space:]]*#[[:space:]]*//')
      if [[ -z "$desc" ]]; then
        desc="$comment"
      else
        desc="$comment $desc"
      fi
      ((search_line--))
    else
      break
    fi
  done
  
  echo "$desc"
}

# Main script
main() {
  log_info "Busibox Vault Update Script"
  echo ""
  
  # Check dependencies
  log_info "Checking dependencies..."
  if ! check_dependencies; then
    exit 2
  fi
  log_success "All dependencies found"
  
  # Check if files exist
  if [[ ! -f "$VAULT_FILE" ]]; then
    log_error "Vault file not found: $VAULT_FILE"
    exit 1
  fi
  
  if [[ ! -f "$EXAMPLE_FILE" ]]; then
    log_error "Example file not found: $EXAMPLE_FILE"
    exit 1
  fi
  
  # Create temporary directory
  TEMP_DIR=$(mktemp -d)
  DECRYPTED_VAULT="${TEMP_DIR}/vault.yml"
  UPDATED_VAULT="${TEMP_DIR}/vault_updated.yml"
  
  # Decrypt vault
  log_info "Decrypting vault file..."
  if ! ansible-vault decrypt ${VAULT_PASSWORD_ARGS} --output "${DECRYPTED_VAULT}" "${VAULT_FILE}" 2>/dev/null; then
    log_error "Failed to decrypt vault file"
    log_info "Make sure you have the correct vault password"
    exit 3
  fi
  log_success "Vault decrypted"
  
  # Copy decrypted vault to updated vault
  cp "${DECRYPTED_VAULT}" "${UPDATED_VAULT}"
  
  # Extract all keys from example file
  log_info "Analyzing vault structure..."
  local keys
  keys=$(extract_keys "$EXAMPLE_FILE")
  
  local changes_made=0
  local keys_processed=0
  
  echo ""
  log_info "Checking for missing or CHANGE_ME values..."
  
  while IFS= read -r key; do
    ((keys_processed++))
    
    # Get values from both files
    local example_value
    local current_value
    example_value=$(get_yaml_value "$EXAMPLE_FILE" ".$key")
    current_value=$(get_yaml_value "$UPDATED_VAULT" ".$key")
    
    # Skip if example value is null or empty
    if [[ "$example_value" == "null" || -z "$example_value" ]]; then
      continue
    fi
    
    # Skip if it's a Jinja template in the example
    if is_jinja_template "$example_value"; then
      # If key doesn't exist in vault, add the template
      if [[ "$current_value" == "null" ]]; then
        log_info "Adding Jinja template: $key"
        set_yaml_value "$UPDATED_VAULT" ".$key" "$example_value"
        ((changes_made++))
      fi
      continue
    fi
    
    # Check if value needs to be changed
    local should_prompt=false
    
    # Value is missing in current vault
    if [[ "$current_value" == "null" ]]; then
      should_prompt=true
    # Value still has CHANGE_ME placeholder
    elif needs_change "$current_value"; then
      should_prompt=true
    fi
    
    if [[ "$should_prompt" == "true" ]]; then
      local description
      description=$(get_key_description "$key")
      
      local new_value
      new_value=$(prompt_for_value "$key" "$current_value" "$description")
      
      if [[ "$new_value" != "null" ]]; then
        log_info "Updating: $key"
        set_yaml_value "$UPDATED_VAULT" ".$key" "$new_value"
        ((changes_made++))
      else
        log_warn "Skipped: $key"
      fi
    fi
    
  done <<< "$keys"
  
  echo ""
  log_info "Processed $keys_processed keys"
  
  if [[ $changes_made -gt 0 ]]; then
    log_success "$changes_made values updated"
    
    # Ask for confirmation
    echo ""
    read -rp "Do you want to save these changes? (y/N): " confirm
    
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
      # Encrypt the updated vault
      log_info "Encrypting updated vault..."
      if ansible-vault encrypt ${VAULT_PASSWORD_ARGS} --output "${VAULT_FILE}" "${UPDATED_VAULT}" 2>/dev/null; then
        log_success "Vault updated and encrypted successfully"
        log_info "Backup of original vault saved to: ${VAULT_FILE}.backup"
        cp "${VAULT_FILE}" "${VAULT_FILE}.backup.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
      else
        log_error "Failed to encrypt vault"
        exit 1
      fi
    else
      log_info "Changes discarded"
    fi
  else
    log_info "No changes needed - vault is up to date"
  fi
  
  echo ""
  log_success "Vault update complete"
}

# Run main function
main "$@"

