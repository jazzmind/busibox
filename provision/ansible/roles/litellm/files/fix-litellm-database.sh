#!/usr/bin/env bash
#
# Fix LiteLLM Database Schema Issues
#
# EXECUTION CONTEXT: Inside LiteLLM container (as litellm user)
# PURPOSE: Properly initialize/fix LiteLLM database schema
#
# USAGE:
#   bash /usr/local/bin/fix-litellm-database.sh
#
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

echo "=========================================="
echo "LiteLLM Database Schema Fix"
echo "=========================================="
echo ""

# Load environment
if [ -f /etc/default/litellm ]; then
    log_info "Loading environment from /etc/default/litellm"
    
    # Check if we have read permission
    if [ ! -r /etc/default/litellm ]; then
        log_error "Cannot read /etc/default/litellm (permission denied)"
        log_info "File permissions:"
        ls -la /etc/default/litellm
        exit 1
    fi
    
    # Source with set +e to catch errors
    set +e
    source /etc/default/litellm
    SOURCE_EXIT=$?
    set -e
    
    if [ $SOURCE_EXIT -ne 0 ]; then
        log_error "Failed to source /etc/default/litellm (exit code: $SOURCE_EXIT)"
        exit 1
    fi
else
    log_error "/etc/default/litellm not found"
    exit 1
fi

# Check DATABASE_URL
if [ -z "${DATABASE_URL:-}" ]; then
    log_error "DATABASE_URL not set after sourcing /etc/default/litellm"
    log_info "Available environment variables:"
    env | grep -i database || echo "  No DATABASE variables found"
    exit 1
fi

log_info "Database URL: ${DATABASE_URL%%@*}@***" # Hide password
log_success "Environment loaded successfully"
echo ""

# Activate venv
log_info "Activating virtual environment"
source /opt/litellm/venv/bin/activate

# Get LiteLLM directory
LITELLM_DIR=$(python -c "import os, litellm; print(os.path.dirname(litellm.__file__))")
PRISMA_DIR="${LITELLM_DIR}/proxy"

log_info "LiteLLM directory: $LITELLM_DIR"
log_info "Prisma schema directory: $PRISMA_DIR"
echo ""

# Navigate to prisma directory
cd "$PRISMA_DIR"

# Check if schema.prisma exists
if [ ! -f "schema.prisma" ]; then
    log_error "schema.prisma not found in $PRISMA_DIR"
    exit 1
fi

log_success "Found Prisma schema"
echo ""

# Step 1: Clean existing Prisma generated files
log_info "Step 1: Cleaning existing Prisma generated files"
if [ -d ".prisma" ]; then
    rm -rf .prisma
    log_success "Removed .prisma directory"
fi

# Step 2: Generate Prisma client
log_info "Step 2: Generating Prisma client"
export DATABASE_URL
prisma generate 2>&1 | tee /tmp/prisma-generate.log

if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log_error "Prisma generate failed. Check /tmp/prisma-generate.log"
    exit 1
fi

log_success "Prisma client generated"
echo ""

# Step 3: Check database connection
log_info "Step 3: Checking database connection"
psql "${DATABASE_URL}" -c "SELECT 1;" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    log_success "Database connection successful"
else
    log_error "Cannot connect to database"
    exit 1
fi

echo ""

# Step 4: Drop all LiteLLM tables (DESTRUCTIVE - recreates fresh)
log_warn "Step 4: Dropping existing LiteLLM tables (if any)"
log_warn "This will DELETE all existing data!"
echo ""
read -p "Continue? (yes/no): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    log_error "Aborted by user"
    exit 1
fi

# Drop tables
psql "${DATABASE_URL}" << 'EOF' 2>&1 | tee /tmp/prisma-drop.log
DO $$ DECLARE
    r RECORD;
BEGIN
    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'LiteLLM_%') LOOP
        EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
        RAISE NOTICE 'Dropped table: %', r.tablename;
    END LOOP;
END $$;
EOF

log_success "Dropped existing LiteLLM tables"
echo ""

# Step 5: Push fresh schema to database
log_info "Step 5: Creating fresh database schema"
export DATABASE_URL
prisma db push --accept-data-loss --skip-generate 2>&1 | tee /tmp/prisma-push.log

if [ ${PIPESTATUS[0]} -ne 0 ]; then
    log_error "Prisma db push failed. Check /tmp/prisma-push.log"
    exit 1
fi

log_success "Database schema created successfully"
echo ""

# Step 6: Verify tables were created
log_info "Step 6: Verifying tables"
psql "${DATABASE_URL}" -c "\dt LiteLLM_*" 2>&1 | tee /tmp/prisma-tables.log

TABLE_COUNT=$(psql "${DATABASE_URL}" -t -c "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'LiteLLM_%';")

log_info "Found ${TABLE_COUNT} LiteLLM tables"

if [ "$TABLE_COUNT" -gt 20 ]; then
    log_success "Database schema looks correct"
else
    log_warn "Expected more than 20 tables, only found ${TABLE_COUNT}"
fi

echo ""
echo "=========================================="
log_success "Database schema fixed!"
echo "=========================================="
echo ""
log_info "Next steps:"
echo "1. Restart LiteLLM service: systemctl restart litellm"
echo "2. Check logs: journalctl -u litellm -f"
echo "3. Test API: curl http://localhost:4000/health"
echo ""

