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
  'k8s-deploy': {
    description: 'Full K8s deployment: secrets + manifests + rollout restart',
    category: 'k8s',
    variables: {
      SERVICE: 'Optional: deploy a single service',
      K8S_OVERLAY: 'Kustomize overlay (default: rackspace-spot)',
      K8S_TAG: 'Image tag (default: git short SHA)',
    },
    examples: ['make k8s-deploy', 'make k8s-deploy SERVICE=authz-api'],
  },
  'k8s-apply': {
    description: 'Apply K8s manifests only (images must already exist)',
    category: 'k8s',
    variables: { K8S_OVERLAY: 'Kustomize overlay' },
    examples: ['make k8s-apply'],
  },
  'k8s-secrets': {
    description: 'Generate and apply K8s secrets from Ansible vault',
    category: 'k8s',
    variables: { K8S_OVERLAY: 'Kustomize overlay' },
    examples: ['make k8s-secrets'],
  },
  'k8s-status': {
    description: 'Show K8s deployment status (pods, services)',
    category: 'k8s',
    variables: { K8S_OVERLAY: 'Kustomize overlay' },
    examples: ['make k8s-status'],
  },
  'k8s-logs': {
    description: 'View K8s pod logs',
    category: 'k8s',
    variables: {
      SERVICE: 'Required: pod name (e.g. authz-api, data-api)',
      K8S_OVERLAY: 'Kustomize overlay',
    },
    examples: ['make k8s-logs SERVICE=authz-api'],
  },
  'k8s-delete': {
    description: 'Delete all K8s resources',
    category: 'k8s',
    variables: { K8S_OVERLAY: 'Kustomize overlay' },
    examples: ['make k8s-delete'],
  },
  'k8s-sync': {
    description: 'Sync code to in-cluster build server (legacy)',
    category: 'k8s',
    variables: { SERVICE: 'Optional: sync a single service' },
    examples: ['make k8s-sync', 'make k8s-sync SERVICE=authz-api'],
  },
  'k8s-build': {
    description: 'Build images on in-cluster build server (legacy)',
    category: 'k8s',
    variables: { SERVICE: 'Optional: build a single service' },
    examples: ['make k8s-build', 'make k8s-build SERVICE=authz-api'],
  },
  connect: {
    description: 'Start HTTPS tunnel to K8s cluster',
    category: 'k8s',
    variables: {
      DOMAIN: 'Local domain (default: busibox.local)',
      LOCAL_PORT: 'Local port (default: 443)',
    },
    examples: ['make connect', 'make connect DOMAIN=my.local LOCAL_PORT=8443'],
  },
  disconnect: {
    description: 'Stop HTTPS tunnel to K8s cluster',
    category: 'k8s',
    examples: ['make disconnect'],
  },
  mcp: {
    description: 'Build the MCP server for Cursor AI',
    category: 'mcp',
    examples: ['make mcp'],
  },
};

/**
 * Ansible Makefile targets (provision/ansible/Makefile)
 * These are Proxmox-only targets that run via SSH on the Proxmox host.
 */
