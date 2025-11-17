---
title: Database Commands - Quick Reference
created: 2025-01-13
updated: 2025-01-13
status: active
category: reference
tags: [database, commands, quick-reference]
---

# Database Commands - Quick Reference

## From Admin Workstation

```bash
# Connect to database
bash scripts/psql-connect.sh <database> <environment>
bash scripts/psql-connect.sh ai_portal production

# Check database
bash scripts/check-database.sh <database> <environment>
bash scripts/check-database.sh ai_portal production

# Initialize app database
bash scripts/init-app-database.sh <app-name> <environment>
bash scripts/init-app-database.sh ai-portal production
```

## Common Databases

- `busibox` - Platform database
- `ai_portal` - AI Portal
- `agent_client` - Agent Client

## Container IPs

- **Production**: 10.96.200.203
- **Test**: 10.96.201.203

## psql Commands

```sql
\dt              -- List tables
\d TableName     -- Describe table
\l               -- List databases
\du              -- List users
\q               -- Quit
```

## Quick Fixes

### Missing Tables

```bash
bash scripts/init-app-database.sh ai-portal production
ssh root@10.96.200.201 'pm2 restart ai-portal'
```

### Check Tables

```bash
bash scripts/check-database.sh ai_portal production
```

### Query Data

```bash
bash scripts/psql-connect.sh ai_portal production
```

```sql
SELECT * FROM "User" LIMIT 10;
```

