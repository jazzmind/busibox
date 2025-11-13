#!/usr/bin/env bash
# Deploy Application from Branch
# Usage: deploy-app.sh <app_name> [environment] [branch]
# Example: deploy-app.sh ai-portal production main
#
# Execution Context: Admin workstation
# Purpose: Deploy an application directly from a GitHub branch without creating a release
# Requirements: Ansible, GitHub access token

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANSIBLE_DIR="${SCRIPT_DIR}/../provision/ansible"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Parse arguments
APP_NAME="${1:-}"
ENVIRONMENT="${2:-test}"
BRANCH="${3:-main}"

if [ -z "$APP_NAME" ]; then
    echo -e "${RED}Error: App name is required${NC}"
    echo "Usage: $0 <app_name> [environment] [branch]"
    echo ""
    echo "Examples:"
    echo "  $0 ai-portal production main"
    echo "  $0 doc-intel test dev"
    echo "  $0 agent-client production feature/new-ui"
    echo ""
    echo "Available apps:"
    echo "  - ai-portal"
    echo "  - agent-client"
    echo "  - doc-intel"
    exit 1
fi

# Validate environment
if [ "$ENVIRONMENT" != "production" ] && [ "$ENVIRONMENT" != "test" ] && [ "$ENVIRONMENT" != "local" ]; then
    echo -e "${RED}Error: Environment must be 'production', 'test', or 'local'${NC}"
    exit 1
fi

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         Deploy Application from Branch                     ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}App:${NC}         $APP_NAME"
echo -e "${GREEN}Environment:${NC} $ENVIRONMENT"
echo -e "${GREEN}Branch:${NC}      $BRANCH"
echo ""

# Change to ansible directory
cd "$ANSIBLE_DIR"

# Determine inventory
if [ "$ENVIRONMENT" == "production" ]; then
    INVENTORY="inventory/production/hosts.yml"
elif [ "$ENVIRONMENT" == "test" ]; then
    INVENTORY="inventory/test/hosts.yml"
else
    INVENTORY="inventory/local/hosts.yml"
fi

# Check if inventory exists
if [ ! -f "$INVENTORY" ]; then
    echo -e "${RED}Error: Inventory file not found: $INVENTORY${NC}"
    exit 1
fi

# Confirm deployment
echo -e "${YELLOW}⚠️  This will:${NC}"
echo "  1. Download the latest code from branch '$BRANCH'"
echo "  2. Install dependencies (npm install)"
echo "  3. Build the application (npm run build)"
echo "  4. Restart the application with PM2"
echo ""
read -p "Continue? (y/N) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${YELLOW}Deployment cancelled${NC}"
    exit 0
fi

echo ""
echo -e "${BLUE}Starting deployment...${NC}"
echo ""

# Run ansible playbook with branch deployment
ansible-playbook \
    -i "$INVENTORY" \
    site.yml \
    --tags "${APP_NAME}" \
    --vault-password-file $HOME/.vault_pass \
    --extra-vars "deploy_app=${APP_NAME}" \
    --extra-vars "deploy_branch=${BRANCH}" \
    --extra-vars "deploy_from_branch=true"

RESULT=$?

echo ""
if [ $RESULT -eq 0 ]; then
    echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${GREEN}║             Deployment Successful! ✓                       ║${NC}"
    echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${GREEN}Next steps:${NC}"
    echo "  1. Check application logs:"
    echo "     ${BLUE}bash scripts/tail-app-logs.sh $APP_NAME $ENVIRONMENT${NC}"
    echo ""
    echo "  2. Verify application is running:"
    echo "     ${BLUE}curl -f https://your-domain/api/health${NC}"
else
    echo -e "${RED}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${RED}║             Deployment Failed! ✗                           ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${RED}Check the error messages above for details${NC}"
    exit $RESULT
fi

