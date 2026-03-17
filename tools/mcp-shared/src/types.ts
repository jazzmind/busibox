/**
 * Shared types for Busibox MCP servers
 */

export interface ContainerConfig {
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

export type Environment = 'production' | 'staging';

export type DeploymentModel = 'proxmox' | 'docker' | 'k8s';

export interface DocEntry {
  name: string;
  path: string;
}

export interface ScriptEntry {
  name: string;
  path: string;
  executable: boolean;
  context?: string;
}

export interface ScriptInfo {
  purpose?: string;
  context?: string;
  privileges?: string;
  dependencies?: string[];
  usage?: string;
}

export interface MakeTargetInfo {
  description: string;
  category: string;
  requiresEnv?: boolean;
  requiresVault?: boolean;
  deploymentModels?: DeploymentModel[];
  makefile?: 'root' | 'ansible';
}

export interface MainMakefileTarget {
  description: string;
  category: 'menu' | 'setup' | 'deploy' | 'test' | 'docker' | 'k8s' | 'mcp';
  variables?: Record<string, string>;
  examples?: string[];
}
