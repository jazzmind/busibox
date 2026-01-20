# Docker Development Mode

## Overview

Docker development mode allows you to run all Busibox services in Docker while editing Next.js apps (`ai-portal`, `agent-manager`) and `busibox-app` locally with hot reload.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Docker Compose                               │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  ai-portal   │  │agent-manager │  │  Python APIs         │  │
│  │  (Next.js)   │  │  (Next.js)   │  │  authz, agent, etc   │  │
│  │              │  │              │  │                      │  │
│  │  /app ←──────│──│── volume ────│──│── ../ai-portal       │  │
│  │  /app/.busibox-app ← volume ──│──│── ../busibox-app     │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  PostgreSQL  │  │    Milvus    │  │   Redis, MinIO, etc  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## How busibox-app Linking Works

### The Challenge

Next.js 16 uses Turbopack by default, which has restrictions on resolving files outside the project directory. This breaks traditional `npm link` setups.

### The Solution

1. **Mount inside project**: `busibox-app` is mounted at `/app/.busibox-app` (inside the Next.js project directory)
2. **Symlink at runtime**: The entrypoint script creates a symlink from `node_modules/@jazzmind/busibox-app` → `/app/.busibox-app`
3. **transpilePackages**: Next.js config includes `transpilePackages: ['@jazzmind/busibox-app']` to handle TypeScript source

### Volume Mounts (docker-compose.dev.yml)

```yaml
volumes:
  # Mount source code for live editing
  - ../ai-portal:/app
  # Preserve node_modules from container
  - ai-portal-node-modules:/app/node_modules
  # Preserve .next build cache
  - ai-portal-next-cache:/app/.next
  # Mount busibox-app INSIDE /app for Turbopack compatibility
  - ../busibox-app:/app/.busibox-app:ro
```

### Entrypoint Script

The `dev-entrypoint.sh` script runs at container startup:

```bash
# Create symlink to mounted busibox-app
mkdir -p /app/node_modules/@jazzmind
rm -rf /app/node_modules/@jazzmind/busibox-app
ln -s /app/.busibox-app /app/node_modules/@jazzmind/busibox-app
```

## Prerequisites

Before starting Docker dev mode:

1. **Build busibox-app on your host**:
   ```bash
   cd ~/Code/busibox-app
   npm install
   npm run build
   ```

2. **Set up environment**:
   ```bash
   cd ~/Code/busibox
   cp env.local.example .env.local
   # Edit .env.local with your GITHUB_AUTH_TOKEN
   ```

## Usage

### Start Development Mode

```bash
cd ~/Code/busibox
make docker-up ENV=development
```

Or manually:
```bash
docker compose -f docker-compose.local.yml -f docker-compose.dev.yml --env-file .env.local up -d
```

### Verify Setup

```bash
# Check if busibox-app is symlinked correctly
docker exec local-ai-portal ls -la /app/node_modules/@jazzmind/
# Should show: busibox-app -> /app/.busibox-app

# Check logs
docker logs local-ai-portal
# Should show: "✓ Symlinked: node_modules/@jazzmind/busibox-app -> /app/.busibox-app"
```

### Development Workflow

1. **Edit busibox-app**:
   ```bash
   cd ~/Code/busibox-app
   # Make changes to src/...
   npm run build  # Rebuild TypeScript
   ```

2. **Changes appear automatically** - Next.js hot reload picks up the new dist files

3. **Edit ai-portal or agent-manager**:
   ```bash
   cd ~/Code/ai-portal
   # Make changes - hot reload works automatically
   ```

## Troubleshooting

### "Module not found: Can't resolve '@jazzmind/busibox-app'"

**Cause**: Symlink not created or busibox-app not built

**Fix**:
```bash
# 1. Build busibox-app on host
cd ~/Code/busibox-app
npm run build

# 2. Restart container
docker restart local-ai-portal
```

### "distDirRoot should not navigate out of projectPath"

**Cause**: `outputFileTracingRoot` pointing outside the project

**Fix**: Don't use `outputFileTracingRoot` - the mount at `/app/.busibox-app` keeps everything inside the project.

### lightningcss "Can't resolve '../pkg'" error

**Cause**: PostCSS config format issue with Tailwind 4

**Fix**: Use object syntax in `postcss.config.mjs`:
```javascript
export default {
  plugins: {
    '@tailwindcss/postcss': {},
  },
};
```

### Changes to busibox-app not appearing

**Cause**: TypeScript not rebuilt

**Fix**:
```bash
cd ~/Code/busibox-app
npm run build
# Changes will be picked up by Next.js hot reload
```

### Container high CPU usage

**Cause**: Don't set `outputFileTracingRoot: "/"` - it causes Turbopack to scan the entire filesystem

**Fix**: Remove `outputFileTracingRoot` from next.config.ts. The mount at `/app/.busibox-app` makes it unnecessary.

## Files Involved

| File | Purpose |
|------|---------|
| `docker-compose.dev.yml` | Development overlay with volume mounts |
| `ai-portal/Dockerfile.dev` | Dev container image |
| `ai-portal/scripts/docker/dev-entrypoint.sh` | Creates symlink at startup |
| `ai-portal/next.config.ts` | `transpilePackages` for busibox-app |
| `ai-portal/postcss.config.mjs` | Tailwind 4 PostCSS config |

## Comparison: Dev vs Prod Mode

| Aspect | Development Mode | Production Mode |
|--------|------------------|-----------------|
| busibox-app source | Local volume mount | npm package from GitHub |
| ai-portal/agent-manager | Local volume mount | Cloned from GitHub |
| Hot reload | ✅ Yes | ❌ No (production build) |
| Next.js mode | Turbopack dev server | Standalone production |
| Use case | Active development | Testing prod behavior |

## See Also

- [Docker Compose Local](../../docker-compose.local.yml) - Base infrastructure
- [Docker Compose Dev](../../docker-compose.dev.yml) - Development overlay
- [Docker Compose Prod](../../docker-compose.prod.yml) - Production overlay
