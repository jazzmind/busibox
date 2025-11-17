#!/bin/bash
# tail-app-logs.sh
# 
# Follow (tail -f) application logs in real-time
# 
# Execution Context: admin workstation OR apps-lxc container
# Dependencies: pm2 (if in container), ssh (if on host)
# 
# Usage:
#   From host:    bash scripts/tail-app-logs.sh <app-name> [environment]
#   In container: bash /usr/local/bin/tail-app-logs.sh <app-name>
# 
# Examples:
#   bash scripts/tail-app-logs.sh ai-portal production
#   bash scripts/tail-app-logs.sh agent-client test
#   bash /usr/local/bin/tail-app-logs.sh ai-portal  # From inside container

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
APP_NAME="${1:-}"
ENVIRONMENT="${2:-production}"

# Check if running inside container (has pm2) or on host (needs ssh)
if command -v pm2 &> /dev/null; then
    RUNNING_IN_CONTAINER=true
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
    else
        echo "  $0 ai-portal production"
        echo "  $0 agent-client test"
    fi
    exit 1
}

if [ -z "$APP_NAME" ]; then
    echo -e "${RED}Error: Application name is required${NC}"
    usage
fi

# Main execution
if [ "$RUNNING_IN_CONTAINER" = true ]; then
    # Running inside container - direct PM2 access
    echo -e "${GREEN}Following logs for ${APP_NAME}...${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    echo ""
    
    if ! pm2 describe "$APP_NAME" &> /dev/null; then
        echo -e "${RED}Error: Application '${APP_NAME}' not found in PM2${NC}"
        echo ""
        echo -e "${YELLOW}Available applications:${NC}"
        pm2 list
        exit 1
    fi
    
    pm2 logs "$APP_NAME"
else
    # Running on host - SSH to container
    # Determine container IP based on environment
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
    
    echo -e "${GREEN}Following logs for ${APP_NAME} on ${ENVIRONMENT}...${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop${NC}"
    echo ""
    
    ssh -o StrictHostKeyChecking=no "root@$APPS_IP" "pm2 logs '$APP_NAME'"
fi

