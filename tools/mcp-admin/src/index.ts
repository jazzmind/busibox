#!/usr/bin/env node

/**
 * Busibox MCP Administrator Server
 *
 * Supports three deployment models:
 *   - proxmox: SSH into Proxmox host, run make targets remotely
 *   - docker: Run make targets locally on the admin workstation
 *   - k8s: Run kubectl/make targets locally for Kubernetes clusters
 *
 * Vault password injection is handled for targets that require it.
 * Destructive operations require explicit confirm: true.
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
  BUSIBOX_LOCAL_PATH,
  DEFAULT_DEPLOYMENT_MODEL,
  K8S_OVERLAY,
  DOC_CATEGORIES,
  DOC_NESTED_PATHS,
  CONTAINERS,
  ANSIBLE_MAKE_TARGETS,
  MAIN_MAKEFILE_TARGETS,
  getAllMakeTargets,
  getDocsByCategory,
  searchDocs,
  safeReadFile,
  getContainer,
  getContainerIP,
  executeSSHCommand,
  executeLocalCommand,
} from '@busibox/mcp-shared';
import type { DeploymentModel, MakeTargetInfo } from '@busibox/mcp-shared';
import { isDestructiveCommand, isDestructiveMakeTarget } from './destructive.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PROJECT_ROOT = join(__dirname, '..', '..', '..');

const ADMIN_DOC_CATEGORIES = ['administrators'] as const;

let storedVaultPassword: string | null = null;

/**
 * Execute a command on the appropriate target based on deployment model.
 * For proxmox: SSH to host. For docker/k8s: run locally.
 */
async function executeCommand(
  command: string,
  cwd: string,
  timeout: number,
  deploymentModel: DeploymentModel,
  env?: Record<string, string>
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  if (deploymentModel === 'proxmox') {
    const fullCmd = `cd ${cwd} && ${command}`;
    return executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, fullCmd, PROXMOX_SSH_KEY_PATH, timeout);
  }
  return executeLocalCommand(command, cwd, timeout, env);
}

/**
 * Build environment variables for a make command, including vault password if needed.
 */
function buildMakeEnv(vaultPassword?: string): Record<string, string> {
  const env: Record<string, string> = {};
  const pw = vaultPassword || storedVaultPassword;
  if (pw) {
    env.ANSIBLE_VAULT_PASSWORD = pw;
  }
  return env;
}

/**
 * Get the working directory for a make target based on which Makefile it belongs to.
 */
function getMakeCwd(target: MakeTargetInfo, deploymentModel: DeploymentModel): string {
  const basePath = deploymentModel === 'proxmox' ? BUSIBOX_PATH_ON_PROXMOX : BUSIBOX_LOCAL_PATH;
  if (target.makefile === 'ansible') {
    return join(basePath, 'provision', 'ansible');
  }
  return basePath;
}

const server = new Server(
  { name: 'busibox-mcp-admin', version: '2.0.0' },
  { capabilities: { resources: {}, tools: {}, prompts: {} } }
);

// ── Resources ──────────────────────────────────────────────────────

