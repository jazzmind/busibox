#!/usr/bin/env node

/**
 * Busibox MCP Server
 * 
 * Provides Model Context Protocol (MCP) access to:
 * - Busibox documentation (organized by category)
 * - Script information and usage
 * - Project structure and organization rules
 * - Common maintenance tasks
 * - SSH command execution on Proxmox host and containers
 * - Git operations and Make targets
 * - Container and service information
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
import { readFileSync, readdirSync, statSync, existsSync } from 'fs';
import { join, dirname, relative } from 'path';
import { fileURLToPath } from 'url';
import { glob } from 'glob';
import { Client as SSHClient } from 'ssh2';
import { homedir } from 'os';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Project root is 3 levels up from dist/index.js: dist -> mcp-server -> tools -> busibox
const PROJECT_ROOT = join(__dirname, '..', '..', '..');

/**
 * Configuration
 */
const PROXMOX_HOST_IP = process.env.PROXMOX_HOST_IP || '10.96.200.1';
const PROXMOX_HOST_USER = process.env.PROXMOX_HOST_USER || 'root';
const PROXMOX_SSH_KEY_PATH = process.env.PROXMOX_SSH_KEY_PATH || join(homedir(), '.ssh', 'id_rsa');
const CONTAINER_SSH_KEY_PATH = process.env.CONTAINER_SSH_KEY_PATH || join(homedir(), '.ssh', 'id_rsa');
const BUSIBOX_PATH_ON_PROXMOX = process.env.BUSIBOX_PATH_ON_PROXMOX || '/root/busibox';

/**
 * Documentation categories - updated to reflect current docs structure
 * 
 * Current structure:
 * - docs/architecture/     - System design and architecture
 * - docs/deployment/       - Deployment procedures
 * - docs/development/      - Development docs (includes session-notes, troubleshooting, tasks)
 * - docs/guides/           - How-to guides (includes configuration, API guides)
 * - docs/reference/        - Reference documentation
 */
const DOC_CATEGORIES = [
  'architecture',
  'deployment',
  'development',
  'guides',
  'reference',
] as const;

/**
 * Nested documentation paths for deeper searches
 */
const DOC_NESTED_PATHS: Record<string, string[]> = {
  'development': [
    'development/session-notes',
    'development/troubleshooting',
    'development/tasks',
    'development/decisions',
    'development/reference',
  ],
  'guides': [
    'guides/agent-api',
    'guides/ingest-api',
    'guides/search-api',
    'guides/auth-api',
  ],
};

/**
 * Script directories and their execution contexts
 */
const SCRIPT_LOCATIONS = {
  'admin-workstation': 'scripts',
  'proxmox-host': 'provision/pct',
  'ansible-files': 'provision/ansible/roles/*/files',
  'ansible-templates': 'provision/ansible/roles/*/templates',
} as const;

/**
 * Complete container configuration with all details
 */
interface ContainerConfig {
  id: number;
  testId: number;
  name: string;
  ip: string;
  testIp: string;
  purpose: string;
  ports: { port: number; service: string }[];
  services: string[];
  notes?: string;
}

const CONTAINERS: ContainerConfig[] = [
  {
    id: 200,
    testId: 300,
    name: 'proxy-lxc',
    ip: '10.96.200.200',
    testIp: '10.96.201.200',
    purpose: 'nginx reverse proxy',
    ports: [
      { port: 80, service: 'HTTP' },
      { port: 443, service: 'HTTPS' },
    ],
    services: ['nginx'],
    notes: 'Fronts apps; terminates TLS in production',
  },
  {
    id: 201,
    testId: 301,
    name: 'apps-lxc',
    ip: '10.96.200.201',
    testIp: '10.96.201.201',
    purpose: 'Next.js apps (AI Portal, Agent Manager, etc.)',
    ports: [{ port: 3000, service: 'Next.js apps (proxied via proxy-lxc)' }],
    services: ['nginx', 'ai-portal', 'agent-manager', 'doc-intel', 'foundation', 'project-analysis', 'innovation'],
    notes: 'No direct access to ingest/search; proxies internal calls',
  },
  {
    id: 202,
    testId: 302,
    name: 'agent-lxc',
    ip: '10.96.200.202',
    testIp: '10.96.201.202',
    purpose: 'Agent API (FastAPI)',
    ports: [{ port: 8000, service: 'Agent API' }],
    services: ['agent-api'],
    notes: 'Calls search + liteLLM for AI operations',
  },
  {
    id: 203,
    testId: 303,
    name: 'pg-lxc',
    ip: '10.96.200.203',
    testIp: '10.96.201.203',
    purpose: 'PostgreSQL database',
    ports: [{ port: 5432, service: 'PostgreSQL' }],
    services: ['postgresql'],
    notes: 'RLS policies enforced; ingest/search/authz write here',
  },
  {
    id: 204,
    testId: 304,
    name: 'milvus-lxc',
    ip: '10.96.200.204',
    testIp: '10.96.201.204',
    purpose: 'Milvus vector DB + Search API',
    ports: [
      { port: 19530, service: 'Milvus' },
      { port: 9091, service: 'Milvus health' },
      { port: 8003, service: 'Search API' },
    ],
    services: ['milvus', 'search-api'],
    notes: 'Stores document embeddings; partitioned by user/role',
  },
  {
    id: 205,
    testId: 305,
    name: 'files-lxc',
    ip: '10.96.200.205',
    testIp: '10.96.201.205',
    purpose: 'MinIO object storage (S3-compatible)',
    ports: [
      { port: 9000, service: 'MinIO S3 API' },
      { port: 9001, service: 'MinIO Console' },
    ],
    services: ['minio'],
    notes: 'Holds originals and derived artifacts',
  },
  {
    id: 206,
    testId: 306,
    name: 'ingest-lxc',
    ip: '10.96.200.206',
    testIp: '10.96.201.206',
    purpose: 'Ingestion API + worker + Redis',
    ports: [
      { port: 8000, service: 'Ingest API' },
      { port: 6379, service: 'Redis' },
    ],
    services: ['ingest-api', 'ingest-worker', 'redis'],
    notes: 'Internal-only API for upload/status/search/embeddings',
  },
  {
    id: 207,
    testId: 307,
    name: 'litellm-lxc',
    ip: '10.96.200.207',
    testIp: '10.96.201.207',
    purpose: 'LiteLLM gateway',
    ports: [{ port: 4000, service: 'LiteLLM' }],
    services: ['litellm'],
    notes: 'Fronts vLLM/Ollama/remote providers; used by ingest + search',
  },
  {
    id: 208,
    testId: 308,
    name: 'vllm-lxc',
    ip: '10.96.200.208',
    testIp: '10.96.201.208',
    purpose: 'vLLM inference server (GPU)',
    ports: [
      { port: 8000, service: 'vLLM (chat/completions)' },
      { port: 8001, service: 'vLLM embedding' },
      { port: 8002, service: 'ColPali visual' },
    ],
    services: ['vllm', 'vllm-embedding', 'colpali'],
    notes: 'GPU-capable local model serving; test env uses production vLLM by default',
  },
  {
    id: 209,
    testId: 309,
    name: 'ollama-lxc',
    ip: '10.96.200.209',
    testIp: '10.96.201.209',
    purpose: 'Ollama inference server',
    ports: [{ port: 11434, service: 'Ollama' }],
    services: ['ollama'],
    notes: 'Local model serving option',
  },
  {
    id: 210,
    testId: 310,
    name: 'authz-lxc',
    ip: '10.96.200.210',
    testIp: '10.96.201.210',
    purpose: 'AuthZ service (OAuth2/JWT)',
    ports: [{ port: 8010, service: 'AuthZ API' }],
    services: ['authz'],
    notes: 'Issues HS256 JWTs and records audit events',
  },
];

/**
 * Main Makefile targets (in /root/busibox/Makefile)
 * These are used with `make <target>` from the project root
 */
const MAIN_MAKEFILE_TARGETS: Record<string, {
  description: string;
  category: 'menu' | 'setup' | 'deploy' | 'test' | 'docker' | 'mcp';
  variables?: Record<string, string>;
  examples?: string[];
}> = {
  // Main menu
  'menu': {
    description: 'Interactive menu with environment selection and health checks (default)',
    category: 'menu',
    variables: { ENV: 'Environment: local, staging, production' },
    examples: ['make', 'make ENV=staging', 'make ENV=production'],
  },
  'help': {
    description: 'Show all available commands with examples',
    category: 'menu',
    examples: ['make help'],
  },
  
  // Setup & Configure
  'setup': {
    description: 'Initial setup - install dependencies (Ansible, etc.)',
    category: 'setup',
    examples: ['make setup'],
  },
  'configure': {
    description: 'Configure models, GPUs, secrets (interactive wizard)',
    category: 'setup',
    examples: ['make configure'],
  },
  
  // Deploy (Ansible-based)
  'deploy': {
    description: 'Deploy services via Ansible',
    category: 'deploy',
    variables: {
      SERVICE: 'Service to deploy (authz, ingest, search, agent, etc.)',
      INV: 'Inventory: staging or production',
    },
    examples: ['make deploy', 'make deploy SERVICE=authz INV=staging'],
  },
  
  // Testing
  'test': {
    description: 'Run tests on containers (via SSH)',
    category: 'test',
    variables: {
      SERVICE: 'Service to test: authz, ingest, search, agent, all',
      INV: 'Inventory: staging or production',
      MODE: 'Test mode: container (default)',
      ARGS: 'Extra pytest arguments',
    },
    examples: ['make test', 'make test SERVICE=agent INV=staging'],
  },
  'test-local': {
    description: 'Run tests locally against remote containers',
    category: 'test',
    variables: {
      SERVICE: 'Required: authz, ingest, search, agent, all',
      INV: 'Required: staging or production',
      FAST: 'Skip slow/GPU tests (default: 1)',
      WORKER: 'Start local ingest worker (default: 0)',
      ARGS: 'Extra pytest arguments',
    },
    examples: [
      'make test-local SERVICE=agent INV=staging',
      'make test-local SERVICE=all INV=production',
      "make test-local SERVICE=agent INV=staging ARGS='-k test_weather'",
    ],
  },
  'test-docker': {
    description: 'Run tests against local Docker services',
    category: 'test',
    variables: {
      SERVICE: 'Required: authz, ingest, search, agent, all',
      FAST: 'Skip slow/GPU tests (default: 1)',
      ARGS: 'Extra pytest arguments',
    },
    examples: [
      'make test-docker SERVICE=agent',
      'make test-docker SERVICE=all',
      "make test-docker SERVICE=agent ARGS='-k test_weather'",
      'make test-docker SERVICE=agent FAST=0  # Include slow tests',
    ],
  },
  'test-db-init': {
    description: 'Bootstrap test databases (schema + OAuth clients + signing keys)',
    category: 'test',
    examples: ['make test-db-init'],
  },
  'test-db-check': {
    description: 'Check if test databases are properly initialized',
    category: 'test',
    examples: ['make test-db-check'],
  },
  'test-security': {
    description: 'Run security tests',
    category: 'test',
    examples: ['make test-security'],
  },
  
  // Docker development
  'docker-up': {
    description: 'Start Docker services',
    category: 'docker',
    variables: { SERVICE: 'Optional: specific service to start' },
    examples: ['make docker-up', 'make docker-up SERVICE=authz-api'],
  },
  'docker-down': {
    description: 'Stop all Docker services',
    category: 'docker',
    examples: ['make docker-down'],
  },
  'docker-restart': {
    description: 'Restart Docker services',
    category: 'docker',
    variables: { SERVICE: 'Optional: specific service to restart' },
    examples: ['make docker-restart', 'make docker-restart SERVICE=authz-api'],
  },
  'docker-build': {
    description: 'Build Docker images',
    category: 'docker',
    variables: {
      SERVICE: 'Optional: specific service to build',
      NO_CACHE: 'Set to 1 to rebuild without cache',
    },
    examples: [
      'make docker-build',
      'make docker-build SERVICE=authz-api',
      'make docker-build NO_CACHE=1',
    ],
  },
  'docker-ps': {
    description: 'Show Docker service status',
    category: 'docker',
    examples: ['make docker-ps'],
  },
  'docker-logs': {
    description: 'View Docker logs',
    category: 'docker',
    variables: { SERVICE: 'Optional: specific service logs' },
    examples: ['make docker-logs', 'make docker-logs SERVICE=authz-api'],
  },
  'docker-clean': {
    description: 'Remove all containers and volumes (interactive confirmation)',
    category: 'docker',
    examples: ['make docker-clean'],
  },
  'ssl-check': {
    description: 'Check/generate SSL certificates for local development',
    category: 'docker',
    examples: ['make ssl-check'],
  },
  
  // MCP
  'mcp': {
    description: 'Build the MCP server for Cursor AI',
    category: 'mcp',
    examples: ['make mcp'],
  },
};

