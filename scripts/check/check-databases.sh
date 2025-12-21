#!/bin/bash
# Check PostgreSQL database status
# Created: $(date -u +"%Y-%m-%d")
# Status: Active
# Category: Diagnostics

set -euo pipefail

# Configuration
PG_IP="10.96.200.203"
PG_USER="busibox_user"

echo "==================================="
echo "PostgreSQL Database Check"
echo "==================================="
echo ""

# Check if we can connect to PostgreSQL
echo "=== Connection Test ==="
if ssh root@${PG_IP} "su - postgres -c 'psql -c \"SELECT version();\"' > /dev/null 2>&1"; then
    echo "✓ PostgreSQL is accessible on ${PG_IP}"
else
    echo "✗ Cannot connect to PostgreSQL on ${PG_IP}"
    exit 1
fi

# Get PostgreSQL version
echo ""
echo "=== PostgreSQL Version ==="
ssh root@${PG_IP} "su - postgres -c 'psql -c \"SELECT version();\"'"

# List all databases
echo ""
echo "==================================="
echo "All Databases:"
echo "==================================="
ssh root@${PG_IP} "su - postgres -c 'psql -c \"\\l\"'"

# Check specific application databases
echo ""
echo "==================================="
echo "Application Database Status:"
echo "==================================="

DATABASES=(
    "agent_server"
    "ai_portal"
    "agent_client"
    "doc_intel"
    "innovation"
    "litellm"
    "files"
    "busibox"
)

for db in "${DATABASES[@]}"; do
    echo -n "  ${db}: "
    DB_EXISTS=$(ssh root@${PG_IP} "su - postgres -c \"psql -tAc \\\"SELECT 1 FROM pg_database WHERE datname='${db}'\\\"\"")
    
    if [[ "${DB_EXISTS}" == "1" ]]; then
        echo "✓ EXISTS"
        
        # Check owner
        OWNER=$(ssh root@${PG_IP} "su - postgres -c \"psql -tAc \\\"SELECT pg_catalog.pg_get_userbyid(d.datdba) FROM pg_catalog.pg_database d WHERE d.datname = '${db}'\\\"\"")
        echo "    Owner: ${OWNER}"
        
        # Count tables (if database exists)
        TABLE_COUNT=$(ssh root@${PG_IP} "su - postgres -c \"psql -d ${db} -tAc \\\"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public'\\\"\"" 2>/dev/null || echo "0")
        echo "    Tables: ${TABLE_COUNT}"
        
    else
        echo "✗ MISSING"
    fi
done

# Check user privileges
echo ""
echo "==================================="
echo "User '${PG_USER}' Privileges:"
echo "==================================="
ssh root@${PG_IP} "su - postgres -c \"psql -c \\\"SELECT rolname, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles WHERE rolname = '${PG_USER}';\\\"\""

# For each existing database, check if busibox_user can connect
echo ""
echo "==================================="
echo "Connection Test (as ${PG_USER}):"
echo "==================================="

for db in "${DATABASES[@]}"; do
    DB_EXISTS=$(ssh root@${PG_IP} "su - postgres -c \"psql -tAc \\\"SELECT 1 FROM pg_database WHERE datname='${db}'\\\"\"")
    
    if [[ "${DB_EXISTS}" == "1" ]]; then
        echo -n "  ${db}: "
        
        # Try to connect and run a simple query
        if ssh root@${PG_IP} "PGPASSWORD='0f7806b26ec51d4884ea1fa74cb0e58b4cb6cf396249ce2f95c793554019a833' psql -U ${PG_USER} -h localhost -d ${db} -c 'SELECT 1;' > /dev/null 2>&1"; then
            echo "✓ Can connect"
        else
            echo "✗ Cannot connect (check password/privileges)"
        fi
    fi
done

echo ""
echo "==================================="
echo "Complete"
echo "==================================="

