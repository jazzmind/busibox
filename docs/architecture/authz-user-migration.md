---
created: 2024-12-22
updated: 2024-12-22
status: draft
category: architecture
---

# AuthZ User & Role Migration Plan

## Overview

This document outlines the migration of user account and role management from ai-portal's local database to the centralized authz service. After migration:

- **authz** becomes the single source of truth for users, roles, and user-role assignments
- **ai-portal** retains only app-specific data (Apps, RolePermissions, Sessions, Videos, etc.)
- All user/role management UI in ai-portal calls authz APIs instead of local Prisma

## Current Architecture

### ai-portal Database (PostgreSQL)
```
User
├── id, email, emailVerified, status
├── lastLoginAt, pendingExpiresAt
├── sessions (1:N → Session)
├── magicLinks (1:N → MagicLink)
├── totpCodes (1:N → TotpCode)
├── passkeys (1:N → Passkey)
├── userRoles (1:N → UserRole)
└── [app-specific relations: videos, documents, conversations, etc.]

Role
├── id, name, description, isSystem
├── userRoles (1:N → UserRole)
└── rolePermissions (1:N → RolePermission)  ← stays in ai-portal

UserRole
├── userId, roleId, assignedBy, assignedAt
```

### authz Database (PostgreSQL - shared busibox DB)
```
authz_users
├── user_id, email, status
├── idp_provider, idp_tenant_id, idp_object_id
├── idp_roles, idp_groups
└── created_at, updated_at

authz_roles
├── id, name, description, scopes
└── created_at, updated_at

authz_user_roles
├── user_id, role_id
└── created_at
```

## Target Architecture

### authz Database (becomes user authority)
```
authz_users (EXTENDED)
├── user_id (UUID, PK)
├── email (unique)
├── email_verified_at (timestamptz, nullable)
├── status (text: PENDING, ACTIVE, DEACTIVATED)
├── last_login_at (timestamptz, nullable)
├── pending_expires_at (timestamptz, nullable)
├── idp_provider, idp_tenant_id, idp_object_id
├── idp_roles, idp_groups
└── created_at, updated_at

authz_roles (EXTENDED)
├── id (UUID, PK)
├── name (unique)
├── description
├── scopes (text[])
├── is_system (boolean, default false)  ← NEW
└── created_at, updated_at

authz_user_roles (EXTENDED)
├── user_id, role_id (composite PK)
├── assigned_by (UUID, nullable)  ← NEW
└── created_at
```

### ai-portal Database (app-specific only)
```
User (SLIM - reference only)
├── id (references authz user_id)
├── [app-specific relations only]
└── Sessions, MagicLinks, TotpCodes, Passkeys stay here (auth UX)

Role (REMOVED - use authz)
UserRole (REMOVED - use authz)

RolePermission (STAYS - app-specific)
├── roleId (references authz role)
├── appId
└── [ai-portal specific]
```

## Migration Phases

### Phase 1: Extend authz Schema
**Goal**: Add missing fields to authz tables without breaking existing sync

1. Add columns to `authz_users`:
   - `email_verified_at` (timestamptz, nullable)
   - `last_login_at` (timestamptz, nullable)
   - `pending_expires_at` (timestamptz, nullable)

2. Add columns to `authz_roles`:
   - `is_system` (boolean, default false)

3. Add columns to `authz_user_roles`:
   - `assigned_by` (UUID, nullable)

### Phase 2: Add User CRUD to authz
**Goal**: authz can create/manage users, not just sync them

New endpoints in `srv/authz/src/routes/admin.py`:

```
POST   /admin/users              - Create user
GET    /admin/users              - List users (paginated)
GET    /admin/users/{user_id}    - Get user details
PATCH  /admin/users/{user_id}    - Update user (status, etc.)
DELETE /admin/users/{user_id}    - Delete user

POST   /admin/users/{user_id}/activate    - Activate pending user
POST   /admin/users/{user_id}/deactivate  - Deactivate user
POST   /admin/users/{user_id}/reactivate  - Reactivate deactivated user
```

### Phase 3: Update busibox-app Package
**Goal**: Expose user management functions for ai-portal to use

Add to `@jazzmind/busibox-app`:

```typescript
// User management
createUser(email: string, roleIds?: string[]): Promise<User>
getUser(userId: string): Promise<User | null>
listUsers(options?: ListUsersOptions): Promise<PaginatedUsers>
updateUser(userId: string, data: UpdateUserData): Promise<User>
deleteUser(userId: string): Promise<void>

activateUser(userId: string): Promise<User>
deactivateUser(userId: string): Promise<User>
reactivateUser(userId: string): Promise<User>

// Role management (already exists, extend)
createRole(name: string, description?: string, isSystem?: boolean): Promise<Role>
updateRole(roleId: string, data: UpdateRoleData): Promise<Role>
deleteRole(roleId: string): Promise<void>
```

### Phase 4: Migrate ai-portal Admin Routes
**Goal**: ai-portal calls authz instead of local Prisma for user/role ops

