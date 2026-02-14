---
title: "Busibox-App Library Architecture"
category: "developer"
order: 137
description: "Shared component and utility library for Busibox applications"
published: true
---

# Busibox-App Library Architecture

## Overview

`busibox-app` is the **shared component and utility library** for all Busibox applications. It provides:

1. **Service Clients** - Authenticated clients for ingest, agent, search, milvus services
2. **AuthZ Integration** - Token management, audit, RBAC clients
3. **UI Components** - Chat interface, document management, video components
4. **Shared Utilities** - Search providers, storage types, icons

## Architecture Principles

### 1. Service Clients Use Token Manager

All service clients accept `AuthzTokenManager` for authentication:

```typescript
// Client-side usage
const tokenManager = createAuthzTokenManager({
  exchangeEndpoint: '/api/authz/token',
});

const result = await uploadFile(file, tokenManager);
```

**Benefits**:
- Works client-side and server-side
- Automatic token caching and refresh
- Consistent authentication across all services

### 2. AuthZ is the Authority

AuthZ service is the single source of truth for:
- **Audit Logs** - All apps log to authz audit endpoint
- **RBAC** - User-role assignments managed by authz
- **OAuth** - Token exchange and validation via authz

**Apps should not**:
- Implement their own audit logging
- Manage role assignments locally
- Generate custom JWT tokens

### 3. Apps Provide Configuration

Busibox-app utilities accept configuration as parameters (not environment variables):

```typescript
// App loads config from its own database/env
const searchConfigs = await loadSearchProviderConfigs();

// Pass to busibox-app
SearchProviderFactory.initialize(searchConfigs);
```

**Benefits**:
- Apps control their own configuration
- No hidden dependencies on environment variables
- Easier testing and mocking

## Library Structure

```
busibox-app/src/
├── lib/
│   ├── authz/              # AuthZ integration
│   │   ├── token-manager.ts    # Token acquisition & caching
│   │   ├── audit-client.ts     # Audit logging via authz
│   │   └── rbac-client.ts      # RBAC queries via authz
│   ├── ingest/             # Ingest service client
│   │   └── client.ts
│   ├── agent/              # Agent service client
│   │   └── client.ts
│   ├── search/             # Web search providers
│   │   └── providers.ts
│   ├── milvus/             # Milvus vector database
│   │   └── client.ts
│   └── http/               # HTTP utilities
│       └── fetch-with-fallback.ts
├── components/             # React components
│   ├── chat/              # Chat interface
│   ├── documents/         # Document management
│   ├── videos/            # Video components
│   └── shared/            # Shared UI components
├── contexts/              # React contexts
│   ├── ApiContext.tsx
│   ├── ThemeContext.tsx
│   └── CustomizationContext.tsx
└── types/                 # TypeScript types
    ├── storage.ts
    ├── chat.ts
    ├── documents.ts
    └── video.ts
```

## Service Clients

### Ingest Client

**Purpose**: Upload, parse, and process documents

**Usage**:
```typescript
import { uploadChatAttachment, parseFileToMarkdown } from 'busibox-app/ingest';

// Client-side
const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });
const result = await uploadChatAttachment(file, tokenManager);

// Server-side
const result = await uploadChatAttachmentServer(file, userId, '/api/authz/token');
```

**Functions**:
- `uploadChatAttachment()` - Upload file to MinIO
- `parseFileToMarkdown()` - Parse file without full ingestion
- `ingestChatAttachment()` - Full ingestion (chunking, embeddings)
- `getChatAttachmentUrl()` - Get presigned download URL
- `deleteChatAttachment()` - Delete file

### Agent Client

**Purpose**: Call agent-api for LLM operations

**Usage**:
```typescript
import { agentChat, agentFetch } from 'busibox-app/agent';

const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });

const response = await agentChat('Hello', {
  tokenManager,
  agentId: 'default',
  purpose: 'chat',
});
```

**Functions**:
- `agentFetch()` - Generic fetch to agent-api
- `agentChat()` - Chat with agent

### Search Providers

**Purpose**: Web search abstraction (Tavily, SerpAPI, Perplexity, Bing)

**Usage**:
```typescript
import { SearchProviderFactory, searchWeb } from 'busibox-app/search';

// Initialize with configs
SearchProviderFactory.initialize({
  tavily: { apiKey: process.env.TAVILY_API_KEY },
  serpapi: { apiKey: process.env.SERPAPI_API_KEY },
});

// Search
const results = await searchWeb('query', { maxResults: 5 });
```

### Milvus Client

**Purpose**: Vector storage and search for chat insights

**Usage**:
```typescript
import { insertInsights, searchInsights } from 'busibox-app/milvus';

// Store insights
await insertInsights([
  {
    id: 'insight-1',
    userId: 'user-123',
    content: 'Important insight',
    embedding: [0.1, 0.2, ...],
    conversationId: 'conv-1',
    analyzedAt: Date.now(),
  },
]);

// Search insights
const results = await searchInsights('query', 'user-123', { limit: 3 });
```

## AuthZ Integration

### Token Manager

**Purpose**: Acquire and cache authz-issued access tokens

**Usage**:
```typescript
import { createAuthzTokenManager, useAuthzTokenManager } from 'busibox-app/authz';

// Client-side (React)
const tokenManager = useAuthzTokenManager({
  exchangeEndpoint: '/api/authz/token',
});

// Get token for specific service
const token = await tokenManager.getToken({
  audience: 'search-api',
  scopes: ['search.read'],
});

// Or use fetch wrapper
const response = await tokenManager.fetchWithToken('https://search-api/search', {
  audience: 'search-api',
  scopes: ['search.read'],
  method: 'POST',
  body: JSON.stringify({ query: 'test' }),
});
```

