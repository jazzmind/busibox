#!/usr/bin/env bash
#
# Rename ingest-api to data-api across the busibox codebase
#
# EXECUTION CONTEXT: Run from busibox root directory
# PREREQUISITES: 
#   - All services stopped
#   - Database backup taken
#   - No uncommitted changes
#
# This script performs a comprehensive rename of:
#   - Service names: ingest-api -> data-api, ingest-worker -> data-worker
#   - Container names: ingest-lxc -> data-lxc
#   - Environment variables: INGEST_* -> DATA_*
#   - Ansible roles and groups
#   - Configuration files
#
# USAGE:
#   # Dry run (default) - shows what would be changed
#   ./scripts/refactor/rename-ingest-to-data.sh
#
#   # Execute the rename
#   ./scripts/refactor/rename-ingest-to-data.sh --execute
#
# ROLLBACK:
#   git checkout -- .
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Flags
DRY_RUN=true
VERBOSE=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --execute)
            DRY_RUN=false
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [--execute] [--verbose]"
            echo "  --execute  Actually perform the rename (default: dry run)"
            echo "  --verbose  Show detailed output"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Ensure we're in busibox root
if [[ ! -f "CLAUDE.md" ]] || [[ ! -d "provision/ansible" ]]; then
    echo -e "${RED}Error: Run this script from the busibox root directory${NC}"
    exit 1
fi

echo -e "${BLUE}=== Busibox Service Rename: ingest-api -> data-api ===${NC}"
echo ""

if $DRY_RUN; then
    echo -e "${YELLOW}DRY RUN MODE - No changes will be made${NC}"
    echo "Run with --execute to perform the rename"
else
    echo -e "${RED}EXECUTE MODE - Changes will be made!${NC}"
    echo ""
    read -p "Are you sure you want to proceed? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "Aborted."
        exit 0
    fi
fi
echo ""

# Track counts
declare -i FILES_CHANGED=0
declare -i REPLACEMENTS=0

# Function to perform sed replacement
do_replace() {
    local pattern="$1"
    local replacement="$2"
    local file="$3"
    
    if grep -q "$pattern" "$file" 2>/dev/null; then
        local count=$(grep -c "$pattern" "$file" 2>/dev/null || echo 0)
        
        if $VERBOSE; then
            echo "  $file: $count occurrence(s) of '$pattern'"
        fi
        
        if ! $DRY_RUN; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                sed -i '' "s|$pattern|$replacement|g" "$file"
            else
                sed -i "s|$pattern|$replacement|g" "$file"
            fi
        fi
        
        REPLACEMENTS+=$count
        return 0
    fi
    return 1
}

# Function to rename a file
do_rename() {
    local old_path="$1"
    local new_path="$2"
    
    if [[ -e "$old_path" ]]; then
        echo "  Rename: $old_path -> $new_path"
        if ! $DRY_RUN; then
            mv "$old_path" "$new_path"
        fi
        return 0
    fi
    return 1
}

# ============================================================================
# STEP 1: Rename Ansible role directories
# ============================================================================
echo -e "${GREEN}Step 1: Rename Ansible roles${NC}"

# We'll keep the source code directory as srv/ingest for now
# since changing it would require updating many imports

# Rename role directories if they exist
if [[ -d "provision/ansible/roles/ingest" ]]; then
    echo "  Copying role: provision/ansible/roles/ingest -> provision/ansible/roles/data"
    if ! $DRY_RUN; then
        cp -r provision/ansible/roles/ingest provision/ansible/roles/data
    fi
fi

if [[ -d "provision/ansible/roles/ingest_api" ]]; then
    echo "  Copying role: provision/ansible/roles/ingest_api -> provision/ansible/roles/data_api"
    if ! $DRY_RUN; then
        cp -r provision/ansible/roles/ingest_api provision/ansible/roles/data_api
    fi
fi

# ============================================================================
# STEP 2: Rename template files
# ============================================================================
echo ""
echo -e "${GREEN}Step 2: Rename template files${NC}"