Routes to migrate:
- `POST /api/admin/users` → calls authz createUser
- `GET /api/admin/users` → calls authz listUsers
- `GET /api/admin/users/[userId]` → calls authz getUser
- `PATCH /api/admin/users/[userId]` → calls authz updateUser
- `DELETE /api/admin/users/[userId]` → calls authz deleteUser
- `POST /api/admin/users/[userId]/roles` → calls authz addUserRole
- `DELETE /api/admin/users/[userId]/roles` → calls authz removeUserRole

- `POST /api/admin/roles` → calls authz createRole
- `GET /api/admin/roles` → calls authz listRoles
- `GET /api/admin/roles/[roleId]` → calls authz getRole
- `PATCH /api/admin/roles/[roleId]` → calls authz updateRole
- `DELETE /api/admin/roles/[roleId]` → calls authz deleteRole

**Keep in ai-portal** (app-specific):
- `POST /api/admin/roles/[roleId]/permissions` → manages RolePermission (app access)
- `DELETE /api/admin/roles/[roleId]/permissions/[appId]`

### Phase 5: Data Migration
**Goal**: Move existing users/roles from ai-portal to authz

1. Create migration script that:
   - Reads all Users from ai-portal
   - Creates corresponding authz_users (preserving IDs)
   - Reads all Roles from ai-portal
   - Creates corresponding authz_roles (preserving IDs)
   - Reads all UserRoles from ai-portal
   - Creates corresponding authz_user_roles

2. Run migration on test environment first
3. Validate data integrity
4. Run on production

### Phase 6: Remove Duplicate Tables
**Goal**: Clean up ai-portal schema

1. Remove User fields that moved to authz (keep id as reference)
2. Remove Role table from ai-portal
3. Remove UserRole table from ai-portal
4. Update RolePermission to reference authz role IDs

## Authentication Flow (Post-Migration)

```
User Login Request
       │
       ▼
┌─────────────────┐
│   ai-portal     │  (handles login UX, magic links, sessions)
│   /api/auth/*   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   authz         │  (validates user exists, returns roles)
│   /oauth/token  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Downstream     │  (ingest, search, agent)
│  Services       │  (validate JWT via JWKS)
└─────────────────┘
```

## User Creation Flow (Post-Migration)

```
Admin creates user in ai-portal UI
              │
              ▼
┌─────────────────────────────────────┐
│ ai-portal: POST /api/admin/users    │
│ → calls authz createUser API        │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ authz: POST /admin/users            │
│ → creates authz_users record        │
│ → creates authz_user_roles          │
│ → returns user object               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ ai-portal: sends magic link email   │
│ → creates MagicLink record locally  │
└─────────────────────────────────────┘
```

## API Contract Changes

### authz Admin User Endpoints

#### POST /admin/users
```json
// Request
{
  "email": "user@example.com",
  "role_ids": ["uuid-1", "uuid-2"],
  "status": "PENDING"  // optional, defaults to PENDING
}

// Response
{
  "user_id": "uuid",
  "email": "user@example.com",
  "status": "PENDING",
  "pending_expires_at": "2024-12-29T00:00:00Z",
  "roles": [
    {"id": "uuid-1", "name": "Admin"},
    {"id": "uuid-2", "name": "User"}
  ],
  "created_at": "2024-12-22T00:00:00Z"
}
```

#### GET /admin/users
```json
// Query params: ?page=1&limit=20&status=ACTIVE&search=email

// Response
{
  "users": [...],
  "pagination": {
    "page": 1,
    "limit": 20,
    "total_count": 100,
    "total_pages": 5
  }
}
```

#### PATCH /admin/users/{user_id}
```json
// Request
{
  "status": "ACTIVE",
  "email_verified_at": "2024-12-22T00:00:00Z"
}

// Response
{
  "user_id": "uuid",
  "email": "user@example.com",
  "status": "ACTIVE",
  ...
}
```

## Rollback Plan

If issues arise:
1. ai-portal can fall back to local Prisma queries
2. authz sync continues to work (bidirectional if needed)
3. Feature flags control which path is used

## Success Criteria

1. All user CRUD operations go through authz
2. All role CRUD operations go through authz
3. ai-portal User table only contains app-specific relations
4. ai-portal Role/UserRole tables removed
5. No functionality regression in admin UI
6. RolePermission (app access) still works correctly

## Timeline Estimate

- Phase 1 (Schema): 1-2 hours
- Phase 2 (authz endpoints): 4-6 hours
- Phase 3 (busibox-app): 2-3 hours
- Phase 4 (ai-portal migration): 4-6 hours
- Phase 5 (data migration): 2-3 hours
- Phase 6 (cleanup): 1-2 hours

**Total: 14-22 hours**

## Open Questions

1. Should Sessions/MagicLinks/TotpCodes/Passkeys move to authz?
   - **Recommendation**: No, keep in ai-portal. These are login UX concerns.

2. Should AuditLog move to authz?
   - **Recommendation**: Partial. User/role audit events → authz. App-specific events → ai-portal.

3. How to handle RolePermission foreign key to Role?
   - **Recommendation**: Keep roleId as UUID, validate against authz on write.

