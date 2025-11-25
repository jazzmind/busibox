#!/usr/bin/env node

/**
 * Busibox MCP Server
 * 
 * Provides Model Context Protocol (MCP) access to:
 * - Busibox documentation (organized by category)
 * - Script information and usage
 * - Project structure and organization rules
 * - Common maintenance tasks
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

/**
 * Documentation categories as defined in .cursor/rules/001-documentation-organization.md
 */
const DOC_CATEGORIES = [
  'architecture',
  'deployment',
  'configuration',
  'troubleshooting',
  'reference',
  'guides',
  'session-notes',
] as const;

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
 * Helper: Get documentation files by category
 */
function getDocsByCategory(category: string): Array<{ name: string; path: string }> {
  const docsDir = join(PROJECT_ROOT, 'docs', category);
  if (!existsSync(docsDir)) {
    return [];
  }

  const files = listFilesRecursive(`docs/${category}`, '*.md');
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
  timeout: number = 30000
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
 * Get container IP address by name
 */
function getContainerIP(containerName: string): string | null {
  // Hardcoded container IPs from architecture
  const containerIPs: Record<string, string> = {
    'proxy-lxc': '10.96.200.200',
    'apps-lxc': '10.96.200.201',
    'agent-lxc': '10.96.200.202',
    'pg-lxc': '10.96.200.203',
    'milvus-lxc': '10.96.200.204',
    'files-lxc': '10.96.200.205',
    'ingest-lxc': '10.96.200.206',
    'litellm-lxc': '10.96.200.207',
    'vllm-lxc': '10.96.200.208',
    'ollama-lxc': '10.96.200.210',
  };

  // Handle variations like "milvus" -> "milvus-lxc"
  const normalizedName = containerName.endsWith('-lxc') ? containerName : `${containerName}-lxc`;
  return containerIPs[normalizedName] || containerIPs[containerName] || null;
}

/**
 * Initialize MCP Server
 */
const server = new Server(
  {
    name: 'busibox-mcp-server',
    version: '1.0.0',
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
      description: 'Main architecture document',
    },
    {
      uri: 'busibox://quickstart',
      mimeType: 'text/markdown',
      name: 'Quick Start Guide',
      description: 'Quick reference for common tasks',
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
    const category = uri.replace('busibox://docs/', '');
    const docs = getDocsByCategory(category);
    
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
      content += `## ${category.charAt(0).toUpperCase() + category.slice(1)} (${docs.length} documents)\n\n`;
      
      for (const doc of docs) {
        content += `- **${doc.name}** - \`${doc.path}\`\n`;
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

  // Handle architecture document
  if (uri === 'busibox://architecture') {
    const archPath = join(PROJECT_ROOT, 'docs', 'architecture', 'architecture.md');
    const content = safeReadFile(archPath);
    
    return {
      contents: [
        {
          uri,
          mimeType: 'text/markdown',
          text: content || 'Architecture document not found',
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
              enum: [...DOC_CATEGORIES, 'all'],
              description: 'Limit search to specific documentation category',
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

      const categoriesToSearch = category === 'all' ? DOC_CATEGORIES : [category];

      for (const cat of categoriesToSearch) {
        const docs = getDocsByCategory(cat);
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
      const archPath = join(PROJECT_ROOT, 'docs', 'architecture', 'architecture.md');
      const content = safeReadFile(archPath);
      
      // Container information matching vars.env
      const containers = [
        { id: 200, name: 'proxy-lxc', ip: '10.96.200.200', purpose: 'Main reverse proxy' },
        { id: 201, name: 'apps-lxc', ip: '10.96.200.201', purpose: 'nginx and Next.js apps' },
        { id: 202, name: 'agent-lxc', ip: '10.96.200.202', purpose: 'Agent API and liteLLM' },
        { id: 203, name: 'pg-lxc', ip: '10.96.200.203', purpose: 'PostgreSQL database' },
        { id: 204, name: 'milvus-lxc', ip: '10.96.200.204', purpose: 'Milvus vector database' },
        { id: 205, name: 'files-lxc', ip: '10.96.200.205', purpose: 'MinIO for S3 storage' },
        { id: 206, name: 'ingest-lxc', ip: '10.96.200.206', purpose: 'Worker and Redis' },
        { id: 207, name: 'litellm-lxc', ip: '10.96.200.207', purpose: 'LiteLLM gateway' },
        { id: 208, name: 'vllm-lxc', ip: '10.96.200.208', purpose: 'vLLM inference server' },
        { id: 210, name: 'ollama-lxc', ip: '10.96.200.210', purpose: 'Ollama LLM server' },
      ];

      return {
        content: [
          {
            type: 'text',
            text: JSON.stringify(containers, null, 2),
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
      const { command, working_directory = '/root/busibox', timeout = 300000 } = args as {
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

1. **Check Prerequisites**:
   - Ensure you're on the admin workstation
   - Verify VPN connection to environment
   - Have Ansible vault password ready

2. **Navigate to Ansible directory**:
   \`\`\`bash
   cd provision/ansible
   \`\`\`

3. **Deploy the service**:
   \`\`\`bash
   # For specific service:
   ansible-playbook -i inventory/${environment}/hosts.yml site.yml \\
       --tags ${service} \\
       --ask-vault-pass

   # Or use the Makefile:
   make ${environment}
   \`\`\`

4. **Validate deployment**:
   \`\`\`bash
   # Run infrastructure tests:
   bash scripts/test-infrastructure.sh

   # Check specific service:
   ssh root@<container-ip>
   systemctl status ${service}
   journalctl -u ${service} -n 50
   \`\`\`

5. **Verify in browser** (if applicable):
   - Test: https://${service}.test.busibox.internal
   - Production: https://${service}.busibox.com

**Reference Documentation**:
- Deployment guide: docs/deployment/${service}.md
- Configuration: docs/configuration/${service}-configuration.md
- Troubleshooting: docs/troubleshooting/deployment-fixes.md`,
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

**Initial Steps**:
1. Check documentation in \`docs/troubleshooting/\`
2. Review relevant logs
3. Verify configuration matches environment

**For Deployment Issues**:
\`\`\`bash
# Check Ansible connection:
ansible -i inventory/test/hosts.yml all -m ping

# Run with verbose output:
ansible-playbook -i inventory/test/hosts.yml site.yml -vvv

# Check vault secrets:
ansible-vault view provision/ansible/roles/secrets/vars/vault.yml
\`\`\`

**For Container Issues**:
\`\`\`bash
# Check container status (on Proxmox host):
pct status <CTID>
pct enter <CTID>

# Inside container:
systemctl status <service>
journalctl -xe
\`\`\`

**For Service Issues**:
\`\`\`bash
# SSH into container:
ssh root@<container-ip>

# Check service:
systemctl status <service>
journalctl -u <service> -n 100 --no-pager

# Check service-specific logs:
tail -f /var/log/<service>/<service>.log
\`\`\`

**For Network Issues**:
\`\`\`bash
# Check connectivity:
ping <container-ip>
curl -v http://<container-ip>:<port>/health

# Check nginx config (on proxy-lxc or apps-lxc):
nginx -t
systemctl status nginx
\`\`\`

**Next Steps**:
- Document the issue and solution in docs/troubleshooting/
- Update relevant configuration if needed
- Test on test environment before production`,
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

# Create main tasks file:
cat > roles/${service_name}/tasks/main.yml << 'EOF'
---
- name: Install ${service_name} dependencies
  apt:
    name:
      - <list dependencies>
    state: present
    update_cache: yes

- name: Create ${service_name} user
  user:
    name: ${service_name}
    system: yes
    shell: /bin/bash
    home: /opt/${service_name}

- name: Deploy ${service_name} configuration
  template:
    src: config.yml.j2
    dest: /etc/${service_name}/config.yml
    owner: ${service_name}
    group: ${service_name}
    mode: '0644'
  notify: restart ${service_name}

- name: Deploy ${service_name} systemd service
  template:
    src: ${service_name}.service.j2
    dest: /etc/systemd/system/${service_name}.service
    mode: '0644'
  notify:
    - reload systemd
    - restart ${service_name}

- name: Enable and start ${service_name}
  systemd:
    name: ${service_name}
    enabled: yes
    state: started
EOF
\`\`\`

**5. Update Inventory**:

Edit \`provision/ansible/inventory/<env>/hosts.yml\`:
\`\`\`yaml
${service_name}:
  hosts:
    ${service_name}-lxc:
      ansible_host: {{ ip_${service_name} }}
\`\`\`

Edit \`provision/ansible/inventory/<env>/group_vars/all/00-main.yml\`:
\`\`\`yaml
# Add IP and configuration variables
ip_${service_name}: "10.96.200.<IP>"
\`\`\`

**6. Update Site Playbook**:

Edit \`provision/ansible/site.yml\`:
\`\`\`yaml
- name: Configure ${service_name}
  hosts: ${service_name}
  become: yes
  tags: ['${service_name}']
  roles:
    - role: ${service_name}
\`\`\`

**7. Create Documentation**:

\`\`\`bash
# Architecture documentation:
docs/architecture/${service_name}-design.md

# Deployment guide:
docs/deployment/${service_name}.md

# Configuration guide:
docs/configuration/${service_name}-configuration.md
\`\`\`

**8. Deploy and Test**:

\`\`\`bash
# Create container (on Proxmox host):
cd /root/busibox/provision/pct
bash create_lxc_base.sh test

# Deploy service (from admin workstation):
cd provision/ansible
ansible-playbook -i inventory/test/hosts.yml site.yml \\
    --tags ${service_name} \\
    --ask-vault-pass

# Validate:
bash scripts/test-infrastructure.sh
\`\`\`

**Reference Documentation**:
- Architecture: docs/architecture/architecture.md
- Organization rules: .cursor/rules/
- Example services: provision/ansible/roles/`,
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

**2. Choose Appropriate Filename**:

Use \`kebab-case\` for all documentation files:
- Architecture: \`${topic}-design.md\` or \`${topic}-architecture.md\`
- Deployment: \`${topic}-deployment.md\`
- Configuration: \`${topic}-configuration.md\`
- Troubleshooting: \`${topic}-fixes.md\` or \`troubleshooting-${topic}.md\`
- Reference: \`${topic}-reference.md\` or \`${topic}-api.md\`
- Session notes: \`session-YYYY-MM-DD-${topic}.md\`

**3. Create Document with Proper Structure**:

\`\`\`markdown
# ${topic.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}

**Created**: $(date +%Y-%m-%d)
**Last Updated**: $(date +%Y-%m-%d)
**Status**: Draft
**Category**: <category>
**Related Docs**: [list related documentation paths]

## Overview

[Brief description of what this document covers]

## [Main Content Sections]

...

## Related Documentation

- [Link to related docs with relative paths]

## References

- External links or additional resources
\`\`\`

**4. Follow Content Guidelines**:

- Use clear, descriptive section headings
- Include code examples with proper syntax highlighting
- Add diagrams or ASCII art for complex concepts
- Cross-reference related documentation
- Include troubleshooting tips where relevant

**5. Validate and Commit**:

\`\`\`bash
# Verify the file is in the correct location
# Check for broken links
# Commit with descriptive message:
git add docs/<category>/${topic}.md
git commit -m "docs: add ${topic} documentation to <category>"
\`\`\`

**Organization Rules Reference**:
- See \`.cursor/rules/001-documentation-organization.md\` for complete rules
- See \`docs/ORGANIZATION_RULES_SUMMARY.md\` for quick reference
- Examples: Look at existing docs in the target category`,
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

1. **Navigate to Ansible directory**:
   \`\`\`bash
   cd provision/ansible
   \`\`\`

2. **Interactive Test Menu** (Recommended):
   \`\`\`bash
   make test-menu
   \`\`\`
   
   This shows an interactive menu with all test options.

3. **Direct Test Commands**:

   ${service === 'ingest' || service === 'all' ? `
   **Ingest Service**:
   \`\`\`bash
   make test-ingest              # Unit tests
   make test-ingest-all          # All tests including integration
   make test-ingest-coverage     # With coverage report
   
   # Extraction strategy tests:
   make test-extraction-simple   # Basic PDF extraction
   make test-extraction-llm      # LLM-enhanced extraction
   make test-extraction-marker   # Marker extraction (GPU)
   make test-extraction-colpali  # ColPali visual extraction
   \`\`\`
   ` : ''}

   ${service === 'search' || service === 'all' ? `
   **Search Service**:
   \`\`\`bash
   make test-search              # All tests
   make test-search-unit         # Unit tests only
   make test-search-integration  # Integration tests only
   make test-search-coverage     # With coverage report
   \`\`\`
   ` : ''}

   ${service === 'agent' || service === 'all' ? `
   **Agent Service**:
   \`\`\`bash
   make test-agent
   \`\`\`
   ` : ''}

   ${service === 'apps' || service === 'all' ? `
   **Applications**:
   \`\`\`bash
   make test-apps
   \`\`\`
   ` : ''}

   ${service === 'all' ? `
   **All Services**:
   \`\`\`bash
   make test-all
   \`\`\`
   ` : ''}

4. **Verification**:
   \`\`\`bash
   make verify              # All health checks
   make verify-health       # Service health checks
   make verify-smoke        # Database smoke tests
   \`\`\`

For more details, see: docs/guides/testing-guide.md`,
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

1. **Check Prerequisites**:
   - Ensure you're on the admin workstation
   - Verify network connectivity to ${environment} environment
   - Have Ansible vault password ready (if using secrets)

2. **Navigate to Ansible directory**:
   \`\`\`bash
   cd provision/ansible
   \`\`\`

3. **Deploy the application**:
   \`\`\`bash
   # Using new make target (recommended):
   make deploy-${app_name} ${inv}
   
   # Or using ansible directly:
   ansible-playbook -i inventory/${environment}/hosts.yml site.yml \\
       --tags app_deployer,secrets \\
       --extra-vars "deploy_app=${app_name}" \\
       --ask-vault-pass
   \`\`\`

4. **Verify deployment**:
   \`\`\`bash
   # Check PM2 status
   ssh root@<apps-ip>
   pm2 list
   pm2 logs ${app_name}
   
   # Or use AI Portal log viewer (if deployed)
   \`\`\`

5. **Test the application**:
   - Visit the application URL
   - Check health endpoint
   - Verify functionality

**Available Applications**:
- \`ai-portal\` - AI Portal dashboard
- \`agent-client\` - Agent management UI
- \`doc-intel\` - Document intelligence
- \`foundation\` - Foundation app
- \`project-analysis\` - Project analysis
- \`innovation\` - Innovation app

**Deploy All Apps**:
\`\`\`bash
make deploy-apps ${inv}
\`\`\`

For more details, see:
- docs/deployment/ai-portal.md
- docs/guides/testing-guide.md
- ai-portal/docs/DEPLOYMENT_SYSTEM.md`,
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
  console.error('Busibox MCP Server running on stdio');
}

main().catch((error) => {
  console.error('Fatal error in main():', error);
  process.exit(1);
});








