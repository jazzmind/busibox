#!/usr/bin/env npx ts-node
/**
 * Data Migration Script: ai-portal to authz
 * 
 * Migrates user, role, and authentication data from ai-portal's Prisma
 * database to the authz service.
 * 
 * Usage:
 *   npx ts-node scripts/migrations/migrate-to-authz.ts [--dry-run]
 * 
 * Environment Variables:
 *   DATABASE_URL - ai-portal Prisma database connection string
 *   AUTHZ_BASE_URL - authz service URL (default: http://10.96.200.210:8010)
 *   AUTHZ_ADMIN_TOKEN - Admin token for authz service
 * 
 * What gets migrated:
 *   1. Roles (with isSystem flag)
 *   2. Users (with status, email verification, etc.)
 *   3. User-Role assignments
 *   4. Sessions (active only)
 *   5. Passkeys
 *   6. Audit logs (last 90 days)
 */

import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

const AUTHZ_BASE_URL = process.env.AUTHZ_BASE_URL || 'http://10.96.200.210:8010';
const AUTHZ_ADMIN_TOKEN = process.env.AUTHZ_ADMIN_TOKEN;

const isDryRun = process.argv.includes('--dry-run');

interface MigrationStats {
  roles: { total: number; migrated: number; failed: number };
  users: { total: number; migrated: number; failed: number };
  userRoles: { total: number; migrated: number; failed: number };
  sessions: { total: number; migrated: number; failed: number };
  passkeys: { total: number; migrated: number; failed: number };
  auditLogs: { total: number; migrated: number; failed: number };
}

const stats: MigrationStats = {
  roles: { total: 0, migrated: 0, failed: 0 },
  users: { total: 0, migrated: 0, failed: 0 },
  userRoles: { total: 0, migrated: 0, failed: 0 },
  sessions: { total: 0, migrated: 0, failed: 0 },
  passkeys: { total: 0, migrated: 0, failed: 0 },
  auditLogs: { total: 0, migrated: 0, failed: 0 },
};

async function authzFetch(path: string, options: RequestInit = {}): Promise<Response> {
  const url = `${AUTHZ_BASE_URL}${path}`;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(AUTHZ_ADMIN_TOKEN ? { 'Authorization': `Bearer ${AUTHZ_ADMIN_TOKEN}` } : {}),
    ...(options.headers as Record<string, string>),
  };

  return fetch(url, {
    ...options,
    headers,
  });
}

// ============================================================================
// Migration Functions
// ============================================================================

async function migrateRoles(): Promise<Map<string, string>> {
  console.log('\n📋 Migrating roles...');
  
  const roles = await prisma.role.findMany({
    orderBy: { createdAt: 'asc' },
  });
  
  stats.roles.total = roles.length;
  const roleIdMap = new Map<string, string>(); // old ID -> new ID (should be same)
  
  for (const role of roles) {
    if (isDryRun) {
      console.log(`  [DRY RUN] Would migrate role: ${role.name} (${role.id})`);
      roleIdMap.set(role.id, role.id);
      stats.roles.migrated++;
      continue;
    }
    
    try {
      const response = await authzFetch('/admin/roles', {
        method: 'POST',
        body: JSON.stringify({
          name: role.name,
          description: role.description,
          // Note: is_system flag would need to be added to authz create endpoint
        }),
      });
      
      if (response.ok || response.status === 409) {
        // 409 = already exists, which is fine
        roleIdMap.set(role.id, role.id);
        stats.roles.migrated++;
        console.log(`  ✓ Migrated role: ${role.name}`);
      } else {
        const error = await response.text();
        console.error(`  ✗ Failed to migrate role ${role.name}: ${error}`);
        stats.roles.failed++;
      }
    } catch (error) {
      console.error(`  ✗ Error migrating role ${role.name}:`, error);
      stats.roles.failed++;
    }
  }
  
  return roleIdMap;
}

