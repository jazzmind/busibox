#!/bin/bash
# Create application databases on PostgreSQL
# Created: $(date -u +"%Y-%m-%d")
# Status: Active
# Category: Database Setup

set -euo pipefail

# Configuration
PG_IP="10.96.200.203"
PG_USER="busibox_user"

echo "==================================="
echo "Creating Application Databases"
echo "==================================="
echo ""

# List of databases to create
DATABASES=(
    "agent_server"
    "ai_portal"
    "agent_client"
    "doc_intel"
    "innovation"
    "litellm"
    "files"
)

echo "This script will create the following databases:"
for db in "${DATABASES[@]}"; do
    echo "  - $db"
done
echo ""

# Check if we can connect to PostgreSQL
echo "Checking PostgreSQL connection..."
if ! ssh root@${PG_IP} "su - postgres -c 'psql -c \"SELECT version();\"' > /dev/null 2>&1"; then
    echo "❌ Cannot connect to PostgreSQL on ${PG_IP}"
    exit 1
fi
echo "✓ PostgreSQL is accessible"
echo ""

# Create each database
for db in "${DATABASES[@]}"; do
    echo -n "Creating database: ${db}... "
    
    # Check if database exists
    DB_EXISTS=$(ssh root@${PG_IP} "su - postgres -c \"psql -tAc \\\"SELECT 1 FROM pg_database WHERE datname='${db}'\\\"\"")
    
    if [[ "${DB_EXISTS}" == "1" ]]; then
        echo "✓ Already exists"
    else
        # Create database and grant privileges
        ssh root@${PG_IP} "su - postgres -c \"psql -c \\\"CREATE DATABASE ${db};\\\"\"" > /dev/null 2>&1 || {
            echo "❌ Failed to create"
            continue
        }
        
        # Grant privileges to busibox_user
        ssh root@${PG_IP} "su - postgres -c \"psql -c \\\"GRANT ALL PRIVILEGES ON DATABASE ${db} TO ${PG_USER};\\\"\"" > /dev/null 2>&1
        
        # Connect to the database and grant schema privileges
        ssh root@${PG_IP} "su - postgres -c \"psql -d ${db} -c \\\"GRANT ALL ON SCHEMA public TO ${PG_USER};\\\"\"" > /dev/null 2>&1
        ssh root@${PG_IP} "su - postgres -c \"psql -d ${db} -c \\\"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO ${PG_USER};\\\"\"" > /dev/null 2>&1
        ssh root@${PG_IP} "su - postgres -c \"psql -d ${db} -c \\\"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO ${PG_USER};\\\"\"" > /dev/null 2>&1
        
        echo "✓ Created and configured"
    fi
done

echo ""
echo "==================================="
echo "Database List:"
echo "==================================="
ssh root@${PG_IP} "su - postgres -c 'psql -c \"\\l\"'" | grep -E "agent_server|ai_portal|agent_client|doc_intel|innovation|litellm|files|Name" || true

echo ""
echo "==================================="
echo "Complete!"
echo "==================================="
echo ""
echo "You can now run migrations for each application."

