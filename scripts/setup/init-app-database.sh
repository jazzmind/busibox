#!/bin/bash
# init-app-database.sh
#
# Initialize or sync application database schema using Prisma
#
# Execution Context: admin workstation OR apps-lxc container
# Dependencies: Node.js, npm, Prisma
#
# Usage:
#   From host:    bash scripts/init-app-database.sh <app-name> [environment]
#   In container: bash /usr/local/bin/init-app-database.sh <app-name>
#
# Examples:
#   bash scripts/init-app-database.sh ai-portal production
#   bash /usr/local/bin/init-app-database.sh ai-portal

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

APP_NAME="${1:-}"
ENVIRONMENT="${2:-production}"

# Check if running in container
if [ -d "/srv/apps" ]; then
    RUNNING_IN_CONTAINER=true
    # If only 1 arg in container, assume production
    if [ $# -eq 1 ]; then
        ENVIRONMENT="production"
    fi
else
    RUNNING_IN_CONTAINER=false
fi

usage() {
    echo -e "${BLUE}Usage:${NC}"
    if [ "$RUNNING_IN_CONTAINER" = true ]; then
        echo "  $0 <app-name>"
    else
        echo "  $0 <app-name> [environment]"
    fi
    echo ""
    echo "Examples:"
    if [ "$RUNNING_IN_CONTAINER" = true ]; then
        echo "  $0 ai-portal"
        echo "  $0 agent-manager"
    else
        echo "  $0 ai-portal production"
        echo "  $0 agent-manager test"
    fi
    exit 1
}

if [ -z "$APP_NAME" ]; then
    echo -e "${RED}Error: Application name is required${NC}"
    usage
fi

# Function to initialize database
init_database() {
    local app="$1"
    local deploy_path="$2"
    
    echo -e "${GREEN}=== Initializing database for ${app} ===${NC}"
    echo ""
    
    # Check if app directory exists
    if [ ! -d "$deploy_path" ]; then
        echo -e "${RED}Error: Application directory not found: ${deploy_path}${NC}"
        exit 1
    fi
    
    # Check if package.json exists
    if [ ! -f "$deploy_path/package.json" ]; then
        echo -e "${RED}Error: No package.json found in ${deploy_path}${NC}"
        exit 1
    fi
    
    # Check if Prisma schema exists
    if [ ! -f "$deploy_path/prisma/schema.prisma" ]; then
        echo -e "${YELLOW}Warning: No Prisma schema found. This app may not use Prisma.${NC}"
        exit 0
    fi
    
    # Change to app directory
    cd "$deploy_path"
    
    echo -e "${BLUE}Step 1: Generating Prisma client...${NC}"
    if ! npm run db:generate 2>&1; then
        echo -e "${RED}Failed to generate Prisma client${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Prisma client generated${NC}"
    echo ""
    
    echo -e "${BLUE}Step 2: Pushing schema to database...${NC}"
    if ! npm run db:push 2>&1; then
        echo -e "${RED}Failed to push schema${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ Schema pushed to database${NC}"
    echo ""
    
    # Check if seed script exists
    if grep -q '"db:seed"' package.json; then
        echo -e "${BLUE}Step 3: Seeding database (optional)...${NC}"
        echo -e "${YELLOW}Do you want to seed the database? This will create initial data. (y/N)${NC}"
        read -r response
        if [[ "$response" =~ ^[Yy]$ ]]; then
            if npm run db:seed 2>&1; then
                echo -e "${GREEN}✓ Database seeded${NC}"
            else
                echo -e "${YELLOW}⚠ Seeding failed or skipped${NC}"
            fi
        else
            echo -e "${YELLOW}Skipping seed${NC}"
        fi
    fi
    
    echo ""
    echo -e "${GREEN}=== Database initialization complete ===${NC}"
}

# Main execution
if [ "$RUNNING_IN_CONTAINER" = true ]; then
    # Running inside container
    echo -e "${GREEN}Running in container${NC}"
    APP_PATH="/srv/apps/${APP_NAME}"
    
    if [ ! -d "$APP_PATH" ]; then
        # Try alternative paths
        APP_PATH="/srv/${APP_NAME}"
    fi
    
    init_database "$APP_NAME" "$APP_PATH"
else
    # Running on host - SSH to container
    case "$ENVIRONMENT" in
        production)
            APPS_IP="10.96.200.201"
            ;;
        test)
            APPS_IP="10.96.201.201"
            ;;
        *)
            echo -e "${RED}Error: Invalid environment '${ENVIRONMENT}'${NC}"
            echo "Valid environments: production, test"
            exit 1
            ;;
    esac
    
    echo -e "${GREEN}Connecting to apps-lxc ($ENVIRONMENT - $APPS_IP)...${NC}"
    echo ""
    
    # SSH and run initialization
    ssh -o StrictHostKeyChecking=no "root@$APPS_IP" "bash -s" <<EOF
        set -euo pipefail
        
        APP_PATH="/srv/apps/$APP_NAME"
        if [ ! -d "\$APP_PATH" ]; then
            APP_PATH="/srv/$APP_NAME"
        fi
        
        if [ ! -d "\$APP_PATH" ]; then
            echo "Error: Application directory not found for $APP_NAME"
            exit 1
        fi
        
        cd "\$APP_PATH"
        
        echo "=== Initializing database for $APP_NAME ==="
        echo ""
        
        # Generate Prisma client
        echo "Step 1: Generating Prisma client..."
        npm run db:generate
        echo "✓ Prisma client generated"
        echo ""
        
        # Push schema
        echo "Step 2: Pushing schema to database..."
        npm run db:push
        echo "✓ Schema pushed to database"
        echo ""
        
        echo "=== Database initialization complete ==="
EOF
fi

echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo -e "  1. Verify tables: ${BLUE}bash scripts/check-database.sh $APP_NAME $ENVIRONMENT${NC}"
echo -e "  2. Restart app: ${BLUE}ssh root@\$APPS_IP 'systemctl restart $APP_NAME'${NC}"
echo -e "  3. Check logs: ${BLUE}bash scripts/tail-app-logs.sh $APP_NAME $ENVIRONMENT${NC}"

