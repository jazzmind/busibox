#!/bin/bash
# psql-connect.sh
#
# Connect to PostgreSQL database via psql
#
# Execution Context: admin workstation OR pg-lxc container
# Dependencies: psql (if in container)
#
# Usage:
#   From host:    bash scripts/psql-connect.sh [database] [environment]
#   In container: psql -U busibox_user -d <database>
#
# Examples:
#   bash scripts/psql-connect.sh ai_portal production
#   bash scripts/psql-connect.sh busibox test
#   bash scripts/psql-connect.sh  # Interactive selection

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

DATABASE="${1:-}"
ENVIRONMENT="${2:-production}"

# Check if running in container
if command -v psql &> /dev/null && [ -f "/var/lib/postgresql/data/postgresql.conf" ]; then
    RUNNING_IN_CONTAINER=true
else
    RUNNING_IN_CONTAINER=false
fi

usage() {
    echo -e "${BLUE}Usage:${NC}"
    if [ "$RUNNING_IN_CONTAINER" = true ]; then
        echo "  psql -U busibox_user -d <database>"
    else
        echo "  $0 [database] [environment]"
    fi
    echo ""
    echo "Examples:"
    if [ "$RUNNING_IN_CONTAINER" = true ]; then
        echo "  psql -U busibox_user -d ai_portal"
    else
        echo "  $0 ai_portal production"
        echo "  $0 busibox test"
        echo "  $0  # Interactive selection"
    fi
    exit 1
}

# Function to list databases
list_databases() {
    local pg_ip="$1"
    echo -e "${BLUE}Available databases:${NC}"
    ssh -o StrictHostKeyChecking=no "root@$pg_ip" "su - postgres -c 'psql -l -t'" | grep -v template | grep -v "^\s*$" | awk '{print "  - " $1}'
}

# Function to connect
connect_db() {
    local pg_ip="$1"
    local database="$2"
    
    echo -e "${GREEN}Connecting to database: ${database}${NC}"
    echo -e "${YELLOW}Useful commands:${NC}"
    echo -e "  ${BLUE}\\dt${NC}          - List tables"
    echo -e "  ${BLUE}\\d <table>${NC}   - Describe table"
    echo -e "  ${BLUE}\\du${NC}          - List users"
    echo -e "  ${BLUE}\\l${NC}           - List databases"
    echo -e "  ${BLUE}\\q${NC}           - Quit"
    echo ""
    
    ssh -t -o StrictHostKeyChecking=no "root@$pg_ip" "su - postgres -c 'psql -d $database'"
}

# Main execution
if [ "$RUNNING_IN_CONTAINER" = true ]; then
    echo -e "${YELLOW}You're already in the PostgreSQL container!${NC}"
    echo -e "Use: ${BLUE}psql -U busibox_user -d <database>${NC}"
    echo ""
    list_databases "localhost"
    exit 0
fi

# Determine container IP
case "$ENVIRONMENT" in
    production)
        PG_IP="10.96.200.203"
        ;;
    test)
        PG_IP="10.96.201.203"
        ;;
    *)
        echo -e "${RED}Error: Invalid environment '${ENVIRONMENT}'${NC}"
        echo "Valid environments: production, test"
        exit 1
        ;;
esac

# If no database specified, show list and prompt
if [ -z "$DATABASE" ]; then
    list_databases "$PG_IP"
    echo ""
    echo -e "${YELLOW}Enter database name (or press Enter for 'busibox'):${NC} "
    read -r DATABASE
    if [ -z "$DATABASE" ]; then
        DATABASE="busibox"
    fi
fi

connect_db "$PG_IP" "$DATABASE"

