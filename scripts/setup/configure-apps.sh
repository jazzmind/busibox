#!/usr/bin/env bash
#
# Configure AI Portal and Agent Manager Applications
#
# This script sets up:
# 1. Admin user activation
# 2. Built-in apps (Video Generator, AI Chat, Document Manager)
# 3. AuthZ client registration for ai-portal and agent-manager
# 4. Environment variable configuration
#
# USAGE:
#   ./scripts/setup/configure-apps.sh [--all|--admin|--apps|--authz]
#
# OPTIONS:
#   --all     Run all configuration steps (default)
#   --admin   Only activate admin user
#   --apps    Only fix built-in apps
#   --authz   Only register authz clients
#
set -euo pipefail

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Source UI library if available
if [ -f "${REPO_ROOT}/scripts/lib/ui.sh" ]; then
    source "${REPO_ROOT}/scripts/lib/ui.sh"
else
    # Minimal fallback UI functions
    info() { echo "[INFO] $*"; }
    success() { echo "[SUCCESS] $*"; }
    error() { echo "[ERROR] $*" >&2; }
    warn() { echo "[WARN] $*"; }
    header() { echo ""; echo "=== $1 ==="; echo ""; }
fi

# Configuration
AI_PORTAL_DIR="${AI_PORTAL_DIR:-$(cd "${REPO_ROOT}/../ai-portal" 2>/dev/null && pwd || echo "")}"
AGENT_MANAGER_DIR="${AGENT_MANAGER_DIR:-$(cd "${REPO_ROOT}/../agent-manager" 2>/dev/null && pwd || echo "")}"
AUTHZ_BASE_URL="${AUTHZ_BASE_URL:-https://localhost/api/authz}"
AUTHZ_ADMIN_TOKEN="${AUTHZ_ADMIN_TOKEN:-}"

# =============================================================================
# Helper Functions
# =============================================================================

check_ai_portal() {
    if [ -z "$AI_PORTAL_DIR" ] || [ ! -d "$AI_PORTAL_DIR" ]; then
        error "ai-portal directory not found. Expected at: ${REPO_ROOT}/../ai-portal"
        error "Set AI_PORTAL_DIR environment variable to override"
        return 1
    fi
    return 0
}

check_agent_manager() {
    if [ -z "$AGENT_MANAGER_DIR" ] || [ ! -d "$AGENT_MANAGER_DIR" ]; then
        error "agent-manager directory not found. Expected at: ${REPO_ROOT}/../agent-manager"
        error "Set AGENT_MANAGER_DIR environment variable to override"
        return 1
    fi
    return 0
}

