/**
 * Static reference data for app builders
 * Based on @jazzmind/busibox-app v3.x and busibox-template patterns
 *
 * Source of truth: busibox APIs → busibox-app lib → busibox-template → this file
 */

export const BUSIBOX_APP_EXPORTS = `# @jazzmind/busibox-app Exports (v3.x)

## Subpath Imports (preferred for tree-shaking)

| Import Path | Purpose |
|-------------|---------|
| @jazzmind/busibox-app | Root barrel (ThemeProvider, CustomizationProvider, FetchWrapper, Header, Footer, etc.) |
| @jazzmind/busibox-app/components | UI components barrel |
| @jazzmind/busibox-app/components/auth/SessionProvider | SessionProvider, useSession |
| @jazzmind/busibox-app/components/auth/* | ProtectedRoute, PasskeyRequiredWrapper |
| @jazzmind/busibox-app/components/documents/* | DocumentUpload, DocumentList, DocumentSearch, AppDataList |
| @jazzmind/busibox-app/contexts | ThemeProvider, CustomizationProvider |
| @jazzmind/busibox-app/layout | Header, Footer, ThemeToggle |
| @jazzmind/busibox-app/types | Shared TypeScript types |
| @jazzmind/busibox-app/lib/authz | Zero Trust token exchange, SSO validation, RBAC, session client, passkeys |
| @jazzmind/busibox-app/lib/authz/session-route-handlers | createSessionRouteHandlers |
| @jazzmind/busibox-app/lib/agent | Agent API clients, chat, sync, streaming |
| @jazzmind/busibox-app/lib/agent/sync | syncAgentDefinitions, getAgentSyncStatus |
| @jazzmind/busibox-app/lib/data | Data API CRUD, embeddings, documents, sharing |
| @jazzmind/busibox-app/lib/data/sharing | Team roles, visibility, member management |
| @jazzmind/busibox-app/lib/data/documents | ensureDocuments, queryRecords, insertRecords, etc. |
| @jazzmind/busibox-app/lib/search | Search service clients |
| @jazzmind/busibox-app/lib/deploy | Deploy API client, manifests |
| @jazzmind/busibox-app/lib/media | Media upload, processing, status |
| @jazzmind/busibox-app/lib/docs | Docs service client |
| @jazzmind/busibox-app/lib/next | Middleware, service-client, api-url helpers |
| @jazzmind/busibox-app/lib/http | fetch-with-fallback |
| @jazzmind/busibox-app/lib/hooks | useChatStream, useImageUrls, useAutosave, useIsMobile |
| @jazzmind/busibox-app/lib/sse | Server-sent events utilities |
| @jazzmind/busibox-app/lib/date-utils | Date formatting utilities |

## Key Components

- **Auth**: SessionProvider, useSession, ProtectedRoute, PasskeyRequiredWrapper
- **Chat**: SimpleChatInterface, FullChatInterface, ChatInterface, ChatPage, MessageInput
- **Documents**: DocumentUpload, DocumentList, DocumentSearch, AppDataList, SchemaEditor
- **Layout**: Header, Footer, ThemeToggle, FetchWrapper, UserDropdown, DynamicFavicon
- **Libraries**: LibrarySelector, LibrarySidebar

## Auth (from lib/authz)

\`\`\`typescript
// Zero Trust token exchange
import { exchangeTokenZeroTrust, getAuthHeaderZeroTrust } from '@jazzmind/busibox-app/lib/authz';

// SSO route handlers (for app/api/sso/route.ts)
import { createSSOGetHandler, createSSOPostHandler } from '@jazzmind/busibox-app/lib/authz';

// Session route handlers (for app/api/auth/session/route.ts)
import { createSessionRouteHandlers } from '@jazzmind/busibox-app/lib/authz/session-route-handlers';

// Token utilities
import { getTokenFromRequest, isTokenExpired, parseJWTPayload } from '@jazzmind/busibox-app/lib/authz';

// SSO validation (server-side)
import { validateSSOToken, hasSessionRole, isSessionAdmin } from '@jazzmind/busibox-app/lib/authz';
\`\`\`

## Data API (from lib/data)

\`\`\`typescript
// Generic CRUD operations
import {
  ensureDocuments, queryRecords, insertRecords, updateRecords, deleteRecords,
  generateId, getNow, extractAppRoleIdFromToken, getDocumentByName,
} from '@jazzmind/busibox-app/lib/data/documents';

// File operations
import { dataFetch, uploadChatAttachment, parseFileToMarkdown } from '@jazzmind/busibox-app/lib/data';

// Embeddings
import { generateEmbedding, generateEmbeddings } from '@jazzmind/busibox-app/lib/data';

// Sharing
import {
  ensureTeamRole, addRoleToDocuments, listTeamMembers,
  addTeamMember, removeTeamMember, searchUsers,
  setDocumentVisibility, getSSOTokenFromRequest,
} from '@jazzmind/busibox-app/lib/data/sharing';
\`\`\`

## Agent API (from lib/agent)

\`\`\`typescript
// Agent sync (define agents in code, sync to agent-api)
import { syncAgentDefinitions, getAgentSyncStatus } from '@jazzmind/busibox-app/lib/agent/sync';
import type { AgentDefinitionInput, AgentSyncResult, SyncStatus } from '@jazzmind/busibox-app/lib/agent';

// Chat client (browser-side)
import { sendChatMessage, streamChatMessage, getConversations } from '@jazzmind/busibox-app/lib/agent';

// Server-side client
import { createAgentClient } from '@jazzmind/busibox-app/lib/agent';
\`\`\`

## CRITICAL: What NOT to do

- **NEVER** import from \`@jazzmind/busibox-app/lib/auth\` — the path is \`lib/authz\`
- **NEVER** access PostgreSQL, Redis, Milvus, or LiteLLM directly from app code
- **NEVER** call LiteLLM API endpoints directly — use agent-api's /runs/invoke for structured output
- **NEVER** use Prisma or any ORM — all data goes through data-api
- **NEVER** create service-to-service API keys or shared secrets (Zero Trust architecture)
- **NEVER** use APP_MODE env var — all apps are frontend-mode (data-api for storage)
`;

