#!/usr/bin/env bash
# Update Bedrock credentials in Ansible vault
# 
# Execution context: Admin workstation
# Purpose: Safely update Bedrock API credentials in vault
# Usage: bash scripts/update-bedrock-credentials.sh [test|production]

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUSIBOX_ROOT="$(dirname "$SCRIPT_DIR")"
ENV="${1:-test}"

VAULT_FILE="$BUSIBOX_ROOT/provision/ansible/roles/secrets/vars/vault.yml"

echo -e "${BLUE}=== Update Bedrock Credentials ===${NC}"
echo "Environment: $ENV"
echo "Vault file: $VAULT_FILE"
echo ""

# Check if vault file exists
if [[ ! -f "$VAULT_FILE" ]]; then
    echo -e "${RED}✗ Vault file not found: $VAULT_FILE${NC}"
    exit 1
fi

echo -e "${YELLOW}This script will help you update Bedrock credentials in the vault.${NC}"
echo ""

# Determine credential format
echo -e "${BLUE}What type of Bedrock credentials do you have?${NC}"
echo ""
echo "1) AWS IAM credentials (Access Key ID + Secret Access Key) - RECOMMENDED"
echo "2) Bedrock bearer token API key"
echo "3) I don't know / run diagnostic first"
echo ""
read -p "Enter choice [1-3]: " cred_type

case $cred_type in
    1)
        echo ""
        echo -e "${GREEN}You selected: AWS IAM credentials${NC}"
        echo "This is the correct format for LiteLLM."
        echo ""
        
        echo "Enter AWS Access Key ID (20 characters, starts with AKIA):"
        read -r access_key_id
        
        echo "Enter AWS Secret Access Key (40 characters):"
        read -rs secret_key
        echo ""
        
        # Validate format
        if [[ ! "$access_key_id" =~ ^AKIA[A-Z0-9]{16}$ ]]; then
            echo -e "${YELLOW}⚠ Warning: Access Key ID doesn't match expected format (AKIAXXXXXXXXXXXXXXXX)${NC}"
            echo "  This might still work if it's a valid AWS credential."
            read -p "Continue anyway? [y/N]: " confirm
            [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
        fi
        
        BEDROCK_API_KEY="${access_key_id}:${secret_key}"
        ;;
        
    2)
        echo ""
        echo -e "${YELLOW}⚠ Bedrock bearer tokens don't work directly with LiteLLM${NC}"
        echo ""
        echo "LiteLLM uses the AWS SDK which requires IAM credentials."
        echo "You have two options:"
        echo ""
        echo "1. Get AWS IAM credentials instead (recommended)"
        echo "2. Set up a custom proxy (advanced)"
        echo ""
        read -p "Do you want to continue with bearer token anyway? [y/N]: " confirm
        
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            echo "Please obtain AWS IAM credentials and run this script again."
            exit 0
        fi
        
        echo "Enter Bedrock bearer token:"
        read -rs BEDROCK_API_KEY
        echo ""
        ;;
        
    3)
        echo ""
        echo -e "${BLUE}Running diagnostic script...${NC}"
        echo ""
        bash "$SCRIPT_DIR/diagnose-bedrock-auth.sh"
        echo ""
        echo "After reviewing the diagnostic results, run this script again."
        exit 0
        ;;
        
    *)
        echo -e "${RED}Invalid choice${NC}"
        exit 1
        ;;
esac

# Get region
echo ""
echo "Enter AWS region [us-east-1]:"
read -r region
region="${region:-us-east-1}"

# Confirm before updating
echo ""
echo -e "${YELLOW}=== Review Settings ===${NC}"
echo "Credential format: ${cred_type}"
if [[ $cred_type == "1" ]]; then
    echo "Access Key ID: ${access_key_id:0:10}... (${#access_key_id} chars)"
    echo "Secret Key: ****** (${#secret_key} chars)"
else
    echo "API Key: ${BEDROCK_API_KEY:0:10}... (${#BEDROCK_API_KEY} chars)"
fi
echo "Region: $region"
echo ""
read -p "Update vault with these credentials? [y/N]: " confirm

if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi

# Create temporary Python script to update vault
cat > /tmp/update_vault.py <<'PYEOF'
import sys
import yaml
import subprocess
import tempfile
import os

vault_file = sys.argv[1]
api_key = sys.argv[2]
region = sys.argv[3]

# Decrypt vault
result = subprocess.run(
    ['ansible-vault', 'decrypt', '--output', '-', vault_file],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print(f"Error decrypting vault: {result.stderr}")
    sys.exit(1)

# Parse YAML
try:
    vault_data = yaml.safe_load(result.stdout)
except Exception as e:
    print(f"Error parsing vault YAML: {e}")
    sys.exit(1)

# Update Bedrock credentials
if 'secrets' not in vault_data:
    vault_data['secrets'] = {}

vault_data['secrets']['bedrock_api_key'] = api_key
vault_data['secrets']['bedrock_region'] = region

# Add litellm section if it doesn't exist
if 'litellm' not in vault_data['secrets']:
    vault_data['secrets']['litellm'] = {}

vault_data['secrets']['litellm']['bedrock_api_key'] = api_key

# Write to temp file
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yml') as tmp:
    yaml.dump(vault_data, tmp, default_flow_style=False)
    tmp_path = tmp.name

# Re-encrypt vault
result = subprocess.run(
    ['ansible-vault', 'encrypt', '--output', vault_file, tmp_path],
    capture_output=True,
    text=True
)

# Clean up
os.unlink(tmp_path)

if result.returncode != 0:
    print(f"Error encrypting vault: {result.stderr}")
    sys.exit(1)

print("✓ Vault updated successfully")
PYEOF

# Update vault
echo ""
echo -e "${BLUE}Updating vault...${NC}"

python3 /tmp/update_vault.py "$VAULT_FILE" "$BEDROCK_API_KEY" "$region" 2>&1

if [[ $? -eq 0 ]]; then
    echo -e "${GREEN}✓ Bedrock credentials updated in vault${NC}"
    echo ""
    echo -e "${BLUE}Next steps:${NC}"
    echo ""
    echo "1. Deploy LiteLLM configuration:"
    echo "   cd $BUSIBOX_ROOT/provision/ansible"
    echo "   make ${ENV}-litellm"
    echo ""
    echo "2. Test Bedrock models:"
    echo "   cd $BUSIBOX_ROOT"
    echo "   export LITELLM_MASTER_KEY='your-master-key'"
    echo "   bash scripts/test-bedrock-setup.sh $ENV"
    echo ""
else
    echo -e "${RED}✗ Failed to update vault${NC}"
    exit 1
fi

# Clean up
rm -f /tmp/update_vault.py