# Rename files in the new data role (if created)
if [[ -d "provision/ansible/roles/data/templates" ]]; then
    for f in provision/ansible/roles/data/templates/ingest*; do
        if [[ -f "$f" ]]; then
            new_name="${f//ingest/data}"
            do_rename "$f" "$new_name"
        fi
    done
fi

if [[ -d "provision/ansible/roles/data_api/templates" ]]; then
    for f in provision/ansible/roles/data_api/templates/ingest*; do
        if [[ -f "$f" ]]; then
            new_name="${f//ingest/data}"
            do_rename "$f" "$new_name"
        fi
    done
fi

# ============================================================================
# STEP 3: Update Docker Compose files
# ============================================================================
echo ""
echo -e "${GREEN}Step 3: Update Docker Compose files${NC}"

COMPOSE_FILES=(
    "docker-compose.yml"
    "docker-compose.local-dev.yml"
    "docker-compose.github.yml"
)

for compose_file in "${COMPOSE_FILES[@]}"; do
    if [[ -f "$compose_file" ]]; then
        echo "  Processing: $compose_file"
        
        # Service names
        do_replace "ingest-api:" "data-api:" "$compose_file" && FILES_CHANGED+=1
        do_replace "ingest-worker:" "data-worker:" "$compose_file" && FILES_CHANGED+=1
        
        # Container names
        do_replace "container_name: \${CONTAINER_PREFIX:-dev}-ingest-api" "container_name: \${CONTAINER_PREFIX:-dev}-data-api" "$compose_file"
        do_replace "container_name: \${CONTAINER_PREFIX:-dev}-ingest-worker" "container_name: \${CONTAINER_PREFIX:-dev}-data-worker" "$compose_file"
        
        # Hostnames
        do_replace "hostname: ingest-api" "hostname: data-api" "$compose_file"
        do_replace "hostname: ingest-worker" "hostname: data-worker" "$compose_file"
        
        # Dependencies
        do_replace "depends_on:.*ingest-api" "depends_on:\n      data-api" "$compose_file"
        
        # Volume names
        do_replace "ingest_temp:" "data_temp:" "$compose_file"
        do_replace "- ingest_temp" "- data_temp" "$compose_file"
        
        # Environment variables
        do_replace "AUTHZ_AUDIENCE: ingest-api" "AUTHZ_AUDIENCE: data-api" "$compose_file"
        do_replace "INGEST_API_URL" "DATA_API_URL" "$compose_file"
        do_replace "INGEST_API_HOST" "DATA_API_HOST" "$compose_file"
        do_replace "INGEST_API_PORT" "DATA_API_PORT" "$compose_file"
        do_replace "NEXT_PUBLIC_INGEST_API_URL" "NEXT_PUBLIC_DATA_API_URL" "$compose_file"
        
        # Audience lists
        do_replace "ingest-api" "data-api" "$compose_file"
    fi
done

# ============================================================================
# STEP 4: Update Ansible configuration
# ============================================================================
echo ""
echo -e "${GREEN}Step 4: Update Ansible configuration${NC}"

# site.yml
if [[ -f "provision/ansible/site.yml" ]]; then
    echo "  Processing: provision/ansible/site.yml"
    do_replace "hosts: ingest" "hosts: data" "provision/ansible/site.yml" && FILES_CHANGED+=1
    do_replace "role: ingest" "role: data" "provision/ansible/site.yml"
    do_replace "apis_ingest" "apis_data" "provision/ansible/site.yml"
    do_replace "ingest_api" "data_api" "provision/ansible/site.yml"
    do_replace "ingest_worker" "data_worker" "provision/ansible/site.yml"
fi

# Makefile
if [[ -f "provision/ansible/Makefile" ]]; then
    echo "  Processing: provision/ansible/Makefile"
    do_replace "ingest-api" "data-api" "provision/ansible/Makefile" && FILES_CHANGED+=1
    do_replace "ingest-worker" "data-worker" "provision/ansible/Makefile"
    do_replace "ingest-lxc" "data-lxc" "provision/ansible/Makefile"
    do_replace "INGEST_IP" "DATA_IP" "provision/ansible/Makefile"
fi

