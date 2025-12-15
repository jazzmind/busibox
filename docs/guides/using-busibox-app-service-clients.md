---
title: Using Busibox-App Service Clients
category: guides
created: 2024-12-14
updated: 2024-12-14
status: active
---

# Using Busibox-App Service Clients

## Overview

`busibox-app` provides service clients for interacting with Busibox infrastructure services (ingest, agent, milvus, search). All clients support **AuthZ token-based authentication** and work in both client-side and server-side contexts.

## Quick Start

### Installation

```bash
# In your app's package.json
{
  "dependencies": {
    "busibox-app": "workspace:*"
  }
}
```

### Client-Side Usage (React)

```typescript
import { 
  uploadChatAttachment, 
  agentChat,
  useAuthzTokenManager 
} from 'busibox-app';

function MyComponent() {
  // Create token manager (reuse across requests)
  const tokenManager = useAuthzTokenManager({
    exchangeEndpoint: '/api/authz/token',
  });

  async function handleUpload(file: File) {
    const result = await uploadChatAttachment(file, { tokenManager });
    console.log('Uploaded:', result.fileId);
  }

  async function handleChat(message: string) {
    const response = await agentChat(message, { tokenManager });
    console.log('Agent:', response.response);
  }

  return (
    <div>
      <input type="file" onChange={(e) => handleUpload(e.target.files[0])} />
      <button onClick={() => handleChat('Hello')}>Chat</button>
    </div>
  );
}
```

### Server-Side Usage (API Routes)

```typescript
import { uploadChatAttachment } from 'busibox-app';
import { exchangeDownstreamAccessToken, syncUserToAuthz } from '@/lib/authz-client';

export async function POST(request: Request) {
  const user = await requireAuth(request);
  const formData = await request.formData();
  const file = formData.get('file') as File;

  // Define token acquisition function
  async function getAuthzToken(userId: string, audience: string, scopes: string[]) {
    await syncUserToAuthz(userId);
    const result = await exchangeDownstreamAccessToken({
      userId,
      audience: audience as any,
      scopes,
      purpose: 'file-upload',
    });
    return result.accessToken;
  }

  // Upload with authz token
  const result = await uploadChatAttachment(file, {
    userId: user.id,
    getAuthzToken,
  });

  return Response.json(result);
}
```

## Service Clients

### Ingest Client

**Purpose**: Upload, parse, and process documents

#### Upload File

```typescript
import { uploadChatAttachment } from 'busibox-app';

const result = await uploadChatAttachment(file, { tokenManager });
// Returns: { fileId, filename, mimeType, sizeBytes, url }
```

#### Parse to Markdown

```typescript
import { parseFileToMarkdown } from 'busibox-app';

const result = await parseFileToMarkdown(fileId, { tokenManager });
// Returns: { markdown, metadata: { pageCount, wordCount, language } }
```

#### Full Ingestion

```typescript
import { ingestChatAttachment } from 'busibox-app';

const result = await ingestChatAttachment(
  fileId,
  { tokenManager },
  { collection: 'default', chunkSize: 512, chunkOverlap: 50 }
);
// Returns: { jobId, status, documentId }
```

#### Get Download URL

```typescript
import { getChatAttachmentUrl } from 'busibox-app';

const url = await getChatAttachmentUrl(fileId, { tokenManager }, 3600);
// Returns presigned URL (expires in 3600 seconds)
```

#### Delete File

```typescript
import { deleteChatAttachment } from 'busibox-app';

await deleteChatAttachment(fileId, { tokenManager });
```

### Agent Client

**Purpose**: Call agent-api for LLM operations

#### Chat with Agent

```typescript
import { agentChat } from 'busibox-app';

const response = await agentChat('What is the capital of France?', {
  tokenManager,
  agentId: 'default',
  context: { conversationId: 'conv-123' },
});
// Returns: { response: string, success: boolean }
```

#### Custom Agent Call

```typescript
import { agentFetch } from 'busibox-app';

const response = await agentFetch(
  'POST /api/custom - custom operation',
  '/api/custom',
  {
    tokenManager,
    method: 'POST',
    body: JSON.stringify({ data: 'value' }),
  }
);
const data = await response.json();
```

### Milvus Client

**Purpose**: Vector storage and search for chat insights

#### Insert Insights

```typescript
import { insertInsights } from 'busibox-app';

await insertInsights([
  {
    id: 'insight-1',
    userId: 'user-123',
    content: 'Important insight from conversation',
    embedding: [0.1, 0.2, ...], // 1024-dimensional vector
    conversationId: 'conv-123',
    analyzedAt: Date.now(),
  },
]);
```

