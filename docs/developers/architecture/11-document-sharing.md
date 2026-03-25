---
title: "Document Sharing & Data Access Control"
category: "developer"
order: 12
description: "Unified document sharing model with app roles, team roles, visibility modes, and RLS-based access control"
published: true
---

# Document Sharing & Data Access Control

**Created**: 2026-03-17  
**Last Updated**: 2026-03-25  
**Status**: Active  
**Category**: Architecture  
**Related Docs**:  
- `architecture/03-authentication.md`  
- `architecture/07-apps.md`

---

## Overview

Busibox provides a unified document sharing model that controls access to data documents (`data_files` with `doc_type = 'data'`) and their records (`data_records`). The model supports multiple visibility modes and two categories of roles, backed by PostgreSQL Row-Level Security (RLS) with `FORCE ROW LEVEL SECURITY` on all tables.

## Role Types

There are two categories of roles relevant to data access:

### App Roles

Pattern: `app:<app-name>` (exactly two segments, e.g., `app:busibox-workforce`)

- Created automatically when an app is deployed
- Managed via authz admin API (`/admin/roles`)
- Own the app's document collections
- Set as a `document_roles` entry on all app documents
- When a document has `visibility = 'shared'` and a `document_roles` entry for an app role, the admin UI shows this as "App" visibility

### Team/Sub-Roles

Pattern: `app:<app-name>:<entity-name>` (three or more segments, e.g., `app:busibox-workforce:employees-team`)

- Created via authz self-service API (`POST /roles`)
- Used for fine-grained access: per-team, per-campaign, per-project
- Can be assigned to individual records (`record_roles`) for record-level access control
- Created by apps at runtime using `ensureTeamRole()`

### User Roles

Standard roles without the `app:` prefix (e.g., `Admin`, `User`, `Finance Admin`)

- Managed via authz admin API
- Used for organization-wide access control and "Shared" visibility on documents
- Carry OAuth2 scopes that control operations (not just data access)

## Visibility Modes

| UI Label | `data_files.visibility` | `document_roles` | Who Can Access |
|----------|------------------------|-------------------|----------------|
| **Personal** | `personal` | empty | Only the document owner (`owner_id = app.user_id`) |
| **App** | `shared` | app role(s) | Users whose JWT contains a matching app role |
| **Shared** | `shared` | user/team role(s) | Users whose JWT contains a matching user or team role |
| **Authenticated** | `authenticated` | N/A | Any authenticated user (owner-only for UPDATE/DELETE) |

> **Note**: "App" and "Shared" both use `visibility = 'shared'` in the database. The distinction is a UI concept based on whether the assigned roles are app roles (`app:<name>`) or user/team roles.

## Token Exchange & Role Population

Understanding which roles appear in a data-api token is critical:

### Non-App-Scoped Exchange (standard)

Used by: apps calling `requireAuthWithTokenExchange(request, 'data-api')`, admin BFF

```
Session JWT → authz token exchange (audience: data-api, no resource_id)
→ ALL user roles go into the access token
```

This means a user's data-api token contains every role they hold: Admin, User, `app:busibox-workforce`, `app:busibox-workforce:employees-team`, etc.

### App-Scoped Exchange

Used by: portal SSO launch with `resource_id`

```
Session JWT → authz token exchange (audience: app-name, resource_id: app-uuid)
→ Only roles bound to the app go into the access token
```

This restricts the token to roles that are specifically bound to the target app.

## RLS Enforcement

### PostgreSQL Session Variables

The data-api middleware extracts JWT claims into PostgreSQL session variables before each query:

| Variable | Source | Used By |
|----------|--------|---------|
| `app.user_id` | JWT `sub` | Owner checks |
| `app.user_role_ids_read` | JWT `roles[].id` (CSV) | SELECT policies |
| `app.user_role_ids_create` | JWT `roles[].id` (CSV) | INSERT policies |
| `app.user_role_ids_update` | JWT `roles[].id` (CSV) | UPDATE/DELETE policies |
| `app.user_role_ids_delete` | JWT `roles[].id` (CSV) | DELETE policies |
| `app.is_admin` | Set by application code | Admin bypass policies |

> Currently all four `role_ids_*` variables contain the same CSV. In the future they could be differentiated for granular CRUD permissions.

### `data_files` RLS Policies

