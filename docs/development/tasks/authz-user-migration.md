---
created: 2024-12-22
updated: 2024-12-22
status: draft
category: architecture
---

# AuthZ User & Role Migration Plan

## Overview

This document outlines the migration of user identity, authentication, and authorization from ai-portal's local database to the centralized authz service. After migration:

- **authz** becomes the single source of truth for:
  - Users (accounts, status, verification)
  - Roles and user-role assignments
  - Sessions and authentication tokens
  - Magic links, TOTP codes, passkeys
  - All audit logging
- **ai-portal** retains only app-specific data (Apps, RolePermissions, Videos, Documents, etc.)
- All identity/auth operations call authz APIs

## Current Architecture

### ai-portal Database (PostgreSQL)
```
User
├── id, email, emailVerified, status
├── lastLoginAt, pendingExpiresAt
├── sessions (1:N → Session)           ← MOVES TO AUTHZ
├── magicLinks (1:N → MagicLink)       ← MOVES TO AUTHZ
├── totpCodes (1:N → TotpCode)         ← MOVES TO AUTHZ
├── passkeys (1:N → Passkey)           ← MOVES TO AUTHZ
├── userRoles (1:N → UserRole)         ← MOVES TO AUTHZ
└── [app-specific relations: videos, documents, conversations, etc.]

Role                                    ← MOVES TO AUTHZ
├── id, name, description, isSystem
├── userRoles (1:N → UserRole)
└── rolePermissions (1:N → RolePermission)  ← STAYS in ai-portal

UserRole                                ← MOVES TO AUTHZ
├── userId, roleId, assignedBy, assignedAt

Session                                 ← MOVES TO AUTHZ
├── id, userId, token, expiresAt, ipAddress, userAgent

MagicLink                               ← MOVES TO AUTHZ
├── id, userId, token, email, expiresAt, usedAt

TotpCode                                ← MOVES TO AUTHZ
├── id, userId, code, email, expiresAt, usedAt

Passkey                                 ← MOVES TO AUTHZ
├── id, userId, credentialId, credentialPublicKey
├── counter, deviceType, backedUp, transports, aaguid
├── name, lastUsedAt

PasskeyChallenge                        ← MOVES TO AUTHZ
├── id, challenge, userId, type, expiresAt

AuditLog                                ← MOVES TO AUTHZ
├── id, eventType, userId, targetUserId, targetRoleId, targetAppId
├── action, details, ipAddress, userAgent, success, errorMessage
```

### authz Database (PostgreSQL - shared busibox DB)
```
audit_logs (already exists)
├── id, actor_id, action, resource_type, resource_id, details

authz_users
├── user_id, email, status
├── idp_provider, idp_tenant_id, idp_object_id
├── idp_roles, idp_groups

authz_roles
├── id, name, description, scopes

authz_user_roles
├── user_id, role_id

authz_oauth_clients, authz_signing_keys (already exists)
authz_key_encryption_keys, authz_wrapped_data_keys (already exists)
```

## Target Architecture

### authz Database (becomes identity authority)
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

authz_sessions (NEW)
├── id (UUID, PK)
├── user_id (FK → authz_users)
├── token (unique, indexed)
├── expires_at (timestamptz)
├── ip_address (text, nullable)
├── user_agent (text, nullable)
├── created_at (timestamptz)

authz_magic_links (NEW)
├── id (UUID, PK)
├── user_id (FK → authz_users)
├── token (unique, indexed)
├── email (text)
├── expires_at (timestamptz)
├── used_at (timestamptz, nullable)
├── created_at (timestamptz)

authz_totp_codes (NEW)
├── id (UUID, PK)
├── user_id (FK → authz_users)
├── code_hash (text)  -- hashed 6-digit code
├── email (text)
├── expires_at (timestamptz)
├── used_at (timestamptz, nullable)
├── created_at (timestamptz)

authz_passkeys (NEW)
├── id (UUID, PK)
├── user_id (FK → authz_users)
├── credential_id (text, unique)  -- Base64URL
├── credential_public_key (text)  -- Base64URL
├── counter (bigint, default 0)
├── device_type (text)  -- 'singleDevice' or 'multiDevice'
├── backed_up (boolean, default false)
├── transports (text[])  -- ['internal', 'hybrid']
├── aaguid (text, nullable)
├── name (text)  -- user-friendly device name
├── last_used_at (timestamptz, nullable)
├── created_at, updated_at (timestamptz)

authz_passkey_challenges (NEW)
├── id (UUID, PK)
├── challenge (text, unique)  -- Base64URL
├── user_id (UUID, nullable)  -- null for auth, set for registration
├── type (text)  -- 'registration' or 'authentication'
├── expires_at (timestamptz)
├── created_at (timestamptz)

