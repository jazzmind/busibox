#!/usr/bin/env node

/**
 * Busibox MCP Administrator Server
 *
 * For operators managing deployments (including Claude Code/Cowork driving installs)
 * Tools: deployment, service management, SSH, git, make - with confirmation for destructive ops
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
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import {
  PROXMOX_HOST_IP,
  PROXMOX_HOST_USER,
  PROXMOX_SSH_KEY_PATH,
  CONTAINER_SSH_KEY_PATH,
  BUSIBOX_PATH_ON_PROXMOX,
  DOC_CATEGORIES,
  DOC_NESTED_PATHS,
  CONTAINERS,
  MAKE_TARGETS,
  getDocsByCategory,
  searchDocs,
  safeReadFile,
  getContainer,
  getContainerIP,
  executeSSHCommand,
} from '@busibox/mcp-shared';
import { isDestructiveCommand, isDestructiveMakeTarget } from './destructive.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = join(__dirname, '..', '..', '..');

const ADMIN_DOC_CATEGORIES = ['administrators'] as const;

const server = new Server(
  { name: 'busibox-mcp-admin', version: '1.0.0' },
  { capabilities: { resources: {}, tools: {}, prompts: {} } }
);

server.setRequestHandler(ListResourcesRequestSchema, async () => ({
  resources: [
    { uri: 'busibox://docs/administrators', mimeType: 'text/markdown', name: 'Administrator Docs', description: 'Deployment and operations' },
    { uri: 'busibox://containers', mimeType: 'application/json', name: 'Container Map', description: 'Container IPs and services' },
    { uri: 'busibox://make-targets', mimeType: 'application/json', name: 'Make Targets', description: 'Deployment targets' },
    { uri: 'busibox://quickstart', mimeType: 'text/markdown', name: 'Quick Start', description: 'CLAUDE.md' },
    { uri: 'busibox://rules', mimeType: 'text/markdown', name: 'Rules', description: '.cursor/rules/' },
  ],
}));

server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = request.params.uri;

  if (uri.startsWith('busibox://docs/')) {
    const category = uri.replace('busibox://docs/', '');
    const docs = getDocsByCategory(PROJECT_ROOT, category);
    const nested = DOC_NESTED_PATHS[category] || [];
    let allDocs = [...docs];
    for (const p of nested) allDocs.push(...getDocsByCategory(PROJECT_ROOT, p));
    let content = `# ${category}\n\n${allDocs.length} documents.\n\n`;
    for (const doc of allDocs) {
      const dc = safeReadFile(join(PROJECT_ROOT, doc.path));
      content += `## ${doc.name}\n\`${doc.path}\`\n\n${(dc || '').slice(0, 300)}...\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  if (uri === 'busibox://containers') {
    const data = {
      production: CONTAINERS.map((c) => ({ id: c.id, name: c.name, ip: c.ip, purpose: c.purpose, services: c.services })),
      staging: CONTAINERS.map((c) => ({ id: c.testId, name: c.name, ip: c.testIp, purpose: c.purpose, services: c.services })),
    };
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify(data, null, 2) }] };
  }

  if (uri === 'busibox://make-targets') {
    const byCategory: Record<string, typeof MAKE_TARGETS> = {};
    for (const [target, info] of Object.entries(MAKE_TARGETS)) {
      if (!byCategory[info.category]) byCategory[info.category] = {};
      byCategory[info.category][target] = info;
    }
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify({ targets: MAKE_TARGETS, byCategory }, null, 2) }] };
  }

  if (uri === 'busibox://quickstart') {
    const content = safeReadFile(join(PROJECT_ROOT, 'CLAUDE.md'));
    return { contents: [{ uri, mimeType: 'text/markdown', text: content || 'CLAUDE.md not found' }] };
  }

  if (uri === 'busibox://rules') {
    const rulesDir = join(PROJECT_ROOT, '.cursor', 'rules');
    const ruleFiles = readdirSync(rulesDir).filter((f) => f.endsWith('.md'));
    let content = '# Busibox Rules\n\n';
    for (const file of ruleFiles.sort()) {
      const rc = safeReadFile(join(rulesDir, file));
      if (rc) content += `## ${file}\n\n${rc}\n\n---\n\n`;
    }
    return { contents: [{ uri, mimeType: 'text/markdown', text: content }] };
  }

  throw new Error(`Unknown resource: ${uri}`);
});

const ADMIN_TOOLS = [
  { name: 'search_docs', description: 'Search administrator docs', inputSchema: { type: 'object', properties: { query: { type: 'string' }, category: { type: 'string', enum: [...ADMIN_DOC_CATEGORIES, 'all'] } }, required: ['query'] } },
  { name: 'get_doc', description: 'Get doc content', inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] } },
  { name: 'list_containers', description: 'List containers', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_container_info', description: 'Get container details', inputSchema: { type: 'object', properties: { container: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } }, required: ['container'] } },
  { name: 'get_service_endpoints', description: 'Get service IPs/ports', inputSchema: { type: 'object', properties: { service: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } } } },
  { name: 'get_deployment_info', description: 'Get environment config', inputSchema: { type: 'object', properties: { environment: { type: 'string', enum: ['staging', 'production'] } }, required: ['environment'] } },
  {
    name: 'execute_proxmox_command',
    description: 'Execute command on Proxmox (destructive commands require confirm: true)',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string' },
        working_directory: { type: 'string' },
        timeout: { type: 'number' },
        confirm: { type: 'boolean', description: 'Required for destructive commands (rm, reset, drop, force, etc.)' },
      },
      required: ['command'],
    },
  },
  { name: 'get_container_logs', description: 'Get container logs', inputSchema: { type: 'object', properties: { container: { type: 'string' }, service: { type: 'string' }, lines: { type: 'number' } }, required: ['container'] } },
  { name: 'get_container_service_status', description: 'Get service status', inputSchema: { type: 'object', properties: { container: { type: 'string' }, service: { type: 'string' } }, required: ['container', 'service'] } },
  {
    name: 'git_pull_busibox',
    description: 'Pull code on Proxmox (reset_hard requires confirm: true)',
    inputSchema: {
      type: 'object',
      properties: {
        branch: { type: 'string' },
        reset_hard: { type: 'boolean' },
        confirm: { type: 'boolean', description: 'Required when reset_hard is true' },
      },
    },
  },
  { name: 'git_status', description: 'Git status on Proxmox', inputSchema: { type: 'object', properties: {} } },
  {
    name: 'run_make_target',
    description: 'Run make target (destructive targets require confirm: true). Use list_make_targets to see options.',
    inputSchema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Make target (e.g. all, authz, deploy-ai-portal, verify-health)' },
        environment: { type: 'string', enum: ['production', 'staging'] },
        extra_args: { type: 'string' },
        timeout: { type: 'number' },
        confirm: { type: 'boolean', description: 'Required for destructive targets (docker-clean, etc.)' },
      },
      required: ['target', 'environment'],
    },
  },
  { name: 'list_make_targets', description: 'List make targets', inputSchema: { type: 'object', properties: { category: { type: 'string', enum: ['deployment', 'app-deployment', 'verification', 'testing', 'configuration', 'all'] } } } },
  { name: 'check_environment_health', description: 'Run verify-health', inputSchema: { type: 'object', properties: { environment: { type: 'string', enum: ['production', 'staging'] } }, required: ['environment'] } },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: ADMIN_TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const a = (args || {}) as Record<string, unknown>;
  const text = (t: string) => ({ content: [{ type: 'text' as const, text: t }] });

  switch (name) {
    case 'search_docs': {
      const query = String(a.query || '');
      const category = String(a.category || 'all');
      let paths: string[] = category === 'all' ? [...DOC_CATEGORIES] : [category];
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
      const endpoints = CONTAINERS.flatMap((c) => {
        const ip = env === 'staging' ? c.testIp : c.ip;
        return c.ports.map((p) => ({ service: p.service, container: c.name, ip, port: p.port, url: `http://${ip}:${p.port}` }));
      }).filter((e) => !service || e.service.toLowerCase().includes(service.toLowerCase()));
      return text(JSON.stringify({ environment: env, filter: service || 'all', endpoints }, null, 2));
    }

    case 'get_deployment_info': {
      const env = String(a.environment || 'staging');
      const invPath = join(PROJECT_ROOT, 'provision', 'ansible', 'inventory', env, 'group_vars', 'all', '00-main.yml');
      const content = safeReadFile(invPath);
      return text(content || `Deployment info not found for ${env}`);
    }

    case 'execute_proxmox_command': {
      const command = String(a.command || '');
      const confirm = a.confirm === true;
      if (isDestructiveCommand(command) && !confirm) {
        return text(JSON.stringify({ error: 'Destructive command requires confirm: true', command, hint: 'Add "confirm": true to the tool arguments for rm, reset, drop, force, etc.' }, null, 2));
      }
      const wd = String(a.working_directory || BUSIBOX_PATH_ON_PROXMOX);
      const timeout = Number(a.timeout) || 300000;
      try {
        const fullCmd = `cd ${wd} && ${command}`;
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, fullCmd, PROXMOX_SSH_KEY_PATH, timeout);
        return text(JSON.stringify({ command: fullCmd, exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, command }, null, 2));
      }
    }

    case 'get_container_logs': {
      const container = String(a.container || '');
      const service = String(a.service || '');
      const lines = Number(a.lines) || 50;
      const ip = getContainerIP(container) || container;
      try {
        const cmd = service ? `journalctl -u ${service} -n ${lines} --no-pager` : `journalctl -n ${lines} --no-pager`;
        const result = await executeSSHCommand(ip, 'root', cmd, CONTAINER_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ container, ip, service: service || 'all', lines, logs: result.stdout, error: result.stderr || undefined }, null, 2));
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
        return text(JSON.stringify({ container, ip, service, status: result.stdout, error: result.stderr || undefined }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, container, service }, null, 2));
      }
    }

    case 'git_pull_busibox': {
      const resetHard = a.reset_hard === true;
      const confirm = a.confirm === true;
      if (resetHard && !confirm) {
        return text(JSON.stringify({ error: 'reset_hard requires confirm: true', hint: 'Add "confirm": true when using reset_hard' }, null, 2));
      }
      const branch = String(a.branch || '');
      let commands: string;
      if (resetHard && branch) commands = `git fetch origin && git reset --hard origin/${branch}`;
      else if (resetHard) commands = 'git fetch origin && BRANCH=$(git rev-parse --abbrev-ref HEAD) && git reset --hard origin/$BRANCH';
      else if (branch) commands = `git checkout ${branch} && git pull origin ${branch}`;
      else commands = 'git pull';
      try {
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, `cd ${BUSIBOX_PATH_ON_PROXMOX} && ${commands}`, PROXMOX_SSH_KEY_PATH, 60000);
        return text(JSON.stringify({ action: resetHard ? 'reset --hard' : 'pull', branch: branch || '(current)', exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message }, null, 2));
      }
    }

    case 'git_status': {
      try {
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, `cd ${BUSIBOX_PATH_ON_PROXMOX} && git status && echo "---" && git log -1 --oneline`, PROXMOX_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ path: BUSIBOX_PATH_ON_PROXMOX, output: result.stdout, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message }, null, 2));
      }
    }

    case 'run_make_target': {
      const target = String(a.target || '');
      const env = String(a.environment || 'staging');
      const confirm = a.confirm === true;
      if (isDestructiveMakeTarget(target) && !confirm) {
        return text(JSON.stringify({ error: 'Destructive make target requires confirm: true', target, hint: 'Add "confirm": true for docker-clean, docker-clean-all, vault-migrate' }, null, 2));
      }
      if (!MAKE_TARGETS[target]) return text(JSON.stringify({ error: `Unknown target: ${target}`, available: Object.keys(MAKE_TARGETS) }, null, 2));
      const inv = env === 'staging' ? 'INV=inventory/staging' : '';
      const extra = a.extra_args ? `EXTRA_ARGS="${a.extra_args}"` : '';
      const timeout = Number(a.timeout) || 600000;
      const makeCmd = `make ${target} ${inv} ${extra}`.trim();
      try {
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, `cd ${BUSIBOX_PATH_ON_PROXMOX}/provision/ansible && ${makeCmd}`, PROXMOX_SSH_KEY_PATH, timeout);
        return text(JSON.stringify({ target, environment: env, command: makeCmd, exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, target, environment: env }, null, 2));
      }
    }

    case 'list_make_targets': {
      const cat = String(a.category || 'all');
      const filtered = cat === 'all' ? MAKE_TARGETS : Object.fromEntries(Object.entries(MAKE_TARGETS).filter(([, i]) => i.category === cat));
      const byCategory: Record<string, Array<{ target: string; description: string }>> = {};
      for (const [t, info] of Object.entries(filtered)) {
        if (!byCategory[info.category]) byCategory[info.category] = [];
        byCategory[info.category].push({ target: t, description: info.description });
      }
      return text(JSON.stringify({ filter: cat, targets: byCategory, usage: { production: 'make <target>', staging: 'make <target> INV=inventory/staging' } }, null, 2));
    }

    case 'check_environment_health': {
      const env = String(a.environment || 'staging');
      const inv = env === 'staging' ? 'INV=inventory/staging' : '';
      try {
        const result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, `cd ${BUSIBOX_PATH_ON_PROXMOX}/provision/ansible && make verify-health ${inv}`.trim(), PROXMOX_SSH_KEY_PATH, 120000);
        return text(JSON.stringify({ environment: env, exitCode: result.exitCode, output: result.stdout, error: result.stderr || undefined, healthy: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, environment: env }, null, 2));
      }
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

const ADMIN_PROMPTS = [
  { name: 'deploy_service', description: 'Deploy service', arguments: [{ name: 'service', required: true }, { name: 'environment', required: true }] },
  { name: 'deployment_workflow', description: 'Deployment workflow', arguments: [{ name: 'target', required: true }, { name: 'service', required: false }] },
  { name: 'update_and_deploy', description: 'Update and deploy', arguments: [{ name: 'environment', required: true }, { name: 'service', required: false }] },
  { name: 'troubleshoot_issue', description: 'Troubleshooting', arguments: [{ name: 'issue_type', required: true }] },
  { name: 'create_documentation', description: 'Create docs', arguments: [{ name: 'topic', required: true }] },
];

server.setRequestHandler(ListPromptsRequestSchema, async () => ({ prompts: ADMIN_PROMPTS }));

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
    case 'deploy_service':
      return msg(`Deploy ${a.service} to ${a.environment}`, `1. git_pull_busibox 2. run_make_target target="${a.service}" environment="${a.environment}" 3. get_container_service_status to verify`);
    case 'deployment_workflow':
      return msg(`Deploy to ${a.target}`, `Use run_make_target with target="${a.service || 'all'}" environment="${a.target}". For destructive ops add confirm: true.`);
    case 'update_and_deploy':
      return msg(`Update and deploy to ${a.environment}`, `1. git_pull_busibox 2. run_make_target target="${a.service || 'all'}" environment="${a.environment}" 3. check_environment_health`);
    case 'troubleshoot_issue':
      return msg(`Troubleshoot ${a.issue_type}`, 'Use get_container_logs, get_container_service_status. Search docs with search_docs.');
    case 'create_documentation':
      return msg(`Create doc for ${a.topic}`, 'Place in docs/administrators/ or docs/developers/ per audience. Use get_doc to read existing structure.');
    default:
      throw new Error(`Unknown prompt: ${name}`);
  }
});

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Busibox MCP Administrator Server v1.0.0 running on stdio');
}

main().catch((e) => {
  console.error('Fatal:', e);
  process.exit(1);
});