#### Search Insights

```typescript
import { searchInsights } from 'busibox-app';

const results = await searchInsights(
  'query text',
  { userId: 'user-123', tokenManager },
  { limit: 3, scoreThreshold: 0.7 }
);
// Returns: InsightSearchResult[]
```

#### Delete Conversation Insights

```typescript
import { deleteConversationInsights } from 'busibox-app';

await deleteConversationInsights('conv-123', 'user-123');
```

### Embeddings Client

**Purpose**: Generate embeddings using FastEmbed (bge-large-en-v1.5)

#### Generate Single Embedding

```typescript
import { generateEmbedding } from 'busibox-app';

const embedding = await generateEmbedding('text to embed', { tokenManager });
// Returns: number[] (1024 dimensions)
```

#### Generate Multiple Embeddings

```typescript
import { generateEmbeddings } from 'busibox-app';

const embeddings = await generateEmbeddings(
  ['text 1', 'text 2', 'text 3'],
  { tokenManager }
);
// Returns: number[][] (array of 1024-dimensional vectors)
```

### Search Providers

**Purpose**: Web search abstraction (Tavily, SerpAPI, Perplexity, Bing)

#### Initialize Factory

```typescript
import { SearchProviderFactory } from 'busibox-app';

// Load configs from your app's database or env
const configs = {
  tavily: { apiKey: process.env.TAVILY_API_KEY },
  serpapi: { apiKey: process.env.SERPAPI_API_KEY },
};

SearchProviderFactory.initialize(configs, 'tavily');
```

#### Search Web

```typescript
import { searchWeb } from 'busibox-app';

const results = await searchWeb('query', {
  maxResults: 5,
  searchDepth: 'basic',
  includeAnswer: true,
});
// Returns: SearchResponse with results[]
```

#### Use Specific Provider

```typescript
import { SearchProviderFactory } from 'busibox-app';

const provider = SearchProviderFactory.getProvider('serpapi');
const results = await provider.search('query', { maxResults: 10 });
```

## Authentication Patterns

### Pattern 1: Client-Side with Token Manager

**Best for**: React components, client-side operations

```typescript
import { useAuthzTokenManager, uploadChatAttachment } from 'busibox-app';

const tokenManager = useAuthzTokenManager({
  exchangeEndpoint: '/api/authz/token',
});

const result = await uploadChatAttachment(file, { tokenManager });
```

**How it works**:
1. Token manager calls `/api/authz/token` (BFF endpoint)
2. BFF endpoint authenticates user via session
3. BFF calls authz service to exchange for service token
4. Token manager caches token (in-memory + sessionStorage)
5. Service client uses cached token for requests

### Pattern 2: Server-Side with Custom Token Acquisition

**Best for**: API routes, server-side operations

```typescript
import { uploadChatAttachment } from 'busibox-app';
import { exchangeDownstreamAccessToken, syncUserToAuthz } from '@/lib/authz-client';

async function getAuthzToken(userId: string, audience: string, scopes: string[]) {
  await syncUserToAuthz(userId);
  const result = await exchangeDownstreamAccessToken({ userId, audience, scopes });
  return result.accessToken;
}

const result = await uploadChatAttachment(file, { userId, getAuthzToken });
```

**How it works**:
1. Service client calls `getAuthzToken` when needed
2. `syncUserToAuthz` syncs user roles to authz service
3. `exchangeDownstreamAccessToken` gets service-scoped token
4. Service client uses token for request

### Pattern 3: Unauthenticated (Optional)

**Best for**: Public operations (if service allows)

```typescript
import { agentChat } from 'busibox-app';

// No tokenManager or userId provided
const response = await agentChat('Hello');
```

**Note**: Most services require authentication. This only works if the service allows unauthenticated access.

## Error Handling

### Ingest Service Errors

```typescript
import { uploadChatAttachment, type IngestServiceError } from 'busibox-app';

try {
  const result = await uploadChatAttachment(file, { tokenManager });
} catch (error) {
  if (error && typeof error === 'object' && 'context' in error) {
    const ingestError = error as IngestServiceError;
    console.error('Ingest error:', {
      context: ingestError.context,
      url: ingestError.url,
      statusCode: ingestError.statusCode,
    });
  }
}
```

### Agent Service Errors