export const ANSIBLE_MAKE_TARGETS: Record<string, MakeTargetInfo> = {
  all: { description: 'Deploy all services', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  ping: { description: 'Ping all hosts to verify connectivity', category: 'verification', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  files: { description: 'Deploy MinIO file storage', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  pg: { description: 'Deploy PostgreSQL database', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  authz: { description: 'Deploy AuthZ service', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-api': { description: 'Deploy Deploy API service', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  litellm: { description: 'Deploy LiteLLM gateway', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  vllm: { description: 'Deploy vLLM inference server', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'vllm-embedding': { description: 'Deploy vLLM embedding model', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  colpali: { description: 'Deploy ColPali visual model', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  milvus: { description: 'Deploy Milvus vector database', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  nginx: { description: 'Deploy nginx reverse proxy', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  search: { description: 'Deploy Milvus + Search API', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'search-api': { description: 'Deploy Search API only', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  agent: { description: 'Deploy Agent API', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  data: { description: 'Deploy Data service', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'data-api': { description: 'Deploy Data API only', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'data-worker': { description: 'Deploy Data worker only', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  apps: { description: 'Deploy all Next.js apps', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'apps-frontend': { description: 'Deploy frontend apps (portal, agents, appbuilder)', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  bridge: { description: 'Deploy Bridge service (messaging integrations)', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  docs: { description: 'Deploy Docs API service', category: 'deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-apps': { description: 'Deploy all applications (app_deployer + secrets + nginx)', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-frontend': { description: 'Deploy all frontend apps from busibox-frontend monorepo', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-portal': { description: 'Deploy Busibox Portal app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-admin': { description: 'Deploy Busibox Admin app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-agents': { description: 'Deploy Agent Manager app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-chat': { description: 'Deploy Busibox Chat app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-appbuilder': { description: 'Deploy Busibox App Builder', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-media': { description: 'Deploy Busibox Media app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-busibox-documents': { description: 'Deploy Busibox Documents app', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'deploy-app-ref': { description: 'Deploy a specific app from a branch/tag (APP= REF=)', category: 'app-deployment', requiresEnv: true, requiresVault: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  verify: { description: 'Run all verification checks', category: 'verification', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'verify-health': { description: 'Service health checks', category: 'verification', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'verify-smoke': { description: 'Database smoke tests', category: 'verification', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-all': { description: 'Run all service tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-data': { description: 'Run data service tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-data-all': { description: 'Run all data tests including integration', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-data-coverage': { description: 'Run data tests with coverage', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-search': { description: 'Run search service tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-search-unit': { description: 'Run search unit tests only', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-search-integration': { description: 'Run search integration tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-search-coverage': { description: 'Run search tests with coverage', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-agent': { description: 'Run agent service tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-agent-unit': { description: 'Run agent unit tests only', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-agent-integration': { description: 'Run agent integration tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-agent-coverage': { description: 'Run agent tests with coverage', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-authz': { description: 'Run authz service tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-apps': { description: 'Run app tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-security': { description: 'Run security tests', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-extraction-simple': { description: 'Test simple PDF extraction', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-extraction-llm': { description: 'Test LLM-enhanced extraction', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-extraction-marker': { description: 'Test Marker extraction (GPU)', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  'test-extraction-colpali': { description: 'Test ColPali visual extraction', category: 'testing', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
  configure: { description: 'Run configuration wizard', category: 'configuration', deploymentModels: ['proxmox'], makefile: 'ansible' },
  'generate-token-keys': { description: 'Generate token service keys', category: 'configuration', deploymentModels: ['proxmox'], makefile: 'ansible' },
  'bootstrap-test-creds': { description: 'Bootstrap test credentials', category: 'configuration', requiresEnv: true, deploymentModels: ['proxmox'], makefile: 'ansible' },
};

/**
 * @deprecated Use ANSIBLE_MAKE_TARGETS instead. Kept for backward compatibility.
 */
export const MAKE_TARGETS = ANSIBLE_MAKE_TARGETS;

/**
 * All targets across both Makefiles, unified for MCP server lookups.
 * Merges root Makefile targets (as MakeTargetInfo) with Ansible targets.
 */
export function getAllMakeTargets(): Record<string, MakeTargetInfo> {
  const rootTargets: Record<string, MakeTargetInfo> = {};
  for (const [name, info] of Object.entries(MAIN_MAKEFILE_TARGETS)) {
    const deploymentModels: import('./types.js').DeploymentModel[] = [];
    if (info.category === 'docker') deploymentModels.push('docker');
    else if (info.category === 'k8s') deploymentModels.push('k8s');
    else deploymentModels.push('proxmox', 'docker', 'k8s');

    const requiresVault = ['deploy', 'setup'].includes(info.category) ||
      name === 'k8s-secrets' || name === 'k8s-deploy';

    rootTargets[name] = {
      description: info.description,
      category: info.category,
      requiresEnv: !!info.variables?.['INV'] || !!info.variables?.['ENV'],
      requiresVault,
      deploymentModels,
      makefile: 'root',
    };
  }
  return { ...rootTargets, ...ANSIBLE_MAKE_TARGETS };
}
