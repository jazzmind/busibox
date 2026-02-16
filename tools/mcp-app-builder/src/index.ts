#!/usr/bin/env node

/**
 * Busibox MCP App Builder Server
 *
 * For developers building Next.js apps that deploy on busibox
 * Tools: busibox-app exports, auth patterns, busibox-template reference, service endpoints
 */

import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import {
  CallToolRequestSchema,
  ListResourcesRequestSchema,
  ListToolsRequestSchema,
  ReadResourceRequestSchema,
  ListPromptsRequestSchema,
  GetPromptRequestSchema,
} from '@modelcontextprotocol/sdk/types.js';
import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  DOC_NESTED_PATHS,
  getDocsByCategory,
  searchDocs,
  safeReadFile,
  CONTAINERS,
} from '@busibox/mcp-shared';
import { BUSIBOX_APP_EXPORTS, AUTH_PATTERNS, APP_TEMPLATE_STRUCTURE } from './app-builder-data.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = join(__dirname, '..', '..', '..');
const BUSIBOX_APP_PATH = process.env.BUSIBOX_APP_PATH || join(PROJECT_ROOT, '..', 'busibox-app');
const APP_TEMPLATE_PATH = process.env.APP_TEMPLATE_PATH || join(PROJECT_ROOT, '..', 'busibox-template');

const server = new Server(
  { name: 'busibox-mcp-app-builder', version: '1.0.0' },
  { capabilities: { resources: {}, tools: {}, prompts: {} } }
);

const APP_BUILDER_DOC_CATEGORIES = ['users', 'developers'] as const;

server.setRequestHandler(ListResourcesRequestSchema, async () => ({
  resources: [
    { uri: 'busibox://app-builder/guide', mimeType: 'text/markdown', name: 'App Builder Guide', description: 'How to build Busibox apps' },
    { uri: 'busibox://app-builder/busibox-app', mimeType: 'text/markdown', name: 'busibox-app API', description: 'Library exports reference' },
    { uri: 'busibox://app-builder/auth', mimeType: 'text/markdown', name: 'Auth Patterns', description: 'SSO and token exchange' },
    { uri: 'busibox://app-builder/service-endpoints', mimeType: 'application/json', name: 'Service Endpoints', description: 'Backend service URLs' },
    { uri: 'busibox://docs/users', mimeType: 'text/markdown', name: 'User Docs', description: 'Platform user guides' },
    { uri: 'busibox://docs/developers', mimeType: 'text/markdown', name: 'Developer Docs', description: 'Developer documentation' },
  ],
}));

server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = request.params.uri;

  if (uri === 'busibox://app-builder/guide') {
    const content = APP_TEMPLATE_STRUCTURE + '\n\n' + AUTH_PATTERNS;
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }
  if (uri === 'busibox://app-builder/busibox-app') {
    return { contents: [{ uri, mimeType: 'text/markdown', text: BUSIBOX_APP_EXPORTS }] };
  }
  if (uri === 'busibox://app-builder/auth') {
    return { contents: [{ uri, mimeType: 'text/markdown', text: AUTH_PATTERNS }] };
  }
  if (uri === 'busibox://app-builder/service-endpoints') {
    const endpoints = CONTAINERS.flatMap((c) =>
      c.ports.map((p) => ({ service: p.service, container: c.name, production: c.ip, staging: c.testIp, port: p.port }))
    );
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify({ endpoints }, null, 2) }] };
  }

  if (uri.startsWith('busibox://docs/')) {
    const category = uri.replace('busibox://docs/', '');
    const docs = getDocsByCategory(PROJECT_ROOT, category);
    const nested = DOC_NESTED_PATHS[category] || [];
    let allDocs = [...docs];
    for (const p of nested) allDocs.push(...getDocsByCategory(PROJECT_ROOT, p));
    let content = `# ${category}\n\n${allDocs.length} documents.\n\n`;
    for (const doc of allDocs) {
      const dc = safeReadFile(join(PROJECT_ROOT, doc.path));
      content += `## ${doc.name}\n\`${doc.path}\`\n\n${(dc || '').slice(0, 200)}...\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  throw new Error(`Unknown resource: ${uri}`);
});

const APP_BUILDER_TOOLS = [
  { name: 'search_docs', description: 'Search docs', inputSchema: { type: 'object', properties: { query: { type: 'string' }, category: { type: 'string', enum: [...APP_BUILDER_DOC_CATEGORIES, 'all'] } }, required: ['query'] } },
  { name: 'get_doc', description: 'Get doc content', inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] } },
  { name: 'get_busibox_app_exports', description: 'Get @jazzmind/busibox-app library exports', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_auth_patterns', description: 'Get auth patterns (SSO, token exchange)', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_app_template_files', description: 'Read busibox-template reference files', inputSchema: { type: 'object', properties: { path: { type: 'string', description: 'Path relative to busibox-template root (e.g. lib/auth-middleware.ts)' } } } },
  { name: 'get_service_endpoints', description: 'Get backend service IPs/ports', inputSchema: { type: 'object', properties: { environment: { type: 'string', enum: ['production', 'staging'] } } } },
  { name: 'validate_app_config', description: 'Check busibox.json structure', inputSchema: { type: 'object', properties: { config_path: { type: 'string' } } } },
  { name: 'get_component_docs', description: 'Get docs for busibox-app component', inputSchema: { type: 'object', properties: { component: { type: 'string', description: 'Component name (e.g. SimpleChatInterface, DocumentUpload)' } } } },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: APP_BUILDER_TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const a = (args || {}) as Record<string, unknown>;
  const text = (t: string) => ({ content: [{ type: 'text' as const, text: t }] });

  switch (name) {
    case 'search_docs': {
      const query = String(a.query || '');
      const category = String(a.category || 'all');
      let paths: string[] = category === 'all' ? [...APP_BUILDER_DOC_CATEGORIES] : [category];
      const nested = DOC_NESTED_PATHS[category] || [];
      paths = [...new Set([...paths, ...nested])];
      const results: Array<{ file: string; matches: string[] }> = [];
      for (const p of paths) results.push(...searchDocs(PROJECT_ROOT, query, p));
      return text(JSON.stringify(results, null, 2));
    }
    case 'get_doc': {
      const path = String(a.path || '');
      const content = safeReadFile(join(PROJECT_ROOT, 'docs', path));
      return text(content || `Doc not found: ${path}`);
    }
    case 'get_busibox_app_exports':
      return text(BUSIBOX_APP_EXPORTS);
    case 'get_auth_patterns':
      return text(AUTH_PATTERNS);
    case 'get_app_template_files': {
      const path = String(a.path || 'lib/auth-middleware.ts');
      const fullPath = join(APP_TEMPLATE_PATH, path);
      if (!existsSync(fullPath)) return text(`File not found: ${path} (checked ${APP_TEMPLATE_PATH})`);
      const content = readFileSync(fullPath, 'utf-8');
      return text(content);
    }
    case 'get_service_endpoints': {
      const env = (a.environment as 'production' | 'staging') || 'production';
      const endpoints = CONTAINERS.flatMap((c) => {
        const ip = env === 'staging' ? c.testIp : c.ip;
        return c.ports.map((p) => ({ service: p.service, container: c.name, ip, port: p.port, url: `http://${ip}:${p.port}` }));
      });
      return text(JSON.stringify({ environment: env, endpoints }, null, 2));
    }
    case 'validate_app_config': {
      const configPath = String(a.config_path || 'busibox.json');
      const resolved = configPath.startsWith('/') ? configPath : join(process.cwd(), configPath);
      if (!existsSync(resolved)) return text(JSON.stringify({ valid: false, error: `Config not found: ${configPath}` }, null, 2));
      try {
        const raw = readFileSync(resolved, 'utf-8');
        const config = JSON.parse(raw);
        const required = ['id', 'name'];
        const missing = required.filter((k) => !(k in config));
        return text(JSON.stringify({ valid: missing.length === 0, missing, config }, null, 2));
      } catch (e) {
        return text(JSON.stringify({ valid: false, error: (e as Error).message }, null, 2));
      }
    }
    case 'get_component_docs': {
      const component = String(a.component || '');
      const compLower = component.toLowerCase();
      if (compLower.includes('chat')) return text(`${BUSIBOX_APP_EXPORTS}\n\nChat: SimpleChatInterface, FullChatInterface, ChatInterface. Use requireAuthWithTokenExchange in API routes, auth.apiToken for backend calls.`);
      if (compLower.includes('document')) return text(`${BUSIBOX_APP_EXPORTS}\n\nDocuments: DocumentUpload, DocumentList, DocumentSearch. Use dataFetch, uploadChatAttachment from lib/data.`);
      if (compLower.includes('auth')) return text(AUTH_PATTERNS);
      return text(BUSIBOX_APP_EXPORTS);
    }
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