| Policy | Operation | Condition |
|--------|-----------|-----------|
| `personal_docs_select` | SELECT | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_select` | SELECT | `visibility = 'shared' AND EXISTS(document_roles matching user roles)` |
| `authenticated_docs_select` | SELECT | `visibility = 'authenticated' AND app.user_id IS NOT NULL` |
| `admin_docs_select` | SELECT | `app.is_admin = 'true'` |
| `data_files_insert` | INSERT | `owner_id = app.user_id` |
| `personal_docs_update` | UPDATE | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_update` | UPDATE | `visibility = 'shared' AND (owner OR has matching role)` |
| `authenticated_docs_update` | UPDATE | `visibility = 'authenticated' AND owner_id = app.user_id` |
| `admin_docs_update` | UPDATE | `app.is_admin = 'true'` |
| `personal_docs_delete` | DELETE | `visibility = 'personal' AND owner_id = app.user_id` |
| `shared_docs_delete` | DELETE | `visibility = 'shared' AND user can delete all bound roles` |
| `authenticated_docs_delete` | DELETE | `visibility = 'authenticated' AND owner_id = app.user_id` |
| `admin_docs_delete` | DELETE | `app.is_admin = 'true'` |

> **Important**: `SELECT ... FOR UPDATE` requires BOTH the SELECT and UPDATE policies to pass. This is why admins need `acquire_admin` (which sets `app.is_admin`) when operating on documents they don't own.

### `document_roles` RLS Policies

| Policy | Operation | Condition |
|--------|-----------|-----------|
| `document_roles_select` | SELECT | `role_id IN user_role_ids_read` |
| `document_roles_insert` | INSERT | `role_id IN user_role_ids_create` |
| `document_roles_update` | UPDATE | `role_id IN user_role_ids_update` |
| `document_roles_delete` | DELETE | `role_id IN user_role_ids_update` |
| `admin_document_roles_*` | ALL | `app.is_admin = 'true'` |

### `data_records` RLS Policies

Records support three visibility modes: `inherit`, `personal`, `shared`.

| Policy | Condition |
|--------|-----------|
| `records_inherit_*` | Parent document is visible (via `data_files` RLS) |
| `records_personal_*` | `owner_id = app.user_id` |
| `records_shared_*` | Matching role in `record_roles` |
| `admin_records_select` | `app.is_admin = 'true'` |
| `admin_records_delete` | `app.is_admin = 'true'` |

### Admin Bypass Mechanism

Admin access is controlled via the `app.is_admin` PostgreSQL session variable:

1. **Not set by default** — the JWT middleware only sets `app.user_id` and `app.user_role_ids_*`
2. **Set by `acquire_admin`** — a connection context manager that sets `app.is_admin = 'true'` for the entire connection
3. **Set by `SET LOCAL`** — used within transactions when the caller is verified as an admin or document owner
4. **Scope check**: Application code verifies `data.admin` scope (Admin role has `*` wildcard) before activating
5. **No cross-table subqueries** — admin policies use only `current_setting()`, never subqueries to other tables

The `acquire_admin` context manager is used for operations that need to see all documents regardless of ownership or visibility (admin list, admin role updates, etc.).

## Database Trigger: `ensure_document_has_roles`

A `BEFORE DELETE` trigger on `document_roles` prevents removing the last role from a `shared` document. The `set_document_roles` service method works around this by temporarily setting visibility to `personal` before deleting roles, then re-inserting and setting the final visibility.

## Architecture

```
┌─────────────────────────────────────┐
│          App (Next.js)              │
│                                     │
│  SSO Token ──► Authz Self-Service   │
│    (busibox-session cookie)         │
│    • Create/find team roles         │
│    • Add/remove members             │
│    • Search users                   │
│                                     │
│  Data Token ──► Data API            │
│    (from token exchange)            │
│    • Set document visibility        │
│    • Add roles to documents         │
│    • CRUD with RLS enforcement      │
└─────────────────────────────────────┘
         │                    │
         ▼                    ▼
┌────────────────┐  ┌────────────────┐
│   Authz        │  │   Data API     │
│                │  │                │
│ authz_roles    │  │ data_files     │
│ authz_user_    │  │   visibility   │
│   roles        │  │   owner_id     │
│                │  │                │
│ Admin roles:   │  │ document_roles │
│ /admin/roles   │  │   role_id      │
│                │  │   file_id      │
│ Self-service:  │  │                │
│ POST /roles    │  │ data_records   │
│ GET /roles     │  │   visibility   │
│ /roles/{id}/   │  │   (inherit/    │
│   members      │  │    personal/   │
│ /roles/users/  │  │    shared)     │
│   search       │  │                │
│                │  │ record_roles   │
│                │  │   role_id      │
│                │  │   record_id    │
│                │  │                │
│                │  │ PostgreSQL RLS │
│                │  │ enforces access│
└────────────────┘  └────────────────┘
```

## Token Types

**Two tokens are always needed for sharing operations:**