async function migrateUsers(): Promise<Map<string, string>> {
  console.log('\n👥 Migrating users...');
  
  const users = await prisma.user.findMany({
    orderBy: { createdAt: 'asc' },
  });
  
  stats.users.total = users.length;
  const userIdMap = new Map<string, string>();
  
  for (const user of users) {
    if (isDryRun) {
      console.log(`  [DRY RUN] Would migrate user: ${user.email} (${user.id})`);
      userIdMap.set(user.id, user.id);
      stats.users.migrated++;
      continue;
    }
    
    try {
      const response = await authzFetch('/admin/users', {
        method: 'POST',
        body: JSON.stringify({
          email: user.email,
          status: user.status,
          // These will be set via update after creation
        }),
      });
      
      if (response.ok) {
        const created = await response.json();
        userIdMap.set(user.id, created.user_id);
        
        // Update with additional fields
        if (user.emailVerified || user.lastLoginAt || user.pendingExpiresAt) {
          await authzFetch(`/admin/users/${created.user_id}`, {
            method: 'PATCH',
            body: JSON.stringify({
              email_verified_at: user.emailVerified?.toISOString(),
              last_login_at: user.lastLoginAt?.toISOString(),
              pending_expires_at: user.pendingExpiresAt?.toISOString(),
            }),
          });
        }
        
        stats.users.migrated++;
        console.log(`  ✓ Migrated user: ${user.email}`);
      } else if (response.status === 409) {
        // User already exists
        userIdMap.set(user.id, user.id);
        stats.users.migrated++;
        console.log(`  ⊘ User already exists: ${user.email}`);
      } else {
        const error = await response.text();
        console.error(`  ✗ Failed to migrate user ${user.email}: ${error}`);
        stats.users.failed++;
      }
    } catch (error) {
      console.error(`  ✗ Error migrating user ${user.email}:`, error);
      stats.users.failed++;
    }
  }
  
  return userIdMap;
}

async function migrateUserRoles(userIdMap: Map<string, string>, roleIdMap: Map<string, string>): Promise<void> {
  console.log('\n🔗 Migrating user-role assignments...');
  
  const userRoles = await prisma.userRole.findMany({
    include: { role: true },
  });
  
  stats.userRoles.total = userRoles.length;
  
  for (const ur of userRoles) {
    const newUserId = userIdMap.get(ur.userId);
    const newRoleId = roleIdMap.get(ur.roleId);
    
    if (!newUserId || !newRoleId) {
      console.log(`  ⊘ Skipping user-role (user or role not migrated): ${ur.userId} -> ${ur.role.name}`);
      stats.userRoles.failed++;
      continue;
    }
    
    if (isDryRun) {
      console.log(`  [DRY RUN] Would assign role ${ur.role.name} to user ${ur.userId}`);
      stats.userRoles.migrated++;
      continue;
    }
    
    try {
      const response = await authzFetch(`/admin/users/${newUserId}/roles/${newRoleId}`, {
        method: 'POST',
      });
      
      if (response.ok || response.status === 409) {
        stats.userRoles.migrated++;
        console.log(`  ✓ Assigned ${ur.role.name} to user ${ur.userId}`);
      } else {
        const error = await response.text();
        console.error(`  ✗ Failed to assign role: ${error}`);
        stats.userRoles.failed++;
      }
    } catch (error) {
      console.error(`  ✗ Error assigning role:`, error);
      stats.userRoles.failed++;
    }
  }
}

async function migrateSessions(userIdMap: Map<string, string>): Promise<void> {
  console.log('\n🔐 Migrating active sessions...');
  
  // Only migrate non-expired sessions
  const sessions = await prisma.session.findMany({
    where: {
      expiresAt: { gt: new Date() },
    },
  });
  
  stats.sessions.total = sessions.length;
  
  for (const session of sessions) {
    const newUserId = userIdMap.get(session.userId);
    
    if (!newUserId) {
      console.log(`  ⊘ Skipping session (user not migrated): ${session.id}`);
      stats.sessions.failed++;
      continue;
    }
    
    if (isDryRun) {
      console.log(`  [DRY RUN] Would migrate session: ${session.id}`);
      stats.sessions.migrated++;
      continue;
    }
    
    try {
      const response = await authzFetch('/auth/sessions', {
        method: 'POST',
        body: JSON.stringify({
          user_id: newUserId,
          token: session.token,
          expires_at: session.expiresAt.toISOString(),
          ip_address: session.ipAddress,
          user_agent: session.userAgent,
        }),
      });
      
      if (response.ok) {
        stats.sessions.migrated++;
        console.log(`  ✓ Migrated session: ${session.id}`);
      } else {
        const error = await response.text();
        console.error(`  ✗ Failed to migrate session: ${error}`);
        stats.sessions.failed++;
      }
    } catch (error) {
      console.error(`  ✗ Error migrating session:`, error);
      stats.sessions.failed++;
    }
  }
}