export const AUTH_PATTERNS = `# Busibox App Authentication Patterns

## Architecture: Zero Trust Token Exchange

Apps NEVER talk to databases or backend services directly with static credentials.
Instead, the user's session JWT is exchanged for a scoped API token per request.

## Token Flow

1. User clicks app in Busibox Portal
2. Portal exchanges session JWT for app-scoped token via authz
3. authz verifies RBAC and issues RS256 token with app_id claim and user's roles
4. App validates token via authz JWKS
5. For each API route, app exchanges session JWT for backend-specific token (data-api, agent-api, search-api)

## SessionProvider (Root Layout)

\`\`\`typescript
// app/layout.tsx (server component)
import { SessionProvider } from "@jazzmind/busibox-app/components/auth/SessionProvider";

export default function RootLayout({ children }) {
  const appId = process.env.APP_NAME || 'my-app';
  const portalUrl = process.env.NEXT_PUBLIC_BUSIBOX_PORTAL_URL || '';
  const basePath = process.env.NEXT_PUBLIC_BASE_PATH || '';

  return (
    <html>
      <body>
        <SessionProvider appId={appId} portalUrl={portalUrl} basePath={basePath}>
          {children}
        </SessionProvider>
      </body>
    </html>
  );
}
\`\`\`

## useSession (Client Components)

\`\`\`typescript
'use client';
import { useSession } from "@jazzmind/busibox-app/components/auth/SessionProvider";

export function MyComponent() {
  const { user, isAuthenticated, logout } = useSession();
  if (!isAuthenticated) return null;
  return <div>Hello {user?.name}</div>;
}
\`\`\`

## API Route Auth (lib/auth-middleware.ts)

\`\`\`typescript
import { requireAuthWithTokenExchange } from '@/lib/auth-middleware';

export async function GET(request: NextRequest) {
  // Second argument specifies the target backend service
  const auth = await requireAuthWithTokenExchange(request, 'data-api');
  if (auth instanceof NextResponse) return auth;

  // auth.ssoToken - original session JWT (use for authz self-service APIs)
  // auth.apiToken - exchanged API token for the specified backend service
  // Use auth.apiToken in Authorization: Bearer for backend calls
}
\`\`\`

Valid audiences: \`'data-api'\`, \`'agent-api'\`, \`'search-api'\`

## SSO Route (app/api/sso/route.ts)

\`\`\`typescript
import { NextRequest, NextResponse } from "next/server";
import { createSSOGetHandler, createSSOPostHandler } from "@jazzmind/busibox-app/lib/authz";

const handleGet = createSSOGetHandler(NextResponse, { defaultAppName: 'my-app' });
const handlePost = createSSOPostHandler(NextResponse, { defaultAppName: 'my-app' });

export async function GET(request: NextRequest) { return handleGet(request); }
export async function POST(request: NextRequest) { return handlePost(request); }
\`\`\`

## Session Route (app/api/auth/session/route.ts)

\`\`\`typescript
import { createSessionRouteHandlers } from '@jazzmind/busibox-app/lib/authz/session-route-handlers';
export const { GET, POST } = createSessionRouteHandlers('my-app');
\`\`\`

## Auth Token Route (app/api/auth/token/route.ts)

Returns agent-api token for client-side chat components:

\`\`\`typescript
import { NextRequest, NextResponse } from "next/server";
import { requireAuthWithTokenExchange } from "@/lib/auth-middleware";

export async function GET(request: NextRequest) {
  const auth = await requireAuthWithTokenExchange(request, "agent-api");
  if (auth instanceof NextResponse) return auth;
  return NextResponse.json({ token: auth.apiToken });
}
\`\`\`

## Required Env Vars

- \`AUTHZ_BASE_URL\` — AuthZ service URL (e.g. http://localhost:8010)
- \`APP_NAME\` — Token audience and cookie prefix
- \`DEFAULT_API_AUDIENCE\` — Default backend audience (data-api, agent-api, or search-api)
- \`NEXT_PUBLIC_BUSIBOX_PORTAL_URL\` — Portal URL for SSO redirects
- \`NEXT_PUBLIC_BASE_PATH\` — App base path for nginx proxy (e.g. /myapp)
- \`TEST_SESSION_JWT\` — Optional, for local dev without Portal SSO
`;