const APP_BUILDER_PROMPTS = [
  { name: 'create_app', description: 'Create new app', arguments: [{ name: 'app_name', required: true }] },
  { name: 'add_auth', description: 'Add auth to app', arguments: [] },
  { name: 'add_chat', description: 'Add chat to app', arguments: [] },
  { name: 'add_document_management', description: 'Add document management', arguments: [] },
  { name: 'deploy_app', description: 'Deploy app', arguments: [{ name: 'app_name', required: true }, { name: 'environment', required: true }] },
  { name: 'create_api_route', description: 'Create API route', arguments: [{ name: 'route_name', required: true }] },
];

server.setRequestHandler(ListPromptsRequestSchema, async () => ({ prompts: APP_BUILDER_PROMPTS }));

server.setRequestHandler(GetPromptRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const a = (args || {}) as Record<string, string>;
  const msg = (user: string, assistant: string) => ({
    messages: [
      { role: 'user' as const, content: { type: 'text' as const, text: user } },
      { role: 'assistant' as const, content: { type: 'text' as const, text: assistant } },
    ],
  });

  switch (name) {
    case 'create_app':
      return msg(`Create app ${a.app_name}`, `Use busibox-template. Set APP_MODE=frontend or prisma. Add busibox.json with id and name. Use get_auth_patterns for SSO.`);
    case 'add_auth':
      return msg('Add auth', 'Use requireAuthWithTokenExchange in API routes. Add app/api/sso/route.ts with createSSOGetHandler and createSSOPostHandler from @jazzmind/busibox-app/lib/auth.');
    case 'add_chat':
      return msg('Add chat', 'Use SimpleChatInterface from @jazzmind/busibox-app. Ensure auth.apiToken is passed. Use agentChat or streamChatMessage from lib/agent.');
    case 'add_document_management':
      return msg('Add documents', 'Use DocumentUpload, DocumentList, DocumentSearch from @jazzmind/busibox-app. Use dataFetch, uploadChatAttachment from lib/data.');
    case 'deploy_app':
      return msg(`Deploy ${a.app_name} to ${a.environment}`, `Use make install SERVICE=${a.app_name} or Deploy API. Ensure busibox.json and docs/portal/ exist.`);
    case 'create_api_route':
      return msg(`Create API route ${a.route_name}`, 'Use requireAuthWithTokenExchange. Use auth.apiToken for backend calls. See get_app_template_files for lib/auth-middleware.ts.');
    default:
      throw new Error(`Unknown prompt: ${name}`);
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Busibox MCP App Builder Server v1.0.0 running on stdio');
}

main().catch((e) => {
  console.error('Fatal:', e);
  process.exit(1);
});