audit_logs (EXTENDED - already exists)
├── id (UUID, PK)
├── actor_id (UUID)
├── action (text)
├── resource_type (text)
├── resource_id (UUID, nullable)
├── event_type (text, nullable)  ← NEW (for ai-portal event types)
├── target_user_id (UUID, nullable)  ← NEW
├── target_role_id (UUID, nullable)  ← NEW
├── target_app_id (UUID, nullable)  ← NEW
├── ip_address (text, nullable)  ← NEW
├── user_agent (text, nullable)  ← NEW
├── success (boolean, default true)  ← NEW
├── error_message (text, nullable)  ← NEW
├── details (jsonb)
├── created_at (timestamptz)
```

### ai-portal Database (app-specific only)
```
User (REMOVED - use authz)
Role (REMOVED - use authz)
UserRole (REMOVED - use authz)
Session (REMOVED - use authz)
MagicLink (REMOVED - use authz)
TotpCode (REMOVED - use authz)
Passkey (REMOVED - use authz)
PasskeyChallenge (REMOVED - use authz)
AuditLog (REMOVED - use authz)

RolePermission (STAYS - app-specific)
├── roleId (references authz role by UUID)
├── appId
└── [ai-portal specific]

App, Video, Document, Conversation, etc. (STAYS - app-specific)
```

## Migration Phases

### Phase 1: Extend authz Schema
**Goal**: Add missing fields and new tables to authz without breaking existing sync

#### 1.1 Extend existing tables

Add columns to `authz_users`:
- `email_verified_at` (timestamptz, nullable)
- `last_login_at` (timestamptz, nullable)
- `pending_expires_at` (timestamptz, nullable)

Add columns to `authz_roles`:
- `is_system` (boolean, default false)

Add columns to `authz_user_roles`:
- `assigned_by` (UUID, nullable)

Extend `audit_logs`:
- `event_type` (text, nullable)
- `target_user_id` (UUID, nullable)
- `target_role_id` (UUID, nullable)
- `target_app_id` (UUID, nullable)
- `ip_address` (text, nullable)
- `user_agent` (text, nullable)
- `success` (boolean, default true)
- `error_message` (text, nullable)

#### 1.2 Add new authentication tables

Create `authz_sessions`:
```sql
CREATE TABLE IF NOT EXISTS authz_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
  token text NOT NULL UNIQUE,
  expires_at timestamptz NOT NULL,
  ip_address text NULL,
  user_agent text NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_authz_sessions_token ON authz_sessions(token);
CREATE INDEX idx_authz_sessions_user_id ON authz_sessions(user_id);
CREATE INDEX idx_authz_sessions_expires_at ON authz_sessions(expires_at);
```

Create `authz_magic_links`:
```sql
CREATE TABLE IF NOT EXISTS authz_magic_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
  token text NOT NULL UNIQUE,
  email text NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_authz_magic_links_token ON authz_magic_links(token);
CREATE INDEX idx_authz_magic_links_user_id ON authz_magic_links(user_id);
CREATE INDEX idx_authz_magic_links_expires_at ON authz_magic_links(expires_at);
```

Create `authz_totp_codes`:
```sql
CREATE TABLE IF NOT EXISTS authz_totp_codes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
  code_hash text NOT NULL,
  email text NOT NULL,
  expires_at timestamptz NOT NULL,
  used_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_authz_totp_codes_user_id ON authz_totp_codes(user_id);
CREATE INDEX idx_authz_totp_codes_email_code ON authz_totp_codes(email, code_hash);
CREATE INDEX idx_authz_totp_codes_expires_at ON authz_totp_codes(expires_at);
```

Create `authz_passkeys`:
```sql
CREATE TABLE IF NOT EXISTS authz_passkeys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES authz_users(user_id) ON DELETE CASCADE,
  credential_id text NOT NULL UNIQUE,
  credential_public_key text NOT NULL,
  counter bigint NOT NULL DEFAULT 0,
  device_type text NOT NULL,
  backed_up boolean NOT NULL DEFAULT false,
  transports text[] NOT NULL DEFAULT '{}'::text[],
  aaguid text NULL,
  name text NOT NULL,
  last_used_at timestamptz NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_authz_passkeys_user_id ON authz_passkeys(user_id);
