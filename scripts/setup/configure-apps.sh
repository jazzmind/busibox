#!/usr/bin/env bash
#
# Configure Busibox Portal and Agent Manager Applications
#
# This script sets up:
# 1. Admin user activation
# 2. Built-in apps (Video Generator, AI Chat, Document Manager)
# 3. AuthZ client registration (deprecated - no-op under Zero Trust)
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
BUSIBOX_PORTAL_DIR="${BUSIBOX_PORTAL_DIR:-$(cd "${REPO_ROOT}/../busibox-portal" 2>/dev/null && pwd || echo "")}"
BUSIBOX_AGENTS_DIR="${BUSIBOX_AGENTS_DIR:-$(cd "${REPO_ROOT}/../busibox-agents" 2>/dev/null && pwd || echo "")}"
AUTHZ_BASE_URL="${AUTHZ_BASE_URL:-https://localhost/api/authz}"
# DEPRECATED: Zero Trust model eliminates admin tokens. This is kept for backward
# compatibility during migration. AuthZ client registration now uses user tokens
# via the admin UI or token exchange flow.
AUTHZ_ADMIN_TOKEN="${AUTHZ_ADMIN_TOKEN:-}"

# =============================================================================
# Helper Functions
# =============================================================================

check_busibox_portal() {
    if [ -z "$BUSIBOX_PORTAL_DIR" ] || [ ! -d "$BUSIBOX_PORTAL_DIR" ]; then
        error "busibox-portal directory not found. Expected at: ${REPO_ROOT}/../busibox-portal"
        error "Set BUSIBOX_PORTAL_DIR environment variable to override"
        return 1
    fi
    return 0
}

check_busibox_agents() {
    if [ -z "$BUSIBOX_AGENTS_DIR" ] || [ ! -d "$BUSIBOX_AGENTS_DIR" ]; then
        error "busibox-agents directory not found. Expected at: ${REPO_ROOT}/../busibox-agents"
        error "Set BUSIBOX_AGENTS_DIR environment variable to override"
        return 1
    fi
    return 0
}

get_authz_admin_token() {
    # DEPRECATED: Zero Trust model eliminates admin tokens.
    # This function is kept for backward compatibility during migration.
    # New integrations should use the admin UI or token exchange flow.
    
    # Try to get admin token from various sources
    if [ -n "$AUTHZ_ADMIN_TOKEN" ]; then
        echo "$AUTHZ_ADMIN_TOKEN"
        return 0
    fi
    
    # Try busibox-portal .env
    if [ -f "${BUSIBOX_PORTAL_DIR}/.env" ]; then
        local token=$(grep "^AUTHZ_ADMIN_TOKEN=" "${BUSIBOX_PORTAL_DIR}/.env" 2>/dev/null | cut -d'=' -f2 | tr -d '"' | tr -d "'")
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
    
    if ! check_busibox_portal; then
        return 1
    fi
    
    cd "$BUSIBOX_PORTAL_DIR"
    
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
    
    if ! check_busibox_portal; then
        return 1
    fi
    
    cd "$BUSIBOX_PORTAL_DIR"
    
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
# Step 3: Register AuthZ Clients (DEPRECATED - No-op)
# =============================================================================
# Zero Trust model: AuthZ issues session JWTs directly. Token exchange uses
# session JWT as subject_token - no client credentials needed for portal/agents.
# OAuth client registration is obsolete for these apps.

register_authz_clients() {
    header "Registering AuthZ Clients"
    warn "AuthZ client registration is deprecated (Zero Trust model)"
    warn "busibox-portal and busibox-agents use session JWT token exchange - no client credentials needed"
    info "Skipping - no action required"
    return 0
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
        echo "  BUSIBOX_PORTAL_DIR  Path to busibox-portal (default: ../busibox-portal)"
        echo "  BUSIBOX_AGENTS_DIR  Path to busibox-agents (default: ../busibox-agents)"
        echo "  AUTHZ_BASE_URL      AuthZ service URL (default: https://localhost/api/authz)"
        echo "  AUTHZ_ADMIN_TOKEN   Admin token for authz service (admin/role assignment only)"
        ;;
    *)
        error "Unknown option: $1"
        echo "Use --help for usage information"
        exit 1
        ;;
esac

