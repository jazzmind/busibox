# Database Migration Practices

**Purpose**: Ensure safe, non-destructive database schema changes in deployments

## Overview

Busibox uses Prisma for database schema management across multiple apps (ai-portal, etc.). 
This rule ensures database migrations never cause unexpected data loss.

## Critical Rules

### NEVER Use Destructive Flags

**These flags are FORBIDDEN in all contexts:**

```bash
# NEVER use these:
prisma db push --accept-data-loss    # Silently drops data
prisma db push --force-reset         # Wipes entire database
prisma migrate reset                  # Wipes entire database
```

### Pre-Migration Scripts for Data Cleanup

When schema changes require data modifications (e.g., adding unique constraints that might fail on existing duplicates):

1. **Create a pre-migration SQL script** in `prisma/pre-migrations/`
2. **Script runs BEFORE `prisma db push`** (handled by Ansible `prisma-setup.sh.j2`)
3. **Make scripts idempotent** - safe to run multiple times
4. **Log operations** for debugging

### Pre-Migration Script Location

```
your-app/
├── prisma/
│   ├── schema.prisma
│   ├── pre-migrations/           # SQL scripts run BEFORE db push
│   │   └── 001-cleanup-xyz.sql   # Numbered for ordering
│   └── migrations/               # SQL scripts run AFTER db push
│       └── add_something.sql
```

## Example: Adding Unique Constraint

When adding `@@unique([userId, libraryType])` to a model:

**Problem**: If duplicate entries exist, `prisma db push` will fail.

**Solution**: Create `prisma/pre-migrations/001-deduplicate-table.sql`:

```sql
-- Delete duplicates, keeping most recent
WITH ranked AS (
    SELECT id, ROW_NUMBER() OVER (
        PARTITION BY "userId", "libraryType" 
        ORDER BY "updatedAt" DESC
    ) AS rn
    FROM "Library"
    WHERE "userId" IS NOT NULL AND "libraryType" IS NOT NULL
)
DELETE FROM "Library" WHERE id IN (
    SELECT id FROM ranked WHERE rn > 1
);
```

## Deployment Flow

The Ansible `prisma-setup.sh.j2` template executes in this order:

1. **Load .env** - Get DATABASE_URL
2. **Run pre-migrations** - `prisma/pre-migrations/*.sql` (sorted)
3. **Run prisma db push** - Apply schema changes
4. **Run post-migrations** - `prisma/migrations/*.sql` (sorted)
5. **Seed database** - In test environment only

## Decision Tree

```
Schema change needed?
├─ Adding unique constraint
│   └─ Could duplicates exist?
│       ├─ Yes → Create pre-migration to deduplicate
│       └─ No → Safe to proceed
├─ Dropping column/table
│   └─ STOP - Discuss with team first
├─ Adding column
│   └─ Has default value?
│       ├─ Yes → Safe to proceed
│       └─ No → Create pre-migration to populate
└─ Other change
    └─ Could it fail on existing data?
        ├─ Yes → Create pre-migration
        └─ No → Safe to proceed
```

## AI Agent Instructions

When working with Prisma schema changes:

1. **Check for potential conflicts** - Would this fail on existing data?
2. **NEVER suggest `--accept-data-loss`** - This flag is forbidden
3. **Create pre-migration scripts** when data cleanup is needed
4. **Make scripts idempotent** - Use IF EXISTS, ON CONFLICT, etc.
5. **Test locally first** - Run against test database

## Related Rules

- See user rules: "NEVER run `npx prisma db push --force-reset`"
- See user rules: "Do not create prisma migrations unless asked - use prisma db push"

## Ansible Template Reference

Template: `provision/ansible/roles/app_deployer/templates/prisma-setup.sh.j2`

This template handles the migration flow for all Prisma-based apps deployed via Ansible.