CREATE INDEX idx_authz_passkeys_credential_id ON authz_passkeys(credential_id);
```

Create `authz_passkey_challenges`:
```sql
CREATE TABLE IF NOT EXISTS authz_passkey_challenges (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  challenge text NOT NULL UNIQUE,
  user_id uuid NULL,
  type text NOT NULL,
  expires_at timestamptz NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_authz_passkey_challenges_challenge ON authz_passkey_challenges(challenge);
CREATE INDEX idx_authz_passkey_challenges_expires_at ON authz_passkey_challenges(expires_at);
```

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

### Phase 3: Add Authentication Endpoints to authz
**Goal**: authz handles all authentication token management

New endpoints in `srv/authz/src/routes/auth.py`:

```
# Sessions
POST   /auth/sessions            - Create session (login)
GET    /auth/sessions/{token}    - Validate session
DELETE /auth/sessions/{token}    - Delete session (logout)
DELETE /auth/sessions/user/{user_id}  - Delete all user sessions

# Magic Links
POST   /auth/magic-links         - Create magic link
GET    /auth/magic-links/{token} - Validate magic link
POST   /auth/magic-links/{token}/use  - Use magic link (marks used)

# TOTP
POST   /auth/totp                - Create TOTP code
POST   /auth/totp/verify         - Verify TOTP code

# Passkeys
POST   /auth/passkeys/challenge  - Create passkey challenge
GET    /auth/passkeys/challenge/{challenge}  - Get challenge
POST   /auth/passkeys            - Register passkey
GET    /auth/passkeys/user/{user_id}  - List user's passkeys
DELETE /auth/passkeys/{passkey_id}    - Remove passkey
POST   /auth/passkeys/authenticate    - Authenticate with passkey
```

### Phase 4: Add Audit Logging Endpoints
**Goal**: All audit events go through authz

New endpoints in `srv/authz/src/routes/audit.py`:

```
POST   /audit/log                - Create audit log entry
GET    /audit/logs               - List audit logs (paginated, filtered)
GET    /audit/logs/user/{user_id}  - Get user's audit trail
```

### Phase 5: Update busibox-app Package
**Goal**: Expose user management and auth functions for ai-portal to use

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

// Session management
createSession(userId: string, options?: SessionOptions): Promise<Session>
validateSession(token: string): Promise<Session | null>
deleteSession(token: string): Promise<void>
deleteUserSessions(userId: string): Promise<void>

// Magic links
createMagicLink(userId: string, email: string): Promise<MagicLink>
validateMagicLink(token: string): Promise<MagicLink | null>
useMagicLink(token: string): Promise<User>

// TOTP
createTotpCode(userId: string, email: string): Promise<{ code: string }>
verifyTotpCode(email: string, code: string): Promise<User | null>

// Passkeys
createPasskeyChallenge(type: 'registration' | 'authentication', userId?: string): Promise<Challenge>
registerPasskey(userId: string, credential: PasskeyCredential): Promise<Passkey>
listUserPasskeys(userId: string): Promise<Passkey[]>
deletePasskey(passkeyId: string): Promise<void>
authenticateWithPasskey(credential: PasskeyAssertion): Promise<User>

// Audit logging
logAuditEvent(event: AuditEvent): Promise<void>
getAuditLogs(options?: AuditLogOptions): Promise<PaginatedAuditLogs>
```

### Phase 6: Migrate ai-portal Routes
**Goal**: ai-portal calls authz instead of local Prisma for all identity/auth ops

#### Admin Routes to migrate:
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

#### Auth Routes to migrate:
- `POST /api/auth/magic-link` → calls authz createMagicLink
- `GET /api/auth/verify-magic-link` → calls authz useMagicLink
- `POST /api/auth/verify-totp` → calls authz verifyTotpCode
- `POST /api/auth/passkey/*` → calls authz passkey endpoints
- `POST /api/auth/logout` → calls authz deleteSession

#### Audit Routes to migrate:
- All `logXxx()` calls → call authz logAuditEvent
- `GET /api/admin/logging` → calls authz getAuditLogs

**Keep in ai-portal** (app-specific):
- `POST /api/admin/roles/[roleId]/permissions` → manages RolePermission (app access)
- `DELETE /api/admin/roles/[roleId]/permissions/[appId]`
- `POST /api/admin/apps/*` → manages App table

### Phase 7: Data Migration
**Goal**: Move existing data from ai-portal to authz

1. Create migration script that:
   - Reads all Users from ai-portal → creates authz_users (preserving IDs)
   - Reads all Roles from ai-portal → creates authz_roles (preserving IDs)
   - Reads all UserRoles from ai-portal → creates authz_user_roles
   - Reads all Sessions from ai-portal → creates authz_sessions
   - Reads all MagicLinks from ai-portal → creates authz_magic_links
   - Reads all TotpCodes from ai-portal → creates authz_totp_codes
   - Reads all Passkeys from ai-portal → creates authz_passkeys
   - Reads all AuditLogs from ai-portal → creates audit_logs

2. Run migration on test environment first
3. Validate data integrity
4. Run on production

### Phase 8: Remove Duplicate Tables from ai-portal
**Goal**: Clean up ai-portal schema

1. Remove from ai-portal Prisma schema:
   - `User` model (entirely)
   - `Role` model (entirely)
   - `UserRole` model (entirely)
   - `Session` model (entirely)
   - `MagicLink` model (entirely)
   - `TotpCode` model (entirely)
   - `Passkey` model (entirely)
   - `PasskeyChallenge` model (entirely)
   - `AuditLog` model (entirely)

2. Update `RolePermission` to not require FK to Role (validate against authz on write)

3. Update all app-specific tables that reference User:
   - `Video.ownerId` → just a UUID, no FK
   - `Document.userId` → just a UUID, no FK
   - `Conversation.ownerId` → just a UUID, no FK
   - etc.

4. Generate new Prisma migration
5. Deploy schema changes

## Authentication Flow (Post-Migration)

```
User Login Request (Magic Link)
       │
       ▼
┌─────────────────┐
│   ai-portal     │  (renders login UI)
│   /login        │
└────────┬────────┘
         │ POST /api/auth/magic-link
         ▼
┌─────────────────┐
│   authz         │  (creates magic link, stores in authz_magic_links)
│   /auth/magic-links
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   ai-portal     │  (sends email with link)
│   email service │
└─────────────────┘

User Clicks Magic Link
       │
       ▼
┌─────────────────┐
│   ai-portal     │  (handles /verify?token=xxx)
│   /verify       │
└────────┬────────┘
         │ POST /auth/magic-links/{token}/use
         ▼
┌─────────────────┐
│   authz         │  (validates token, creates session)
│   /auth/*       │  (returns session token + user)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   ai-portal     │  (sets session cookie)
│   redirect home │
└─────────────────┘

Authenticated Request to Downstream Service
       │
       ▼
┌─────────────────┐
│   ai-portal     │  (validates session via authz)
│   /api/*        │
└────────┬────────┘
         │ token exchange
         ▼
┌─────────────────┐
│   authz         │  (issues audience-bound JWT)
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
│ → logs audit event                  │
│ → returns user object               │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ authz: POST /auth/magic-links       │
│ → creates magic link in authz       │
│ → returns token                     │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ ai-portal: sends magic link email   │
│ (email sending stays in ai-portal)  │
└─────────────────────────────────────┘
```

## Passkey Authentication Flow (Post-Migration)

```
User clicks "Sign in with Passkey"
              │
              ▼
┌─────────────────────────────────────┐
│ ai-portal: GET /api/auth/passkey/challenge
│ → calls authz createPasskeyChallenge
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ authz: POST /auth/passkeys/challenge
│ → creates authz_passkey_challenges  │
│ → returns challenge                 │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ Browser: WebAuthn API               │
│ → user authenticates with device    │
│ → returns signed assertion          │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ ai-portal: POST /api/auth/passkey/verify
│ → calls authz authenticateWithPasskey
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ authz: POST /auth/passkeys/authenticate
│ → validates assertion               │
│ → creates session                   │
│ → logs audit event                  │
│ → returns session + user            │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│ ai-portal: sets session cookie      │
│ → redirects to dashboard            │
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

### authz Auth Endpoints

#### POST /auth/sessions
```json
// Request
{
  "user_id": "uuid",
  "ip_address": "192.168.1.1",  // optional
  "user_agent": "Mozilla/5.0..."  // optional
}

// Response
{
  "session_id": "uuid",
  "token": "session-token-string",
  "user_id": "uuid",
  "expires_at": "2024-12-23T00:00:00Z",
  "created_at": "2024-12-22T00:00:00Z"
}
```

#### POST /auth/magic-links
```json
// Request
{
  "user_id": "uuid",
  "email": "user@example.com"
}

// Response
{
  "magic_link_id": "uuid",
  "token": "magic-link-token",
  "expires_at": "2024-12-22T00:15:00Z"
}
```

#### POST /auth/magic-links/{token}/use
```json
// Response
{
  "user": {
    "user_id": "uuid",
    "email": "user@example.com",
    "status": "ACTIVE"
  },
  "session": {
    "token": "new-session-token",
    "expires_at": "2024-12-23T00:00:00Z"
  }
}
```

#### POST /auth/passkeys/challenge
```json
// Request
{
  "type": "authentication",  // or "registration"
  "user_id": "uuid"  // optional for authentication
}

// Response
{
  "challenge": "base64url-encoded-challenge",
  "expires_at": "2024-12-22T00:05:00Z"
}
```

#### POST /auth/passkeys
```json
// Request (registration)
{
  "user_id": "uuid",
  "credential_id": "base64url",
  "credential_public_key": "base64url",
  "counter": 0,
  "device_type": "multiDevice",
  "backed_up": true,
  "transports": ["internal", "hybrid"],
  "aaguid": "optional-aaguid",
  "name": "My iPhone"
}

// Response
{
  "passkey_id": "uuid",
  "name": "My iPhone",
  "created_at": "2024-12-22T00:00:00Z"
}
```

#### POST /auth/passkeys/authenticate
```json
// Request
{
  "credential_id": "base64url",
  "authenticator_data": "base64url",
  "client_data_json": "base64url",
  "signature": "base64url"
}

// Response
{
  "user": {
    "user_id": "uuid",
    "email": "user@example.com",
    "status": "ACTIVE"
  },
  "session": {
    "token": "new-session-token",
    "expires_at": "2024-12-23T00:00:00Z"
  }
}
```

### authz Audit Endpoints

#### POST /audit/log
```json
// Request
{
  "actor_id": "uuid",
  "action": "USER_LOGIN",
  "event_type": "USER_LOGIN",  // matches ai-portal AuditEventType
  "resource_type": "user",
  "resource_id": "uuid",
  "target_user_id": "uuid",
  "ip_address": "192.168.1.1",
  "user_agent": "Mozilla/5.0...",
  "success": true,
  "details": { "method": "magic_link" }
}

// Response
{
  "audit_log_id": "uuid",
  "created_at": "2024-12-22T00:00:00Z"
}
```

#### GET /audit/logs
```json
// Query params: ?page=1&limit=50&actor_id=uuid&event_type=USER_LOGIN&from=2024-12-01&to=2024-12-31

// Response
{
  "logs": [
    {
      "id": "uuid",
      "actor_id": "uuid",
      "action": "USER_LOGIN",
      "event_type": "USER_LOGIN",
      "resource_type": "user",
      "resource_id": "uuid",
      "details": {...},
      "ip_address": "192.168.1.1",
      "success": true,
      "created_at": "2024-12-22T00:00:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "limit": 50,
    "total_count": 1000,
    "total_pages": 20
  }
}
```

## Rollback Plan

If issues arise:
1. ai-portal can fall back to local Prisma queries via feature flag
2. authz sync continues to work (bidirectional if needed)
3. Feature flags control which path is used:
   - `AUTHZ_USER_MANAGEMENT=true` - use authz for user CRUD
   - `AUTHZ_SESSIONS=true` - use authz for sessions
   - `AUTHZ_AUDIT=true` - use authz for audit logging

## Success Criteria

1. All user CRUD operations go through authz
2. All role CRUD operations go through authz
3. All session management goes through authz
4. All authentication (magic links, TOTP, passkeys) goes through authz
5. All audit logging goes through authz
6. ai-portal database contains ONLY app-specific data
7. No functionality regression in admin UI or login flows
8. RolePermission (app access) still works correctly

## Timeline Estimate

- Phase 1 (Schema extensions): 2-3 hours
- Phase 2 (User CRUD endpoints): 4-6 hours
- Phase 3 (Auth endpoints): 6-8 hours
- Phase 4 (Audit endpoints): 2-3 hours
- Phase 5 (busibox-app package): 4-6 hours
- Phase 6 (ai-portal migration): 8-12 hours
- Phase 7 (Data migration): 3-4 hours
- Phase 8 (Cleanup): 2-3 hours

**Total: 31-45 hours**

## Open Questions

1. How to handle email sending?
   - **Decision**: Keep email sending in ai-portal. authz creates tokens, ai-portal sends emails.

2. How to handle RolePermission foreign key to Role?
   - **Decision**: Keep roleId as UUID, validate against authz on write. No FK constraint.

3. Should we keep a User reference table in ai-portal for app-specific relations?
   - **Decision**: No. App tables (Video, Document, etc.) will store user_id as plain UUID.
   - Validation happens at application layer via authz API.

4. How to handle the transition period?
   - **Decision**: Use feature flags. Both systems run in parallel during migration.
   - Sync data bidirectionally until cutover is complete.

## Dependencies

- `@jazzmind/busibox-app` package must be updated before ai-portal migration
- authz service must be deployed with new endpoints before ai-portal changes
- Data migration script must run before removing tables from ai-portal

