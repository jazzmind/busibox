#!/usr/bin/env bash
# Check if Bedrock credentials are configured in LiteLLM
# Run on Proxmox host

set -euo pipefail

LITELLM_IP="${1:-10.96.200.207}"  # Default to production

echo "=== Checking Bedrock Configuration on $LITELLM_IP ==="
echo ""

echo "1. Checking environment file for AWS credentials..."
ssh root@$LITELLM_IP 'cat /etc/default/litellm | grep -E "^AWS_"' || echo "  ⚠️  No AWS credentials found"

echo ""
echo "2. Checking LiteLLM config for Bedrock models..."
ssh root@$LITELLM_IP 'grep -A 5 "frontier" /etc/litellm/config.yaml' || echo "  ⚠️  No frontier model found"

echo ""
echo "3. Checking if Bedrock credentials are set (masked)..."
AWS_KEY=$(ssh root@$LITELLM_IP 'grep AWS_ACCESS_KEY_ID /etc/default/litellm | cut -d= -f2' || echo "")
AWS_SECRET=$(ssh root@$LITELLM_IP 'grep AWS_SECRET_ACCESS_KEY /etc/default/litellm | cut -d= -f2' || echo "")

if [[ -n "$AWS_KEY" && "$AWS_KEY" != "" ]]; then
    echo "  ✓ AWS_ACCESS_KEY_ID is set (length: ${#AWS_KEY})"
else
    echo "  ✗ AWS_ACCESS_KEY_ID is NOT set"
fi

if [[ -n "$AWS_SECRET" && "$AWS_SECRET" != "" ]]; then
    echo "  ✓ AWS_SECRET_ACCESS_KEY is set (length: ${#AWS_SECRET})"
else
    echo "  ✗ AWS_SECRET_ACCESS_KEY is NOT set"
fi

echo ""
echo "4. Checking LiteLLM service status..."
ssh root@$LITELLM_IP 'systemctl is-active litellm' || echo "  ⚠️  Service not active"

echo ""
echo "5. Checking recent logs for Bedrock errors..."
ssh root@$LITELLM_IP 'journalctl -u litellm --since "5 minutes ago" | grep -i "bedrock\|aws\|authentication" | tail -5' || echo "  No recent Bedrock-related logs"

echo ""
if [[ -z "$AWS_KEY" || -z "$AWS_SECRET" || "$AWS_KEY" == "" || "$AWS_SECRET" == "" ]]; then
    echo "❌ BEDROCK CREDENTIALS ARE NOT CONFIGURED!"
    echo ""
    echo "To fix:"
    echo "1. Edit the vault: ansible-vault edit roles/secrets/vars/vault.yml"
    echo "2. Add:"
    echo "   secrets:"
    echo "     bedrock_api_key: 'YOUR_KEY'"
    echo "     litellm:"
    echo "       bedrock_api_key: 'YOUR_KEY'"
    echo "3. Redeploy: make production-litellm"
else
    echo "✓ Bedrock credentials appear to be configured"
    echo "  If tests still fail, check credential validity or permissions"
fi

