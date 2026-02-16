/**
 * Static reference data for app builders
 * Based on @jazzmind/busibox-app and busibox-template patterns
 */

export const BUSIBOX_APP_EXPORTS = `# @jazzmind/busibox-app Exports

## Subpath Imports (preferred for tree-shaking)

| Import Path | Purpose |
|-------------|---------|
| @jazzmind/busibox-app/components | UI components |
| @jazzmind/busibox-app/contexts | React contexts |
| @jazzmind/busibox-app/layout | Header, Footer, ThemeToggle |
| @jazzmind/busibox-app/types | Shared types |
| @jazzmind/busibox-app/lib/agent | Agent API client |
| @jazzmind/busibox-app/lib/auth | Auth, sessions, magic links, TOTP, passkeys |
| @jazzmind/busibox-app/lib/authz | Zero Trust token exchange |
| @jazzmind/busibox-app/lib/audit | Audit logging |
| @jazzmind/busibox-app/lib/data | Data/ingest (uploads, embeddings) |
| @jazzmind/busibox-app/lib/rbac | RBAC (roles, users, bindings) |
| @jazzmind/busibox-app/lib/search | Web + document search |
| @jazzmind/busibox-app/sso | SSO token validation |

## Key Components

- **Chat**: SimpleChatInterface, FullChatInterface, ChatInterface
- **Documents**: DocumentUpload, DocumentList, DocumentSearch
- **Layout**: Header, Footer, ThemeToggle, FetchWrapper
- **Auth**: AuthProvider, useAuth

## Key Service Clients

- **Agent**: createAgentClient, agentChat, streamChatMessage
- **AuthZ**: exchangeTokenZeroTrust, getAuthHeaderZeroTrust
- **Data**: dataFetch, uploadChatAttachment, parseFileToMarkdown
- **RBAC**: hasRole, isAdmin, getUserAccessibleResources
`;

export const AUTH_PATTERNS = `# Busibox App Authentication Patterns

## Token Flow (Zero Trust)

1. User clicks app in Busibox Portal
2. Busibox Portal exchanges session JWT for app-scoped token via authz
3. authz verifies RBAC and issues RS256 token with app_id claim
4. App validates token via authz JWKS
5. App exchanges session JWT for backend API tokens (agent-api, data-api, search-api)

## API Route Auth (auth-middleware.ts)

\`\`\`typescript
import { requireAuthWithTokenExchange } from '@/lib/auth-middleware';

export async function GET(request: NextRequest) {
  const auth = await requireAuthWithTokenExchange(request);
  if (auth instanceof NextResponse) return auth;

  // auth.ssoToken - original session JWT
  // auth.apiToken - exchanged API token for backend calls
  // Use auth.apiToken in Authorization: Bearer for backend calls
}
\`\`\`

## SSO Route (app/api/sso/route.ts)

\`\`\`typescript
import { createSSOGetHandler, createSSOPostHandler } from "@jazzmind/busibox-app/lib/auth";

const handleGet = createSSOGetHandler(NextResponse, { defaultAppName: 'my-app' });
const handlePost = createSSOPostHandler(NextResponse, { defaultAppName: 'my-app' });

export async function GET(request: NextRequest) { return handleGet(request); }
export async function POST(request: NextRequest) { return handlePost(request); }
\`\`\`

**Use POST for client-side token exchange** - browsers don't process Set-Cookie on manual redirects.

## Required Env Vars

- AUTHZ_BASE_URL - AuthZ service URL
- APP_NAME - token audience
- DEFAULT_API_AUDIENCE - default backend audience (e.g. backend-api)
- TEST_SESSION_JWT - optional, for local dev without SSO
`;

export const APP_TEMPLATE_STRUCTURE = `# App Template Structure

## Key Files

- lib/auth-middleware.ts - requireAuthWithTokenExchange, optionalAuth
- lib/authz-client.ts - Token exchange helpers
- app/api/sso/route.ts - SSO GET/POST handlers
- app/api/auth/exchange/route.ts - Token exchange
- app/api/session/route.ts - Session info
- lib/api-client.ts - API client (frontend mode)
- lib/prisma.ts - Prisma client (prisma mode)

## APP_MODE

- frontend: API proxy pattern, no direct DB
- prisma: Direct database access
`;