export const DATA_API_PATTERNS = `# Data API Storage Patterns

## Architecture

All app data is stored via the Busibox data-api service using structured documents.
Apps NEVER access PostgreSQL directly — all CRUD goes through data-api.

## Document Setup (lib/data-api-client.ts)

\`\`\`typescript
import {
  generateId, getNow, queryRecords, insertRecords,
  updateRecords, deleteRecords, ensureDocuments,
} from '@jazzmind/busibox-app';
import { extractAppRoleIdFromToken } from '@jazzmind/busibox-app/lib/data/documents';
import type { AppDataSchema } from '@jazzmind/busibox-app';

export const DOCUMENTS = {
  ITEMS: 'my-app-items',
} as const;

export const itemSchema: AppDataSchema = {
  fields: {
    id: { type: 'string', required: true, hidden: true },
    name: { type: 'string', required: true, label: 'Name', order: 1 },
    status: { type: 'string', label: 'Status', order: 2 },
    createdAt: { type: 'string', readonly: true, hidden: true },
    updatedAt: { type: 'string', readonly: true, hidden: true },
  },
  displayName: 'Items',
  itemLabel: 'Item',
  sourceApp: 'my-app',
  visibility: 'authenticated',
  allowSharing: false,
  graphNode: '',
  graphRelationships: [],
};

export async function ensureDataDocuments(token: string) {
  const appRoleIds = extractAppRoleIdFromToken(token, 'my-app');
  const options = appRoleIds.length > 0 ? { appRoleId: appRoleIds[0] } : undefined;
  return ensureDocuments(token, {
    items: { name: DOCUMENTS.ITEMS, schema: itemSchema },
  }, 'my-app', options);
}
\`\`\`

## CRUD Operations

\`\`\`typescript
// List records
const result = await queryRecords<Item>(token, documentId, {
  orderBy: [{ field: 'createdAt', direction: 'desc' }],
  limit: 50,
  offset: 0,
});
// result.records, result.total

// Filter records
const result = await queryRecords<Item>(token, documentId, {
  where: { field: 'status', op: 'eq', value: 'active' },
});

// Get by ID
const result = await queryRecords<Item>(token, documentId, {
  where: { field: 'id', op: 'eq', value: itemId },
  limit: 1,
});

// Create
const item = { id: generateId(), ...input, createdAt: getNow(), updatedAt: getNow() };
await insertRecords(token, documentId, [item]);

// Update
await updateRecords(token, documentId, { ...updates, updatedAt: getNow() },
  { field: 'id', op: 'eq', value: itemId });

// Delete
await deleteRecords(token, documentId, { field: 'id', op: 'eq', value: itemId });
\`\`\`

## API Route Pattern

\`\`\`typescript
import { NextRequest, NextResponse } from 'next/server';
import { requireAuthWithTokenExchange } from '@/lib/auth-middleware';
import { ensureDataDocuments } from '@/lib/data-api-client';

export async function GET(request: NextRequest) {
  const auth = await requireAuthWithTokenExchange(request, 'data-api');
  if (auth instanceof NextResponse) return auth;

  const documentIds = await ensureDataDocuments(auth.apiToken);
  const result = await queryRecords(auth.apiToken, documentIds.items, {
    orderBy: [{ field: 'createdAt', direction: 'desc' }],
  });
  return NextResponse.json({ items: result.records, total: result.total });
}
\`\`\`

## Query Filter Operators

- \`eq\`, \`neq\` — Equality
- \`gt\`, \`gte\`, \`lt\`, \`lte\` — Comparison
- \`contains\`, \`starts_with\`, \`ends_with\` — String matching
- \`in\`, \`not_in\` — Array membership
- \`is_null\`, \`is_not_null\` — Null checks

## CRITICAL Rules

- ALWAYS use \`requireAuthWithTokenExchange(request, 'data-api')\` for data routes
- ALWAYS call \`ensureDataDocuments()\` before any CRUD operation
- ALWAYS use \`generateId()\` and \`getNow()\` from @jazzmind/busibox-app
- NEVER access PostgreSQL, Redis, or any database directly
- NEVER hardcode document IDs — use ensureDocuments to get them dynamically
`;

