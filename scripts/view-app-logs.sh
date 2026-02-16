#!/bin/bash
# view-app-logs.sh
# 
# View application logs from systemd-managed apps
# 
# Execution Context: admin workstation OR apps-lxc container
# Dependencies: systemd, ssh (if on host)
# 
# Usage:
#   From host:    bash scripts/view-app-logs.sh <app-name> [environment] [lines]
#   In container: bash /usr/local/bin/view-app-logs.sh <app-name> [lines]
# 
# Examples:
#   bash scripts/view-app-logs.sh busibox-portal production 100
#   bash scripts/view-app-logs.sh busibox-agents test 50
#   bash /usr/local/bin/view-app-logs.sh busibox-portal 100  # From inside container

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
LINES="${3:-100}"

# Check if running inside container or on host (needs ssh)
if [ -f /etc/hostname ] && grep -q "apps-lxc" /etc/hostname 2>/dev/null; then
    RUNNING_IN_CONTAINER=true
    # If only 2 args provided in container, second is lines not environment
    if [ $# -eq 2 ] && [[ "${2}" =~ ^[0-9]+$ ]]; then
        LINES="${2}"
        ENVIRONMENT="production"
    fi
else
    RUNNING_IN_CONTAINER=false
fi

usage() {
    echo -e "${BLUE}Usage:${NC}"
    if [ "$RUNNING_IN_CONTAINER" = true ]; then
        echo "  $0 <app-name> [lines]"
        echo ""
        echo "Examples:"
        echo "  $0 busibox-portal 50         # Show last 50 lines"
        echo "  $0 busibox-agents         # Show last 100 lines (default)"
    else
        echo "  $0 <app-name> [environment] [lines]"
        echo ""
        echo "Examples:"
        echo "  $0 busibox-portal production 50   # Show last 50 lines from production"
        echo "  $0 busibox-agents test         # Show last 100 lines from test"
    fi
    exit 1
}

if [ -z "$APP_NAME" ]; then
    echo -e "${RED}Error: Application name is required${NC}"
    usage
fi

# Function to display logs
view_logs() {
    local app="$1"
    local lines="$2"
    
    echo -e "${GREEN}=== Viewing logs for ${app} (last ${lines} lines) ===${NC}"
    echo ""
    
    # Check if service exists
    if ! systemctl list-units --type=service --all | grep -q "${app}.service"; then
        echo -e "${RED}Error: Service '${app}' not found${NC}"
        echo ""
        echo -e "${YELLOW}Available services:${NC}"
        systemctl list-units --type=service --state=running | grep -E '(busibox-portal|busibox-agents|doc-intel|innovation)'
        exit 1
    fi
    
    # Show logs from journald
    echo -e "${BLUE}Service Logs:${NC}"
    journalctl -u "${app}.service" -n "$lines" --no-pager
}

# Main execution
if [ "$RUNNING_IN_CONTAINER" = true ]; then
    # Running inside container - direct access
    echo -e "${GREEN}Running in container - accessing logs directly${NC}"
    echo ""
    view_logs "$APP_NAME" "$LINES"
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
    
    echo -e "${GREEN}Connecting to apps-lxc ($ENVIRONMENT - $APPS_IP)...${NC}"
    echo ""
    
    # SSH to container and view logs
    ssh -o StrictHostKeyChecking=no "root@$APPS_IP" "bash -c '
        set -euo pipefail
        
        if ! systemctl list-units --type=service --all | grep -q \"$APP_NAME.service\"; then
            echo \"Error: Service '\'$APP_NAME\'' not found\"
            echo \"\"
            echo \"Available services:\"
            systemctl list-units --type=service --state=running | grep -E '\''(busibox-portal|busibox-agents|doc-intel|innovation)'\''
            exit 1
        fi
        
        echo \"=== Viewing logs for $APP_NAME (last $LINES lines) ===\"
        echo \"\"
        journalctl -u \"$APP_NAME.service\" -n \"$LINES\" --no-pager
    '"
fi

echo ""
echo -e "${YELLOW}Tip: To follow logs in real-time, use:${NC}"
if [ "$RUNNING_IN_CONTAINER" = true ]; then
    echo -e "  ${BLUE}journalctl -u $APP_NAME.service -f${NC}"
else
    echo -e "  ${BLUE}ssh root@$APPS_IP 'journalctl -u $APP_NAME.service -f'${NC}"
fi

