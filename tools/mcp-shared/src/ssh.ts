/**
 * SSH execution helpers for Proxmox host and containers
 */

import { readFileSync } from 'fs';
import { Client as SSHClient } from 'ssh2';

/**
 * Read SSH private key from file
 */
export function readSSHKey(keyPath: string): string | null {
  try {
    return readFileSync(keyPath, 'utf-8');
  } catch {
    return null;
  }
}

/**
 * Execute SSH command on remote host
 */
export async function executeSSHCommand(
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
