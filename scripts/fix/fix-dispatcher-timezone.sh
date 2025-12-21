#!/bin/bash
# Fix dispatcher_decision_log timezone column
# Run this on the Proxmox host or from admin workstation

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "================================"
echo "Fix Dispatcher Timezone Column"
echo "================================"
echo ""

# Determine environment
ENV="${1:-production}"
if [ "$ENV" = "test" ]; then
    AGENT_IP="10.96.201.207"
    PG_IP="10.96.201.203"
else
    AGENT_IP="10.96.200.207"
    PG_IP="10.96.200.203"
fi

echo "Environment: $ENV"
echo "Agent LXC: $AGENT_IP"
echo "PostgreSQL: $PG_IP"
echo ""

# Option 1: Apply via SQL directly on PostgreSQL
echo "${YELLOW}Applying fix via PostgreSQL...${NC}"
ssh root@$PG_IP "psql -U busibox_user -d busibox -c \"ALTER TABLE dispatcher_decision_log ALTER COLUMN timestamp TYPE TIMESTAMP WITH TIME ZONE;\"" 2>&1 | grep -v "already exists" || true

if [ $? -eq 0 ]; then
    echo "${GREEN}✓ Database schema updated successfully${NC}"
else
    echo "${RED}✗ Failed to update database schema${NC}"
    echo ""
    echo "Trying via Alembic migration..."
    
    # Option 2: Apply via Alembic on agent-lxc
    ssh root@$AGENT_IP "cd /srv/agent && source venv/bin/activate && export PYTHONPATH=/srv/agent && alembic upgrade head"
    
    if [ $? -eq 0 ]; then
        echo "${GREEN}✓ Migration applied successfully${NC}"
    else
        echo "${RED}✗ Failed to apply migration${NC}"
        exit 1
    fi
fi

echo ""
echo "${GREEN}✓ Fix applied successfully!${NC}"
echo ""
echo "Next steps:"
echo "1. Restart agent-api service: ssh root@$AGENT_IP 'systemctl restart agent-api'"
echo "2. Run tests: cd busibox-app && npm test"
echo ""
