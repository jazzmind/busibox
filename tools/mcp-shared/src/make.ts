/**
 * Make target registry for Busibox
 * Covers both top-level Makefile and provision/ansible/Makefile
 */

import type { MakeTargetInfo, MainMakefileTarget } from './types.js';

/**
 * Main Makefile targets (in project root)
 */
export const MAIN_MAKEFILE_TARGETS: Record<string, MainMakefileTarget> = {
  menu: {
    description: 'Interactive menu with environment selection and health checks (default)',
    category: 'menu',
    variables: { ENV: 'Environment: local, staging, production' },
    examples: ['make', 'make ENV=staging', 'make ENV=production'],
  },
  help: {
    description: 'Show all available commands with examples',
    category: 'menu',
    examples: ['make help'],
  },
  setup: {
    description: 'Initial setup - install dependencies (Ansible, etc.)',
    category: 'setup',
    examples: ['make setup'],
  },
  configure: {
    description: 'Configure models, GPUs, secrets (interactive wizard)',
    category: 'setup',
    examples: ['make configure'],
  },
  install: {
    description: 'Deploy services via make install (primary deployment method)',
    category: 'deploy',
    variables: {
      SERVICE: 'Service to deploy (authz, agent, data, search, etc.)',
      INV: 'Inventory: staging or production',
    },
    examples: ['make install SERVICE=authz', 'make install SERVICE=authz INV=staging'],
  },
  manage: {
    description: 'Service management (restart, logs, status, redeploy)',
    category: 'deploy',
    variables: {
      SERVICE: 'Service to manage',
      ACTION: 'start, stop, restart, logs, status, redeploy',
    },
    examples: ['make manage SERVICE=authz ACTION=restart', 'make manage SERVICE=authz ACTION=logs'],
  },
  deploy: {
    description: 'Deploy services via Ansible',
    category: 'deploy',
    variables: {
      SERVICE: 'Service to deploy',
      INV: 'Inventory: staging or production',
    },
    examples: ['make deploy', 'make deploy SERVICE=authz INV=staging'],
  },
  test: {
    description: 'Run tests on containers (via SSH)',
    category: 'test',
    variables: {
      SERVICE: 'Service to test: authz, data, search, agent, all',
      INV: 'Inventory: staging or production',
      ARGS: 'Extra pytest arguments',
    },
    examples: ['make test', 'make test SERVICE=agent INV=staging'],
  },
  'test-local': {
    description: 'Run tests locally against remote containers',
    category: 'test',
    variables: {
      SERVICE: 'Required: authz, data, search, agent, all',
      INV: 'Required: staging or production',
      FAST: 'Skip slow/GPU tests (default: 1)',
      WORKER: 'Start local data worker (default: 0)',
      ARGS: 'Extra pytest arguments',
    },
    examples: [
      'make test-local SERVICE=agent INV=staging',
      'make test-local SERVICE=all INV=production',
    ],
  },
  'test-docker': {
    description: 'Run tests against local Docker services',
    category: 'test',
    variables: {
      SERVICE: 'Required: authz, data, search, agent, all',
      FAST: 'Skip slow/GPU tests (default: 1)',
      ARGS: 'Extra pytest arguments',
    },
    examples: ['make test-docker SERVICE=agent', 'make test-docker SERVICE=all'],
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
    examples: ['make docker-build', 'make docker-build SERVICE=authz-api', 'make docker-build NO_CACHE=1'],
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
  mcp: {
    description: 'Build the MCP server for Cursor AI',
    category: 'mcp',
    examples: ['make mcp'],
  },
};

/**
 * Ansible Makefile targets (provision/ansible/Makefile)
 */
export const MAKE_TARGETS: Record<string, MakeTargetInfo> = {
  all: { description: 'Deploy all services', category: 'deployment', requiresEnv: true },
  ping: { description: 'Ping all hosts to verify connectivity', category: 'verification', requiresEnv: true },
  files: { description: 'Deploy MinIO file storage', category: 'deployment', requiresEnv: true },
  pg: { description: 'Deploy PostgreSQL database', category: 'deployment', requiresEnv: true },
  authz: { description: 'Deploy AuthZ service', category: 'deployment', requiresEnv: true },
  'deploy-api': { description: 'Deploy Deploy API service', category: 'deployment', requiresEnv: true },
  litellm: { description: 'Deploy LiteLLM gateway', category: 'deployment', requiresEnv: true },
  vllm: { description: 'Deploy vLLM inference server', category: 'deployment', requiresEnv: true },
  'vllm-embedding': { description: 'Deploy vLLM embedding model', category: 'deployment', requiresEnv: true },
  colpali: { description: 'Deploy ColPali visual model', category: 'deployment', requiresEnv: true },
  milvus: { description: 'Deploy Milvus vector database', category: 'deployment', requiresEnv: true },
  nginx: { description: 'Deploy nginx reverse proxy', category: 'deployment', requiresEnv: true },
  search: { description: 'Deploy Milvus + Search API', category: 'deployment', requiresEnv: true },
  'search-api': { description: 'Deploy Search API only', category: 'deployment', requiresEnv: true },
  agent: { description: 'Deploy Agent API', category: 'deployment', requiresEnv: true },
  data: { description: 'Deploy Data service', category: 'deployment', requiresEnv: true },
  'data-api': { description: 'Deploy Data API only', category: 'deployment', requiresEnv: true },
  'data-worker': { description: 'Deploy Data worker only', category: 'deployment', requiresEnv: true },
  apps: { description: 'Deploy all Next.js apps', category: 'deployment', requiresEnv: true },
  'deploy-apps': { description: 'Deploy all applications', category: 'app-deployment', requiresEnv: true },
  'deploy-ai-portal': { description: 'Deploy AI Portal app', category: 'app-deployment', requiresEnv: true },
  'deploy-agent-manager': { description: 'Deploy Agent Manager app', category: 'app-deployment', requiresEnv: true },
  'deploy-doc-intel': { description: 'Deploy Doc Intel app', category: 'app-deployment', requiresEnv: true },
  'deploy-foundation': { description: 'Deploy Foundation app', category: 'app-deployment', requiresEnv: true },
  'deploy-project-analysis': { description: 'Deploy Project Analysis app', category: 'app-deployment', requiresEnv: true },
  'deploy-innovation': { description: 'Deploy Innovation app', category: 'app-deployment', requiresEnv: true },
  verify: { description: 'Run all verification checks', category: 'verification', requiresEnv: true },
  'verify-health': { description: 'Service health checks', category: 'verification', requiresEnv: true },
  'verify-smoke': { description: 'Database smoke tests', category: 'verification', requiresEnv: true },
  'test-all': { description: 'Run all service tests', category: 'testing', requiresEnv: true },
  'test-data': { description: 'Run data service tests', category: 'testing', requiresEnv: true },
  'test-data-all': { description: 'Run all data tests including integration', category: 'testing', requiresEnv: true },
  'test-data-coverage': { description: 'Run data tests with coverage', category: 'testing', requiresEnv: true },
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
  configure: { description: 'Run configuration wizard', category: 'configuration' },
  'generate-token-keys': { description: 'Generate token service keys', category: 'configuration' },
  'bootstrap-test-creds': { description: 'Bootstrap test credentials', category: 'configuration', requiresEnv: true },
};