# Inventory files
INVENTORY_DIRS=(
    "provision/ansible/inventory/production"
    "provision/ansible/inventory/staging"
    "provision/ansible/inventory/docker"
    "provision/ansible/inventory/local"
    "provision/ansible/inventory/test"
)

for inv_dir in "${INVENTORY_DIRS[@]}"; do
    if [[ -d "$inv_dir" ]]; then
        echo "  Processing inventory: $inv_dir"
        
        # hosts.yml
        if [[ -f "$inv_dir/hosts.yml" ]]; then
            do_replace "ingest:" "data:" "$inv_dir/hosts.yml"
            do_replace "ingest-lxc" "data-lxc" "$inv_dir/hosts.yml"
            do_replace "STAGE-ingest-lxc" "STAGE-data-lxc" "$inv_dir/hosts.yml"
            do_replace "local-ingest" "local-data" "$inv_dir/hosts.yml"
        fi
        
        # group_vars
        for gv_file in "$inv_dir"/group_vars/**/*.yml; do
            if [[ -f "$gv_file" ]]; then
                do_replace "ingest_ip" "data_ip" "$gv_file"
                do_replace "ingest_host" "data_host" "$gv_file"
                do_replace "ingest_port" "data_port" "$gv_file"
                do_replace "ingest_api_port" "data_api_port" "$gv_file"
                do_replace "ingest_worker_host" "data_worker_host" "$gv_file"
                do_replace "ingest-api" "data-api" "$gv_file"
            fi
        done
    fi
done

# ============================================================================
# STEP 5: Update other Ansible roles that reference ingest
# ============================================================================
echo ""
echo -e "${GREEN}Step 5: Update cross-role references${NC}"

ROLES_TO_UPDATE=(
    "authz"
    "agent_api"
    "search_api"
    "internal_dns"
    "voice_agent"
    "secrets"
)

