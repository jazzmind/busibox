/**
 * Migration Script: Move RolePermission and LibraryRole to Authz
 * 
 * This one-time migration script reads existing role-app and role-library
 * bindings from the ai-portal database and creates corresponding
 * authz_role_bindings entries in the authz service.
 * 
 * Prerequisites:
 * - ai-portal database accessible
 * - authz service running with admin token
 * 
 * Usage:
 *   npx tsx scripts/migrations/migrate-role-bindings-to-authz.ts [--dry-run]
 * 
 * Environment variables:
 *   AI_PORTAL_DATABASE_URL - PostgreSQL connection string for ai-portal
 *   AUTHZ_BASE_URL - Base URL for authz service (default: http://10.96.200.210:8010)
 *   AUTHZ_ADMIN_TOKEN - Admin token for authz service
 */

import { PrismaClient } from '@prisma/client';

// Configuration
const AI_PORTAL_DATABASE_URL = process.env.AI_PORTAL_DATABASE_URL || process.env.DATABASE_URL;
const AUTHZ_BASE_URL = process.env.AUTHZ_BASE_URL || 'http://10.96.200.210:8010';
const AUTHZ_ADMIN_TOKEN = process.env.AUTHZ_ADMIN_TOKEN;

if (!AI_PORTAL_DATABASE_URL) {
  console.error('❌ AI_PORTAL_DATABASE_URL or DATABASE_URL is required');
  process.exit(1);
}

if (!AUTHZ_ADMIN_TOKEN) {
  console.error('❌ AUTHZ_ADMIN_TOKEN is required');
  process.exit(1);
}

const isDryRun = process.argv.includes('--dry-run');

// Initialize Prisma client for ai-portal
const prisma = new PrismaClient({
  datasources: {
    db: { url: AI_PORTAL_DATABASE_URL },
  },
});

interface MigrationStats {
  appBindingsFound: number;
  appBindingsCreated: number;
  appBindingsSkipped: number;
  libraryBindingsFound: number;
  libraryBindingsCreated: number;
  libraryBindingsSkipped: number;
  errors: string[];
}

const stats: MigrationStats = {
  appBindingsFound: 0,
  appBindingsCreated: 0,
  appBindingsSkipped: 0,
  libraryBindingsFound: 0,
  libraryBindingsCreated: 0,
  libraryBindingsSkipped: 0,
  errors: [],
};

async function createBinding(
  roleId: string,
  resourceType: 'app' | 'library',
  resourceId: string
): Promise<boolean> {
  const url = `${AUTHZ_BASE_URL}/admin/bindings`;
  
  try {
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${AUTHZ_ADMIN_TOKEN}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        role_id: roleId,
        resource_type: resourceType,
        resource_id: resourceId,
      }),
    });

    if (response.status === 201) {
      return true;
    } else if (response.status === 409) {
      // Already exists
      return false;
    } else {
      const text = await response.text();
      throw new Error(`HTTP ${response.status}: ${text}`);
    }
  } catch (error: any) {
    throw error;
  }
}

async function migrateAppBindings(): Promise<void> {
  console.log('\n📦 Migrating RolePermission (app bindings)...');
  
  // Note: This query assumes the old schema still exists
  // If RolePermission has been removed, this will fail
  const rolePermissions = await prisma.$queryRaw<
    Array<{ roleId: string; appId: string }>
  >`SELECT "roleId", "appId" FROM "RolePermission"`;
  
  stats.appBindingsFound = rolePermissions.length;
  console.log(`   Found ${rolePermissions.length} app bindings`);

  for (const rp of rolePermissions) {
    try {
      if (isDryRun) {
        console.log(`   [DRY RUN] Would create binding: role=${rp.roleId} -> app=${rp.appId}`);
        stats.appBindingsCreated++;
      } else {
        const created = await createBinding(rp.roleId, 'app', rp.appId);
        if (created) {
          console.log(`   ✓ Created binding: role=${rp.roleId} -> app=${rp.appId}`);
          stats.appBindingsCreated++;
        } else {
          console.log(`   ⊘ Skipped (exists): role=${rp.roleId} -> app=${rp.appId}`);
          stats.appBindingsSkipped++;
        }
      }
    } catch (error: any) {
      console.error(`   ✗ Failed: role=${rp.roleId} -> app=${rp.appId}: ${error.message}`);
      stats.errors.push(`App binding ${rp.roleId}->${rp.appId}: ${error.message}`);
    }
  }
}

