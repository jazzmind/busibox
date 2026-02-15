/**
 * Destructive operation detection for admin MCP server
 * Requires explicit confirm: true for dangerous operations
 */

const DESTRUCTIVE_PATTERNS = [
  /\brm\s+-rf/i,
  /\brm\s+-r\s+/i,
  /\breset\s+--hard/i,
  /\bdrop\s+(database|table|schema)/i,
  /\bforce/i,
  /\b--force\b/i,
  /\b--accept-data-loss\b/i,
  /\bprisma\s+migrate\s+reset/i,
  /\bdb\s+push\s+--force-reset/i,
  /\btruncate\s+/i,
  /\bdelete\s+from\s+/i,
  /\bpurge\b/i,
  /\bclean\b.*\b(all|volumes)\b/i,
  /\bdocker\s+system\s+prune\s+-a/i,
];

export function isDestructiveCommand(command: string): boolean {
  const normalized = command.trim().toLowerCase();
  return DESTRUCTIVE_PATTERNS.some((p) => p.test(normalized));
}

export function isDestructiveMakeTarget(target: string): boolean {
  const destructive = ['docker-clean', 'docker-clean-all', 'vault-migrate'];
  return destructive.includes(target.toLowerCase());
}
