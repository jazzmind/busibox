---
title: Database Management Guide
created: 2025-01-13
updated: 2025-01-13
status: active
category: guides
tags: [database, postgresql, prisma, admin]
---

# Database Management Guide

This guide explains how to manage PostgreSQL databases for applications in the Busibox platform.

## Overview

Applications use PostgreSQL for data storage. The platform provides multiple ways to interact with databases:

1. **Command-line scripts** - For direct database access and inspection
2. **Application tools** - Using Prisma for schema management
3. **AI Portal UI** - Web-based database operations (coming soon)

## Quick Reference

### Common Database Names

- `busibox` - Main platform database (shared)
- `ai_portal` - AI Portal application database
- `agent_client` - Agent Client database
- Individual app databases follow pattern: `<app_name>`

### Container IPs

- **Production PostgreSQL**: 10.96.200.203
- **Test PostgreSQL**: 10.96.201.203

## Scripts

### 1. Connect to Database (psql)

Interactive SQL shell for querying and managing databases.

**Usage:**

```bash
# Connect to specific database
bash scripts/psql-connect.sh ai_portal production

# Connect to busibox database (default)
bash scripts/psql-connect.sh

# Test environment
bash scripts/psql-connect.sh ai_portal test

# Interactive selection
bash scripts/psql-connect.sh
# Will list available databases and prompt
```

**Common psql Commands:**

```sql
-- List tables
\dt

-- Describe table structure
\d User
\d+ User  -- More detail

-- List all databases
\l

-- List users/roles
\du

-- Show current database
SELECT current_database();

-- Show table row counts
SELECT schemaname, tablename, 
       n_tup_ins as inserts, 
       n_tup_upd as updates,
       n_tup_del as deletes
FROM pg_stat_user_tables;

-- Quit
\q
```

### 2. Check Database Status

View tables, sizes, and schema information without entering interactive mode.

**Usage:**

```bash
# Check specific database
bash scripts/check-database.sh ai_portal production

# Check busibox database (default)
bash scripts/check-database.sh

# Test environment
bash scripts/check-database.sh ai_portal test
```

**Output:**
- Database existence verification
- List of all tables
- Total table count
- Table sizes (sorted by size)
- Prisma migration history (if applicable)

### 3. Initialize Application Database

Set up or sync database schema for an application using Prisma.

**Usage:**

```bash
# Initialize database for app
bash scripts/init-app-database.sh ai-portal production

# Test environment
bash scripts/init-app-database.sh ai-portal test

# From inside container
ssh root@10.96.200.201
bash /usr/local/bin/init-app-database.sh ai-portal
```

**What it does:**
1. Generates Prisma client
2. Pushes schema to database (`prisma db push`)
3. Optionally seeds initial data
4. Verifies schema is applied

**When to use:**
- First deployment of an app
- After schema changes in the application
- When tables are missing or out of sync
- After database restoration

## Common Tasks

### Task 1: Fix Missing Tables

**Problem:** Application shows "table does not exist" errors.

**Solution:**

```bash
# Step 1: Verify database exists
bash scripts/check-database.sh ai_portal production

# Step 2: Initialize/sync schema
bash scripts/init-app-database.sh ai-portal production

# Step 3: Restart application
ssh root@10.96.200.201 'pm2 restart ai-portal'

# Step 4: Verify logs
bash scripts/tail-app-logs.sh ai-portal production
```

### Task 2: Inspect Database Tables

**Check if tables exist:**

```bash
bash scripts/check-database.sh ai_portal production
```

**View table structure:**

```bash
bash scripts/psql-connect.sh ai_portal production
```

Then in psql:
```sql
\d User
\d App
\d Role
```

### Task 3: Query Data

**Example: List all users:**

```bash
bash scripts/psql-connect.sh ai_portal production
```

```sql
SELECT id, email, status, "createdAt" 
FROM "User" 
ORDER BY "createdAt" DESC 
LIMIT 10;
```

**Example: Count records:**

```sql
SELECT 
  (SELECT COUNT(*) FROM "User") as users,
  (SELECT COUNT(*) FROM "App") as apps,
  (SELECT COUNT(*) FROM "Role") as roles;
```

### Task 4: Backup and Restore

**Backup database:**

```bash
# SSH to PostgreSQL container
ssh root@10.96.200.203

# Backup to file
su - postgres -c 'pg_dump ai_portal > /tmp/ai_portal_backup.sql'

# Copy to host
exit
scp root@10.96.200.203:/tmp/ai_portal_backup.sql ./ai_portal_backup_$(date +%Y%m%d).sql
```