server.setRequestHandler(ListResourcesRequestSchema, async () => ({
  resources: [
    { uri: 'busibox://docs/administrators', mimeType: 'text/markdown', name: 'Administrator Docs', description: 'Deployment and operations' },
    { uri: 'busibox://containers', mimeType: 'application/json', name: 'Container Map', description: 'Container IPs and services (Proxmox only)' },
    { uri: 'busibox://make-targets', mimeType: 'application/json', name: 'Make Targets', description: 'All deployment targets across deployment models' },
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
      note: 'Container IPs are for Proxmox LXC deployments only. Docker uses localhost, K8s uses service names.',
      production: CONTAINERS.map((c) => ({ id: c.id, name: c.name, ip: c.ip, purpose: c.purpose, services: c.services })),
      staging: CONTAINERS.map((c) => ({ id: c.testId, name: c.name, ip: c.testIp, purpose: c.purpose, services: c.services })),
    };
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify(data, null, 2) }] };
  }

  if (uri === 'busibox://make-targets') {
    const allTargets = getAllMakeTargets();
    const byCategory: Record<string, Record<string, MakeTargetInfo>> = {};
    for (const [target, info] of Object.entries(allTargets)) {
      if (!byCategory[info.category]) byCategory[info.category] = {};
      byCategory[info.category][target] = info;
    }
    return { contents: [{ uri, mimeType: 'application/json', text: JSON.stringify({ targets: allTargets, byCategory }, null, 2) }] };
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

// ── Tools ──────────────────────────────────────────────────────────

const DEPLOYMENT_MODEL_PROP = {
  type: 'string',
  enum: ['proxmox', 'docker', 'k8s'],
  description: `Deployment model. Defaults to ${DEFAULT_DEPLOYMENT_MODEL}. proxmox=SSH to host, docker=local commands, k8s=kubectl/local make`,
};

const ADMIN_TOOLS = [
  { name: 'search_docs', description: 'Search administrator docs', inputSchema: { type: 'object', properties: { query: { type: 'string' }, category: { type: 'string', enum: [...ADMIN_DOC_CATEGORIES, 'all'] } }, required: ['query'] } },
  { name: 'get_doc', description: 'Get doc content', inputSchema: { type: 'object', properties: { path: { type: 'string' } }, required: ['path'] } },
  {
    name: 'set_vault_password',
    description: 'Store the Ansible vault password for this session. Required before running make targets that need vault access (deployment, secrets). The password is held in memory only.',
    inputSchema: {
      type: 'object',
      properties: {
        vault_password: { type: 'string', description: 'The Ansible vault password' },
      },
      required: ['vault_password'],
    },
  },
  {
    name: 'get_deployment_status',
    description: 'Get current MCP server configuration: deployment model, vault password status, connection info',
    inputSchema: { type: 'object', properties: {} },
  },
  { name: 'list_containers', description: 'List Proxmox LXC containers (Proxmox deployment model only)', inputSchema: { type: 'object', properties: {} } },
  { name: 'get_container_info', description: 'Get Proxmox container details', inputSchema: { type: 'object', properties: { container: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } }, required: ['container'] } },
  { name: 'get_service_endpoints', description: 'Get service IPs/ports (Proxmox only)', inputSchema: { type: 'object', properties: { service: { type: 'string' }, environment: { type: 'string', enum: ['production', 'staging'] } } } },
  { name: 'get_deployment_info', description: 'Get environment config', inputSchema: { type: 'object', properties: { environment: { type: 'string', enum: ['staging', 'production'] } }, required: ['environment'] } },
  {
    name: 'execute_command',
    description: 'Execute a shell command on the deployment target (Proxmox: via SSH, Docker/K8s: locally). Destructive commands require confirm: true.',
    inputSchema: {
      type: 'object',
      properties: {
        command: { type: 'string' },
        working_directory: { type: 'string' },
        timeout: { type: 'number' },
        deployment_model: DEPLOYMENT_MODEL_PROP,
        confirm: { type: 'boolean', description: 'Required for destructive commands (rm, reset, drop, force, etc.)' },
      },
      required: ['command'],
    },
  },
  {
    name: 'get_container_logs',
    description: 'Get service logs. Proxmox: journalctl via SSH. Docker: docker compose logs. K8s: kubectl logs.',
    inputSchema: {
      type: 'object',
      properties: {
        container: { type: 'string', description: 'Container name (Proxmox) or service name (Docker/K8s)' },
        service: { type: 'string', description: 'systemd service name (Proxmox only)' },
        lines: { type: 'number' },
        deployment_model: DEPLOYMENT_MODEL_PROP,
      },
      required: ['container'],
    },
  },
  {
    name: 'get_container_service_status',
    description: 'Get service status. Proxmox: systemctl via SSH. Docker: docker compose ps. K8s: kubectl get pods.',
    inputSchema: {
      type: 'object',
      properties: {
        container: { type: 'string', description: 'Container name (Proxmox) or service name (Docker/K8s)' },
        service: { type: 'string' },
        deployment_model: DEPLOYMENT_MODEL_PROP,
      },
      required: ['container'],
    },
  },
  {
    name: 'git_pull_busibox',
    description: 'Pull latest code. Proxmox: on remote host. Docker/K8s: locally. reset_hard requires confirm: true.',
    inputSchema: {
      type: 'object',
      properties: {
        branch: { type: 'string' },
        reset_hard: { type: 'boolean' },
        confirm: { type: 'boolean', description: 'Required when reset_hard is true' },
        deployment_model: DEPLOYMENT_MODEL_PROP,
      },
    },
  },
  {
    name: 'git_status',
    description: 'Git status of the busibox repo on the deployment target',
    inputSchema: {
      type: 'object',
      properties: { deployment_model: DEPLOYMENT_MODEL_PROP },
    },
  },
  {
    name: 'run_make_target',
    description: 'Run a make target. Automatically selects the correct Makefile (root or provision/ansible) and injects vault password if needed. Use list_make_targets to see available targets for your deployment model.',
    inputSchema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Make target name (e.g. install, manage, k8s-deploy, authz, docker-up)' },
        service: { type: 'string', description: 'SERVICE= value (e.g. authz, agent, all)' },
        action: { type: 'string', description: 'ACTION= value for manage target (start, stop, restart, logs, status, redeploy)' },
        environment: { type: 'string', enum: ['production', 'staging'] },
        vault_password: { type: 'string', description: 'Vault password for this command (overrides stored password)' },
        extra_args: { type: 'string', description: 'Additional arguments to pass to make' },
        timeout: { type: 'number' },
        deployment_model: DEPLOYMENT_MODEL_PROP,
        confirm: { type: 'boolean', description: 'Required for destructive targets (docker-clean, k8s-delete, etc.)' },
      },
      required: ['target'],
    },
  },
  {
    name: 'list_make_targets',
    description: 'List available make targets, optionally filtered by category or deployment model',
    inputSchema: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['deployment', 'app-deployment', 'verification', 'testing', 'configuration', 'docker', 'k8s', 'deploy', 'test', 'menu', 'setup', 'mcp', 'all'] },
        deployment_model: DEPLOYMENT_MODEL_PROP,
      },
    },
  },
  {
    name: 'check_environment_health',
    description: 'Check service health. Proxmox: verify-health via Ansible. Docker: docker-ps. K8s: k8s-status.',
    inputSchema: {
      type: 'object',
      properties: {
        environment: { type: 'string', enum: ['production', 'staging'] },
        deployment_model: DEPLOYMENT_MODEL_PROP,
      },
    },
  },
];

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: ADMIN_TOOLS }));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const a = (args || {}) as Record<string, unknown>;
  const text = (t: string) => ({ content: [{ type: 'text' as const, text: t }] });
  const dm = (a.deployment_model as DeploymentModel) || DEFAULT_DEPLOYMENT_MODEL;
  const busiboxPath = dm === 'proxmox' ? BUSIBOX_PATH_ON_PROXMOX : BUSIBOX_LOCAL_PATH;

  switch (name) {
    // ── Docs ──────────────────────────────────────────────────────
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

    // ── Vault & Status ────────────────────────────────────────────
    case 'set_vault_password': {
      const pw = String(a.vault_password || '');
      if (!pw) return text(JSON.stringify({ error: 'vault_password is required' }));
      storedVaultPassword = pw;
      return text(JSON.stringify({ success: true, message: 'Vault password stored for this session. It will be injected into make targets that require vault access.' }));
    }

    case 'get_deployment_status': {
      return text(JSON.stringify({
        deployment_model: DEFAULT_DEPLOYMENT_MODEL,
        vault_password_set: !!storedVaultPassword,
        proxmox_host: PROXMOX_HOST_IP,
        local_path: BUSIBOX_LOCAL_PATH,
        k8s_overlay: K8S_OVERLAY,
        note: 'Use set_vault_password before running targets that require vault access. Use deployment_model parameter on individual tools to override the default.',
      }, null, 2));
    }

    // ── Containers (Proxmox) ──────────────────────────────────────
    case 'list_containers': {
      if (dm !== 'proxmox') {
        return text(JSON.stringify({
          note: `Container listing is for Proxmox deployments. Current model: ${dm}`,
          docker_hint: 'Use run_make_target with target="docker-ps" for Docker',
          k8s_hint: 'Use run_make_target with target="k8s-status" for Kubernetes',
        }, null, 2));
      }
      return text(JSON.stringify({
        production: CONTAINERS.map((c) => ({ id: c.id, name: c.name, ip: c.ip, purpose: c.purpose, services: c.services })),
        staging: CONTAINERS.map((c) => ({ id: c.testId, name: c.name, ip: c.testIp, purpose: c.purpose, services: c.services })),
      }, null, 2));
    }

    case 'get_container_info': {
      const container = getContainer(String(a.container || ''));
      const env = (a.environment as 'production' | 'staging') || 'production';
      if (!container) return text(JSON.stringify({ error: 'Container not found', available: CONTAINERS.map((c) => c.name) }, null, 2));
      return text(JSON.stringify({ name: container.name, id: env === 'staging' ? container.testId : container.id, ip: env === 'staging' ? container.testIp : container.ip, purpose: container.purpose, ports: container.ports, services: container.services }, null, 2));
    }

    case 'get_service_endpoints': {
      const service = String(a.service || '');
      const env = (a.environment as 'production' | 'staging') || 'production';
      if (dm === 'docker') {
        return text(JSON.stringify({
          note: 'Docker services run on localhost. Check docker-compose.yml for port mappings.',
          hint: 'Use run_make_target with target="docker-ps" to see running services',
        }, null, 2));
      }
      if (dm === 'k8s') {
        return text(JSON.stringify({
          note: 'K8s services use ClusterIP or NodePort. Use k8s-status to see service addresses.',
          hint: 'Use run_make_target with target="k8s-status" to see service status',
        }, null, 2));
      }
      const endpoints = CONTAINERS.flatMap((c) => {
        const ip = env === 'staging' ? c.testIp : c.ip;
        return c.ports.map((p) => ({ service: p.service, container: c.name, ip, port: p.port, url: `http://${ip}:${p.port}` }));
      }).filter((e) => !service || e.service.toLowerCase().includes(service.toLowerCase()));
      return text(JSON.stringify({ environment: env, filter: service || 'all', endpoints }, null, 2));
    }

    case 'get_deployment_info': {
      const env = String(a.environment || 'staging');
      if (dm === 'docker') {
        const envFile = safeReadFile(join(BUSIBOX_LOCAL_PATH, '.env'));
        const composeFile = safeReadFile(join(BUSIBOX_LOCAL_PATH, 'docker-compose.yml'));
        return text(JSON.stringify({
          deployment_model: 'docker',
          environment: env,
          env_file: envFile ? '(loaded)' : 'not found',
          compose_file: composeFile ? '(present)' : 'not found',
          path: BUSIBOX_LOCAL_PATH,
        }, null, 2));
      }
      const invPath = join(PROJECT_ROOT, 'provision', 'ansible', 'inventory', env, 'group_vars', 'all', '00-main.yml');
      const content = safeReadFile(invPath);
      return text(content || `Deployment info not found for ${env}`);
    }

    // ── Command Execution ─────────────────────────────────────────
    case 'execute_command': {
      const command = String(a.command || '');
      const confirm = a.confirm === true;
      if (isDestructiveCommand(command) && !confirm) {
        return text(JSON.stringify({ error: 'Destructive command requires confirm: true', command, hint: 'Add "confirm": true to the tool arguments for rm, reset, drop, force, etc.' }, null, 2));
      }
      const wd = String(a.working_directory || busiboxPath);
      const timeout = Number(a.timeout) || 300000;
      try {
        const result = await executeCommand(command, wd, timeout, dm);
        return text(JSON.stringify({ deployment_model: dm, command, exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, command, deployment_model: dm }, null, 2));
      }
    }

    // ── Logs ──────────────────────────────────────────────────────
    case 'get_container_logs': {
      const container = String(a.container || '');
      const service = String(a.service || '');
      const lines = Number(a.lines) || 50;

      if (dm === 'docker') {
        try {
          const cmd = `docker compose logs --tail=${lines} ${container}`;
          const result = await executeLocalCommand(cmd, BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'docker', container, lines, logs: result.stdout, error: result.stderr || undefined }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, container, deployment_model: 'docker' }, null, 2));
        }
      }

      if (dm === 'k8s') {
        try {
          const kubecfg = K8S_OVERLAY ? `--kubeconfig k8s/kubeconfig-${K8S_OVERLAY}.yaml` : '';
          const cmd = `kubectl ${kubecfg} logs -l app=${container} --tail=${lines} -n busibox 2>/dev/null || kubectl ${kubecfg} logs deployment/${container} --tail=${lines} -n busibox`;
          const result = await executeLocalCommand(cmd, BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'k8s', container, lines, logs: result.stdout, error: result.stderr || undefined }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, container, deployment_model: 'k8s' }, null, 2));
        }
      }

      // Proxmox
      const ip = getContainerIP(container) || container;
      try {
        const cmd = service ? `journalctl -u ${service} -n ${lines} --no-pager` : `journalctl -n ${lines} --no-pager`;
        const result = await executeSSHCommand(ip, 'root', cmd, CONTAINER_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ deployment_model: 'proxmox', container, ip, service: service || 'all', lines, logs: result.stdout, error: result.stderr || undefined }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, container, service, deployment_model: 'proxmox' }, null, 2));
      }
    }

    // ── Service Status ────────────────────────────────────────────
    case 'get_container_service_status': {
      const container = String(a.container || '');
      const service = String(a.service || '');

      if (dm === 'docker') {
        try {
          const cmd = container ? `docker compose ps ${container}` : 'docker compose ps';
          const result = await executeLocalCommand(cmd, BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'docker', container, status: result.stdout, error: result.stderr || undefined }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, container, deployment_model: 'docker' }, null, 2));
        }
      }

      if (dm === 'k8s') {
        try {
          const kubecfg = K8S_OVERLAY ? `--kubeconfig k8s/kubeconfig-${K8S_OVERLAY}.yaml` : '';
          const cmd = container
            ? `kubectl ${kubecfg} get pods -l app=${container} -n busibox -o wide 2>/dev/null || kubectl ${kubecfg} get pods -n busibox | grep ${container}`
            : `kubectl ${kubecfg} get pods -n busibox -o wide`;
          const result = await executeLocalCommand(cmd, BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'k8s', container, status: result.stdout, error: result.stderr || undefined }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, container, deployment_model: 'k8s' }, null, 2));
        }
      }

      // Proxmox
      const ip = getContainerIP(container) || container;
      try {
        const cmd = service
          ? `systemctl status ${service} --no-pager -l`
          : 'systemctl list-units --type=service --state=running --no-pager';
        const result = await executeSSHCommand(ip, 'root', cmd, CONTAINER_SSH_KEY_PATH, 30000);
        return text(JSON.stringify({ deployment_model: 'proxmox', container, ip, service, status: result.stdout, error: result.stderr || undefined }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, container, service, deployment_model: 'proxmox' }, null, 2));
      }
    }

    // ── Git ────────────────────────────────────────────────────────
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
        const result = await executeCommand(commands, busiboxPath, 60000, dm);
        return text(JSON.stringify({ deployment_model: dm, action: resetHard ? 'reset --hard' : 'pull', branch: branch || '(current)', exitCode: result.exitCode, stdout: result.stdout, stderr: result.stderr, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, deployment_model: dm }, null, 2));
      }
    }

    case 'git_status': {
      try {
        const result = await executeCommand('git status && echo "---" && git log -1 --oneline', busiboxPath, 30000, dm);
        return text(JSON.stringify({ deployment_model: dm, path: busiboxPath, output: result.stdout, success: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, deployment_model: dm }, null, 2));
      }
    }

    // ── Make Targets ──────────────────────────────────────────────
    case 'run_make_target': {
      const target = String(a.target || '');
      const confirm = a.confirm === true;

      if (isDestructiveMakeTarget(target) && !confirm) {
        return text(JSON.stringify({ error: 'Destructive make target requires confirm: true', target, hint: 'Add "confirm": true for docker-clean, k8s-delete, etc.' }, null, 2));
      }

      const allTargets = getAllMakeTargets();
      const targetInfo = allTargets[target];
      if (!targetInfo) {
        const available = Object.entries(allTargets)
          .filter(([, info]) => !info.deploymentModels || info.deploymentModels.includes(dm))
          .map(([name]) => name);
        return text(JSON.stringify({ error: `Unknown target: ${target}`, deployment_model: dm, available_targets: available.slice(0, 30), total: available.length }, null, 2));
      }

      if (targetInfo.deploymentModels && !targetInfo.deploymentModels.includes(dm)) {
        return text(JSON.stringify({
          error: `Target "${target}" is not available for deployment model "${dm}"`,
          available_models: targetInfo.deploymentModels,
          hint: `This target is for ${targetInfo.deploymentModels.join('/')} deployments`,
        }, null, 2));
      }

      const vaultPw = String(a.vault_password || '') || storedVaultPassword;
      if (targetInfo.requiresVault && !vaultPw) {
        return text(JSON.stringify({
          error: 'This target requires the Ansible vault password',
          target,
          hint: 'Call set_vault_password first, or pass vault_password in this request. The vault password decrypts secrets needed for deployment.',
        }, null, 2));
      }

      const env = String(a.environment || '');
      const serviceArg = a.service ? `SERVICE=${a.service}` : '';
      const actionArg = a.action ? `ACTION=${a.action}` : '';
      const inv = env === 'staging' ? 'INV=inventory/staging' : '';
      const k8sOverlay = dm === 'k8s' && target.startsWith('k8s-') ? `K8S_OVERLAY=${K8S_OVERLAY}` : '';
      const extra = a.extra_args ? String(a.extra_args) : '';
      const timeout = Number(a.timeout) || 600000;

      const makeParts = ['make', target, serviceArg, actionArg, inv, k8sOverlay, extra].filter(Boolean);
      const makeCmd = makeParts.join(' ');
      const cwd = getMakeCwd(targetInfo, dm);
      const makeEnv = buildMakeEnv(vaultPw || undefined);

      try {
        let result;
        if (dm === 'proxmox') {
          const envPrefix = makeEnv.ANSIBLE_VAULT_PASSWORD
            ? `export ANSIBLE_VAULT_PASSWORD='${makeEnv.ANSIBLE_VAULT_PASSWORD.replace(/'/g, "'\\''")}' && `
            : '';
          const fullCmd = `cd ${cwd} && ${envPrefix}${makeCmd}`;
          result = await executeSSHCommand(PROXMOX_HOST_IP, PROXMOX_HOST_USER, fullCmd, PROXMOX_SSH_KEY_PATH, timeout);
        } else {
          result = await executeLocalCommand(makeCmd, cwd, timeout, makeEnv);
        }

        return text(JSON.stringify({
          deployment_model: dm,
          target,
          makefile: targetInfo.makefile || 'root',
          environment: env || '(default)',
          command: makeCmd,
          exitCode: result.exitCode,
          stdout: result.stdout,
          stderr: result.stderr,
          success: result.exitCode === 0,
        }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, target, deployment_model: dm }, null, 2));
      }
    }

    case 'list_make_targets': {
      const cat = String(a.category || 'all');
      const allTargets = getAllMakeTargets();

      const filtered = Object.entries(allTargets).filter(([, info]) => {
        if (cat !== 'all' && info.category !== cat) return false;
        if (info.deploymentModels && !info.deploymentModels.includes(dm)) return false;
        return true;
      });

      const byCategory: Record<string, Array<{ target: string; description: string; requires_vault: boolean; makefile: string }>> = {};
      for (const [t, info] of filtered) {
        if (!byCategory[info.category]) byCategory[info.category] = [];
        byCategory[info.category].push({
          target: t,
          description: info.description,
          requires_vault: !!info.requiresVault,
          makefile: info.makefile || 'root',
        });
      }

      return text(JSON.stringify({
        deployment_model: dm,
        filter: cat,
        vault_password_set: !!storedVaultPassword,
        targets: byCategory,
        usage: {
          note: 'Targets with requires_vault=true need vault password. Call set_vault_password first.',
          production: 'run_make_target target="<target>"',
          staging: 'run_make_target target="<target>" environment="staging"',
        },
      }, null, 2));
    }

    // ── Health Check ──────────────────────────────────────────────
    case 'check_environment_health': {
      const env = String(a.environment || 'staging');

      if (dm === 'docker') {
        try {
          const result = await executeLocalCommand('docker compose ps', BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'docker', output: result.stdout, error: result.stderr || undefined, healthy: result.exitCode === 0 }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, deployment_model: 'docker' }, null, 2));
        }
      }

      if (dm === 'k8s') {
        try {
          const kubecfg = K8S_OVERLAY ? `--kubeconfig k8s/kubeconfig-${K8S_OVERLAY}.yaml` : '';
          const cmd = `kubectl ${kubecfg} get pods -n busibox -o wide && echo "---" && kubectl ${kubecfg} get svc -n busibox`;
          const result = await executeLocalCommand(cmd, BUSIBOX_LOCAL_PATH, 30000);
          return text(JSON.stringify({ deployment_model: 'k8s', output: result.stdout, error: result.stderr || undefined, healthy: result.exitCode === 0 }, null, 2));
        } catch (e: unknown) {
          return text(JSON.stringify({ error: (e as Error).message, deployment_model: 'k8s' }, null, 2));
        }
      }

      // Proxmox
      const inv = env === 'staging' ? 'INV=inventory/staging' : '';
      try {
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP, PROXMOX_HOST_USER,
          `cd ${BUSIBOX_PATH_ON_PROXMOX}/provision/ansible && make verify-health ${inv}`.trim(),
          PROXMOX_SSH_KEY_PATH, 120000
        );
        return text(JSON.stringify({ deployment_model: 'proxmox', environment: env, exitCode: result.exitCode, output: result.stdout, error: result.stderr || undefined, healthy: result.exitCode === 0 }, null, 2));
      } catch (e: unknown) {
        return text(JSON.stringify({ error: (e as Error).message, environment: env, deployment_model: 'proxmox' }, null, 2));
      }
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