1. **SSO Session JWT** (`busibox-session` cookie)
   - Used for: authz self-service endpoints (role CRUD, member management, user search)
   - Get with: `getSSOTokenFromRequest(request)` from `@jazzmind/busibox-app/lib/data/sharing`
   - Requirement: Must have `typ: "session"` claim, signed by authz

2. **Data-API Token** (from `requireAuthWithTokenExchange(request, 'data-api')`)
   - Used for: data-api document/library role management, all CRUD operations
   - Contains: user's role IDs in the `roles` claim, used by RLS
   - Contains: aggregated scopes from all user roles

## Busibox-App Sharing API

All sharing helpers live in `@jazzmind/busibox-app/lib/data/sharing`:

### Role Management

| Function | Token | Description |
|----------|-------|-------------|
| `ensureTeamRole(ssoToken, appName, entityName)` | SSO | Create or find a role named `app:{appName}:{entityName}-team` |
| `verifyRoleExists(ssoToken, roleId)` | SSO | Check if a role still exists |

### Document Role Management

| Function | Token | Description |
|----------|-------|-------------|
| `addRoleToDocuments(dataToken, roleId, docIds[])` | Data | Add role to documents (idempotent) |
| `removeRoleFromDocuments(dataToken, roleId, docIds[])` | Data | Remove role from documents |
| `addRoleToLibrary(dataToken, roleId, libraryId)` | Data | Add role to a library |

### Member Management

| Function | Token | Description |
|----------|-------|-------------|
| `listTeamMembers(ssoToken, roleId)` | SSO | List role members |
| `addTeamMember(ssoToken, roleId, userId)` | SSO | Add user to role |
| `removeTeamMember(ssoToken, roleId, userId)` | SSO | Remove user from role |
| `searchUsers(ssoToken, query)` | SSO | Search users by name/email |

### Visibility Management

| Function | Token | Description |
|----------|-------|-------------|
| `setDocumentVisibility(dataToken, docIds[], mode, roleId?)` | Data | Switch documents between modes |
| `resolveVisibilityMode(visibility, roleIds, ...)` | — | Determine mode from doc roles |

## Sharing Patterns

### App-Level Sharing

The app role owns all the app's document collections. When an app creates documents at setup:

1. `ensureDataDocuments()` creates the documents (visibility defaults to `personal`)
2. `ensureAppRoleOnDocuments()` sets the app role on each document and switches visibility to `shared`

All users with the `app:<app-name>` role can see the documents.

### Team Sharing (Sub-Roles)

One team role per logical group. Different groups can have different access.

```
ensureTeamRole(ssoToken, 'busibox-workforce', 'employees')
→ creates role: app:busibox-workforce:employees-team
```

Team roles are added to documents and/or records to control fine-grained access.

### Entity-Level Sharing

One team role per entity (e.g., per campaign, per project). Different entities can have different teams.

```
ensureTeamRole(ssoToken, 'busibox-recruiter', 'campaign-frontend-dev-reviewer')
→ creates role: app:busibox-recruiter:campaign-frontend-dev-reviewer-team
```

## Critical: Adding Team Members

When a team member is added, the team role must be present in the `document_roles` table for **every document** the team should access. If a document is missing the role, RLS will deny access even though the user has the role in their JWT.

For app-level sharing, add the role to all app documents:
```typescript
await addRoleToDocuments(dataToken, role.roleId, Object.values(documentIds));
```

For entity-level sharing, add the role to both entity-specific and app-level documents:
```typescript
await addRoleToDocuments(dataToken, role.roleId, [
  appDocIds.campaigns,
  appDocIds.activities,
  campaign.schemaDocumentId,
]);
await addRoleToLibrary(dataToken, role.roleId, campaign.libraryId);
```

## Self-Service Role Naming Convention

All self-service roles follow the pattern:
```
app:{appName}:{entityName}-team
```

Examples:
- `app:busibox-workforce:employees-team`
- `app:busibox-recruiter:campaign-frontend-dev-reviewer-team`
- `app:my-app:data-team`

The `app:` prefix and naming pattern are enforced by authz. The `source_app` is extracted from the second segment for filtering (e.g., `GET /roles?app=busibox-recruiter`).

## Reference Implementations

- **busibox-template**: `lib/sharing.ts`, `app/api/team/route.ts`, `app/api/settings/visibility/route.ts`
- **busibox-workforce**: App-level sharing in `app/api/settings/visibility/route.ts` and `app/api/settings/team/route.ts`, app role assignment in `lib/data-api-client.ts` (`ensureAppRoleOnDocuments`)
- **busibox-recruiter**: Entity-level sharing in `lib/campaign-setup.ts` and `app/api/campaigns/[id]/team/route.ts`