/**
 * Available Make targets and their descriptions (Ansible Makefile - provision/ansible/Makefile)
 */
const MAKE_TARGETS: Record<string, { description: string; category: string; requiresEnv?: boolean }> = {
  // Basic deployment
  'all': { description: 'Deploy all services', category: 'deployment', requiresEnv: true },
  'ping': { description: 'Ping all hosts to verify connectivity', category: 'verification', requiresEnv: true },
  
  // Service deployment
  'files': { description: 'Deploy MinIO file storage', category: 'deployment', requiresEnv: true },
  'pg': { description: 'Deploy PostgreSQL database', category: 'deployment', requiresEnv: true },
  'authz': { description: 'Deploy AuthZ service', category: 'deployment', requiresEnv: true },
  'litellm': { description: 'Deploy LiteLLM gateway', category: 'deployment', requiresEnv: true },
  'vllm': { description: 'Deploy vLLM inference server', category: 'deployment', requiresEnv: true },
  'vllm-embedding': { description: 'Deploy vLLM embedding model', category: 'deployment', requiresEnv: true },
  'colpali': { description: 'Deploy ColPali visual model', category: 'deployment', requiresEnv: true },
  'milvus': { description: 'Deploy Milvus vector database', category: 'deployment', requiresEnv: true },
  'nginx': { description: 'Deploy nginx reverse proxy', category: 'deployment', requiresEnv: true },
  'search': { description: 'Deploy Milvus + Search API', category: 'deployment', requiresEnv: true },
  'search-api': { description: 'Deploy Search API only', category: 'deployment', requiresEnv: true },
  'agent': { description: 'Deploy Agent API', category: 'deployment', requiresEnv: true },
  'ingest': { description: 'Deploy Ingest service', category: 'deployment', requiresEnv: true },
  'ingest-api': { description: 'Deploy Ingest API only', category: 'deployment', requiresEnv: true },
  'ingest-worker': { description: 'Deploy Ingest worker only', category: 'deployment', requiresEnv: true },
  'apps': { description: 'Deploy all Next.js apps', category: 'deployment', requiresEnv: true },
  
  // App deployment
  'deploy-apps': { description: 'Deploy all applications', category: 'app-deployment', requiresEnv: true },
  'deploy-ai-portal': { description: 'Deploy AI Portal app', category: 'app-deployment', requiresEnv: true },
  'deploy-agent-manager': { description: 'Deploy Agent Manager app', category: 'app-deployment', requiresEnv: true },
  'deploy-doc-intel': { description: 'Deploy Doc Intel app', category: 'app-deployment', requiresEnv: true },
  'deploy-foundation': { description: 'Deploy Foundation app', category: 'app-deployment', requiresEnv: true },
  'deploy-project-analysis': { description: 'Deploy Project Analysis app', category: 'app-deployment', requiresEnv: true },
  'deploy-innovation': { description: 'Deploy Innovation app', category: 'app-deployment', requiresEnv: true },
  
  // Verification
  'verify': { description: 'Run all verification checks', category: 'verification', requiresEnv: true },
  'verify-health': { description: 'Service health checks', category: 'verification', requiresEnv: true },
  'verify-smoke': { description: 'Database smoke tests', category: 'verification', requiresEnv: true },
  
  // Testing
  'test-all': { description: 'Run all service tests', category: 'testing', requiresEnv: true },
  'test-ingest': { description: 'Run ingest service tests', category: 'testing', requiresEnv: true },
  'test-ingest-all': { description: 'Run all ingest tests including integration', category: 'testing', requiresEnv: true },
  'test-ingest-coverage': { description: 'Run ingest tests with coverage', category: 'testing', requiresEnv: true },
  'test-search': { description: 'Run search service tests', category: 'testing', requiresEnv: true },
  'test-search-unit': { description: 'Run search unit tests only', category: 'testing', requiresEnv: true },
  'test-search-integration': { description: 'Run search integration tests', category: 'testing', requiresEnv: true },
  'test-search-coverage': { description: 'Run search tests with coverage', category: 'testing', requiresEnv: true },
  'test-agent': { description: 'Run agent service tests', category: 'testing', requiresEnv: true },
  'test-agent-unit': { description: 'Run agent unit tests only', category: 'testing', requiresEnv: true },
  'test-agent-integration': { description: 'Run agent integration tests', category: 'testing', requiresEnv: true },
  'test-agent-coverage': { description: 'Run agent tests with coverage', category: 'testing', requiresEnv: true },
  'test-authz': { description: 'Run authz service tests', category: 'testing', requiresEnv: true },
  'test-apps': { description: 'Run app tests', category: 'testing', requiresEnv: true },
  'test-security': { description: 'Run security tests', category: 'testing', requiresEnv: true },
  'test-extraction-simple': { description: 'Test simple PDF extraction', category: 'testing', requiresEnv: true },
  'test-extraction-llm': { description: 'Test LLM-enhanced extraction', category: 'testing', requiresEnv: true },
  'test-extraction-marker': { description: 'Test Marker extraction (GPU)', category: 'testing', requiresEnv: true },
  'test-extraction-colpali': { description: 'Test ColPali visual extraction', category: 'testing', requiresEnv: true },
  
  // Configuration
  'configure': { description: 'Run configuration wizard', category: 'configuration' },
  'generate-token-keys': { description: 'Generate token service keys', category: 'configuration' },
  'bootstrap-test-creds': { description: 'Bootstrap test credentials', category: 'configuration', requiresEnv: true },
};

/**
 * Helper: Read file with error handling
 */
function safeReadFile(path: string): string | null {
  try {
    return readFileSync(path, 'utf-8');
  } catch (error) {
    console.error(`Error reading file ${path}:`, error);
    return null;
  }
}

/**
 * Helper: List files in directory recursively
 */
function listFilesRecursive(dir: string, pattern: string = '*'): string[] {
  try {
    const fullPath = join(PROJECT_ROOT, dir);
    if (!existsSync(fullPath)) {
      return [];
    }
    return glob.sync(join(fullPath, '**', pattern), { nodir: true });
  } catch (error) {
    console.error(`Error listing files in ${dir}:`, error);
    return [];
  }
}

/**
 * Helper: Get documentation files by category or path
 * Supports both top-level categories (e.g., 'architecture') and 
 * nested paths (e.g., 'development/session-notes')
 */
function getDocsByCategory(categoryOrPath: string): Array<{ name: string; path: string }> {
  const docsDir = join(PROJECT_ROOT, 'docs', categoryOrPath);
  if (!existsSync(docsDir)) {
    return [];
  }

  const files = listFilesRecursive(`docs/${categoryOrPath}`, '*.md');
  return files.map(file => ({
    name: relative(docsDir, file),
    path: relative(PROJECT_ROOT, file),
  }));
}

/**
 * Helper: Get all scripts from a directory
 */
function getScriptsFromDir(dir: string): Array<{ name: string; path: string; executable: boolean }> {
  const fullPath = join(PROJECT_ROOT, dir);
  if (!existsSync(fullPath)) {
    return [];
  }

  const files = listFilesRecursive(dir, '*.{sh,py,js,ts}');
  return files.map(file => {
    const stats = statSync(file);
    return {
      name: relative(fullPath, file),
      path: relative(PROJECT_ROOT, file),
      executable: (stats.mode & 0o111) !== 0,
    };
  });
}

/**
 * Helper: Extract script header information
 */