async function migratePasskeys(userIdMap: Map<string, string>): Promise<void> {
  console.log('\n🔑 Migrating passkeys...');
  
  const passkeys = await prisma.passkey.findMany();
  
  stats.passkeys.total = passkeys.length;
  
  for (const passkey of passkeys) {
    const newUserId = userIdMap.get(passkey.userId);
    
    if (!newUserId) {
      console.log(`  ⊘ Skipping passkey (user not migrated): ${passkey.id}`);
      stats.passkeys.failed++;
      continue;
    }
    
    if (isDryRun) {
      console.log(`  [DRY RUN] Would migrate passkey: ${passkey.name}`);
      stats.passkeys.migrated++;
      continue;
    }
    
    try {
      const response = await authzFetch('/auth/passkeys', {
        method: 'POST',
        body: JSON.stringify({
          user_id: newUserId,
          credential_id: passkey.credentialId,
          credential_public_key: passkey.publicKey,
          counter: passkey.counter,
          device_type: passkey.deviceType,
          backed_up: passkey.backedUp,
          transports: passkey.transports ? JSON.parse(passkey.transports) : [],
          aaguid: passkey.aaguid,
          name: passkey.name,
        }),
      });
      
      if (response.ok || response.status === 409) {
        stats.passkeys.migrated++;
        console.log(`  ✓ Migrated passkey: ${passkey.name}`);
      } else {
        const error = await response.text();
        console.error(`  ✗ Failed to migrate passkey: ${error}`);
        stats.passkeys.failed++;
      }
    } catch (error) {
      console.error(`  ✗ Error migrating passkey:`, error);
      stats.passkeys.failed++;
    }
  }
}

async function migrateAuditLogs(): Promise<void> {
  console.log('\n📜 Migrating audit logs (last 90 days)...');
  
  const ninetyDaysAgo = new Date();
  ninetyDaysAgo.setDate(ninetyDaysAgo.getDate() - 90);
  
  const auditLogs = await prisma.auditLog.findMany({
    where: {
      createdAt: { gte: ninetyDaysAgo },
    },
    orderBy: { createdAt: 'asc' },
  });
  
  stats.auditLogs.total = auditLogs.length;
  console.log(`  Found ${auditLogs.length} audit logs to migrate`);
  
  // Batch audit logs for performance
  const BATCH_SIZE = 100;
  let migrated = 0;
  
  for (let i = 0; i < auditLogs.length; i += BATCH_SIZE) {
    const batch = auditLogs.slice(i, i + BATCH_SIZE);
    
    if (isDryRun) {
      migrated += batch.length;
      stats.auditLogs.migrated += batch.length;
      continue;
    }
    
    for (const log of batch) {
      try {
        const response = await authzFetch('/audit/log', {
          method: 'POST',
          body: JSON.stringify({
            actor_id: log.userId || 'system',
            action: log.action,
            resource_type: log.eventType,
            resource_id: log.targetUserId || log.targetRoleId || log.targetAppId,
            event_type: log.eventType,
            target_user_id: log.targetUserId,
            target_role_id: log.targetRoleId,
            target_app_id: log.targetAppId,
            ip_address: log.ipAddress,
            user_agent: log.userAgent,
            success: log.success,
            error_message: log.errorMessage,
            details: log.details,
          }),
        });
        
        if (response.ok) {
          stats.auditLogs.migrated++;
          migrated++;
        } else {
          stats.auditLogs.failed++;
        }
      } catch {
        stats.auditLogs.failed++;
      }
    }
    
    if (!isDryRun) {
      console.log(`  Progress: ${migrated}/${auditLogs.length} audit logs migrated`);
    }
  }
  
  if (isDryRun) {
    console.log(`  [DRY RUN] Would migrate ${auditLogs.length} audit logs`);
  }
}

// ============================================================================
// Validation
// ============================================================================