export const AGENT_API_PATTERNS = `# Agent API Patterns

## Architecture

The agent-api provides AI agent execution, chat, conversations, and structured output.
Apps interact with agent-api through:
1. A catch-all proxy route (/api/agent/[...path]) for server-side calls
2. SimpleChatInterface component for client-side chat UI
3. Agent definitions synced from code

## Agent Proxy Route (app/api/agent/[...path]/route.ts)

This is a standard catch-all proxy that handles token exchange and forwards to AGENT_API_URL:

\`\`\`typescript
import { NextRequest, NextResponse } from "next/server";
import { requireAuthWithTokenExchange } from "@/lib/auth-middleware";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8000";

async function forward(request: NextRequest, method: string, path: string[]) {
  const auth = await requireAuthWithTokenExchange(request, "agent-api");
  if (auth instanceof NextResponse) return auth;

  const target = new URL(\`\${AGENT_API_URL}/\${path.join("/")}\`);
  request.nextUrl.searchParams.forEach((v, k) => target.searchParams.set(k, v));

  const headers: Record<string, string> = { Authorization: \`Bearer \${auth.apiToken}\` };
  let body: string | undefined;
  if (method !== "GET" && method !== "HEAD") {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(await request.json());
  }

  const response = await fetch(target.toString(), { method, headers, body });
  const contentType = response.headers.get("content-type") || "";

  if (contentType.includes("text/event-stream")) {
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": contentType, "Cache-Control": "no-cache", Connection: "keep-alive" },
    });
  }
  return NextResponse.json(await response.json(), { status: response.status });
}

export async function GET(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, "GET", (await params).path);
}
export async function POST(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return forward(req, "POST", (await params).path);
}
// ... PUT, PATCH, DELETE similarly
\`\`\`

## Chat UI (Client-Side)

Use SimpleChatInterface with a token from /api/auth/token:

\`\`\`typescript
'use client';
import { SimpleChatInterface } from '@jazzmind/busibox-app/components/chat/SimpleChatInterface';

export function ChatPanel({ agentId, metadata }) {
  const [token, setToken] = useState('');
  useEffect(() => {
    fetch('/api/auth/token').then(r => r.json()).then(d => setToken(d.token));
  }, []);

  if (!token) return <div>Loading...</div>;
  return <SimpleChatInterface token={token} agentId={agentId} metadata={metadata} />;
}
\`\`\`

## Structured Output (POST /runs/invoke)

For programmatic tasks needing deterministic JSON (scoring, classification, extraction):

\`\`\`typescript
const auth = await requireAuthWithTokenExchange(request, "agent-api");
if (auth instanceof NextResponse) return auth;

const response = await fetch(\`\${AGENT_API_URL}/runs/invoke\`, {
  method: "POST",
  headers: { Authorization: \`Bearer \${auth.apiToken}\`, "Content-Type": "application/json" },
  body: JSON.stringify({
    agent_name: "record-extractor",  // built-in no-tool agent
    input: { prompt: "Score these items..." },
    response_schema: {
      name: "scores",
      strict: true,
      schema: {
        type: "object",
        additionalProperties: false,
        required: ["items"],
        properties: { items: { type: "array", maxItems: 50, items: { ... } } },
      },
    },
    agent_tier: "complex",  // "simple" (30s), "complex" (5min), "batch" (30min)
  }),
});
const { output } = await response.json();  // Validated JSON matching schema
\`\`\`

## Agent Definitions (Sync from Code)

Define agents in a file like lib/my-agents.ts:

\`\`\`typescript
import type { AgentDefinitionInput } from '@jazzmind/busibox-app/lib/agent';

export const MY_AGENT: AgentDefinitionInput = {
  name: "my-assistant",
  display_name: "My Assistant",
  description: "Helps users with ...",
  instructions: \`You are a helpful assistant...

## Data Schema (itemsDocumentId)
- id: string
- name: string
- status: string

## How to Answer
Use query_data with the itemsDocumentId to look up data before answering.\`,
  model: "agent",
  tools: { names: ["query_data", "aggregate_data", "get_facets", "document_search"] },
  workflows: { execution_mode: "run_max_iterations", tool_strategy: "llm_driven", max_iterations: 10 },
  allow_frontier_fallback: true,
  is_builtin: false,
  scopes: ["data:read", "search:read"],
};

export const AGENT_DEFINITIONS = [MY_AGENT];
\`\`\`

Then sync via lib/sync.ts:

\`\`\`typescript
import { syncAgentDefinitions, getAgentSyncStatus } from '@jazzmind/busibox-app/lib/agent/sync';
import { AGENT_DEFINITIONS } from './my-agents';

export async function syncAgents(agentApiToken: string) {
  return syncAgentDefinitions(agentApiToken, AGENT_DEFINITIONS);
}
\`\`\`

Wire into app/api/settings/sync/route.ts and app/api/setup/route.ts for auto-sync.

## Available Core Tools (for agent definitions)

- \`query_data\` — Query records from a data document
- \`aggregate_data\` — Count/sum/avg on data documents
- \`get_facets\` — Get unique values for a field
- \`document_search\` — Semantic search across documents
- \`web_search\` — Search the web (requires search:web scope)
- \`create_record\` — Create a new record
- \`update_record\` — Update an existing record
- \`delete_record\` — Delete a record

## Key Agent API Endpoints (via proxy)

| Endpoint | Method | Purpose |
|----------|--------|---------|
| /agents | GET | List all agents |
| /agents/definitions | POST | Create/update agent definition |
| /agents/{id} | GET | Get agent details |
| /agents/tools | GET | List available tools |
| /chat/message/stream | POST | Streaming chat (SSE) |
| /runs/invoke | POST | Structured output (sync) |
| /runs/invoke-async | POST | Async run (background) |
| /conversations | GET/POST | List/create conversations |
| /insights/search | POST | Semantic insight search |

## CRITICAL Rules

- NEVER call LiteLLM directly — use agent-api's /runs/invoke for structured output
- NEVER access the agent-api database (PostgreSQL) directly
- NEVER access Redis or Milvus directly — agent-api handles these internally
- ALWAYS use the /api/agent/[...path] proxy pattern for server-side calls
- ALWAYS use /api/auth/token for client-side chat component tokens
`;