function extractScriptInfo(scriptPath: string): {
  purpose?: string;
  context?: string;
  privileges?: string;
  dependencies?: string[];
  usage?: string;
} {
  const fullPath = join(PROJECT_ROOT, scriptPath);
  const content = safeReadFile(fullPath);
  if (!content) return {};

  const info: ReturnType<typeof extractScriptInfo> = {};
  const lines = content.split('\n').slice(0, 50); // Only check first 50 lines

  for (const line of lines) {
    if (line.includes('# Purpose:')) {
      info.purpose = line.replace(/^.*# Purpose:\s*/, '').trim();
    } else if (line.includes('# Execution Context:')) {
      info.context = line.replace(/^.*# Execution Context:\s*/, '').trim();
    } else if (line.includes('# Required Privileges:')) {
      info.privileges = line.replace(/^.*# Required Privileges:\s*/, '').trim();
    } else if (line.includes('# Dependencies:')) {
      info.dependencies = line
        .replace(/^.*# Dependencies:\s*/, '')
        .split(',')
        .map(d => d.trim());
    } else if (line.includes('# Usage:')) {
      const usageStart = lines.indexOf(line);
      const usageLines = [];
      for (let i = usageStart + 1; i < lines.length; i++) {
        if (lines[i].startsWith('#   ')) {
          usageLines.push(lines[i].replace(/^#\s*/, ''));
        } else if (!lines[i].startsWith('#')) {
          break;
        }
      }
      info.usage = usageLines.join('\n');
    }
  }

  return info;
}

/**
 * SSH Helper Functions
 */

/**
 * Read SSH private key from file
 */
function readSSHKey(keyPath: string): string | null {
  try {
    return readFileSync(keyPath, 'utf-8');
  } catch (error) {
    console.error(`Error reading SSH key from ${keyPath}:`, error);
    return null;
  }
}

/**
 * Execute SSH command on remote host
 */
async function executeSSHCommand(
  host: string,
  user: string,
  command: string,
  keyPath: string,
  timeout: number = 300000
): Promise<{ stdout: string; stderr: string; exitCode: number }> {
  return new Promise((resolve, reject) => {
    const conn = new SSHClient();
    let stdout = '';
    let stderr = '';
    let exitCode = 0;

    const timeoutHandle = setTimeout(() => {
      conn.end();
      reject(new Error(`SSH command timed out after ${timeout}ms`));
    }, timeout);

    conn.on('ready', () => {
      conn.exec(command, (err: Error | null | undefined, stream?: any) => {
        if (err) {
          clearTimeout(timeoutHandle);
          conn.end();
          reject(err);
          return;
        }

        if (!stream) {
          clearTimeout(timeoutHandle);
          conn.end();
          reject(new Error('Failed to create command stream'));
          return;
        }

        stream.on('close', (code: number) => {
          clearTimeout(timeoutHandle);
          conn.end();
          exitCode = code || 0;
          resolve({ stdout, stderr, exitCode });
        });

        stream.on('data', (data: Buffer) => {
          stdout += data.toString();
        });

        stream.stderr.on('data', (data: Buffer) => {
          stderr += data.toString();
        });
      });
    });

    conn.on('error', (err: Error) => {
      clearTimeout(timeoutHandle);
      reject(err);
    });

    const privateKey = readSSHKey(keyPath);
    if (!privateKey) {
      clearTimeout(timeoutHandle);
      reject(new Error(`Failed to read SSH key from ${keyPath}`));
      return;
    }

    conn.connect({
      host,
      port: 22,
      username: user,
      privateKey,
      readyTimeout: 10000,
    });
  });
}

/**
 * Get container by name or ID
 */
function getContainer(nameOrId: string): ContainerConfig | null {
  const normalized = nameOrId.toLowerCase().replace(/-lxc$/, '');
  
  // Try by name
  const byName = CONTAINERS.find(c => 
    c.name.toLowerCase() === nameOrId.toLowerCase() ||
    c.name.toLowerCase() === `${normalized}-lxc`
  );
  if (byName) return byName;
  
  // Try by ID
  const id = parseInt(nameOrId, 10);
  if (!isNaN(id)) {
    return CONTAINERS.find(c => c.id === id || c.testId === id) || null;
  }
  
  // Try by partial name match
  return CONTAINERS.find(c => c.name.toLowerCase().includes(normalized)) || null;
}

/**
 * Get container IP address by name (supports both prod and test)
 */
function getContainerIP(containerName: string, environment: 'production' | 'test' = 'production'): string | null {
  const container = getContainer(containerName);
  if (!container) return null;
  return environment === 'test' ? container.testIp : container.ip;
}

/**
 * Initialize MCP Server
 */
const server = new Server(
  {
    name: 'busibox-mcp-server',
    version: '2.2.0',
  },
  {
    capabilities: {
      resources: {},
      tools: {},
      prompts: {},
    },
  }
);

/**
 * List available resources
 */
server.setRequestHandler(ListResourcesRequestSchema, async () => {
  const resources = [];

  // Add documentation categories as resources
  for (const category of DOC_CATEGORIES) {
    resources.push({
      uri: `busibox://docs/${category}`,
      mimeType: 'text/markdown',
      name: `${category.charAt(0).toUpperCase() + category.slice(1)} Documentation`,
      description: `Browse ${category} documentation`,
    });
  }

  // Add nested documentation paths as resources
  resources.push(
    {
      uri: 'busibox://docs/session-notes',
      mimeType: 'text/markdown',
      name: 'Session Notes',
      description: 'Development session notes (in development/session-notes)',
    },
    {
      uri: 'busibox://docs/troubleshooting',
      mimeType: 'text/markdown',
      name: 'Troubleshooting',
      description: 'Troubleshooting guides (in development/troubleshooting)',
    }
  );

  // Add special resources
  resources.push(
    {
      uri: 'busibox://docs/all',
      mimeType: 'text/markdown',
      name: 'All Documentation',
      description: 'Complete documentation index',
    },
    {
      uri: 'busibox://scripts/index',
      mimeType: 'application/json',
      name: 'Scripts Index',
      description: 'Index of all available scripts by execution context',
    },
    {
      uri: 'busibox://rules',
      mimeType: 'text/markdown',
      name: 'Organization Rules',
      description: 'Project organization rules from .cursor/rules/',
    },
    {
      uri: 'busibox://architecture',
      mimeType: 'text/markdown',
      name: 'System Architecture',
      description: 'Main architecture documents',
    },
    {
      uri: 'busibox://quickstart',
      mimeType: 'text/markdown',
      name: 'Quick Start Guide',
      description: 'Quick reference for common tasks',
    },
    {
      uri: 'busibox://containers',
      mimeType: 'application/json',
      name: 'Container Map',
      description: 'Complete container IP and service mapping',
    },
    {
      uri: 'busibox://make-targets',
      mimeType: 'application/json',
      name: 'Make Targets',
      description: 'Available make targets and their descriptions',
    }
  );

  return { resources };
});

/**
 * Read resource content
 */
server.setRequestHandler(ReadResourceRequestSchema, async (request) => {
  const uri = request.params.uri;

  // Handle documentation category listing
  if (uri.startsWith('busibox://docs/') && uri !== 'busibox://docs/all') {
    let category = uri.replace('busibox://docs/', '');
    
    // Map legacy/shorthand names to actual paths
    const categoryMappings: Record<string, string> = {
      'session-notes': 'development/session-notes',
      'troubleshooting': 'development/troubleshooting',
      'configuration': 'guides',  // Configuration is now in guides
      'tasks': 'development/tasks',
    };
    
    const actualPath = categoryMappings[category] || category;
    const docs = getDocsByCategory(actualPath);
    
    // Also get nested docs if this is a parent category
    const nestedPaths = DOC_NESTED_PATHS[category] || [];
    for (const nestedPath of nestedPaths) {
      docs.push(...getDocsByCategory(nestedPath));
    }
    
    let content = `# ${category.charAt(0).toUpperCase() + category.slice(1)} Documentation\n\n`;
    content += `Found ${docs.length} documents:\n\n`;
    
    for (const doc of docs) {
      content += `## ${doc.name}\n`;
      content += `Path: \`${doc.path}\`\n\n`;
      
      const docContent = safeReadFile(join(PROJECT_ROOT, doc.path));
      if (docContent) {
        // Extract first paragraph or 500 chars
        const preview = docContent.slice(0, 500).split('\n\n')[0];
        content += `${preview}...\n\n`;
        content += `---\n\n`;
      }
    }

    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content,
        },
      ],
    };
  }

  // Handle all documentation index
  if (uri === 'busibox://docs/all') {
    let content = '# Busibox Documentation Index\n\n';
    
    for (const category of DOC_CATEGORIES) {
      const docs = getDocsByCategory(category);
      
      // Get nested docs count
      const nestedPaths = DOC_NESTED_PATHS[category] || [];
      let nestedDocs: Array<{ name: string; path: string }> = [];
      for (const nestedPath of nestedPaths) {
        nestedDocs.push(...getDocsByCategory(nestedPath));
      }
      
      const totalCount = docs.length + nestedDocs.length;
      content += `## ${category.charAt(0).toUpperCase() + category.slice(1)} (${totalCount} documents)\n\n`;
      
      // List direct docs
      for (const doc of docs) {
        content += `- **${doc.name}** - \`${doc.path}\`\n`;
      }
      
      // List nested docs by subfolder
      for (const nestedPath of nestedPaths) {
        const subDocs = getDocsByCategory(nestedPath);
        if (subDocs.length > 0) {
          const subFolder = nestedPath.split('/').pop() || nestedPath;
          content += `\n### ${subFolder} (${subDocs.length})\n`;
          for (const doc of subDocs) {
            content += `- ${doc.name} - \`${doc.path}\`\n`;
          }
        }
      }
      content += '\n';
    }

    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content,
        },
      ],
    };
  }

  // Handle scripts index
  if (uri === 'busibox://scripts/index') {
    const index: Record<string, any[]> = {};

    for (const [context, dir] of Object.entries(SCRIPT_LOCATIONS)) {
      if (dir.includes('*')) {
        // Handle glob patterns for ansible roles
        const files = listFilesRecursive(dir.replace('/*/', '/'), '*.{sh,py,js}');
        index[context] = files.map(f => relative(PROJECT_ROOT, f));
      } else {
        index[context] = getScriptsFromDir(dir);
      }
    }

    return {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify(index, null, 2),
        },
      ],
    };
  }

  // Handle organization rules
  if (uri === 'busibox://rules') {
    const rulesDir = join(PROJECT_ROOT, '.cursor', 'rules');
    const ruleFiles = readdirSync(rulesDir).filter(f => f.endsWith('.md'));
    
    let content = '# Busibox Organization Rules\n\n';
    content += 'These rules define how the project is organized and maintained.\n\n';
    
    for (const file of ruleFiles.sort()) {
      const rulePath = join(rulesDir, file);
      const ruleContent = safeReadFile(rulePath);
      if (ruleContent) {
        content += `## ${file}\n\n`;
        content += ruleContent;
        content += '\n\n---\n\n';
      }
    }

    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content,
        },
      ],
    };
  }

  // Handle architecture document - now returns all architecture docs
  if (uri === 'busibox://architecture') {
    let content = '# Busibox Architecture\n\n';
    const archDocs = getDocsByCategory('architecture');
    
    // Sort to get numbered docs first
    archDocs.sort((a, b) => a.name.localeCompare(b.name));
    
    for (const doc of archDocs) {
      // Skip archive folder
      if (doc.path.includes('/archive/')) continue;
      
      const docContent = safeReadFile(join(PROJECT_ROOT, doc.path));
      if (docContent) {
        content += `---\n\n## ${doc.name}\n\n`;
        content += docContent;
        content += '\n\n';
      }
    }
    
    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content,
        },
      ],
    };
  }

  // Handle quickstart
  if (uri === 'busibox://quickstart') {
    const claudePath = join(PROJECT_ROOT, 'CLAUDE.md');
    const content = safeReadFile(claudePath);
    
    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content || 'CLAUDE.md not found',
        },
      ],
    };
  }

  // Handle containers map
  if (uri === 'busibox://containers') {
    return {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify({
            production: CONTAINERS.map(c => ({
              id: c.id,
              name: c.name,
              ip: c.ip,
              purpose: c.purpose,
              ports: c.ports,
              services: c.services,
              notes: c.notes,
            })),
            test: CONTAINERS.map(c => ({
              id: c.testId,
              name: `TEST-${c.name}`,
              ip: c.testIp,
              purpose: c.purpose,
              ports: c.ports,
              services: c.services,
              notes: c.notes,
            })),
            network: {
              production: {
                cidr: '10.96.200.0/21',
                gateway: '10.96.200.1',
                baseOctet: '10.96.200',
              },
              test: {
                cidr: '10.96.201.0/21',
                gateway: '10.96.201.1',
                baseOctet: '10.96.201',
              },
            },
          }, null, 2),
        },
      ],
    };
  }

  // Handle make targets
  if (uri === 'busibox://make-targets') {
    const byCategory: Record<string, typeof MAKE_TARGETS> = {};
    for (const [target, info] of Object.entries(MAKE_TARGETS)) {
      if (!byCategory[info.category]) {
        byCategory[info.category] = {};
      }
      byCategory[info.category][target] = info;
    }
    
    return {
      contents: [
        {
          uri,
          mimeType: 'application/json',
          text: JSON.stringify({
            targets: MAKE_TARGETS,
            byCategory,
            usage: {
              production: 'make <target>',
              test: 'make <target> INV=inventory/test',
            },
          }, null, 2),
        },
      ],
    };
  }

  throw new Error(`Unknown resource URI: ${uri}`);
});

/**
 * List available tools
 */
