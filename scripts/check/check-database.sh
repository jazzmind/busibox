#!/bin/bash
# check-database.sh
#
# Check database tables and schema for an application
#
# Execution Context: admin workstation OR pg-lxc container
# Dependencies: psql
#
# Usage:
#   From host:    bash scripts/check/check-database.sh [database] [environment]
#   In container: psql -U busibox_user -d <database> -c "\dt"
#
# Examples:
#   bash scripts/check/check-database.sh ai_portal production
#   bash scripts/check/check-database.sh busibox test

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

DATABASE="${1:-busibox}"
ENVIRONMENT="${2:-production}"

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

echo -e "${GREEN}=== Checking database: ${DATABASE} (${ENVIRONMENT}) ===${NC}"
echo ""

# Check if database exists
echo -e "${BLUE}Checking if database exists...${NC}"
if ! ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -lqt'" | cut -d \| -f 1 | grep -qw "$DATABASE"; then
    echo -e "${RED}✗ Database '${DATABASE}' does not exist${NC}"
    echo ""
    echo -e "${YELLOW}Available databases:${NC}"
    ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -l'" | grep -v template
    exit 1
fi
echo -e "${GREEN}✓ Database exists${NC}"
echo ""

# List tables
echo -e "${BLUE}Tables in database:${NC}"
ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -d $DATABASE -c \"\\dt\"'"
echo ""

# Count tables
TABLE_COUNT=$(ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -d $DATABASE -t -c \"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '\'public\'';\"'" | tr -d ' ')
echo -e "${GREEN}Total tables: ${TABLE_COUNT}${NC}"
echo ""

# Show table sizes
echo -e "${BLUE}Table sizes:${NC}"
ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -d $DATABASE -c \"SELECT schemaname, tablename, pg_size_pretty(pg_total_relation_size(quote_ident(schemaname)||'\''.'\''||quote_ident(tablename))) AS size FROM pg_tables WHERE schemaname = '\''public'\'' ORDER BY pg_total_relation_size(quote_ident(schemaname)||'\''.'\''||quote_ident(tablename)) DESC LIMIT 10;\"'"
echo ""

# Check for Prisma migrations table
if ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -d $DATABASE -t -c \"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '\''public'\'' AND table_name = '\''_prisma_migrations'\'');\"'" | grep -q "t"; then
    echo -e "${BLUE}Prisma migration history:${NC}"
    ssh -o StrictHostKeyChecking=no "root@$PG_IP" "su - postgres -c 'psql -d $DATABASE -c \"SELECT migration_name, finished_at, applied_steps_count FROM _prisma_migrations ORDER BY finished_at DESC LIMIT 5;\"'"
    echo ""
fi

echo -e "${GREEN}=== Database check complete ===${NC}"
echo ""
echo -e "${YELLOW}Quick commands:${NC}"
echo -e "  Connect: ${BLUE}bash scripts/psql-connect.sh $DATABASE $ENVIRONMENT${NC}"
echo -e "  Describe table: ${BLUE}ssh root@$PG_IP \"su - postgres -c 'psql -d $DATABASE -c \\\"\\\\d <table>\\\"'\"${NC}"

