#!/usr/bin/env bash
# Check Deployed Application Version
# Usage: check-app-version.sh <app_name> [environment]
# Example: check-app-version.sh busibox-portal production
#
# Execution Context: Admin workstation
# Purpose: Display the currently deployed version of an application
# Requirements: SSH access to target container

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Parse arguments
APP_NAME="${1:-}"
ENVIRONMENT="${2:-production}"

if [ -z "$APP_NAME" ]; then
    echo -e "${RED}Error: App name is required${NC}"
    echo "Usage: $0 <app_name> [environment]"
    echo ""
    echo "Examples:"
    echo "  $0 busibox-portal production"
    echo "  $0 doc-intel test"
    echo "  $0 busibox-agents"
    echo ""
    echo "Available apps:"
    echo "  - busibox-portal"
    echo "  - busibox-agents"
    echo "  - doc-intel"
    exit 1
fi

# Determine deploy path based on app name
case "$APP_NAME" in
    busibox-portal)
        DEPLOY_PATH="/srv/apps/busibox-portal"
        CONTAINER_IP="10.96.200.201"  # apps-lxc
        ;;
    busibox-agents)
        DEPLOY_PATH="/srv/apps/busibox-agents"
        CONTAINER_IP="10.96.200.201"  # apps-lxc
        ;;
    doc-intel)
        DEPLOY_PATH="/srv/apps/doc-intel"
        CONTAINER_IP="10.96.200.201"  # apps-lxc
        ;;
    *)
        echo -e "${RED}Error: Unknown app '$APP_NAME'${NC}"
        exit 1
        ;;
esac

# Adjust for test environment
if [ "$ENVIRONMENT" == "test" ]; then
    CONTAINER_IP="10.96.201.201"  # test-apps-lxc
fi

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         Application Version Info                          ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}App:${NC}         $APP_NAME"
echo -e "${CYAN}Environment:${NC} $ENVIRONMENT"
echo -e "${CYAN}Container:${NC}   $CONTAINER_IP"
echo -e "${CYAN}Deploy Path:${NC} $DEPLOY_PATH"
echo ""

# Check if version file exists
VERSION_FILE="$DEPLOY_PATH/.deployed-version"

echo -e "${YELLOW}Checking version...${NC}"
echo ""

if ssh root@${CONTAINER_IP} "[ -f $VERSION_FILE ]" 2>/dev/null; then
    # Read and parse version file
    VERSION_JSON=$(ssh root@${CONTAINER_IP} "cat $VERSION_FILE" 2>/dev/null)
    
    if [ -n "$VERSION_JSON" ]; then
        echo -e "${GREEN}✓ Deployment version found${NC}"
        echo ""
        
        # Pretty print the JSON with colors
        echo "$VERSION_JSON" | python3 -c "
import sys
import json

try:
    data = json.load(sys.stdin)
    
    print('\033[1;37mDeployment Type:\033[0m', data.get('type', 'unknown').upper())
    
    if data.get('type') == 'release':
        print('\033[1;37mRelease:\033[0m', data.get('release', 'unknown'))
    elif data.get('type') == 'branch':
        print('\033[1;37mBranch:\033[0m', data.get('branch', 'unknown'))
    
    commit = data.get('commit', 'unknown')
    print('\033[1;37mCommit:\033[0m', commit[:7] if len(commit) > 7 else commit, f'({commit})' if len(commit) > 7 else '')
    
    print('\033[1;37mDeployed At:\033[0m', data.get('deployed_at', 'unknown'))
    print('\033[1;37mDeployed By:\033[0m', data.get('deployed_by', 'unknown'))
    
    if 'environment' in data:
        print('\033[1;37mEnvironment:\033[0m', data.get('environment', 'unknown'))
    
    if 'deployment_id' in data:
        print('\033[1;37mDeployment ID:\033[0m', data.get('deployment_id', 'unknown'))
        
except Exception as e:
    print(f'Error parsing version file: {e}', file=sys.stderr)
    sys.exit(1)
"
        
        echo ""
        echo -e "${CYAN}Full version file:${NC}"
        echo "$VERSION_JSON" | jq '.' 2>/dev/null || echo "$VERSION_JSON"
    else
        echo -e "${RED}✗ Version file is empty${NC}"
        exit 1
    fi
else
    echo -e "${RED}✗ No version file found${NC}"
    echo ""
    echo "This could mean:"
    echo "  1. The app was deployed before version tracking was implemented"
    echo "  2. The deployment failed before creating the version file"
    echo "  3. The app has not been deployed yet"
    echo ""
    echo "To deploy the app:"
    echo -e "  ${BLUE}bash scripts/deploy-app.sh $APP_NAME $ENVIRONMENT main${NC}"
    exit 1
fi

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                 Check Complete                             ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"