server.setRequestHandler(ListToolsRequestSchema, async () => {
  return {
    tools: [
      {
        name: 'search_docs',
        description: 'Search documentation by keyword or phrase',
        inputSchema: {
          type: 'object',
          properties: {
            query: {
              type: 'string',
              description: 'Search query (can be keywords or phrases)',
            },
            category: {
              type: 'string',
              enum: [...DOC_CATEGORIES, 'session-notes', 'troubleshooting', 'tasks', 'all'],
              description: 'Limit search to specific documentation category (session-notes and troubleshooting are in development/)',
            },
          },
          required: ['query'],
        },
      },
      {
        name: 'get_script_info',
        description: 'Get detailed information about a script including purpose, usage, and execution context',
        inputSchema: {
          type: 'object',
          properties: {
            script_path: {
              type: 'string',
              description: 'Path to the script (relative to project root)',
            },
          },
          required: ['script_path'],
        },
      },
      {
        name: 'find_scripts',
        description: 'Find scripts by execution context or purpose',
        inputSchema: {
          type: 'object',
          properties: {
            context: {
              type: 'string',
              enum: ['admin-workstation', 'proxmox-host', 'container', 'all'],
              description: 'Execution context for the script',
            },
            purpose: {
              type: 'string',
              description: 'Optional: filter by purpose (deploy, test, setup, etc.)',
            },
          },
          required: ['context'],
        },
      },
      {
        name: 'get_doc',
        description: 'Get the full content of a specific documentation file',
        inputSchema: {
          type: 'object',
          properties: {
            path: {
              type: 'string',
              description: 'Path to documentation file (relative to docs/ directory)',
            },
          },
          required: ['path'],
        },
      },
      {
        name: 'list_containers',
        description: 'Get information about LXC containers and their purposes',
        inputSchema: {
          type: 'object',
          properties: {},
        },
      },
      {
        name: 'get_deployment_info',
        description: 'Get deployment information for a specific environment',
        inputSchema: {
          type: 'object',
          properties: {
            environment: {
              type: 'string',
              enum: ['test', 'production'],
              description: 'Target environment',
            },
          },
          required: ['environment'],
        },
      },
      {
        name: 'execute_proxmox_command',
        description: 'Execute a command on the Proxmox host (make, ansible-playbook, pct, ssh, etc.)',
        inputSchema: {
          type: 'object',
          properties: {
            command: {
              type: 'string',
              description: 'Command to execute on Proxmox host (e.g., "make test", "pct status 200", "cd /root/busibox/provision/ansible && ansible-playbook -i inventory/test/hosts.yml site.yml --tags milvus")',
            },
            working_directory: {
              type: 'string',
              description: 'Working directory for command execution (default: /root/busibox)',
            },
            timeout: {
              type: 'number',
              description: 'Command timeout in milliseconds (default: 300000 = 5 minutes)',
            },
          },
          required: ['command'],
        },
      },
      {
        name: 'get_container_logs',
        description: 'Get logs from a container via SSH (journalctl)',
        inputSchema: {
          type: 'object',
          properties: {
            container: {
              type: 'string',
              description: 'Container name (e.g., "milvus-lxc", "agent-lxc") or IP address',
            },
            service: {
              type: 'string',
              description: 'Service name for journalctl -u (optional, if not provided returns general logs)',
            },
            lines: {
              type: 'number',
              description: 'Number of log lines to retrieve (default: 50)',
            },
          },
          required: ['container'],
        },
      },
      {
        name: 'get_container_service_status',
        description: 'Get systemctl status for a service on a container via SSH',
        inputSchema: {
          type: 'object',
          properties: {
            container: {
              type: 'string',
              description: 'Container name (e.g., "milvus-lxc", "agent-lxc") or IP address',
            },
            service: {
              type: 'string',
              description: 'Service name (e.g., "search-api", "milvus", "nginx")',
            },
          },
          required: ['container', 'service'],
        },
      },
      // NEW: Git operations
      {
        name: 'git_pull_busibox',
        description: 'Pull latest busibox code on Proxmox host (runs git pull in /root/busibox)',
        inputSchema: {
          type: 'object',
          properties: {
            branch: {
              type: 'string',
              description: 'Branch to pull (default: current branch)',
            },
            reset_hard: {
              type: 'boolean',
              description: 'If true, runs git reset --hard origin/<branch> first (discards local changes)',
            },
          },
        },
      },
      // NEW: Make target execution
      {
        name: 'run_make_target',
        description: 'Run a make target in the Ansible directory on Proxmox host',
        inputSchema: {
          type: 'object',
          properties: {
            target: {
              type: 'string',
              description: 'Make target to run (e.g., "all", "ingest", "test-ingest", "deploy-ai-portal")',
              enum: Object.keys(MAKE_TARGETS),
            },
            environment: {
              type: 'string',
              enum: ['production', 'test'],
              description: 'Target environment (production or test). Test uses INV=inventory/test',
            },
            extra_args: {
              type: 'string',
              description: 'Extra arguments to pass (e.g., "-e skip_model_check=true")',
            },
            timeout: {
              type: 'number',
              description: 'Command timeout in milliseconds (default: 600000 = 10 minutes)',
            },
          },
          required: ['target', 'environment'],
        },
      },
      // NEW: List make targets
      {
        name: 'list_make_targets',
        description: 'List available make targets with descriptions, optionally filtered by category',
        inputSchema: {
          type: 'object',
          properties: {
            category: {
              type: 'string',
              enum: ['deployment', 'app-deployment', 'verification', 'testing', 'configuration', 'all'],
              description: 'Filter targets by category (default: all)',
            },
          },
        },
      },
      // NEW: Get container info
      {
        name: 'get_container_info',
        description: 'Get detailed information about a specific container by name or ID',
        inputSchema: {
          type: 'object',
          properties: {
            container: {
              type: 'string',
              description: 'Container name (e.g., "milvus", "agent-lxc") or ID (e.g., "204", "304")',
            },
            environment: {
              type: 'string',
              enum: ['production', 'test'],
              description: 'Environment to get info for (default: production)',
            },
          },
          required: ['container'],
        },
      },
      // NEW: Get service endpoints
      {
        name: 'get_service_endpoints',
        description: 'Get IP addresses and ports for services (useful for connecting to services)',
        inputSchema: {
          type: 'object',
          properties: {
            service: {
              type: 'string',
              description: 'Service name (e.g., "postgresql", "milvus", "search-api", "ingest", "litellm")',
            },
            environment: {
              type: 'string',
              enum: ['production', 'test'],
              description: 'Environment (default: production)',
            },
          },
        },
      },
      // NEW: Git status
      {
        name: 'git_status',
        description: 'Get git status of busibox repo on Proxmox host',
        inputSchema: {
          type: 'object',
          properties: {},
        },
      },
      // =========================================================================
      // MAIN MAKEFILE TOOLS (for testing and deployment workflows)
      // =========================================================================
      {
        name: 'get_makefile_help',
        description: 'Get comprehensive help for using the Busibox Makefile, including all available targets, variables, and examples. Call this first when you need to run tests or deployments.',
        inputSchema: {
          type: 'object',
          properties: {
            category: {
              type: 'string',
              enum: ['menu', 'setup', 'deploy', 'test', 'docker', 'mcp', 'all'],
              description: 'Filter targets by category (default: all)',
            },
          },
        },
      },
      {
        name: 'run_docker_tests',
        description: 'Run tests against local Docker services. Use this for local development testing.',
        inputSchema: {
          type: 'object',
          properties: {
            service: {
              type: 'string',
              enum: ['authz', 'ingest', 'search', 'agent', 'all'],
              description: 'Service to test',
            },
            fast: {
              type: 'boolean',
              description: 'Skip slow/GPU tests (default: true)',
            },
            pytest_args: {
              type: 'string',
              description: 'Extra pytest arguments (e.g., "-k test_name", "-v", "--tb=short")',
            },
          },
          required: ['service'],
        },
      },
      {
        name: 'run_remote_tests',
        description: 'Run tests locally against remote staging/production containers. Tests run on your machine but connect to remote services.',
        inputSchema: {
          type: 'object',
          properties: {
            service: {
              type: 'string',
              enum: ['authz', 'ingest', 'search', 'agent', 'all'],
              description: 'Service to test',
            },
            environment: {
              type: 'string',
              enum: ['staging', 'production'],
              description: 'Target environment',
            },
            fast: {
              type: 'boolean',
              description: 'Skip slow/GPU tests (default: true)',
            },
            worker: {
              type: 'boolean',
              description: 'Start local ingest worker for pipeline tests (default: false)',
            },
            pytest_args: {
              type: 'string',
              description: 'Extra pytest arguments (e.g., "-k test_name", "-v")',
            },
          },
          required: ['service', 'environment'],
        },
      },
      {
        name: 'run_container_tests',
        description: 'Run tests directly on containers via SSH. Tests execute inside the containers.',
        inputSchema: {
          type: 'object',
          properties: {
            service: {
              type: 'string',
              enum: ['authz', 'ingest', 'search', 'agent', 'all'],
              description: 'Service to test',
            },
            environment: {
              type: 'string',
              enum: ['staging', 'production'],
              description: 'Target environment',
            },
            pytest_args: {
              type: 'string',
              description: 'Extra pytest arguments',
            },
          },
          required: ['service', 'environment'],
        },
      },
      {
        name: 'docker_control',
        description: 'Control Docker services for local development (start, stop, restart, status, logs)',
        inputSchema: {
          type: 'object',
          properties: {
            action: {
              type: 'string',
              enum: ['up', 'down', 'restart', 'ps', 'logs', 'build', 'clean'],
              description: 'Docker action to perform',
            },
            service: {
              type: 'string',
              description: 'Optional: specific service (e.g., authz-api, ingest-api, search-api, agent-api)',
            },
            no_cache: {
              type: 'boolean',
              description: 'For build action: rebuild without cache',
            },
          },
          required: ['action'],
        },
      },
      {
        name: 'init_test_databases',
        description: 'Initialize test databases with schema, OAuth clients, and signing keys. Run this before running tests.',
        inputSchema: {
          type: 'object',
          properties: {},
        },
      },
      {
        name: 'check_test_databases',
        description: 'Check if test databases are properly initialized and ready for tests.',
        inputSchema: {
          type: 'object',
          properties: {},
        },
      },
      {
        name: 'get_testing_guide',
        description: 'Get a comprehensive guide on how to run tests in Busibox, including prerequisites, common patterns, and troubleshooting.',
        inputSchema: {
          type: 'object',
          properties: {
            topic: {
              type: 'string',
              enum: ['overview', 'docker', 'remote', 'container', 'troubleshooting', 'all'],
              description: 'Specific topic to get help on (default: overview)',
            },
          },
        },
      },
    ],
  };
});

/**
 * Handle tool execution
 */