**Features**:
- In-memory + sessionStorage caching
- Automatic expiry handling (60s buffer)
- Cache keyed by (audience, scopes)
- Works client-side and server-side

### Audit Client

**Purpose**: Log audit events to authz service

**Usage**:
```typescript
import { logAuditEvent, logUserLogin, logUserLogout } from 'busibox-app/audit';

// Generic audit event
await logAuditEvent({
  actorId: 'user-123',
  action: 'document.upload',
  resourceType: 'document',
  resourceId: 'doc-456',
  details: { filename: 'report.pdf' },
}, {
  tokenManager, // Optional: for user context
});

// Convenience functions
await logUserLogin('user-123', 'session-789', { tokenManager });
await logUserLogout('user-123', 'session-789', { tokenManager });
```

**Features**:
- Centralized audit logging
- Non-blocking (errors don't break app)
- Optional user context via token manager
- Convenience functions for common events

### RBAC Client

**Purpose**: Query user roles and permissions from authz

**Usage**:
```typescript
import { hasRole, isAdmin, getUserRoles } from 'busibox-app/rbac';

// Check role
const isAdminUser = await isAdmin('user-123', { tokenManager });

// Get all roles
const roles = await getUserRoles('user-123', { tokenManager });

// Check specific role
const hasEditorRole = await hasRole('user-123', 'Editor', { tokenManager });
```

**Features**:
- Queries authz service (RBAC authority)
- Optional token manager for user context
- Caching support (via token manager)

## Migration from AI Portal

### Service Clients

**Before** (ai-portal):
```typescript
import { uploadChatAttachment } from '@/lib/ingest/client';

// Used X-User-Id header
await uploadChatAttachment(file, userId);
```

**After** (busibox-app):
```typescript
import { uploadChatAttachment } from 'busibox-app/ingest';

// Uses authz token
const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });
await uploadChatAttachment(file, tokenManager);
```

### Audit Logging

**Before** (ai-portal):
```typescript
import { logUserLogin } from '@/lib/audit';

// Wrote to ai-portal's audit_logs table
await logUserLogin(userId, sessionId);
```

**After** (busibox-app):
```typescript
import { logUserLogin } from 'busibox-app/audit';

// Writes to authz audit endpoint
await logUserLogin(userId, sessionId, { tokenManager });
```

### RBAC

**Before** (ai-portal):
```typescript
import { isAdmin } from '@/lib/permissions';

// Queried ai-portal's role tables
const admin = await isAdmin(userId);
```

**After** (busibox-app):
```typescript
import { isAdmin } from 'busibox-app/rbac';

// Queries authz service
const admin = await isAdmin(userId, { tokenManager });
```

## Best Practices

### 1. Always Pass Token Manager

Service clients should always receive a token manager:

```typescript
// ✅ Good
const result = await uploadFile(file, tokenManager);

// ❌ Bad - no authentication
const result = await uploadFile(file);
```

### 2. Use Convenience Functions

Busibox-app provides convenience functions for common operations:

```typescript
// ✅ Good
await logUserLogin(userId, sessionId, { tokenManager });

// ❌ Bad - manual event construction
await logAuditEvent({
  actorId: userId,
  action: 'user.login',
  resourceType: 'session',
  resourceId: sessionId,
  details: { method: 'magic_link' },
}, { tokenManager });
```

### 3. Handle Errors Gracefully

Service calls can fail - handle errors appropriately:

```typescript
try {
  const result = await uploadFile(file, tokenManager);
  // Handle success
} catch (error) {
  // Handle error
  console.error('Upload failed:', error);
  // Show user-friendly message
}
```

### 4. Cache Token Manager

Don't create new token managers for every request:

```typescript
// ✅ Good - reuse token manager
const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });

function handleUpload(file: File) {
  return uploadFile(file, tokenManager);
}

// ❌ Bad - creates new token manager each time
function handleUpload(file: File) {
  const tokenManager = createAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });
  return uploadFile(file, tokenManager);
}
```

## Testing

### Unit Tests

```typescript
import { uploadChatAttachment } from 'busibox-app/ingest';

describe('uploadChatAttachment', () => {
  it('should use authz token', async () => {
    const mockTokenManager = {
      getToken: jest.fn().mockResolvedValue('mock-token'),
    };

    await uploadChatAttachment(file, mockTokenManager as any);

    expect(mockTokenManager.getToken).toHaveBeenCalledWith({
      audience: 'ingest-api',
      scopes: ['ingest.write'],
      purpose: expect.any(String),
    });
  });
});
```

### Integration Tests

```typescript
describe('Chat Attachments E2E', () => {
  it('should upload and parse with authz', async () => {
    const tokenManager = createAuthzTokenManager({
      exchangeEndpoint: '/api/authz/token',
    });

    const file = new File(['test'], 'test.pdf');
    const { fileId } = await uploadChatAttachment(file, tokenManager);
    const { markdown } = await parseFileToMarkdown(fileId, tokenManager);

    expect(markdown).toContain('test');
  });
});
```

## Related Documentation

- [OAuth2 Token Exchange Implementation](./oauth2-token-exchange-implementation.md)
- [AuthZ Deployment Config](../deployment/authz-deployment-config.md)
- [AI Portal Migration Analysis](../../ai-portal/docs/LIB_AUTHZ_MIGRATION_ANALYSIS.md)
- [Token Manager API](../../busibox-app/src/lib/authz/token-manager.ts)

