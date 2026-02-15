/**
 * Documentation discovery and search for Busibox
 * Updated to match current docs structure: administrators/, developers/, users/, archive/
 */

import { readFileSync, existsSync } from 'fs';
import { join, relative } from 'path';
import { glob } from 'glob';
import type { DocEntry } from './types.js';

/**
 * Documentation categories - matches current docs structure
 */
export const DOC_CATEGORIES = ['administrators', 'developers', 'users', 'archive'] as const;

/**
 * Nested documentation paths for deeper searches
 */
export const DOC_NESTED_PATHS: Record<string, string[]> = {
  developers: [
    'developers/architecture',
    'developers/services',
    'developers/reference',
    'developers/tasks',
  ],
  'developers/services': [
    'developers/services/agents',
    'developers/services/authz',
    'developers/services/data',
    'developers/services/deploy',
    'developers/services/docs',
    'developers/services/search',
    'developers/services/bridge',
  ],
};

/**
 * Safe read file with error handling
 */
export function safeReadFile(path: string): string | null {
  try {
    return readFileSync(path, 'utf-8');
  } catch {
    return null;
  }
}

/**
 * List files in directory recursively
 */
export function listFilesRecursive(
  projectRoot: string,
  dir: string,
  pattern: string = '*'
): string[] {
  try {
    const fullPath = join(projectRoot, dir);
    if (!dir.includes('*') && !existsSync(fullPath)) {
      return [];
    }
    return glob.sync(join(fullPath, '**', pattern), { nodir: true });
  } catch {
    return [];
  }
}

/**
 * Get documentation files by category or path
 */
export function getDocsByCategory(
  projectRoot: string,
  categoryOrPath: string
): DocEntry[] {
  const docsDir = join(projectRoot, 'docs', categoryOrPath);
  if (!existsSync(docsDir)) {
    return [];
  }

  const files = listFilesRecursive(projectRoot, `docs/${categoryOrPath}`, '*.md');
  return files.map((file) => ({
    name: relative(docsDir, file),
    path: relative(projectRoot, file),
  }));
}

/**
 * Search documentation by query
 */
export function searchDocs(
  projectRoot: string,
  query: string,
  categoryOrPath: string
): Array<{ file: string; matches: string[] }> {
  const results: Array<{ file: string; matches: string[] }> = [];
  const docs = getDocsByCategory(projectRoot, categoryOrPath);
  const queryLower = query.toLowerCase();

  for (const doc of docs) {
    const content = safeReadFile(join(projectRoot, doc.path));
    if (!content) continue;

    const lines = content.split('\n');
    const matches: string[] = [];

    lines.forEach((line, idx) => {
      if (line.toLowerCase().includes(queryLower)) {
        const start = Math.max(0, idx - 1);
        const end = Math.min(lines.length, idx + 2);
        const context = lines.slice(start, end).join('\n');
        matches.push(`Line ${idx + 1}:\n${context}\n`);
      }
    });

    if (matches.length > 0) {
      results.push({
        file: doc.path,
        matches: matches.slice(0, 5),
      });
    }
  }

  return results;
}