for role in "${ROLES_TO_UPDATE[@]}"; do
    role_path="provision/ansible/roles/$role"
    if [[ -d "$role_path" ]]; then
        echo "  Processing role: $role"
        
        # Update defaults
        if [[ -f "$role_path/defaults/main.yml" ]]; then
            do_replace "ingest-api" "data-api" "$role_path/defaults/main.yml"
            do_replace "ingest_api" "data_api" "$role_path/defaults/main.yml"
        fi
        
        # Update templates
        for tmpl in "$role_path"/templates/*.j2; do
            if [[ -f "$tmpl" ]]; then
                do_replace "ingest-api" "data-api" "$tmpl"
                do_replace "INGEST_API_URL" "DATA_API_URL" "$tmpl"
                do_replace "ingest_api" "data_api" "$tmpl"
            fi
        done
    fi
done

# ============================================================================
# STEP 6: Update scripts
# ============================================================================
echo ""
echo -e "${GREEN}Step 6: Update scripts${NC}"

SCRIPT_FILES=(
    "scripts/make/service-manage.sh"
    "scripts/make/service-deploy.sh"
    "scripts/make/manage.sh"
    "scripts/make/update.sh"
    "scripts/make/test-menu.sh"
    "scripts/lib/ui.sh"
    "scripts/test/run-local-tests.sh"
    "scripts/test/generate-local-test-env.sh"
    "scripts/test/bootstrap-test-credentials.sh"
    "scripts/docker/bootstrap-test-databases.py"
    "Makefile"
)

for script_file in "${SCRIPT_FILES[@]}"; do
    if [[ -f "$script_file" ]]; then
        echo "  Processing: $script_file"
        do_replace "ingest-api" "data-api" "$script_file" && FILES_CHANGED+=1
        do_replace "ingest-worker" "data-worker" "$script_file"
        do_replace "ingest-lxc" "data-lxc" "$script_file"
        do_replace "ingest|ingest-api" "data|data-api" "$script_file"
        do_replace "INGEST_API_URL" "DATA_API_URL" "$script_file"
        do_replace "INGEST_API_HOST" "DATA_API_HOST" "$script_file"
        do_replace "INGEST_API_PORT" "DATA_API_PORT" "$script_file"
        do_replace "local-ingest-api" "local-data-api" "$script_file"
    fi
done

# ============================================================================
# STEP 7: Update Proxmox/LXC scripts
# ============================================================================
echo ""
echo -e "${GREEN}Step 7: Update Proxmox scripts${NC}"

PCT_FILES=(
    "provision/pct/vars.env"
    "provision/pct/stage-vars.env"
    "provision/pct/containers/create_lxc_base.sh"
    "provision/pct/containers/create-worker-services.sh"
    "provision/pct/host/configure-gpu-allocation.sh"
)

for pct_file in "${PCT_FILES[@]}"; do
    if [[ -f "$pct_file" ]]; then
        echo "  Processing: $pct_file"
        do_replace "ingest-lxc" "data-lxc" "$pct_file" && FILES_CHANGED+=1
        do_replace "CT_INGEST" "CT_DATA" "$pct_file"
        do_replace "IP_INGEST" "IP_DATA" "$pct_file"
        do_replace "STAGE-ingest-lxc" "STAGE-data-lxc" "$pct_file"
    fi
done

# ============================================================================
# STEP 8: Update Python test files
# ============================================================================
echo ""
echo -e "${GREEN}Step 8: Update Python test files${NC}"

PYTHON_TEST_FILES=(
    "srv/authz/tests/conftest.py"
    "srv/authz/src/config.py"
    "srv/ingest/tests/conftest.py"
    "srv/ingest/tests/integration/test_pvt.py"
    "srv/agent/tests/integration/test_token_exchange_ingest.py"
)

for py_file in "${PYTHON_TEST_FILES[@]}"; do
    if [[ -f "$py_file" ]]; then
        echo "  Processing: $py_file"
        do_replace "ingest-api" "data-api" "$py_file" && FILES_CHANGED+=1
        do_replace "INGEST_API_URL" "DATA_API_URL" "$py_file"
    fi
done

# ============================================================================
# STEP 9: Update documentation
# ============================================================================
echo ""
echo -e "${GREEN}Step 9: Update documentation${NC}"

# Find and update markdown files
while IFS= read -r -d '' doc_file; do
    if grep -q "ingest-api\|ingest-lxc\|INGEST_API" "$doc_file" 2>/dev/null; then
        echo "  Processing: $doc_file"
        do_replace "ingest-api" "data-api" "$doc_file"
        do_replace "ingest-lxc" "data-lxc" "$doc_file"
        do_replace "ingest-worker" "data-worker" "$doc_file"
        do_replace "INGEST_API_" "DATA_API_" "$doc_file"
        FILES_CHANGED+=1
    fi
done < <(find docs -name "*.md" -print0 2>/dev/null)

# Update root files
for root_doc in CLAUDE.md README.md; do
    if [[ -f "$root_doc" ]]; then
        if grep -q "ingest-" "$root_doc" 2>/dev/null; then
            echo "  Processing: $root_doc"
            do_replace "ingest-api" "data-api" "$root_doc"
            do_replace "ingest-lxc" "data-lxc" "$root_doc"
            do_replace "ingest-worker" "data-worker" "$root_doc"
        fi
    fi
done

# ============================================================================
# Summary
# ============================================================================
echo ""
echo -e "${BLUE}=== Summary ===${NC}"
echo "Files potentially changed: $FILES_CHANGED"
echo "Total replacements: $REPLACEMENTS"

if $DRY_RUN; then
    echo ""
    echo -e "${YELLOW}This was a DRY RUN. No changes were made.${NC}"
    echo "Run with --execute to perform the actual rename."
    echo ""
    echo "After executing, you should:"
    echo "  1. Review changes with: git diff"
    echo "  2. Run tests: make test-ingest  # or new test target"
    echo "  3. Commit changes: git add -A && git commit -m 'refactor: rename ingest-api to data-api'"
else
    echo ""
    echo -e "${GREEN}Rename complete!${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Review changes with: git diff"
    echo "  2. Test the changes locally"
    echo "  3. Update DNS/hosts entries on Proxmox"
    echo "  4. Deploy to test environment first"
    echo "  5. Commit and push changes"
fi