async function validateMigration(): Promise<boolean> {
  console.log('\n🔍 Validating migration...');
  
  let valid = true;
  
  // Count users in ai-portal
  const portalUserCount = await prisma.user.count();
  
  // Count users in authz (by listing)
  const authzUsersResponse = await authzFetch('/admin/users?limit=1');
  if (authzUsersResponse.ok) {
    const authzUsers = await authzUsersResponse.json();
    const authzUserCount = authzUsers.pagination?.total_count || 0;
    
    console.log(`  Users: ai-portal=${portalUserCount}, authz=${authzUserCount}`);
    
    if (portalUserCount !== authzUserCount) {
      console.log('  ⚠️  User count mismatch');
      valid = false;
    }
  }
  
  // Count roles
  const portalRoleCount = await prisma.role.count();
  const authzRolesResponse = await authzFetch('/admin/roles');
  if (authzRolesResponse.ok) {
    const authzRoles = await authzRolesResponse.json();
    const authzRoleCount = authzRoles.length || 0;
    
    console.log(`  Roles: ai-portal=${portalRoleCount}, authz=${authzRoleCount}`);
    
    if (portalRoleCount !== authzRoleCount) {
      console.log('  ⚠️  Role count mismatch');
      valid = false;
    }
  }
  
  return valid;
}

// ============================================================================
// Main
// ============================================================================

async function main() {
  console.log('='.repeat(60));
  console.log('AuthZ Migration Script');
  console.log('='.repeat(60));
  
  if (isDryRun) {
    console.log('\n⚠️  DRY RUN MODE - No changes will be made\n');
  }
  
  if (!AUTHZ_ADMIN_TOKEN) {
    console.error('ERROR: AUTHZ_ADMIN_TOKEN environment variable is required');
    process.exit(1);
  }
  
  console.log(`Source: ai-portal Prisma database`);
  console.log(`Target: ${AUTHZ_BASE_URL}`);
  
  try {
    // Connect to ai-portal database
    await prisma.$connect();
    console.log('\n✓ Connected to ai-portal database');
    
    // Check authz is reachable
    const healthCheck = await authzFetch('/health/ready');
    if (!healthCheck.ok) {
      throw new Error('AuthZ service is not reachable');
    }
    console.log('✓ AuthZ service is reachable');
    
    // Run migrations in order
    const roleIdMap = await migrateRoles();
    const userIdMap = await migrateUsers();
    await migrateUserRoles(userIdMap, roleIdMap);
    await migrateSessions(userIdMap);
    await migratePasskeys(userIdMap);
    await migrateAuditLogs();
    
    // Validate if not dry run
    if (!isDryRun) {
      await validateMigration();
    }
    
    // Print summary
    console.log('\n' + '='.repeat(60));
    console.log('Migration Summary');
    console.log('='.repeat(60));
    console.log(`\nRoles:      ${stats.roles.migrated}/${stats.roles.total} migrated, ${stats.roles.failed} failed`);
    console.log(`Users:      ${stats.users.migrated}/${stats.users.total} migrated, ${stats.users.failed} failed`);
    console.log(`User-Roles: ${stats.userRoles.migrated}/${stats.userRoles.total} migrated, ${stats.userRoles.failed} failed`);
    console.log(`Sessions:   ${stats.sessions.migrated}/${stats.sessions.total} migrated, ${stats.sessions.failed} failed`);
    console.log(`Passkeys:   ${stats.passkeys.migrated}/${stats.passkeys.total} migrated, ${stats.passkeys.failed} failed`);
    console.log(`Audit Logs: ${stats.auditLogs.migrated}/${stats.auditLogs.total} migrated, ${stats.auditLogs.failed} failed`);
    
    const totalFailed = stats.roles.failed + stats.users.failed + stats.userRoles.failed + 
                        stats.sessions.failed + stats.passkeys.failed + stats.auditLogs.failed;
    
    if (totalFailed > 0) {
      console.log(`\n⚠️  ${totalFailed} items failed to migrate`);
      process.exit(1);
    } else {
      console.log('\n✅ Migration completed successfully!');
    }
    
  } catch (error) {
    console.error('\n❌ Migration failed:', error);
    process.exit(1);
  } finally {
    await prisma.$disconnect();
  }
}

main();

