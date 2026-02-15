/**
 * Script discovery for Busibox
 * Scripts are organized by execution context per .cursor/rules/002-script-organization.md
 */

import { statSync, existsSync } from 'fs';
import { join, relative } from 'path';
import { glob } from 'glob';
import { listFilesRecursive, safeReadFile } from './docs.js';
import type { ScriptEntry, ScriptInfo } from './types.js';

export const SCRIPT_LOCATIONS = {
  'admin-workstation': 'scripts',
  'proxmox-host': 'provision/pct',
  'ansible-files': 'provision/ansible/roles/*/files',
  'ansible-templates': 'provision/ansible/roles/*/templates',
} as const;

/**
 * Get scripts from a directory
 */
export function getScriptsFromDir(
  projectRoot: string,
  dir: string
): Array<ScriptEntry & { path: string }> {
  const fullPath = join(projectRoot, dir);
  if (!dir.includes('*') && !existsSync(fullPath)) {
    return [];
  }

  const files = listFilesRecursive(projectRoot, dir, '*.{sh,py,js,ts}');

  return files.map((file) => {
    const stats = statSync(file);
    const pathFromRoot = relative(projectRoot, file);
    const pathParts = pathFromRoot.split('/');
    const name = pathParts[pathParts.length - 1] || pathFromRoot;
    return {
      name,
      path: pathFromRoot,
      executable: (stats.mode & 0o111) !== 0,
    };
  });
}

/**
 * Extract script header information from file content
 */
export function extractScriptInfo(projectRoot: string, scriptPath: string): ScriptInfo {
  const fullPath = join(projectRoot, scriptPath);
  const content = safeReadFile(fullPath);
  if (!content) return {};

  const info: ScriptInfo = {};
  const lines = content.split('\n').slice(0, 50);

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
        .map((d) => d.trim());
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