get_authz_admin_token() {
    # Try to get admin token from various sources
    if [ -n "$AUTHZ_ADMIN_TOKEN" ]; then
        echo "$AUTHZ_ADMIN_TOKEN"
        return 0
    fi
    
    # Try ai-portal .env
    if [ -f "${AI_PORTAL_DIR}/.env" ]; then
        local token=$(grep "^AUTHZ_ADMIN_TOKEN=" "${AI_PORTAL_DIR}/.env" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'")
        if [ -n "$token" ]; then
            echo "$token"
            return 0
        fi
    fi
    
    # Try busibox .env.local
    if [ -f "${REPO_ROOT}/.env.local" ]; then
        local token=$(grep "^AUTHZ_ADMIN_TOKEN=" "${REPO_ROOT}/.env.local" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'")
        if [ -n "$token" ]; then
            echo "$token"
            return 0
        fi
    fi
    
    echo ""
    return 1
}

generate_secret() {
    openssl rand -base64 32 | tr -d '/+=' | head -c 32
}

# =============================================================================
# Step 1: Activate Admin User and Assign Admin Role
# =============================================================================

activate_admin_user() {
    header "Activating Admin User"
    
    if ! check_ai_portal; then
        return 1
    fi
    
    cd "$AI_PORTAL_DIR"
    
    # Check if activate-user.ts exists
    if [ ! -f "scripts/activate-user.ts" ]; then
        warn "scripts/activate-user.ts not found, creating..."
        mkdir -p scripts
        cat > scripts/activate-user.ts << 'EOF'
import prisma from '../src/lib/db';

async function activateFirstUser() {
  const user = await prisma.user.findFirst({
    orderBy: { createdAt: 'asc' }
  });

  if (!user) {
    console.log('No users found');
    return;
  }

  await prisma.user.update({
    where: { id: user.id },
    data: { status: 'ACTIVE' }
  });

  console.log(`Activated user: ${user.email}`);
  console.log(`User ID: ${user.id}`);
}

activateFirstUser().finally(() => prisma.$disconnect());
EOF
    fi
    
    info "Running admin user activation..."
    local user_output
    if user_output=$(npx tsx scripts/activate-user.ts 2>/dev/null); then
        success "Admin user activated"
        echo "$user_output"
        
        # Extract user ID from output
        local user_id=$(echo "$user_output" | grep "User ID:" | awk '{print $3}')
        
        if [ -n "$user_id" ]; then
            # Now assign Admin role in authz
            assign_admin_role_in_authz "$user_id"
        fi
    else
        warn "Could not activate admin user (may already be active or no users exist)"
    fi
    
    cd - > /dev/null
}

# Helper function to assign Admin role in authz service
assign_admin_role_in_authz() {
    local user_id="$1"
    
    if [ -z "$user_id" ]; then
        warn "No user ID provided for role assignment"
        return 1
    fi
    
    local admin_token=$(get_authz_admin_token)
    
    if [ -z "$admin_token" ]; then
        warn "AUTHZ_ADMIN_TOKEN not found, skipping role assignment"
        return 1
    fi
    
    info "Assigning Admin role to user $user_id in authz..."
    
    # First, get the Admin role ID
    local roles_response=$(curl -sk "${AUTHZ_BASE_URL}/admin/roles" \
        -H "Authorization: Bearer $admin_token" 2>&1)
    
    local admin_role_id=$(echo "$roles_response" | jq -r '.[] | select(.name == "Admin") | .id' 2>/dev/null)
    
    if [ -z "$admin_role_id" ] || [ "$admin_role_id" = "null" ]; then
        warn "Admin role not found in authz service"
        return 1
    fi
    
    info "Found Admin role ID: $admin_role_id"
    
    # Assign the role
    local assign_response=$(curl -sk -w "\n%{http_code}" -X POST \
        "${AUTHZ_BASE_URL}/admin/users/${user_id}/roles/${admin_role_id}" \
        -H "Authorization: Bearer $admin_token" \
        -H "Content-Type: application/json" 2>&1)
    
    local http_code=$(echo "$assign_response" | tail -n1)
    local body=$(echo "$assign_response" | sed '$d')
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        success "Admin role assigned to user"
    elif [ "$http_code" = "409" ]; then
        info "User already has Admin role"
    else
        warn "Failed to assign Admin role (HTTP $http_code): $body"
        return 1
    fi
}

# =============================================================================
# Step 2: Fix Built-in Apps
# =============================================================================

fix_builtin_apps() {
    header "Fixing Built-in Apps"
    
    if ! check_ai_portal; then
        return 1
    fi
    
    cd "$AI_PORTAL_DIR"
    
    # Check if fix-builtin-apps.ts exists
    if [ ! -f "scripts/fix-builtin-apps.ts" ]; then
        warn "scripts/fix-builtin-apps.ts not found, creating..."
        mkdir -p scripts
        cat > scripts/fix-builtin-apps.ts << 'EOF'
import prisma from '../src/lib/db';

const BUILT_IN_APPS_CONFIG = [
  {
    name: 'Video Generator',
    description: 'AI-powered video content generation and library',
    url: '/videos',
    selectedIcon: 'video',
    displayOrder: 3,
  },
  {
    name: 'AI Chat',
    description: 'Chat with AI models via liteLLM',
    url: '/chat',
    selectedIcon: 'chat',
    displayOrder: 4,
  },
  {
    name: 'Document Manager',
    description: 'Upload, process, and search documents with AI',
    url: '/documents',
    selectedIcon: 'documents',
    displayOrder: 5,
  },
];

async function fixBuiltInApps() {
  console.log('🔧 Fixing built-in apps...');
  
  try {
    for (const config of BUILT_IN_APPS_CONFIG) {
      const app = await prisma.app.findUnique({
        where: { name: config.name },
      });

      if (!app) {
        const created = await prisma.app.create({
          data: {
            name: config.name,
            description: config.description,
            type: 'BUILT_IN',
            url: config.url,
            selectedIcon: config.selectedIcon,
            displayOrder: config.displayOrder,
            isActive: true,
          },
        });
        console.log(`✅ Created "${config.name}": type=${created.type}, isActive=${created.isActive}, icon=${created.selectedIcon}`);
      } else if (app.type !== 'BUILT_IN' || !app.isActive || app.selectedIcon !== config.selectedIcon) {
        const updated = await prisma.app.update({
          where: { name: config.name },
          data: {
            type: 'BUILT_IN',
            isActive: true,
            description: config.description,
            url: config.url,
            selectedIcon: config.selectedIcon,
            displayOrder: config.displayOrder,
          },
        });
        console.log(`✅ Updated "${config.name}": type=${updated.type}, isActive=${updated.isActive}, icon=${updated.selectedIcon}`);
      } else {
        console.log(`✓ "${config.name}" is already correct: type=${app.type}, isActive=${app.isActive}, icon=${app.selectedIcon}`);
      }
    }
    
    console.log('');
    console.log('📋 Current built-in apps:');
    const builtInApps = await prisma.app.findMany({
      where: { type: 'BUILT_IN' },
      select: { name: true, type: true, isActive: true, selectedIcon: true, url: true },
    });
    
    for (const app of builtInApps) {
      console.log(`   - ${app.name}: ${app.url} (icon: ${app.selectedIcon}, active: ${app.isActive})`);
    }
    
  } catch (error) {
    console.error('❌ Error fixing built-in apps:', error);
    throw error;
  } finally {
    await prisma.$disconnect();
  }
}

fixBuiltInApps();
EOF
    fi
    
    info "Running built-in apps fix..."
    if npx tsx scripts/fix-builtin-apps.ts; then
        success "Built-in apps configured"
    else
        error "Failed to fix built-in apps"
        cd - > /dev/null
        return 1
    fi
    
    cd - > /dev/null
}

# =============================================================================
# Step 3: Register AuthZ Clients
# =============================================================================

register_authz_clients() {
    header "Registering AuthZ Clients"
    
    local admin_token=$(get_authz_admin_token)
    
    if [ -z "$admin_token" ]; then
        error "AUTHZ_ADMIN_TOKEN not found"
        error "Set it in environment or in ai-portal/.env"
        return 1
    fi
    
    # Generate secrets
    local ai_portal_secret=$(generate_secret)
    local agent_manager_secret=$(generate_secret)
    
    info "Generated secrets:"
    echo "   ai-portal:      $ai_portal_secret"
    echo "   agent-manager:  $agent_manager_secret"
    echo ""
    
    # Define allowed scopes for clients
    # These match the OAuth2 scopes defined in the architecture docs
    # Note: POST to /admin/oauth-clients does upsert, so we can call it multiple times
    local all_scopes='["ingest.read", "ingest.write", "ingest.delete", "search.read", "search.write", "search.delete", "agent.read", "agent.write", "agent.delete", "agent.execute", "agents:read", "agents:write"]'
    
    # Register/update ai-portal client
    info "Registering ai-portal client..."
    local response=$(curl -skL -w "\n%{http_code}" -X POST "${AUTHZ_BASE_URL}/admin/oauth-clients" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $admin_token" \
        -d "{
            \"client_id\": \"ai-portal\",
            \"client_secret\": \"$ai_portal_secret\",
            \"allowed_audiences\": [\"agent-api\", \"ingest-api\", \"search-api\"],
            \"allowed_scopes\": $all_scopes
        }" 2>&1)
    
    local http_code=$(echo "$response" | tail -n1)
    local body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        success "ai-portal client registered/updated"
    else
        error "Failed to register ai-portal client (HTTP $http_code)"
        echo "Response: $body"
        return 1
    fi
    
    # Register/update agent-manager client
    info "Registering agent-manager client..."
    response=$(curl -skL -w "\n%{http_code}" -X POST "${AUTHZ_BASE_URL}/admin/oauth-clients" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $admin_token" \
        -d "{
            \"client_id\": \"agent-manager\",
            \"client_secret\": \"$agent_manager_secret\",
            \"allowed_audiences\": [\"agent-api\", \"ingest-api\", \"search-api\"],
            \"allowed_scopes\": $all_scopes
        }" 2>&1)
    
    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | sed '$d')
    
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        success "agent-manager client registered/updated"
    else
        error "Failed to register agent-manager client (HTTP $http_code)"
        echo "Response: $body"
        return 1
    fi
    
    # Update environment files
    echo ""
    info "Updating environment files..."
    
    # Update ai-portal .env
    if check_ai_portal; then
        local env_file="${AI_PORTAL_DIR}/.env"
        if [ -f "$env_file" ]; then
            # Remove existing entries
            sed -i.bak '/^AUTHZ_CLIENT_ID=/d' "$env_file" 2>/dev/null || true
            sed -i.bak '/^AUTHZ_CLIENT_SECRET=/d' "$env_file" 2>/dev/null || true
            rm -f "${env_file}.bak"
            
            # Add new entries
            echo "AUTHZ_CLIENT_ID=ai-portal" >> "$env_file"
            echo "AUTHZ_CLIENT_SECRET=$ai_portal_secret" >> "$env_file"
            success "Updated ${env_file}"
        else
            warn "ai-portal/.env not found, creating..."
            echo "AUTHZ_CLIENT_ID=ai-portal" > "$env_file"
            echo "AUTHZ_CLIENT_SECRET=$ai_portal_secret" >> "$env_file"
        fi
    fi
    
    # Update agent-manager .env.local
    if check_agent_manager; then
        local env_file="${AGENT_MANAGER_DIR}/.env.local"
        if [ -f "$env_file" ]; then
            # Remove existing entries
            sed -i.bak '/^AUTHZ_CLIENT_ID=/d' "$env_file" 2>/dev/null || true
            sed -i.bak '/^AUTHZ_CLIENT_SECRET=/d' "$env_file" 2>/dev/null || true
            rm -f "${env_file}.bak"
            
            # Add new entries
            echo "AUTHZ_CLIENT_ID=agent-manager" >> "$env_file"
            echo "AUTHZ_CLIENT_SECRET=$agent_manager_secret" >> "$env_file"
            success "Updated ${env_file}"
        else
            warn "agent-manager/.env.local not found, creating..."
            cat > "$env_file" << EOF
# AuthZ Client Credentials
AUTHZ_CLIENT_ID=agent-manager
AUTHZ_CLIENT_SECRET=$agent_manager_secret
AUTHZ_BASE_URL=https://localhost/api/authz

# AI Portal URL
NEXT_PUBLIC_AI_PORTAL_URL=https://localhost

# Base path for nginx proxy
NEXT_PUBLIC_BASE_PATH=/agents
EOF
        fi
    fi
    
    echo ""
    success "AuthZ clients registered and environment files updated"
    echo ""
    echo "📋 Summary:"
    echo "   ai-portal client_id:      ai-portal"
    echo "   ai-portal client_secret:  $ai_portal_secret"
    echo "   agent-manager client_id:  agent-manager"
    echo "   agent-manager secret:     $agent_manager_secret"
    echo ""
    warn "Remember to restart ai-portal and agent-manager to apply changes"
}

# =============================================================================
# Main
# =============================================================================

run_all() {
    local errors=0
    
    activate_admin_user || ((errors++))
    fix_builtin_apps || ((errors++))
    register_authz_clients || ((errors++))
    
    echo ""
    if [ $errors -eq 0 ]; then
        success "All configuration steps completed successfully!"
    else
        warn "$errors step(s) had errors"
    fi
    
    return $errors
}

# Parse arguments
case "${1:-}" in
    --admin)
        activate_admin_user
        ;;
    --apps)
        fix_builtin_apps
        ;;
    --authz)
        register_authz_clients
        ;;
    --all|"")
        run_all
        ;;
    --help|-h)
        echo "Usage: $0 [--all|--admin|--apps|--authz]"
        echo ""
        echo "Options:"
        echo "  --all     Run all configuration steps (default)"
        echo "  --admin   Only activate admin user"
        echo "  --apps    Only fix built-in apps"
        echo "  --authz   Only register authz clients"
        echo ""
        echo "Environment variables:"
        echo "  AI_PORTAL_DIR       Path to ai-portal (default: ../ai-portal)"
        echo "  AGENT_MANAGER_DIR   Path to agent-manager (default: ../agent-manager)"
        echo "  AUTHZ_BASE_URL      AuthZ service URL (default: https://localhost/api/authz)"
        echo "  AUTHZ_ADMIN_TOKEN   Admin token for authz service"
        ;;
    *)
        error "Unknown option: $1"
        echo "Use --help for usage information"
        exit 1
        ;;
esac