```typescript
import { agentChat } from 'busibox-app';

try {
  const response = await agentChat('Hello', { tokenManager });
} catch (error) {
  if (error instanceof Error) {
    if (error.message.includes('timeout')) {
      console.error('Agent request timed out');
    } else if (error.message.includes('Agent API error')) {
      console.error('Agent API returned error:', error.message);
    }
  }
}
```

### Token Acquisition Errors

```typescript
import { useAuthzTokenManager } from 'busibox-app';

const tokenManager = useAuthzTokenManager({
  exchangeEndpoint: '/api/authz/token',
});

try {
  const token = await tokenManager.getToken({
    audience: 'ingest-api',
    scopes: ['ingest.write'],
  });
} catch (error) {
  if (error instanceof Error && error.message.includes('Token exchange failed')) {
    console.error('Failed to get authz token:', error.message);
    // User might not be authenticated or authz service is down
  }
}
```

## Best Practices

### 1. Reuse Token Manager

Don't create a new token manager for every request:

```typescript
// ✅ Good - reuse token manager
const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });

async function upload1(file: File) {
  return uploadChatAttachment(file, { tokenManager });
}

async function upload2(file: File) {
  return uploadChatAttachment(file, { tokenManager });
}

// ❌ Bad - creates new token manager each time
async function upload(file: File) {
  const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });
  return uploadChatAttachment(file, { tokenManager });
}
```

### 2. Handle Errors Gracefully

Service calls can fail - always handle errors:

```typescript
try {
  const result = await uploadChatAttachment(file, { tokenManager });
  // Success
} catch (error) {
  // Handle error
  console.error('Upload failed:', error);
  // Show user-friendly message
}
```

### 3. Use Purpose Labels

Provide purpose labels for audit logging:

```typescript
const result = await uploadChatAttachment(file, {
  tokenManager,
  purpose: 'chat-attachment-upload',
});
```

### 4. Initialize Search Providers Early

Initialize search providers at app startup:

```typescript
// In your app's initialization code
import { SearchProviderFactory } from 'busibox-app';

async function initializeApp() {
  const configs = await loadSearchProviderConfigs();
  SearchProviderFactory.initialize(configs, 'tavily');
}
```

### 5. Clear Token Cache on Logout

Clear cached tokens when user logs out:

```typescript
import { useAuthzTokenManager } from 'busibox-app';

const tokenManager = useAuthzTokenManager({ exchangeEndpoint: '/api/authz/token' });

function handleLogout() {
  tokenManager.clearAllTokens();
  // Proceed with logout
}
```

## TypeScript Types

All clients export TypeScript types for better type safety:

```typescript
import type {
  IngestServiceError,
  IngestClientOptions,
  AgentClientOptions,
  ChatInsight,
  InsightSearchResult,
  SearchResult,
  SearchResponse,
  SearchProvider,
  SearchOptions,
  ProviderConfig,
  EmbeddingsClientOptions,
} from 'busibox-app';
```

## Testing

### Mock Token Manager

```typescript
import { uploadChatAttachment } from 'busibox-app';

const mockTokenManager = {
  getToken: jest.fn().mockResolvedValue('mock-token'),
  clearToken: jest.fn(),
  clearAllTokens: jest.fn(),
  fetchWithToken: jest.fn(),
};

const result = await uploadChatAttachment(file, { tokenManager: mockTokenManager as any });

expect(mockTokenManager.getToken).toHaveBeenCalledWith({
  audience: 'ingest-api',
  scopes: ['ingest.write'],
  purpose: expect.any(String),
});
```

### Mock Service Responses

```typescript
import { agentChat } from 'busibox-app';

global.fetch = jest.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ response: 'Hello!', success: true }),
});

const response = await agentChat('Hi', { tokenManager: mockTokenManager as any });
expect(response.response).toBe('Hello!');
```

## Related Documentation

- [Busibox-App Library Architecture](./busibox-app-library-architecture.md) - Complete architecture guide
- [OAuth2 Token Exchange Implementation](./oauth2-token-exchange-implementation.md) - AuthZ system details
- [Service Clients Migration Summary](../../ai-portal/docs/SERVICE_CLIENTS_MIGRATION_SUMMARY.md) - Migration from ai-portal

## Support

For issues or questions:
1. Check service logs: `journalctl -u <service-name>`
2. Verify authz token exchange: Check `/api/authz/token` endpoint
3. Test service connectivity: Use `curl` to test service endpoints
4. Check authz audit logs: Query authz service `/authz/audit` endpoint


