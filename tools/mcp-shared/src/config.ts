/**
 * Configuration for MCP servers
 */

import { join } from 'path';
import { homedir } from 'os';
import { dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

/**
 * Resolve project root. When running from dist/, go up to busibox root.
 * mcp-shared dist is at tools/mcp-shared/dist/
 * So we need to go: dist -> mcp-shared -> tools -> busibox (3 levels up)
 */
export function getProjectRoot(fromDistDir: string): string {
  return join(fromDistDir, '..', '..', '..');
}

/**
 * Default project root (when used from within busibox)
 */
export const DEFAULT_PROJECT_ROOT = getProjectRoot(__dirname);

export const PROXMOX_HOST_IP = process.env.PROXMOX_HOST_IP || '10.96.200.1';
export const PROXMOX_HOST_USER = process.env.PROXMOX_HOST_USER || 'root';
export const PROXMOX_SSH_KEY_PATH =
  process.env.PROXMOX_SSH_KEY_PATH || join(homedir(), '.ssh', 'id_rsa');
export const CONTAINER_SSH_KEY_PATH =
  process.env.CONTAINER_SSH_KEY_PATH || join(homedir(), '.ssh', 'id_rsa');
export const BUSIBOX_PATH_ON_PROXMOX =
  process.env.BUSIBOX_PATH_ON_PROXMOX || '/root/busibox';