// ── Prompts ────────────────────────────────────────────────────────

const ADMIN_PROMPTS = [
  { name: 'deploy_service', description: 'Deploy a service to an environment', arguments: [{ name: 'service', required: true }, { name: 'environment', required: true }] },
  { name: 'deployment_workflow', description: 'Full deployment workflow', arguments: [{ name: 'target', required: true }, { name: 'service', required: false }] },
  { name: 'update_and_deploy', description: 'Pull latest code and deploy', arguments: [{ name: 'environment', required: true }, { name: 'service', required: false }] },
  { name: 'troubleshoot_issue', description: 'Troubleshoot a service issue', arguments: [{ name: 'issue_type', required: true }] },
  { name: 'create_documentation', description: 'Create documentation', arguments: [{ name: 'topic', required: true }] },
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
      return msg(
        `Deploy ${a.service} to ${a.environment}`,
        `1. get_deployment_status to check vault password\n2. set_vault_password if needed\n3. git_pull_busibox\n4. run_make_target target="install" service="${a.service}" environment="${a.environment}"\n5. check_environment_health`
      );
    case 'deployment_workflow':
      return msg(
        `Deploy to ${a.target}`,
        `1. get_deployment_status\n2. set_vault_password if not set\n3. run_make_target target="install" service="${a.service || 'all'}" environment="${a.target}"\n4. check_environment_health\nFor destructive ops add confirm: true.`
      );
    case 'update_and_deploy':
      return msg(
        `Update and deploy to ${a.environment}`,
        `1. get_deployment_status\n2. set_vault_password if not set\n3. git_pull_busibox\n4. run_make_target target="install" service="${a.service || 'all'}" environment="${a.environment}"\n5. check_environment_health`
      );
    case 'troubleshoot_issue':
      return msg(
        `Troubleshoot ${a.issue_type}`,
        '1. check_environment_health\n2. get_container_logs for affected service\n3. get_container_service_status\n4. search_docs for known issues'
      );
    case 'create_documentation':
      return msg(
        `Create doc for ${a.topic}`,
        'Place in docs/administrators/ or docs/developers/ per audience. Use get_doc to read existing structure.'
      );
    default:
      throw new Error(`Unknown prompt: ${name}`);
  }
});

// ── Main ───────────────────────────────────────────────────────────

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error(`Busibox MCP Administrator Server v2.0.0 running on stdio (default model: ${DEFAULT_DEPLOYMENT_MODEL})`);
}

main().catch((e) => {
  console.error('Fatal:', e);
  process.exit(1);
});
