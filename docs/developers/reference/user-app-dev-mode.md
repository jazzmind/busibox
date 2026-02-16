---
title: "User App Development Mode"
category: "developer"
order: 114
description: "Develop and test user apps locally with hot-reload support"
published: true
---

# User App Development Mode

This guide explains how to develop and test user apps locally with hot-reload support.

## Overview

When developing user apps (external apps deployed via Busibox Portal), you have two options:

1. **Full Deployment**: Busibox Portal clones from GitHub, builds, and runs via systemd
2. **Dev Mode**: Mount local source for hot-reload development

Dev mode is recommended for active development as it provides:
- Instant file sync (no git push required)
- Hot-reload via `npm run dev`
- Same container environment as production

## Setup

### 1. Create Symlink in dev-apps Directory

The `dev-apps/` directory in the busibox repo is pre-mounted into the `user-apps` container.

```bash
cd /path/to/busibox/dev-apps

# Symlink your app (use your app's ID from busibox.json)
ln -s ~/Code/my-app ./my-app
ln -s ~/Code/busibox-analysis ./busibox-analysis
```

### 2. Start Docker Services

```bash
cd /path/to/busibox
make docker-up  # Uses dev mode by default
```

### 3. Register App in Busibox Portal

1. Go to Busibox Portal Admin -> Apps
2. Click "Add App"
3. Enter your GitHub repo URL
4. The manifest will be validated
5. Toggle "Development Mode" if you want to use local source

### 4. Deploy (or Skip for Pure Dev)

If you deploy via Busibox Portal:
- Deploy-api detects `/srv/dev-apps/{app-id}` exists
- Skips git clone, uses your local source
- Runs npm install, build, and creates systemd service

For pure hot-reload development, skip the deploy and run manually:

```bash
docker exec -it local-user-apps bash
cd /srv/dev-apps/my-app
npm install
npm run dev
```

## Directory Structure

```
busibox/
  dev-apps/
    .gitkeep
    README.md
    my-app/          -> symlink to ~/Code/my-app
    busibox-analysis/ -> symlink to ~/Code/busibox-analysis
```

Inside the container:

```
/srv/
  apps/              # Production-deployed apps (git cloned)
    deployed-app-1/
    deployed-app-2/
  dev-apps/          # Development apps (mounted from host)
    my-app/
    busibox-analysis/
```

## How It Works

1. **docker-compose.dev.yml** mounts `./dev-apps:/srv/dev-apps`
2. When you trigger a deployment, **deploy-api** checks:
   - Does `/srv/dev-apps/{app-id}` exist?
   - If yes: use that path (skip git clone)
   - If no: clone from GitHub to `/srv/apps/{app-id}`
3. Build and systemd setup work the same either way

## Hot-Reload Development

For full hot-reload support, run the dev server directly:

```bash
# Enter the container
docker exec -it local-user-apps bash

# Navigate to your app
cd /srv/dev-apps/my-app

# Install dependencies (first time only)
npm install

# Start dev server
npm run dev
```

Your app will be available at `http://localhost:{port}` (check your app's port).

Changes to source files on your host are immediately reflected in the container.

## Production-Like Testing

To test with production build and systemd:

1. Deploy via Busibox Portal (triggers build + systemd service)
2. App runs via systemd, logs to journalctl
3. Check logs: `docker exec local-user-apps journalctl -u my-app -f`

## Multiple Apps

You can run multiple dev apps simultaneously:

```bash
# In separate terminals or use tmux/screen
docker exec -it local-user-apps bash -c "cd /srv/dev-apps/app1 && npm run dev"
docker exec -it local-user-apps bash -c "cd /srv/dev-apps/app2 && npm run dev"
```

Just ensure each app uses a different port (defined in busibox.json).

## Troubleshooting

### Symlink not working

Make sure you're using absolute paths or paths relative to the dev-apps directory:

```bash
# Good - absolute path
ln -s /Users/myuser/Code/my-app ./my-app

# Good - relative path (if dev-apps is sibling to Code)
ln -s ../../Code/my-app ./my-app

# Bad - path that doesn't exist from the container's perspective
ln -s ~/Code/my-app ./my-app  # ~ expands to host home, not container
```

### Changes not appearing

1. Check the symlink is valid: `ls -la dev-apps/`
2. Make sure you saved the file
3. For TypeScript, ensure `npm run dev` is running (not a production build)

### Container can't find app

Verify the mount:
```bash
docker exec local-user-apps ls -la /srv/dev-apps/
```

### npm install fails

The container may need additional system dependencies. For common issues:

```bash
docker exec -it local-user-apps bash
apt-get update && apt-get install -y build-essential python3
```

## Related Documentation

- [Docker Development Mode](docker-dev-mode.md)
- [04-apps](../../administrators/04-apps.md) - Application management