server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case 'search_docs': {
      const { query, category = 'all' } = args as { query: string; category?: string };
      const results: Array<{ file: string; matches: string[] }> = [];

      // Build list of paths to search
      let pathsToSearch: string[] = [];
      
      if (category === 'all') {
        // Search all categories and their nested paths
        pathsToSearch = [...DOC_CATEGORIES];
        for (const [cat, nestedPaths] of Object.entries(DOC_NESTED_PATHS)) {
          pathsToSearch.push(...nestedPaths);
        }
      } else {
        // Map legacy names to actual paths
        const categoryMappings: Record<string, string> = {
          'session-notes': 'development/session-notes',
          'troubleshooting': 'development/troubleshooting',
          'configuration': 'guides',
          'tasks': 'development/tasks',
        };
        const actualPath = categoryMappings[category] || category;
        pathsToSearch = [actualPath];
        
        // Also include nested paths if searching a parent category
        const nestedPaths = DOC_NESTED_PATHS[category] || [];
        pathsToSearch.push(...nestedPaths);
      }

      // Remove duplicates
      pathsToSearch = [...new Set(pathsToSearch)];

      for (const searchPath of pathsToSearch) {
        const docs = getDocsByCategory(searchPath);
        for (const doc of docs) {
          const content = safeReadFile(join(PROJECT_ROOT, doc.path));
          if (!content) continue;

          const lines = content.split('\n');
          const matches: string[] = [];
          const queryLower = query.toLowerCase();

          lines.forEach((line, idx) => {
            if (line.toLowerCase().includes(queryLower)) {
              // Include context: 1 line before and after
              const start = Math.max(0, idx - 1);
              const end = Math.min(lines.length, idx + 2);
              const context = lines.slice(start, end).join('\n');
              matches.push(`Line ${idx + 1}:\n${context}\n`);
            }
          });

          if (matches.length > 0) {
            results.push({
              file: doc.path,
              matches: matches.slice(0, 5), // Limit to 5 matches per file
            });
          }
        }
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(results, null, 2),
          },
        ],
      };
    }

    case 'get_script_info': {
      const { script_path } = args as { script_path: string };
      const fullPath = join(PROJECT_ROOT, script_path);
      
      if (!existsSync(fullPath)) {
        return {
          content: [
            {
              type: 'text',
              text: `Script not found: ${script_path}`,
            },
          ],
        };
      }

      const info = extractScriptInfo(script_path);
      const content = safeReadFile(fullPath);

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              path: script_path,
              ...info,
              preview: content?.slice(0, 1000),
            }, null, 2),
          },
        ],
      };
    }

    case 'find_scripts': {
      const { context, purpose } = args as { context: string; purpose?: string };
      let scripts: any[] = [];

      if (context === 'admin-workstation' || context === 'all') {
        scripts.push(...getScriptsFromDir('scripts').map(s => ({ ...s, context: 'admin-workstation' })));
      }
      if (context === 'proxmox-host' || context === 'all') {
        scripts.push(...getScriptsFromDir('provision/pct').map(s => ({ ...s, context: 'proxmox-host' })));
      }
      if (context === 'container' || context === 'all') {
        const ansibleFiles = listFilesRecursive('provision/ansible', '*.sh');
        scripts.push(...ansibleFiles.map(f => ({
          name: relative(join(PROJECT_ROOT, 'provision/ansible'), f),
          path: relative(PROJECT_ROOT, f),
          context: 'container',
        })));
      }

      if (purpose) {
        scripts = scripts.filter(s => s.name.toLowerCase().includes(purpose.toLowerCase()));
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(scripts, null, 2),
          },
        ],
      };
    }

    case 'get_doc': {
      const { path } = args as { path: string };
      const fullPath = join(PROJECT_ROOT, 'docs', path);
      const content = safeReadFile(fullPath);

      if (!content) {
        return {
          content: [
            {
              type: 'text',
              text: `Documentation not found: ${path}`,
            },
          ],
        };
      }

      return {
        content: [
          {
            type: 'text',
            text: content,
          },
        ],
      };
    }

    case 'list_containers': {
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              production: CONTAINERS.map(c => ({
                id: c.id,
                name: c.name,
                ip: c.ip,
                purpose: c.purpose,
                ports: c.ports,
                services: c.services,
              })),
              test: CONTAINERS.map(c => ({
                id: c.testId,
                name: `TEST-${c.name}`,
                ip: c.testIp,
                purpose: c.purpose,
                ports: c.ports,
                services: c.services,
              })),
            }, null, 2),
          },
        ],
      };
    }

    case 'get_deployment_info': {
      const { environment } = args as { environment: string };
      const inventoryPath = join(
        PROJECT_ROOT,
        'provision',
        'ansible',
        'inventory',
        environment,
        'group_vars',
        'all',
        '00-main.yml'
      );

      const content = safeReadFile(inventoryPath);
      
      return {
        content: [
          {
            type: 'text',
            text: content || `Deployment info not found for ${environment}`,
          },
        ],
      };
    }

    case 'execute_proxmox_command': {
      const { command, working_directory = BUSIBOX_PATH_ON_PROXMOX, timeout = 300000 } = args as {
        command: string;
        working_directory?: string;
        timeout?: number;
      };

      try {
        const fullCommand = `cd ${working_directory} && ${command}`;
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP,
          PROXMOX_HOST_USER,
          fullCommand,
          PROXMOX_SSH_KEY_PATH,
          timeout
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  command: fullCommand,
                  exitCode: result.exitCode,
                  stdout: result.stdout,
                  stderr: result.stderr,
                  success: result.exitCode === 0,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  command,
                  working_directory,
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    case 'get_container_logs': {
      const { container, service, lines = 50 } = args as {
        container: string;
        service?: string;
        lines?: number;
      };

      try {
        const containerIP = getContainerIP(container) || container;
        const logCommand = service
          ? `journalctl -u ${service} -n ${lines} --no-pager`
          : `journalctl -n ${lines} --no-pager`;

        const result = await executeSSHCommand(
          containerIP,
          'root',
          logCommand,
          CONTAINER_SSH_KEY_PATH,
          30000
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  container,
                  containerIP,
                  service: service || 'all',
                  lines,
                  exitCode: result.exitCode,
                  logs: result.stdout,
                  error: result.stderr || undefined,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  container,
                  service,
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    case 'get_container_service_status': {
      const { container, service } = args as { container: string; service: string };

      try {
        const containerIP = getContainerIP(container) || container;
        const statusCommand = `systemctl status ${service} --no-pager -l`;

        const result = await executeSSHCommand(
          containerIP,
          'root',
          statusCommand,
          CONTAINER_SSH_KEY_PATH,
          30000
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  container,
                  containerIP,
                  service,
                  exitCode: result.exitCode,
                  status: result.stdout,
                  error: result.stderr || undefined,
                  isActive: result.exitCode === 0,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  container,
                  service,
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    // NEW: Git pull busibox
    case 'git_pull_busibox': {
      const { branch, reset_hard = false } = args as { branch?: string; reset_hard?: boolean };

      try {
        let commands = [];
        
        // Get current branch if not specified
        if (reset_hard && branch) {
          commands.push(`git fetch origin`);
          commands.push(`git reset --hard origin/${branch}`);
        } else if (reset_hard) {
          commands.push(`git fetch origin`);
          commands.push(`BRANCH=$(git rev-parse --abbrev-ref HEAD) && git reset --hard origin/$BRANCH`);
        } else if (branch) {
          commands.push(`git checkout ${branch}`);
          commands.push(`git pull origin ${branch}`);
        } else {
          commands.push(`git pull`);
        }
        
        const fullCommand = commands.join(' && ');
        
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP,
          PROXMOX_HOST_USER,
          `cd ${BUSIBOX_PATH_ON_PROXMOX} && ${fullCommand}`,
          PROXMOX_SSH_KEY_PATH,
          60000
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  action: reset_hard ? 'git reset --hard' : 'git pull',
                  branch: branch || '(current)',
                  exitCode: result.exitCode,
                  stdout: result.stdout,
                  stderr: result.stderr,
                  success: result.exitCode === 0,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  action: 'git_pull_busibox',
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    // NEW: Run make target
    case 'run_make_target': {
      const { target, environment, extra_args = '', timeout = 600000 } = args as {
        target: string;
        environment: 'production' | 'test';
        extra_args?: string;
        timeout?: number;
      };

      if (!MAKE_TARGETS[target]) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: `Unknown make target: ${target}`,
                  available_targets: Object.keys(MAKE_TARGETS),
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }

      try {
        const invFlag = environment === 'test' ? 'INV=inventory/test' : '';
        const extraFlag = extra_args ? `EXTRA_ARGS="${extra_args}"` : '';
        const makeCommand = `make ${target} ${invFlag} ${extraFlag}`.trim();
        
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP,
          PROXMOX_HOST_USER,
          `cd ${BUSIBOX_PATH_ON_PROXMOX}/provision/ansible && ${makeCommand}`,
          PROXMOX_SSH_KEY_PATH,
          timeout
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  target,
                  environment,
                  command: makeCommand,
                  description: MAKE_TARGETS[target].description,
                  exitCode: result.exitCode,
                  stdout: result.stdout,
                  stderr: result.stderr,
                  success: result.exitCode === 0,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  target,
                  environment,
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    // NEW: List make targets
    case 'list_make_targets': {
      const { category = 'all' } = args as { category?: string };
      
      let targets = Object.entries(MAKE_TARGETS);
      
      if (category !== 'all') {
        targets = targets.filter(([_, info]) => info.category === category);
      }
      
      const byCategory: Record<string, Array<{ target: string; description: string }>> = {};
      for (const [target, info] of targets) {
        if (!byCategory[info.category]) {
          byCategory[info.category] = [];
        }
        byCategory[info.category].push({ target, description: info.description });
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(
              {
                filter: category,
                targets: byCategory,
                usage: {
                  production: 'make <target>',
                  test: 'make <target> INV=inventory/test',
                },
              },
              null,
              2
            ),
          },
        ],
      };
    }

    // NEW: Get container info
    case 'get_container_info': {
      const { container, environment = 'production' } = args as {
        container: string;
        environment?: 'production' | 'test';
      };

      const containerConfig = getContainer(container);
      
      if (!containerConfig) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: `Container not found: ${container}`,
                  available_containers: CONTAINERS.map(c => c.name),
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }

      const isTest = environment === 'test';
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(
              {
                name: isTest ? `TEST-${containerConfig.name}` : containerConfig.name,
                id: isTest ? containerConfig.testId : containerConfig.id,
                ip: isTest ? containerConfig.testIp : containerConfig.ip,
                purpose: containerConfig.purpose,
                ports: containerConfig.ports,
                services: containerConfig.services,
                notes: containerConfig.notes,
                ssh_command: `ssh root@${isTest ? containerConfig.testIp : containerConfig.ip}`,
                environment,
              },
              null,
              2
            ),
          },
        ],
      };
    }

    // NEW: Get service endpoints
    case 'get_service_endpoints': {
      const { service, environment = 'production' } = args as {
        service?: string;
        environment?: 'production' | 'test';
      };

      const isTest = environment === 'test';
      
      // Find all containers that provide the requested service
      let endpoints: Array<{
        service: string;
        container: string;
        ip: string;
        port: number;
        url: string;
      }> = [];

      for (const container of CONTAINERS) {
        const ip = isTest ? container.testIp : container.ip;
        const containerName = isTest ? `TEST-${container.name}` : container.name;
        
        for (const portInfo of container.ports) {
          if (!service || 
              portInfo.service.toLowerCase().includes(service.toLowerCase()) ||
              container.services.some(s => s.toLowerCase().includes(service?.toLowerCase() || ''))) {
            endpoints.push({
              service: portInfo.service,
              container: containerName,
              ip,
              port: portInfo.port,
              url: `http://${ip}:${portInfo.port}`,
            });
          }
        }
      }

      // If looking for a specific service, try exact matches first
      if (service) {
        const exact = endpoints.filter(e => 
          e.service.toLowerCase() === service.toLowerCase()
        );
        if (exact.length > 0) {
          endpoints = exact;
        }
      }

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(
              {
                environment,
                filter: service || 'all',
                endpoints,
              },
              null,
              2
            ),
          },
        ],
      };
    }

    // NEW: Git status
    case 'git_status': {
      try {
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP,
          PROXMOX_HOST_USER,
          `cd ${BUSIBOX_PATH_ON_PROXMOX} && git status && echo "---" && git log -1 --oneline`,
          PROXMOX_SSH_KEY_PATH,
          30000
        );

        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  path: BUSIBOX_PATH_ON_PROXMOX,
                  exitCode: result.exitCode,
                  output: result.stdout,
                  success: result.exitCode === 0,
                },
                null,
                2
              ),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  error: error.message || 'Unknown error',
                  action: 'git_status',
                },
                null,
                2
              ),
            },
          ],
          isError: true,
        };
      }
    }

    // =========================================================================
    // MAIN MAKEFILE TOOL HANDLERS
    // =========================================================================
    
    case 'get_makefile_help': {
      const { category = 'all' } = args as { category?: string };
      
      const filteredTargets = category === 'all'
        ? MAIN_MAKEFILE_TARGETS
        : Object.fromEntries(
            Object.entries(MAIN_MAKEFILE_TARGETS).filter(([_, t]) => t.category === category)
          );
      
      const byCategory: Record<string, typeof MAIN_MAKEFILE_TARGETS> = {};
      for (const [target, info] of Object.entries(filteredTargets)) {
        if (!byCategory[info.category]) {
          byCategory[info.category] = {};
        }
        byCategory[info.category][target] = info;
      }
      
      let helpText = `# Busibox Makefile Help\n\n`;
      helpText += `## Environments\n`;
      helpText += `- **local** - Docker on localhost (development)\n`;
      helpText += `- **staging** - 10.96.201.x network (pre-production)\n`;
      helpText += `- **production** - 10.96.200.x network (live)\n\n`;
      
      for (const [cat, targets] of Object.entries(byCategory)) {
        helpText += `## ${cat.charAt(0).toUpperCase() + cat.slice(1)} Commands\n\n`;
        for (const [target, info] of Object.entries(targets)) {
          helpText += `### make ${target}\n`;
          helpText += `${info.description}\n`;
          if (info.variables) {
            helpText += `\n**Variables:**\n`;
            for (const [v, desc] of Object.entries(info.variables)) {
              helpText += `- \`${v}\`: ${desc}\n`;
            }
          }
          if (info.examples) {
            helpText += `\n**Examples:**\n`;
            for (const ex of info.examples) {
              helpText += `\`\`\`bash\n${ex}\n\`\`\`\n`;
            }
          }
          helpText += `\n`;
        }
      }
      
      return {
        content: [{ type: 'text', text: helpText }],
      };
    }

    case 'run_docker_tests': {
      const { service, fast = true, pytest_args = '' } = args as {
        service: string;
        fast?: boolean;
        pytest_args?: string;
      };
      
      const fastFlag = fast ? 'FAST=1' : 'FAST=0';
      const argsFlag = pytest_args ? `ARGS='${pytest_args}'` : '';
      const cmd = `cd ${BUSIBOX_PATH_ON_PROXMOX} && make test-docker SERVICE=${service} ${fastFlag} ${argsFlag}`.trim();
      
      // For Docker tests, we need to run locally, not on Proxmox
      // Return the command to run instead of executing remotely
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              note: 'Docker tests should be run locally on your development machine, not on Proxmox.',
              command: `make test-docker SERVICE=${service} ${fastFlag} ${argsFlag}`.trim(),
              description: `Run ${service} tests against local Docker services`,
              prerequisites: [
                'Docker services must be running: make docker-up',
                'Test databases must be initialized: make test-db-init',
              ],
              examples: [
                `make test-docker SERVICE=${service}`,
                `make test-docker SERVICE=${service} FAST=0  # Include slow tests`,
                `make test-docker SERVICE=${service} ARGS='-k test_name -v'`,
              ],
            }, null, 2),
          },
        ],
      };
    }

    case 'run_remote_tests': {
      const { service, environment, fast = true, worker = false, pytest_args = '' } = args as {
        service: string;
        environment: string;
        fast?: boolean;
        worker?: boolean;
        pytest_args?: string;
      };
      
      const inv = environment === 'production' ? 'production' : 'staging';
      const fastFlag = fast ? 'FAST=1' : 'FAST=0';
      const workerFlag = worker ? 'WORKER=1' : '';
      const argsFlag = pytest_args ? `ARGS='${pytest_args}'` : '';
      
      const cmd = `make test-local SERVICE=${service} INV=${inv} ${fastFlag} ${workerFlag} ${argsFlag}`.trim();
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              note: 'Remote tests run locally on your machine but connect to remote services.',
              command: cmd,
              description: `Run ${service} tests against ${environment} environment`,
              environment: {
                name: environment,
                network: environment === 'production' ? '10.96.200.x' : '10.96.201.x',
              },
              prerequisites: [
                `VPN or network access to ${environment} network`,
                'Python environment with test dependencies',
              ],
              examples: [
                `make test-local SERVICE=${service} INV=${inv}`,
                `make test-local SERVICE=${service} INV=${inv} FAST=0  # Include slow tests`,
                `make test-local SERVICE=${service} INV=${inv} WORKER=1  # With ingest worker`,
              ],
            }, null, 2),
          },
        ],
      };
    }

    case 'run_container_tests': {
      const { service, environment, pytest_args = '' } = args as {
        service: string;
        environment: string;
        pytest_args?: string;
      };
      
      const inv = environment === 'production' ? 'production' : 'staging';
      const argsFlag = pytest_args ? `ARGS='${pytest_args}'` : '';
      
      try {
        const cmd = `cd ${BUSIBOX_PATH_ON_PROXMOX} && PYTEST_ARGS="${pytest_args}" make test SERVICE=${service} INV=${inv}`;
        
        const result = await executeSSHCommand(
          PROXMOX_HOST_IP,
          PROXMOX_HOST_USER,
          cmd,
          PROXMOX_SSH_KEY_PATH,
          600000 // 10 minute timeout for tests
        );
        
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                command: `make test SERVICE=${service} INV=${inv} ${argsFlag}`.trim(),
                exitCode: result.exitCode,
                success: result.exitCode === 0,
                stdout: result.stdout,
                stderr: result.stderr,
              }, null, 2),
            },
          ],
        };
      } catch (error: any) {
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({
                error: error.message || 'Unknown error',
                note: 'Container tests run via SSH on the Proxmox host',
              }, null, 2),
            },
          ],
          isError: true,
        };
      }
    }

    case 'docker_control': {
      const { action, service, no_cache = false } = args as {
        action: string;
        service?: string;
        no_cache?: boolean;
      };
      
      let cmd = `make docker-${action}`;
      if (service) cmd += ` SERVICE=${service}`;
      if (no_cache && action === 'build') cmd += ' NO_CACHE=1';
      
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              note: 'Docker commands should be run locally on your development machine.',
              command: cmd,
              description: `${action} Docker services${service ? ` (${service})` : ''}`,
              alternatives: {
                'up': 'make docker-up - Start services',
                'down': 'make docker-down - Stop services',
                'restart': 'make docker-restart - Restart services',
                'ps': 'make docker-ps - Show status',
                'logs': 'make docker-logs - View logs',
                'build': 'make docker-build - Build images',
                'clean': 'make docker-clean - Remove all containers/volumes',
              },
            }, null, 2),
          },
        ],
      };
    }

    case 'init_test_databases': {
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              note: 'This command runs locally against Docker.',
              command: 'make test-db-init',
              description: 'Bootstrap test databases with schema, OAuth clients, and signing keys',
              databases: ['test_authz', 'test_files', 'test_agent_server'],
              user: 'busibox_test_user',
              prerequisites: [
                'Docker services must be running: make docker-up',
                'PostgreSQL must be healthy',
              ],
            }, null, 2),
          },
        ],
      };
    }

    case 'check_test_databases': {
      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify({
              note: 'This command runs locally against Docker.',
              command: 'make test-db-check',
              description: 'Check if test databases are properly initialized',
              what_it_checks: [
                'test_authz database has active signing keys',
                'test_files database exists',
                'test_agent_server database exists',
              ],
            }, null, 2),
          },
        ],
      };
    }

    case 'get_testing_guide': {
      const { topic = 'overview' } = args as { topic?: string };
      
      const guides: Record<string, string> = {
        overview: `# Busibox Testing Overview

## Test Types

1. **Docker Tests** (\`make test-docker\`)
   - Run locally against Docker services
   - Best for development and CI
   - Requires: Docker running, test DBs initialized

2. **Remote Tests** (\`make test-local\`)
   - Run locally but connect to remote staging/production
   - Tests your local code against real services
   - Requires: Network access to staging/production

3. **Container Tests** (\`make test\`)
   - Run directly on containers via SSH
   - Tests the deployed code
   - Requires: SSH access to Proxmox

## Quick Start

\`\`\`bash
# Start Docker services
make docker-up

# Initialize test databases (first time)
make test-db-init

# Run agent tests
make test-docker SERVICE=agent

# Run all tests
make test-docker SERVICE=all
\`\`\`
`,
        docker: `# Docker Testing Guide

## Prerequisites
1. Docker Desktop running
2. Services started: \`make docker-up\`
3. Test DBs initialized: \`make test-db-init\`

## Commands

\`\`\`bash
# Run specific service tests
make test-docker SERVICE=authz
make test-docker SERVICE=ingest
make test-docker SERVICE=search
make test-docker SERVICE=agent

# Run all tests
make test-docker SERVICE=all

# Include slow/GPU tests
make test-docker SERVICE=agent FAST=0

# Run specific test
make test-docker SERVICE=agent ARGS='-k test_weather'

# Verbose output
make test-docker SERVICE=agent ARGS='-v --tb=short'
\`\`\`

## Services in Docker
- authz-api: http://localhost:8080
- ingest-api: http://localhost:8001
- search-api: http://localhost:8003
- agent-api: http://localhost:8000
`,
        remote: `# Remote Testing Guide

Test your local code changes against staging/production services.

## Prerequisites
1. Network/VPN access to staging (10.96.201.x) or production (10.96.200.x)
2. Python environment with dependencies

## Commands

\`\`\`bash
# Test against staging
make test-local SERVICE=agent INV=staging

# Test against production
make test-local SERVICE=agent INV=production

# Include slow tests
make test-local SERVICE=agent INV=staging FAST=0

# With ingest worker (for pipeline tests)
make test-local SERVICE=ingest INV=staging WORKER=1

# Specific test
make test-local SERVICE=agent INV=staging ARGS='-k test_weather'
\`\`\`
`,
        container: `# Container Testing Guide

Run tests directly on deployed containers via SSH.

## Prerequisites
1. SSH access to Proxmox host
2. Services deployed to staging/production

## Commands (run from Proxmox or via SSH)

\`\`\`bash
# Interactive menu
make test

# Direct command
make test SERVICE=agent INV=staging

# With pytest args
PYTEST_ARGS='-k test_weather' make test SERVICE=agent INV=staging
\`\`\`

## What happens
1. SSH to the service container
2. Run pytest in the container's code directory
3. Return results
`,
        troubleshooting: `# Testing Troubleshooting

## Common Issues

### "Test databases not initialized"
\`\`\`bash
make test-db-init
\`\`\`

### "Connection refused" in Docker tests
\`\`\`bash
# Check services are running
make docker-ps

# Restart services
make docker-restart
\`\`\`

### "Network unreachable" for remote tests
- Check VPN connection
- Verify network access: \`ping 10.96.201.200\` (staging)

### Tests timeout
\`\`\`bash
# Use FAST mode to skip slow tests
make test-docker SERVICE=agent FAST=1
\`\`\`

### Permission denied
- Check SSH keys are set up for Proxmox access
- Verify container SSH access

## Test Database Info

Tests run against isolated databases:
- test_authz (not authz)
- test_files (not files)  
- test_agent_server (not agent_server)

User: busibox_test_user
`,
      };
      
      if (topic === 'all') {
        return {
          content: [
            {
              type: 'text',
              text: Object.values(guides).join('\n---\n\n'),
            },
          ],
        };
      }
      
      return {
        content: [
          {
            type: 'text',
            text: guides[topic] || guides.overview,
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown tool: ${name}`);
  }
});

/**
 * List available prompts
 */
server.setRequestHandler(ListPromptsRequestSchema, async () => {
  return {
    prompts: [
      {
        name: 'deploy_service',
        description: 'Guide for deploying a service to test or production',
        arguments: [
          {
            name: 'service',
            description: 'Service name (e.g., ai-portal, agent-lxc)',
            required: true,
          },
          {
            name: 'environment',
            description: 'Target environment (test or production)',
            required: true,
          },
        ],
      },
      {
        name: 'troubleshoot_issue',
        description: 'Guide for troubleshooting common issues',
        arguments: [
          {
            name: 'issue_type',
            description: 'Type of issue (deployment, container, service, network)',
            required: true,
          },
        ],
      },
      {
        name: 'add_service',
        description: 'Guide for adding a new service to Busibox',
        arguments: [
          {
            name: 'service_name',
            description: 'Name of the new service',
            required: true,
          },
        ],
      },
      {
        name: 'create_documentation',
        description: 'Guide for creating new documentation following organization rules',
        arguments: [
          {
            name: 'topic',
            description: 'Topic to document',
            required: true,
          },
        ],
      },
      {
        name: 'run_tests',
        description: 'Guide for running tests on Busibox services',
        arguments: [
          {
            name: 'service',
            description: 'Service to test (ingest, search, agent, apps, or all)',
            required: false,
          },
          {
            name: 'test_type',
            description: 'Type of test (unit, integration, coverage, extraction)',
            required: false,
          },
        ],
      },
      // NEW: Testing workflow prompt
      {
        name: 'testing_workflow',
        description: 'Complete guide for testing Busibox services with the Makefile',
        arguments: [
          {
            name: 'environment',
            description: 'Where to run tests: docker (local), staging, or production',
            required: true,
          },
          {
            name: 'service',
            description: 'Service to test: authz, ingest, search, agent, or all',
            required: false,
          },
        ],
      },
      // NEW: Deployment workflow prompt
      {
        name: 'deployment_workflow',
        description: 'Complete guide for deploying Busibox services',
        arguments: [
          {
            name: 'target',
            description: 'Deployment target: staging or production',
            required: true,
          },
          {
            name: 'service',
            description: 'Service to deploy (optional, deploys all if not specified)',
            required: false,
          },
        ],
      },
      // NEW: Docker development prompt
      {
        name: 'docker_development',
        description: 'Guide for local Docker-based development workflow',
        arguments: [],
      },
      {
        name: 'deploy_app',
        description: 'Guide for deploying a specific application (ai-portal, agent-client, etc.)',
        arguments: [
          {
            name: 'app_name',
            description: 'Application name (ai-portal, agent-client, doc-intel, foundation, project-analysis, innovation)',
            required: true,
          },
          {
            name: 'environment',
            description: 'Target environment (test or production)',
            required: true,
          },
        ],
      },
      {
        name: 'update_and_deploy',
        description: 'Guide for pulling latest code and deploying',
        arguments: [
          {
            name: 'environment',
            description: 'Target environment (test or production)',
            required: true,
          },
          {
            name: 'service',
            description: 'Optional: specific service to deploy (default: all)',
            required: false,
          },
        ],
      },
    ],
  };
});

/**
 * Handle prompt requests
 */
server.setRequestHandler(GetPromptRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;

  switch (name) {
    case 'deploy_service': {
      const { service, environment } = args as { service: string; environment: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I deploy ${service} to ${environment}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's how to deploy ${service} to ${environment}:

1. **Pull latest code on Proxmox** (if needed):
   Use the \`git_pull_busibox\` tool to update the code.

2. **Run the deployment**:
   Use the \`run_make_target\` tool with:
   - target: "${service}"
   - environment: "${environment}"

   Or manually via \`execute_proxmox_command\`:
   \`\`\`bash
   cd /root/busibox/provision/ansible
   make ${service} ${environment === 'test' ? 'INV=inventory/test' : ''}
   \`\`\`

3. **Validate deployment**:
   Use \`get_container_service_status\` to check service health.
   Use \`get_container_logs\` to view logs if needed.

4. **Verify in browser** (if applicable):
   - Test: https://${service}.test.busibox.internal
   - Production: https://${service}.busibox.com

**Quick Container Reference**:
${CONTAINERS.map(c => `- ${c.name}: ${environment === 'test' ? c.testIp : c.ip}`).join('\n')}`,
            },
          },
        ],
      };
    }

    case 'troubleshoot_issue': {
      const { issue_type } = args as { issue_type: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I troubleshoot ${issue_type} issues?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's how to troubleshoot ${issue_type} issues:

**Available MCP Tools**:
- \`get_container_logs\`: Get journalctl logs from any container
- \`get_container_service_status\`: Check systemctl status
- \`execute_proxmox_command\`: Run any command on Proxmox
- \`git_status\`: Check if code is up to date

**For Deployment Issues**:
1. Check git status: \`git_status\` tool
2. Pull latest code: \`git_pull_busibox\` tool
3. Run deployment: \`run_make_target\` tool
4. Check logs: \`get_container_logs\` tool

**For Container Issues**:
Use \`execute_proxmox_command\` with:
\`\`\`bash
pct status <CTID>
pct enter <CTID>
\`\`\`

**For Service Issues**:
1. Check status: \`get_container_service_status\` with container and service name
2. Get logs: \`get_container_logs\` with container and service name
3. Restart if needed: \`execute_proxmox_command\` with "ssh root@<ip> systemctl restart <service>"

**Container Reference**:
${CONTAINERS.map(c => `- ${c.name} (${c.id}): ${c.ip} - ${c.services.join(', ')}`).join('\n')}`,
            },
          },
        ],
      };
    }

    case 'add_service': {
      const { service_name } = args as { service_name: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I add a new service called ${service_name} to Busibox?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's the complete process to add ${service_name} to Busibox:

**1. Plan the Service**:
- Determine CTID and IP address (check provision/pct/vars.env for available IDs)
- Define service requirements (CPU, RAM, disk, dependencies)
- Plan networking and security requirements

**2. Update Container Configuration** (on Proxmox host):

Edit \`provision/pct/vars.env\`:
\`\`\`bash
CT_${service_name.toUpperCase()}=<CTID>
IP_${service_name.toUpperCase()}=10.96.200.<IP>
\`\`\`

**3. Update Container Creation Script**:

Edit \`provision/pct/create_lxc_base.sh\` to add container creation logic.

**4. Create Ansible Role**:

\`\`\`bash
cd provision/ansible
mkdir -p roles/${service_name}/{tasks,templates,files,handlers,vars}
\`\`\`

**5. Update Inventory** in both test and production.

**6. Deploy and Test**:
Use \`run_make_target\` with environment: "test" first.

**Reference Documentation**:
- Use \`get_doc\` tool with path "architecture/01-containers.md"
- Use \`search_docs\` to find related documentation`,
            },
          },
        ],
      };
    }

    case 'create_documentation': {
      const { topic } = args as { topic: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I create documentation for ${topic}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's how to create documentation for ${topic} following Busibox organization rules:

**1. Determine Document Category**:

Ask yourself: "What is the primary purpose of this documentation?"

- **Architecture/design** → \`docs/architecture/\`
- **Deployment procedures** → \`docs/deployment/\`
- **Configuration/setup** → \`docs/configuration/\`
- **Troubleshooting/fixes** → \`docs/troubleshooting/\`
- **API/reference** → \`docs/reference/\`
- **How-to guides** → \`docs/guides/\`
- **Session notes** → \`docs/session-notes/\`
- **Development tasks** → \`docs/development/\`

**2. Choose Appropriate Filename**:

Use \`kebab-case\` for all documentation files.

**3. Create Document with Proper Structure** including metadata header.

**Available MCP Tools**:
- \`search_docs\`: Search existing documentation
- \`get_doc\`: Read specific documentation files`,
            },
          },
        ],
      };
    }

    case 'run_tests': {
      const { service = 'all', test_type = 'unit' } = args as { service?: string; test_type?: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I run ${test_type} tests for ${service}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's how to run ${test_type} tests for ${service}:

**Use the \`run_make_target\` tool** with appropriate target:

**Available Test Targets**:
- \`test-all\`: All service tests
- \`test-ingest\`: Ingest unit tests
- \`test-ingest-all\`: Ingest including integration
- \`test-ingest-coverage\`: Ingest with coverage
- \`test-search\`: Search tests
- \`test-search-unit\`: Search unit tests
- \`test-search-integration\`: Search integration tests
- \`test-agent\`: Agent tests
- \`test-agent-unit\`: Agent unit tests
- \`test-authz\`: AuthZ tests
- \`test-apps\`: App tests

**Extraction Tests**:
- \`test-extraction-simple\`: Basic PDF extraction
- \`test-extraction-llm\`: LLM-enhanced extraction
- \`test-extraction-marker\`: Marker extraction (GPU)
- \`test-extraction-colpali\`: ColPali visual extraction

**Example**: Run \`run_make_target\` with:
- target: "test-${service}"
- environment: "test"

Or use \`list_make_targets\` with category: "testing" to see all options.`,
            },
          },
        ],
      };
    }

    case 'deploy_app': {
      const { app_name, environment } = args as { app_name: string; environment: string };
      const inv = environment === 'test' ? 'INV=inventory/test' : '';
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I deploy ${app_name} to ${environment}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's how to deploy ${app_name} to ${environment}:

**Use the \`run_make_target\` tool** with:
- target: "deploy-${app_name}"
- environment: "${environment}"

**Available App Deployment Targets**:
- \`deploy-apps\`: All applications
- \`deploy-ai-portal\`: AI Portal
- \`deploy-agent-manager\`: Agent Manager
- \`deploy-doc-intel\`: Doc Intel
- \`deploy-foundation\`: Foundation
- \`deploy-project-analysis\`: Project Analysis
- \`deploy-innovation\`: Innovation

**Verify Deployment**:
Use \`get_container_service_status\` with:
- container: "apps-lxc"
- service: "${app_name}"

**Container Info**:
- Apps container IP: ${environment === 'test' ? '10.96.201.201' : '10.96.200.201'}`,
            },
          },
        ],
      };
    }

    case 'update_and_deploy': {
      const { environment, service = 'all' } = args as { environment: string; service?: string };
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I update and deploy ${service} to ${environment}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `Here's the complete workflow to update and deploy to ${environment}:

**Step 1: Check Current Status**
Use \`git_status\` to see current state of code on Proxmox.

**Step 2: Pull Latest Code**
Use \`git_pull_busibox\` tool:
- For clean pull: just call with no args
- To discard local changes: set reset_hard: true

**Step 3: Deploy**
Use \`run_make_target\` tool:
- target: "${service === 'all' ? 'all' : service}"
- environment: "${environment}"

**Step 4: Verify**
Use \`run_make_target\` with:
- target: "verify"
- environment: "${environment}"

**Quick Commands** (via \`execute_proxmox_command\`):
\`\`\`bash
# One-liner update and deploy:
cd /root/busibox && git pull && cd provision/ansible && make ${service} ${environment === 'test' ? 'INV=inventory/test' : ''}
\`\`\`

**Environment IPs**:
${environment === 'test' 
  ? CONTAINERS.map(c => `- ${c.name}: ${c.testIp}`).join('\n')
  : CONTAINERS.map(c => `- ${c.name}: ${c.ip}`).join('\n')
}`,
            },
          },
        ],
      };
    }

    // =========================================================================
    // NEW MAKEFILE WORKFLOW PROMPTS
    // =========================================================================

    case 'testing_workflow': {
      const { environment, service = 'all' } = args as { environment: string; service?: string };
      
      const isDocker = environment === 'docker' || environment === 'local';
      const isStaging = environment === 'staging';
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I run tests for ${service} in ${environment}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: isDocker ? `# Testing ${service} in Docker (Local Development)

## Prerequisites

1. **Start Docker services**:
   \`\`\`bash
   make docker-up
   \`\`\`

2. **Initialize test databases** (first time only):
   \`\`\`bash
   make test-db-init
   \`\`\`

3. **Verify databases are ready**:
   \`\`\`bash
   make test-db-check
   \`\`\`

## Run Tests

\`\`\`bash
# Run ${service} tests
make test-docker SERVICE=${service}

# Skip slow/GPU tests (default)
make test-docker SERVICE=${service} FAST=1

# Include slow tests
make test-docker SERVICE=${service} FAST=0

# Run specific test
make test-docker SERVICE=${service} ARGS='-k test_name'

# Verbose output
make test-docker SERVICE=${service} ARGS='-v --tb=short'
\`\`\`

## Available Services
- \`authz\` - Authorization service tests
- \`ingest\` - Ingestion pipeline tests
- \`search\` - Search API tests
- \`agent\` - Agent API tests
- \`all\` - All service tests

## Troubleshooting

If tests fail with "connection refused":
\`\`\`bash
make docker-ps   # Check if services are running
make docker-restart  # Restart services
\`\`\`

If tests fail with "database not initialized":
\`\`\`bash
make test-db-init  # Reinitialize test databases
\`\`\`

## Important Note
Tests run against **isolated test databases** (test_authz, test_files, test_agent_server), not production databases.`
              : `# Testing ${service} Against ${environment.charAt(0).toUpperCase() + environment.slice(1)}

## Option 1: Remote Tests (run locally, connect to ${environment})

Tests run on your machine but connect to remote services.

### Prerequisites
- Network/VPN access to ${isStaging ? '10.96.201.x' : '10.96.200.x'} network
- Python environment with dependencies

### Commands
\`\`\`bash
# Run ${service} tests against ${environment}
make test-local SERVICE=${service} INV=${environment}

# Skip slow tests (default)
make test-local SERVICE=${service} INV=${environment} FAST=1

# Include slow tests
make test-local SERVICE=${service} INV=${environment} FAST=0

# With ingest worker (for pipeline tests)
make test-local SERVICE=${service} INV=${environment} WORKER=1

# Specific test
make test-local SERVICE=${service} INV=${environment} ARGS='-k test_name'
\`\`\`

## Option 2: Container Tests (run on containers via SSH)

Tests run directly inside the deployed containers.

### Prerequisites
- SSH access to Proxmox host
- Services deployed to ${environment}

### Commands (via MCP tools or SSH)
\`\`\`bash
# On Proxmox host or via execute_proxmox_command
make test SERVICE=${service} INV=${environment}

# With pytest args
PYTEST_ARGS='-k test_name' make test SERVICE=${service} INV=${environment}
\`\`\`

## Available Services
- \`authz\` - Authorization service
- \`ingest\` - Ingestion pipeline
- \`search\` - Search API
- \`agent\` - Agent API
- \`all\` - All services

## Network Reference
${isStaging ? `
**Staging (10.96.201.x)**:
- proxy: 10.96.201.200
- apps: 10.96.201.201
- agent: 10.96.201.202
- postgres: 10.96.201.203
- milvus/search: 10.96.201.204
- files: 10.96.201.205
- ingest: 10.96.201.206
` : `
**Production (10.96.200.x)**:
- proxy: 10.96.200.200
- apps: 10.96.200.201
- agent: 10.96.200.202
- postgres: 10.96.200.203
- milvus/search: 10.96.200.204
- files: 10.96.200.205
- ingest: 10.96.200.206
`}`,
            },
          },
        ],
      };
    }

    case 'deployment_workflow': {
      const { target, service } = args as { target: string; service?: string };
      const inv = target === 'production' ? '' : 'INV=inventory/test';
      const networkBase = target === 'production' ? '10.96.200' : '10.96.201';
      
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: `How do I deploy ${service || 'services'} to ${target}?`,
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `# Deploying to ${target.charAt(0).toUpperCase() + target.slice(1)}

## Method 1: Interactive Menu (Recommended)

\`\`\`bash
make            # Opens interactive menu
# Select: Deploy
# Choose services to deploy
\`\`\`

## Method 2: Direct Command

${service ? `
### Deploy ${service}
\`\`\`bash
make deploy SERVICE=${service} INV=${target}
\`\`\`
` : `
### Deploy All Services
\`\`\`bash
make deploy INV=${target}
\`\`\`

### Deploy Specific Service
\`\`\`bash
make deploy SERVICE=<service> INV=${target}
\`\`\`

Available services:
- \`authz\` - Authorization service
- \`ingest\` - Ingestion API + worker
- \`search\` - Search API + Milvus
- \`agent\` - Agent API
- \`apps\` - All Next.js apps
- \`pg\` - PostgreSQL
- \`litellm\` - LiteLLM gateway
- \`nginx\` - Reverse proxy
`}