export const APP_TEMPLATE_STRUCTURE = `# Busibox App Template (Next.js 16)

## Architecture

All busibox apps use the **frontend-mode** pattern:
- Next.js 16 App Router for UI and API routes
- data-api for all persistent storage (via token exchange)
- agent-api for AI/chat features (via proxy route)
- authz for authentication (Zero Trust SSO)

There is NO prisma mode, NO direct database access, NO APP_MODE env var.

## Key Files

| File | Purpose |
|------|---------|
| app/layout.tsx | Root layout with SessionProvider, ThemeProvider |
| app/(authenticated)/layout.tsx | Client layout with useSession(), Header/Footer |
| app/api/sso/route.ts | SSO GET/POST handlers (createSSOGetHandler/createSSOPostHandler) |
| app/api/auth/session/route.ts | Session management (createSessionRouteHandlers) |
| app/api/auth/token/route.ts | Agent-api token for chat components |
| app/api/auth/exchange/route.ts | SSO token → cookie exchange |
| app/api/auth/refresh/route.ts | Token refresh |
| app/api/auth/logout/route.ts | Clear cookies + redirect |
| app/api/agent/[...path]/route.ts | Agent-api catch-all proxy |
| app/api/setup/route.ts | Data document initialization + agent sync |
| app/api/health/route.ts | Health check endpoint |
| lib/auth-middleware.ts | requireAuthWithTokenExchange, optionalAuth |
| lib/authz-client.ts | Zero Trust token exchange helpers |
| lib/data-api-client.ts | Document schemas and CRUD operations |
| lib/types.ts | App-specific TypeScript types |
| lib/sync.ts | Agent definition sync logic |
| busibox.json | App manifest (id, name, description, etc.) |

## Required Dependencies

\`\`\`json
{
  "@jazzmind/busibox-app": "^3.0",
  "next": "^16.0",
  "react": "^19.0",
  "react-dom": "^19.0",
  "jose": "^6.0"
}
\`\`\`

## Required Environment Variables

\`\`\`bash
APP_NAME=my-app
PORT=3002
NEXT_PUBLIC_BASE_PATH=
NEXT_PUBLIC_BUSIBOX_PORTAL_URL=http://localhost:3000
AUTHZ_BASE_URL=http://localhost:8010
DATA_API_URL=http://localhost:8002
AGENT_API_URL=http://localhost:8000
DEFAULT_API_AUDIENCE=data-api
\`\`\`

## Next.js 16 Critical Patterns

### Route params are async (MUST await):
\`\`\`typescript
interface PageProps { params: Promise<{ id: string }>; }
export default async function Page({ params }: PageProps) {
  const { id } = await params;
}
\`\`\`

### API route params:
\`\`\`typescript
export async function GET(request: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
}
\`\`\`

## Development Flow for New Features

1. Define data schemas in \`lib/data-api-client.ts\`
2. Define types in \`lib/types.ts\`
3. Create API routes in \`app/api/\` (with requireAuthWithTokenExchange)
4. Build UI pages in \`app/(authenticated)/\`
5. Add navigation in \`app/(authenticated)/layout.tsx\`
6. (Optional) Define agents in \`lib/my-agents.ts\` and sync via \`lib/sync.ts\`

## CRITICAL Anti-Patterns (NEVER DO)

- ❌ \`import { PrismaClient } from '@prisma/client'\` — No Prisma, no ORMs
- ❌ \`import pg from 'pg'\` — No direct PostgreSQL access
- ❌ \`import Redis from 'ioredis'\` — No direct Redis access
- ❌ \`fetch('http://litellm:4000/v1/...')\` — No direct LiteLLM calls
- ❌ \`import { Milvus } from '@zilliz/milvus2-sdk'\` — No direct vector DB
- ❌ \`process.env.DATABASE_URL\` — No database URLs in app code
- ❌ \`import from '@jazzmind/busibox-app/lib/auth'\` — Wrong path (use lib/authz)
- ❌ Creating service-to-service API keys or shared secrets

## CORRECT Patterns (ALWAYS DO)

- ✅ \`requireAuthWithTokenExchange(request, 'data-api')\` for data
- ✅ \`requireAuthWithTokenExchange(request, 'agent-api')\` for AI
- ✅ \`ensureDocuments()\` + \`queryRecords()\` for CRUD
- ✅ \`/api/agent/[...path]\` proxy for agent-api calls
- ✅ \`SimpleChatInterface\` with token from \`/api/auth/token\`
- ✅ \`syncAgentDefinitions()\` for registering agents
- ✅ \`/runs/invoke\` via proxy for structured AI output
`;
