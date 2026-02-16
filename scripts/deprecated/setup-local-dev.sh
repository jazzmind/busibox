#!/bin/bash
set -e

# Setup local development environment on macOS
# This script prepares your Mac for local Ansible + Docker development

echo "🚀 Setting up local development environment..."
echo ""

# Check if we're on macOS
if [[ "$OSTYPE" != "darwin"* ]]; then
    echo "❌ This script is for macOS only."
    exit 1
fi

# Check for Homebrew
if ! command -v brew &> /dev/null; then
    echo "📦 Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
    echo "✅ Homebrew already installed"
fi

# Check for Docker Desktop
if ! command -v docker &> /dev/null; then
    echo "🐳 Installing Docker Desktop..."
    brew install --cask docker
    echo "⚠️  Please start Docker Desktop manually, then run this script again."
    exit 0
else
    echo "✅ Docker already installed"
fi

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "⚠️  Docker is not running. Please start Docker Desktop and run this script again."
    exit 1
fi

# Check for Ansible
if ! command -v ansible &> /dev/null; then
    echo "📦 Installing Ansible..."
    brew install ansible
else
    echo "✅ Ansible already installed"
fi

# Install Docker Python library for Ansible
echo "📦 Installing Python dependencies..."
pip3 install docker --quiet || true

# Create vault password file
VAULT_PASS_FILE="$HOME/.vault_pass"
if [[ ! -f "$VAULT_PASS_FILE" ]]; then
    echo "🔐 Creating local vault password file..."
    echo "local-dev-password" > "$VAULT_PASS_FILE"
    chmod 600 "$VAULT_PASS_FILE"
    echo "✅ Created $VAULT_PASS_FILE"
else
    echo "✅ Vault password file already exists"
fi

# Create vault file if it doesn't exist
VAULT_FILE="provision/ansible/inventory/local/group_vars/vault.yml"
if [[ ! -f "$VAULT_FILE" ]]; then
    echo "🔐 Creating local vault file..."
    mkdir -p "$(dirname "$VAULT_FILE")"
    
    cat > /tmp/vault_template.yml << 'EOF'
---
secrets:
  github_token: ghp_your_github_token_here
  
  busibox-portal:
    database_url: "postgresql://postgres:devpassword@172.20.0.10:5432/cashman"
    better_auth_secret: "local-dev-secret-change-me"
    better_auth_url: "http://local.ai.localhost:3000"
    resend_api_key: "re_your_resend_key"
    email_from: "noreply@localhost"
    openai_api_key: "sk-your-openai-key"
    sso_jwt_secret: "local-sso-secret"
    litellm_api_key: "sk-local-dev-key"
    admin_email: "admin@localhost"
    allowed_email_domains: "*"
EOF
    
    ansible-vault encrypt /tmp/vault_template.yml \
        --vault-password-file "$VAULT_PASS_FILE" \
        --output "$VAULT_FILE"
    rm /tmp/vault_template.yml
    
    echo "✅ Created encrypted vault file at $VAULT_FILE"
    echo "⚠️  Edit it with: ansible-vault edit --vault-password-file $VAULT_PASS_FILE $VAULT_FILE"
else
    echo "✅ Vault file already exists"
fi

echo ""
echo "✅ Local development environment is ready!"
echo ""
echo "Next steps:"
echo "  1. Edit your secrets:"
echo "     cd provision/ansible"
echo "     ansible-vault edit --vault-password-file .vault_pass_local inventory/local/group_vars/vault.yml"
echo ""
echo "  2. Start containers and deploy:"
echo "     make local-up"
echo "     make local-deploy"
echo ""
echo "  3. Access services:"
echo "     Busibox Portal:      http://localhost:3000"
echo "     MinIO Console:  http://localhost:9001"
echo ""
echo "See docs/LOCAL_DEVELOPMENT.md for more details."