## Method 3: From Proxmox (via MCP)

Use MCP tools in this order:

1. **Pull latest code**:
   Use \`git_pull_busibox\` tool

2. **Deploy**:
   Use \`run_make_target\` with:
   - target: "${service || 'all'}"
   - environment: "${target === 'production' ? 'production' : 'test'}"

3. **Verify**:
   Use \`get_container_service_status\` to check services

## Pre-Deployment Checklist

- [ ] Code is committed and pushed
- [ ] Tests pass: \`make test-local SERVICE=all INV=${target}\`
- [ ] Vault configured: \`make configure\` → Verify Configuration
- [ ] Network access verified

## Post-Deployment Verification

\`\`\`bash
# Check service health
curl -s http://${networkBase}.202:8000/health  # Agent
curl -s http://${networkBase}.204:8003/health  # Search
curl -s http://${networkBase}.206:8000/health  # Ingest
\`\`\`

## Network Reference (${target})

| Service | IP | Port |
|---------|-----|------|
| Proxy | ${networkBase}.200 | 80, 443 |
| Apps | ${networkBase}.201 | 3000 |
| Agent | ${networkBase}.202 | 8000 |
| PostgreSQL | ${networkBase}.203 | 5432 |
| Milvus/Search | ${networkBase}.204 | 19530, 8003 |
| Files (MinIO) | ${networkBase}.205 | 9000 |
| Ingest | ${networkBase}.206 | 8000, 6379 |
| LiteLLM | ${networkBase}.207 | 4000 |`,
            },
          },
        ],
      };
    }

    case 'docker_development': {
      return {
        messages: [
          {
            role: 'user',
            content: {
              type: 'text',
              text: 'How do I set up and use Docker for local development?',
            },
          },
          {
            role: 'assistant',
            content: {
              type: 'text',
              text: `# Docker Local Development Guide

## Initial Setup

1. **Copy environment file**:
   \`\`\`bash
   cp env.local.example .env.local
   # Edit .env.local with your API keys
   \`\`\`

2. **Generate SSL certificates**:
   \`\`\`bash
   make ssl-check
   \`\`\`

3. **Build images**:
   \`\`\`bash
   make docker-build
   \`\`\`

4. **Start services**:
   \`\`\`bash
   make docker-up
   \`\`\`

5. **Initialize test databases**:
   \`\`\`bash
   make test-db-init
   \`\`\`

## Daily Workflow

\`\`\`bash
# Start your day
make docker-up

# Check what's running
make docker-ps

# View logs
make docker-logs              # All services
make docker-logs SERVICE=agent-api  # Specific service

# After code changes, rebuild
make docker-build SERVICE=agent-api
make docker-restart SERVICE=agent-api

# Run tests
make test-docker SERVICE=agent

# End of day
make docker-down
\`\`\`

## Service Ports (Local Docker)

| Service | URL |
|---------|-----|
| AuthZ API | http://localhost:8080 |
| Ingest API | http://localhost:8001 |
| Search API | http://localhost:8003 |
| Agent API | http://localhost:8000 |
| PostgreSQL | localhost:5432 |
| Redis | localhost:6379 |
| MinIO | http://localhost:9000 |
| MinIO Console | http://localhost:9001 |

## Common Commands

| Command | Description |
|---------|-------------|
| \`make docker-up\` | Start all services |
| \`make docker-down\` | Stop all services |
| \`make docker-restart\` | Restart services |
| \`make docker-ps\` | Show status |
| \`make docker-logs\` | View logs |
| \`make docker-build\` | Build images |
| \`make docker-build NO_CACHE=1\` | Rebuild without cache |
| \`make docker-clean\` | Remove everything |

## Troubleshooting

### Service won't start
\`\`\`bash
make docker-logs SERVICE=<service>  # Check logs
make docker-build SERVICE=<service>  # Rebuild
make docker-restart SERVICE=<service>  # Restart
\`\`\`

### Port already in use
\`\`\`bash
# Find what's using the port
lsof -i :<port>
# Kill it or change port in docker-compose.local.yml
\`\`\`

### Database issues
\`\`\`bash
make test-db-check  # Check status
make test-db-init   # Reinitialize
\`\`\`

### Complete reset
\`\`\`bash
make docker-clean   # WARNING: Removes all data
make docker-build
make docker-up
make test-db-init
\`\`\`

## Files Reference

- \`docker-compose.local.yml\` - Service definitions
- \`.env.local\` - Environment variables
- \`ssl/\` - SSL certificates
- \`srv/\` - Service source code`,
            },
          },
        ],
      };
    }

    default:
      throw new Error(`Unknown prompt: ${name}`);
  }
});

/**
 * Start the server
 */
async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error('Busibox MCP Server v2.2.0 running on stdio');
}

main().catch((error) => {
  console.error('Fatal error in main():', error);
  process.exit(1);
});
