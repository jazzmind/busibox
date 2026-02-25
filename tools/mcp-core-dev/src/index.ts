#!/usr/bin/env node

/**
 * Busibox MCP Core Developer Server
 *
 * For developers working on busibox services (srv/agent, srv/data, srv/search, etc.)
 * Tools: docs, scripts, testing, Docker, container debugging, service discovery
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
import { readdirSync } from 'fs';
import { join, dirname, relative } from 'path';
import { fileURLToPath } from 'url';
import {
  PROXMOX_HOST_IP,
  PROXMOX_HOST_USER,
  PROXMOX_SSH_KEY_PATH,
  CONTAINER_SSH_KEY_PATH,
  BUSIBOX_PATH_ON_PROXMOX,
  DOC_CATEGORIES,
  DOC_NESTED_PATHS,
  SCRIPT_LOCATIONS,
  CONTAINERS,
  MAIN_MAKEFILE_TARGETS,
  MAKE_TARGETS,
  TESTING_GUIDES,
  getDocsByCategory,
  searchDocs,
  safeReadFile,
  listFilesRecursive,
  getScriptsFromDir,
  extractScriptInfo,
  getContainer,
  getContainerIP,
  executeSSHCommand,
} from '@busibox/mcp-shared';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = join(__dirname, '..', '..', '..');

const server = new Server(
  { name: 'busibox-mcp-core-dev', version: '1.0.0' },
  { capabilities: { resources: {}, tools: {}, prompts: {} } }
);

// Developer-focused doc categories
const CORE_DEV_DOC_CATEGORIES = ['developers', 'archive'] as const;

server.setRequestHandler(ListResourcesRequestSchema, async () => ({
  resources: [
    { uri: 'busibox://docs/developers', mimeType: 'text/markdown', name: 'Developer Documentation', description: 'Developer docs and architecture' },
    { uri: 'busibox://docs/archive', mimeType: 'text/markdown', name: 'Archive', description: 'Historical documentation' },
    { uri: 'busibox://scripts/index', mimeType: 'application/json', name: 'Scripts Index', description: 'Scripts by execution context' },
    { uri: 'busibox://rules', mimeType: 'text/markdown', name: 'Organization Rules', description: '.cursor/rules/' },
    { uri: 'busibox://architecture', mimeType: 'text/markdown', name: 'Architecture', description: 'System architecture docs' },
    { uri: 'busibox://quickstart', mimeType: 'text/markdown', name: 'Quick Start', description: 'CLAUDE.md' },
    { uri: 'busibox://containers', mimeType: 'application/json', name: 'Container Map', description: 'Container IPs and services' },
    { uri: 'busibox://make-targets', mimeType: 'application/json', name: 'Make Targets', description: 'Make targets and usage' },
  ],
}));

server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = request.params.uri;

  if (uri.startsWith('busibox://docs/') && uri !== 'busibox://docs/all') {
    const category = uri.replace('busibox://docs/', '');
    const docs = getDocsByCategory(PROJECT_ROOT, category);
    const nestedPaths = DOC_NESTED_PATHS[category] || [];
    let allDocs = [...docs];
    for (const p of nestedPaths) {
      allDocs.push(...getDocsByCategory(PROJECT_ROOT, p));
    }
    let content = `# ${category} Documentation\n\nFound ${allDocs.length} documents.\n\n`;
    for (const doc of allDocs) {
      const docContent = safeReadFile(join(PROJECT_ROOT, doc.path));
      content += `## ${doc.name}\nPath: \`${doc.path}\`\n\n${(docContent || '').slice(0, 300)}...\n\n---\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  if (uri === 'busibox://scripts/index') {
    const index: Record<string, unknown[]> = {};
    for (const [ctx, dir] of Object.entries(SCRIPT_LOCATIONS)) {
      index[ctx] = getScriptsFromDir(PROJECT_ROOT, dir);
    }
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify(index, null, 2) }] };
  }

  if (uri === 'busibox://rules') {
    const rulesDir = join(PROJECT_ROOT, '.cursor', 'rules');
    const ruleFiles = readdirSync(rulesDir).filter((f) => f.endsWith('.md'));
    let content = '# Busibox Organization Rules\n\n';
    for (const file of ruleFiles.sort()) {
      const ruleContent = safeReadFile(join(rulesDir, file));
      if (ruleContent) content += `## ${file}\n\n${ruleContent}\n\n---\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  if (uri === 'busibox://architecture') {
    const archDocs = getDocsByCategory(PROJECT_ROOT, 'developers/architecture');
    let content = '# Busibox Architecture\n\n';
    for (const doc of archDocs.sort((a, b) => a.name.localeCompare(b.name))) {
      const docContent = safeReadFile(join(PROJECT_ROOT, doc.path));
      if (docContent && !doc.path.includes('/archive/')) content += `---\n\n## ${doc.name}\n\n${docContent}\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  if (uri === 'busibox://quickstart') {
    const content = safeReadFile(join(PROJECT_ROOT, 'CLAUDE.md'));
    return { contents: [{ uri, mimeType: 'text/markdown', text: content || 'CLAUDE.md not found' }] };
  }

  if (uri === 'busibox://containers') {
    const data = {
      production: CONTAINERS.map((c) => ({ id: c.id, name: c.name, ip: c.ip, purpose: c.purpose, ports: c.ports, services: c.services })),
      staging: CONTAINERS.map((c) => ({ id: c.testId, name: c.name, ip: c.testIp, purpose: c.purpose, ports: c.ports, services: c.services })),
    };
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify(data, null, 2) }] };
  }

  if (uri === 'busibox://make-targets') {
    const byCategory: Record<string, typeof MAKE_TARGETS> = {};
    for (const [target, info] of Object.entries(MAKE_TARGETS)) {
      if (!byCategory[info.category]) byCategory[info.category] = {};
      byCategory[info.category][target] = info;
    }
    return {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify({ targets: MAKE_TARGETS, byCategory, mainTargets: MAIN_MAKEFILE_TARGETS }, null, 2),
        },
      ],
    };
  }

  throw new Error(`Unknown resource: ${uri}`);
});

const CORE_DEV_TOOLS = [
  { name: 'search_docs', description: 'Search documentation', inputSchema: { type: 'object', properties: { query: { type: 'string' }, category: { type: 'string', enum: [...CORE_DEV_DOC_CATEGORIES, 'all'] } }, required: ['query'] } },
  { name: 'get_doc', description: 'Get full doc content', inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] } },
  { name: 'get_script_info', description: 'Get script purpose and usage', inputSchema: { type: 'object', properties: { script_path: { type: 'string' } }, required: ['script_path'] } },
  { name: 'find_scripts', description: 'Find scripts by context', inputSchema: { type: 'object', properties: { context: { type: 'string', enum: ['admin-workstation', 'proxmox-host', 'container', 'all'] }, purpose: { type: 'string' } }, required: ['context'] } },
  { name: 'list_containers', description: 'List LXC containers', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_container_info', description: 'Get container details', inputSchema: { type: 'object', properties: { container: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } }, required: ['container'] } },
  { name: 'get_service_endpoints', description: 'Get service IPs and ports', inputSchema: { type: 'object', properties: { service: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } } } },
  { name: 'get_container_logs', description: 'Get container logs via SSH', inputSchema: { type: 'object', properties: { container: { type: 'string' }, service: { type: 'string' }, lines: { type: 'number' } }, required: ['container'] } },
  { name: 'get_container_service_status', description: 'Get systemctl status', inputSchema: { type: 'object', properties: { container: { type: 'string' }, service: { type: 'string' } }, required: ['container', 'service'] } },
  { name: 'get_makefile_help', description: 'Makefile help', inputSchema: { type: 'object', properties: { category: { type: 'string', enum: ['menu', 'setup', 'deploy', 'test', 'docker', 'mcp', 'all'] } } } },
  { name: 'run_docker_tests', description: 'Run Docker tests (returns command)', inputSchema: { type: 'object', properties: { service: { type: 'string', enum: ['authz', 'data', 'search', 'agent', 'all'] }, fast: { type: 'boolean' }, pytest_args: { type: 'string' } }, required: ['service'] } },
  { name: 'run_remote_tests', description: 'Run remote tests (returns command)', inputSchema: { type: 'object', properties: { service: { type: 'string', enum: ['authz', 'data', 'search', 'agent', 'all'] }, environment: { type: 'string', enum: ['staging', 'production'] }, fast: { type: 'boolean' }, worker: { type: 'boolean' }, pytest_args: { type: 'string' } }, required: ['service', 'environment'] } },
  { name: 'run_container_tests', description: 'Run tests on containers via SSH', inputSchema: { type: 'object', properties: { service: { type: 'string', enum: ['authz', 'data', 'search', 'agent', 'all'] }, environment: { type: 'string', enum: ['staging', 'production'] }, pytest_args: { type: 'string' } }, required: ['service', 'environment'] } },
  { name: 'docker_control', description: 'Docker service control (returns command)', inputSchema: { type: 'object', properties: { action: { type: 'string', enum: ['up', 'down', 'restart', 'ps', 'logs', 'build', 'clean'] }, service: { type: 'string' }, no_cache: { type: 'boolean' } }, required: ['action'] } },
  { name: 'init_test_databases', description: 'Init test DBs (returns command)', inputSchema: { type: 'object', properties: {} } },
  { name: 'check_test_databases', description: 'Check test DBs (returns command)', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_testing_guide', description: 'Get testing guide', inputSchema: { type: 'object', properties: { topic: { type: 'string', enum: ['overview', 'docker', 'remote', 'container', 'troubleshooting', 'all'] } } } },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: CORE_DEV_TOOLS,
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const a = (args || {}) as Record<string, unknown>;

  const text = (t: string) => ({ content: [{ type: 'text' as const, text: t }] });

  switch (name) {
    case 'search_docs': {
      const query = String(a.query || '');
      const category = String(a.category || 'all');
      let paths: string[] = category === 'all' ? [...CORE_DEV_DOC_CATEGORIES] : [category];
      const nested = DOC_NESTED_PATHS[category] || [];
      paths = [...new Set([...paths, ...nested])];
      const results: Array<{ file: string; matches: string[] }> = [];
      for (const p of paths) {
        results.push(...searchDocs(PROJECT_ROOT, query, p));
      }
      return text(JSON.stringify(results, null, 2));
    }

    case 'get_doc': {
      const path = String(a.path || '');
      const content = safeReadFile(join(PROJECT_ROOT, 'docs', path));
      return text(content || `Doc not found: ${path}`);
    }

    case 'get_script_info': {
      const scriptPath = String(a.script_path || '');
      const fullPath = join(PROJECT_ROOT, scriptPath);
      const { existsSync } = await import('fs');
      if (!existsSync(fullPath)) return text(`Script not found: ${scriptPath}`);
      const info = extractScriptInfo(PROJECT_ROOT, scriptPath);
      const content = safeReadFile(fullPath);
      return text(JSON.stringify({ path: scriptPath, ...info, preview: content?.slice(0, 1000) }, null, 2));
    }

    case 'find_scripts': {
      const context = String(a.context || 'all');
      let scripts: unknown[] = [];
      if (context === 'admin-workstation' || context === 'all') scripts.push(...getScriptsFromDir(PROJECT_ROOT, SCRIPT_LOCATIONS['admin-workstation']).map((s) => ({ ...s, context: 'admin-workstation' })));
      if (context === 'proxmox-host' || context === 'all') scripts.push(...getScriptsFromDir(PROJECT_ROOT, SCRIPT_LOCATIONS['proxmox-host']).map((s) => ({ ...s, context: 'proxmox-host' })));
      if (context === 'container' || context === 'all') {
        const files = listFilesRecursive(PROJECT_ROOT, 'provision/ansible', '*.sh');
        scripts.push(...files.map((f) => ({ name: f.split('/').pop(), path: relative(PROJECT_ROOT, f), context: 'container' })));
      }
      const purpose = String(a.purpose || '');
      if (purpose) scripts = scripts.filter((s: unknown) => String((s as { name?: string }).name || '').toLowerCase().includes(purpose.toLowerCase()));
      return text(JSON.stringify(scripts, null, 2));
    }

    case 'list_containers':
      return text(JSON.stringify({ production: CONTAINERS.map((c) => ({ id: c.id, name: c.name, ip: c.ip, purpose: c.purpose, services: c.services })), staging: CONTAINERS.map((c) => ({ id: c.testId, name: c.name, ip: c.testIp, purpose: c.purpose, services: c.services })) }, null, 2));

    case 'get_container_info': {
      const container = getContainer(String(a.container || ''));
      const env = (a.environment as 'production' | 'staging') || 'production';
      if (!container) return text(JSON.stringify({ error: 'Container not found', available: CONTAINERS.map((c) => c.name) }, null, 2));
      return text(JSON.stringify({ name: container.name, id: env === 'staging' ? container.testId : container.id, ip: env === 'staging' ? container.testIp : container.ip, purpose: container.purpose, ports: container.ports, services: container.services }, null, 2));
    }

    case 'get_service_endpoints': {
      const service = String(a.service || '');
      const env = (a.environment as 'production' | 'staging') || 'production';
      const endpoints: Array<{ service: string; container: string; ip: string; port: number; url: string }> = [];
      for (const c of CONTAINERS) {
        const ip = env === 'staging' ? c.testIp : c.ip;
        for (const p of c.ports) {
          if (!service || p.service.toLowerCase().includes(service.toLowerCase()) || c.services.some((s) => s.toLowerCase().includes(service.toLowerCase()))) {
            endpoints.push({ service: p.service, container: c.name, ip, port: p.port, url: `http://${ip}:${p.port}` });
          }
        }
      }
      return text(JSON.stringify({ environment: env, filter: service || 'all', endpoints }, null, 2));
    }

    case 'get_container_logs': {
      const container = String(a.container || '');
      const service = String(a.service || '');
      const lines = Number(a.lines) || 50;
      const ip = getContainerIP(container) || container;
      try {
        const cmd = service ? `journalctl -u ${service} -n ${lines} --no-pager` : `journalctl -n ${lines} --no-pager`;
        const result = await executeSSHCommand(ip, 'root', cmd, CONTAINER_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ container, ip, service: service || 'all', lines, exitCode: result.exitCode, logs: result.stdout, error: result.stderr || undefined }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, container, service }, null, 2));
      }
    }

    case 'get_container_service_status': {
      const container = String(a.container || '');
      const service = String(a.service || '');
      const ip = getContainerIP(container) || container;
      try {
        const result = await executeSSHCommand(ip, 'root', `systemctl status ${service} --no-pager -l`, CONTAINER_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ container, ip, service, exitCode: result.exitCode, status: result.stdout, error: result.stderr || undefined }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, container, service }, null, 2));
      }
    }

    case 'get_makefile_help': {
      const cat = String(a.category || 'all');
      const filtered = cat === 'all' ? MAIN_MAKEFILE_TARGETS : Object.fromEntries(Object.entries(MAIN_MAKEFILE_TARGETS).filter(([, t]) => t.category === cat));
      let help = '# Busibox Makefile Help\n\n## Environments\n- local, staging, production\n\n';
      for (const [target, info] of Object.entries(filtered)) {
        help += `### make ${target}\n${info.description}\n`;
        if (info.examples) help += `Examples: ${info.examples.join(', ')}\n`;
        help += '\n';
      }
      return text(help);
    }

    case 'run_docker_tests': {
      const svc = String(a.service || 'agent');
      const fast = a.fast !== false;
      const ar = String(a.pytest_args || '');
      const cmd = `make test-docker SERVICE=${svc} ${fast ? 'FAST=1' : 'FAST=0'} ${ar ? `ARGS='${ar}'` : ''}`.trim();
      return text(JSON.stringify({ note: 'Run locally', command: cmd, prerequisites: ['make docker-up', 'make test-db-init'] }, null, 2));
    }

    case 'run_remote_tests': {
      const svc = String(a.service || 'agent');
      const env = String(a.environment || 'staging');
      const inv = env === 'production' ? 'production' : 'staging';
      const fast = a.fast !== false;
      const worker = a.worker === true;
      const ar = String(a.pytest_args || '');
      const cmd = `make test-local SERVICE=${svc} INV=${inv} ${fast ? 'FAST=1' : ''} ${worker ? 'WORKER=1' : ''} ${ar ? `ARGS='${ar}'` : ''}`.trim();
      return text(JSON.stringify({ note: 'Run locally, connects to remote', command: cmd }, null, 2));
    }

    case 'run_container_tests': {
      const svc = String(a.service || 'agent');
      const env = String(a.environment || 'staging');
      const inv = env === 'production' ? 'production' : 'staging';
      try {
        const cmd = `cd ${BUSIBOX_PATH_ON_PROXMOX} && make test SERVICE=${svc} INV=${inv}`;
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, cmd, PROXMOX_SSH_KEY_PATH, 600000);
        return text(JSON.stringify({ command: cmd, exitCode: result.exitCode, success: result.exitCode === 0, stdout: result.stdout, stderr: result.stderr }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message }, null, 2));
      }
    }

    case 'docker_control': {
      const action = String(a.action || 'ps');
      const svc = a.service ? ` SERVICE=${a.service}` : '';
      const noCache = a.no_cache && action === 'build' ? ' NO_CACHE=1' : '';
      const cmd = `make docker-${action}${svc}${noCache}`.trim();
      return text(JSON.stringify({ note: 'Run locally', command: cmd }, null, 2));
    }

    case 'init_test_databases':
      return text(JSON.stringify({ command: 'make test-db-init', description: 'Bootstrap test databases', prerequisites: ['make docker-up'] }, null, 2));

    case 'check_test_databases':
      return text(JSON.stringify({ command: 'make test-db-check', description: 'Check test DB readiness' }, null, 2));

    case 'get_testing_guide': {
      const topic = String(a.topic || 'overview');
      const guide = topic === 'all' ? Object.values(TESTING_GUIDES).join('\n---\n\n') : (TESTING_GUIDES[topic] || TESTING_GUIDES.overview);
      return text(guide);
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

const CORE_DEV_PROMPTS = [
  { name: 'run_tests', description: 'Run tests', arguments: [{ name: 'service', required: false }, { name: 'test_type', required: false }] },
  { name: 'testing_workflow', description: 'Testing workflow', arguments: [{ name: 'environment', required: true }, { name: 'service', required: false }] },
  { name: 'docker_development', description: 'Docker dev guide', arguments: [] },
  { name: 'troubleshoot_issue', description: 'Troubleshooting', arguments: [{ name: 'issue_type', required: true }] },
  { name: 'add_service', description: 'Add new service', arguments: [{ name: 'service_name', required: true }] },
];

server.setRequestHandler(ListPromptsRequestSchema, async () => ({ prompts: CORE_DEV_PROMPTS }));

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
    case 'run_tests':
      return msg(`How do I run tests for ${a.service || 'all'}?`, `Use make test-docker SERVICE=${a.service || 'all'} for Docker, or make test-local SERVICE=${a.service || 'all'} INV=staging for remote. First run make test-db-init.`);
    case 'testing_workflow':
      return msg(`Testing workflow for ${a.environment || 'docker'}`, a.environment === 'docker' ? 'make docker-up && make test-db-init && make test-docker SERVICE=' + (a.service || 'agent') : `make test-local SERVICE=${a.service || 'agent'} INV=${a.environment}`);
    case 'docker_development':
      return msg('Docker dev setup?', 'make docker-up, make test-db-init, make docker-ps. Use make docker-logs SERVICE=x for logs.');
    case 'troubleshoot_issue':
      return msg(`Troubleshoot ${a.issue_type}`, 'Use get_container_logs, get_container_service_status. Check docs with search_docs.');
    case 'add_service':
      return msg(`Add service ${a.service_name}`, '1. Update provision/pct/vars.env 2. Edit create_lxc_base.sh 3. Create Ansible role 4. Update inventory. Use get_doc for architecture/01-containers.');
    default:
      throw new Error(`Unknown prompt: ${name}`);
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Busibox MCP Core Developer Server v1.0.0 running on stdio');
}

main().catch((e) => {
  console.error('Fatal:', e);
  process.exit(1);
});