async function migrateLibraryBindings(): Promise<void> {
  console.log('\n📚 Migrating LibraryRole (library bindings)...');
  
  // Note: This query assumes the old schema still exists
  const libraryRoles = await prisma.$queryRaw<
    Array<{ roleId: string; libraryId: string }>
  >`SELECT "roleId", "libraryId" FROM "LibraryRole"`;
  
  stats.libraryBindingsFound = libraryRoles.length;
  console.log(`   Found ${libraryRoles.length} library bindings`);

  for (const lr of libraryRoles) {
    try {
      if (isDryRun) {
        console.log(`   [DRY RUN] Would create binding: role=${lr.roleId} -> library=${lr.libraryId}`);
        stats.libraryBindingsCreated++;
      } else {
        const created = await createBinding(lr.roleId, 'library', lr.libraryId);
        if (created) {
          console.log(`   ✓ Created binding: role=${lr.roleId} -> library=${lr.libraryId}`);
          stats.libraryBindingsCreated++;
        } else {
          console.log(`   ⊘ Skipped (exists): role=${lr.roleId} -> library=${lr.libraryId}`);
          stats.libraryBindingsSkipped++;
        }
      }
    } catch (error: any) {
      console.error(`   ✗ Failed: role=${lr.roleId} -> library=${lr.libraryId}: ${error.message}`);
      stats.errors.push(`Library binding ${lr.roleId}->${lr.libraryId}: ${error.message}`);
    }
  }
}

async function migrateLegacyLibraryRoleId(): Promise<void> {
  console.log('\n📖 Migrating legacy Library.roleId...');
  
  // Find libraries with legacy roleId set
  const libraries = await prisma.$queryRaw<
    Array<{ id: string; roleId: string }>
  >`SELECT id, "roleId" FROM "Library" WHERE "roleId" IS NOT NULL AND "isPersonal" = false`;
  
  console.log(`   Found ${libraries.length} libraries with legacy roleId`);

  for (const lib of libraries) {
    try {
      if (isDryRun) {
        console.log(`   [DRY RUN] Would create binding: role=${lib.roleId} -> library=${lib.id}`);
        stats.libraryBindingsCreated++;
      } else {
        const created = await createBinding(lib.roleId, 'library', lib.id);
        if (created) {
          console.log(`   ✓ Created binding from legacy: role=${lib.roleId} -> library=${lib.id}`);
          stats.libraryBindingsCreated++;
        } else {
          console.log(`   ⊘ Skipped (exists): role=${lib.roleId} -> library=${lib.id}`);
          stats.libraryBindingsSkipped++;
        }
      }
    } catch (error: any) {
      console.error(`   ✗ Failed: role=${lib.roleId} -> library=${lib.id}: ${error.message}`);
      stats.errors.push(`Legacy library binding ${lib.roleId}->${lib.id}: ${error.message}`);
    }
  }
}

async function main(): Promise<void> {
  console.log('═══════════════════════════════════════════════════════════════');
  console.log('   Role Bindings Migration: ai-portal -> authz');
  console.log('═══════════════════════════════════════════════════════════════');
  
  if (isDryRun) {
    console.log('\n⚠️  DRY RUN MODE - No changes will be made');
  }
  
  console.log(`\n   Source: ${AI_PORTAL_DATABASE_URL?.split('@')[1] || 'configured database'}`);
  console.log(`   Target: ${AUTHZ_BASE_URL}`);
  
  try {
    // Test connection to ai-portal
    await prisma.$connect();
    console.log('\n✓ Connected to ai-portal database');
    
    // Test connection to authz
    const healthResp = await fetch(`${AUTHZ_BASE_URL}/health/ready`);
    if (!healthResp.ok) {
      throw new Error(`Authz service not ready: ${healthResp.status}`);
    }
    console.log('✓ Authz service is ready');
    
    // Run migrations
    await migrateAppBindings();
    await migrateLibraryBindings();
    await migrateLegacyLibraryRoleId();
    
    // Print summary
    console.log('\n═══════════════════════════════════════════════════════════════');
    console.log('   Migration Summary');
    console.log('═══════════════════════════════════════════════════════════════');
    console.log(`\n   App Bindings:`);
    console.log(`     Found:   ${stats.appBindingsFound}`);
    console.log(`     Created: ${stats.appBindingsCreated}`);
    console.log(`     Skipped: ${stats.appBindingsSkipped}`);
    console.log(`\n   Library Bindings:`);
    console.log(`     Found:   ${stats.libraryBindingsFound}`);
    console.log(`     Created: ${stats.libraryBindingsCreated}`);
    console.log(`     Skipped: ${stats.libraryBindingsSkipped}`);
    
    if (stats.errors.length > 0) {
      console.log(`\n   ❌ Errors: ${stats.errors.length}`);
      for (const error of stats.errors) {
        console.log(`      - ${error}`);
      }
    } else {
      console.log('\n   ✅ No errors');
    }
    
    if (isDryRun) {
      console.log('\n   ⚠️  This was a dry run. Run without --dry-run to apply changes.');
    }
    
  } catch (error: any) {
    console.error('\n❌ Migration failed:', error.message);
    process.exit(1);
  } finally {
    await prisma.$disconnect();
  }
}

main();