**Restore database:**

```bash
# Copy backup to container
scp ./ai_portal_backup.sql root@10.96.200.203:/tmp/

# SSH to PostgreSQL container
ssh root@10.96.200.203

# Restore
su - postgres -c 'psql ai_portal < /tmp/ai_portal_backup.sql'
```

### Task 5: Create New Application Database

**For new applications:**

```bash
# SSH to PostgreSQL container
ssh root@10.96.200.203

# Create database
su - postgres -c 'createdb <app_name>'

# Grant permissions
su - postgres -c 'psql -c "GRANT ALL PRIVILEGES ON DATABASE <app_name> TO busibox_user;"'

# Exit and initialize schema
exit
bash scripts/init-app-database.sh <app-name> production
```

## Application-Specific Tools

### Prisma Commands

When SSH'd into an app container:

```bash
# SSH to apps container
ssh root@10.96.200.201
cd /srv/apps/ai-portal

# Generate Prisma client
npm run db:generate

# Push schema changes (no migrations)
npm run db:push

# Seed database
npm run db:seed

# Validate schema
npx prisma validate

# View Prisma studio (if enabled)
npx prisma studio
```

### Direct Database Access from App Container

```bash
# SSH to apps container
ssh root@10.96.200.201

# Use DATABASE_URL from .env
cd /srv/apps/ai-portal
source .env

# Connect using psql
psql $DATABASE_URL
```

## Troubleshooting

### "Database does not exist"

**Check:**
```bash
bash scripts/psql-connect.sh
```

Look for the database in the list. If missing:

```bash
ssh root@10.96.200.203
su - postgres -c 'createdb <database_name>'
su - postgres -c 'psql -c "GRANT ALL PRIVILEGES ON DATABASE <database_name> TO busibox_user;"'
```

### "Table does not exist"

**Solution:**
```bash
bash scripts/init-app-database.sh <app-name> production
```

### "Connection refused"

**Check PostgreSQL is running:**
```bash
ssh root@10.96.200.203
systemctl status postgresql
```

**Check from app container:**
```bash
ssh root@10.96.200.201
pg_isready -h 10.96.200.203 -p 5432
```

### "Permission denied"

**Grant permissions:**
```bash
ssh root@10.96.200.203
su - postgres -c 'psql -c "GRANT ALL PRIVILEGES ON DATABASE <database_name> TO busibox_user;"'
su - postgres -c 'psql -d <database_name> -c "GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO busibox_user;"'
```

### Schema Drift

When Prisma schema doesn't match database:

```bash
# Force sync (WARNING: may lose data)
cd /srv/apps/<app-name>
npx prisma db push --force-reset

# Better: Fix schema conflicts manually
npx prisma validate
# Review errors and fix schema.prisma
npm run db:push
```

## Best Practices

1. **Always backup before major changes**
   ```bash
   bash scripts/check-database.sh <db> production
   # Then backup as shown above
   ```

2. **Test in test environment first**
   ```bash
   bash scripts/init-app-database.sh <app> test
   # Verify it works before production
   ```

3. **Use Prisma for schema changes**
   - Update `prisma/schema.prisma` in app repository
   - Run `npm run db:push` to apply
   - Commit schema changes to git

4. **Monitor database sizes**
   ```bash
   bash scripts/check-database.sh <db> production
   # Review table sizes regularly
   ```

5. **Regular backups**
   - Set up automated backups (cron job)
   - Store backups off-server
   - Test restores periodically

## Related Documentation

- [Application Log Viewing](./viewing-application-logs.md) - Debugging database issues
- [Deployment System](./deployment-system.md) - Deployment includes database sync
- [PostgreSQL Container](../configuration/postgres-setup.md) - PostgreSQL configuration

## Quick Command Reference

```bash
# Connect to database
bash scripts/psql-connect.sh <database> <env>

# Check database status
bash scripts/check-database.sh <database> <env>

# Initialize app database
bash scripts/init-app-database.sh <app-name> <env>

# View app logs
bash scripts/tail-app-logs.sh <app-name> <env>
```

## Environment Variables

Applications need these in their `.env` file:

```bash
DATABASE_URL="postgresql://busibox_user:password@10.96.200.203:5432/<database_name>"
```

The deployment system automatically sets this from Ansible vault secrets.

