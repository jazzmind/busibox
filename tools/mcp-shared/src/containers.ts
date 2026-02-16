/**
 * Container configuration for Busibox LXC containers
 * Supports production (10.96.200.x) and staging (10.96.201.x) environments
 */

import type { ContainerConfig, Environment } from './types.js';

export const CONTAINERS: ContainerConfig[] = [
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
    purpose: 'Next.js apps (Busibox Portal, Agent Manager, etc.)',
    ports: [{ port: 3000, service: 'Next.js apps (proxied via proxy-lxc)' }],
    services: ['nginx', 'busibox-portal', 'busibox-agents', 'doc-intel', 'foundation', 'busibox-analysis', 'innovation'],
    notes: 'No direct access to data/search; proxies internal calls',
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
    notes: 'RLS policies enforced; data/search/authz write here',
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
    name: 'data-lxc',
    ip: '10.96.200.206',
    testIp: '10.96.201.206',
    purpose: 'Data API + worker + Redis',
    ports: [
      { port: 8000, service: 'Data API' },
      { port: 6379, service: 'Redis' },
    ],
    services: ['data-api', 'data-worker', 'redis'],
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
    notes: 'Fronts vLLM/Ollama/remote providers; used by data + search',
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
    notes: 'GPU-capable local model serving; staging env uses production vLLM by default',
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
 * Get container by name or ID
 */
export function getContainer(nameOrId: string): ContainerConfig | null {
  const normalized = nameOrId.toLowerCase().replace(/-lxc$/, '');

  const byName = CONTAINERS.find(
    (c) =>
      c.name.toLowerCase() === nameOrId.toLowerCase() ||
      c.name.toLowerCase() === `${normalized}-lxc`
  );
  if (byName) return byName;

  const id = parseInt(nameOrId, 10);
  if (!isNaN(id)) {
    return CONTAINERS.find((c) => c.id === id || c.testId === id) || null;
  }

  return CONTAINERS.find((c) => c.name.toLowerCase().includes(normalized)) || null;
}

/**
 * Get container IP address by name (supports both prod and staging)
 */
export function getContainerIP(
  containerName: string,
  environment: Environment = 'production'
): string | null {
  const container = getContainer(containerName);
  if (!container) return null;
  return environment === 'staging' ? container.testIp : container.ip;
}
