# Dev Apps Directory

This directory is a placeholder. For local development, you should configure
`DEV_APPS_DIR` to point to your code directory.

## Setup

1. **Configure DEV_APPS_DIR** via the interactive menu:
   ```bash
   make configure
   # Select: Docker Configuration
   # Select: Configure Dev Apps Directory
   # Enter: /Users/yourname/Code  (or wherever your apps live)
   ```

2. **Your code directory structure** should look like:
   ```
   /Users/yourname/Code/
     estimator/           <- Your app with busibox.json
     busibox-analysis/    <- Another app with busibox.json
     my-custom-app/       <- etc.
   ```

3. **Restart Docker** to pick up the mount:
   ```bash
   make docker-down && make docker-up
   ```

4. **Register apps in Busibox Portal** with "Development Mode" enabled
   - Toggle "Development Mode" ON
   - Enter the directory name (e.g., `estimator`)
   - The system validates the directory contains a valid `busibox.json`

## Important: No Symlinks!

Docker volume mounts don't follow symlinks. Your apps must be actual directories
within DEV_APPS_DIR, not symlinks.

**This works:**
```
DEV_APPS_DIR=/Users/you/Code
/Users/you/Code/estimator/busibox.json  # actual file
```

**This does NOT work:**
```
DEV_APPS_DIR=/Users/you/busibox/dev-apps
/Users/you/busibox/dev-apps/estimator -> /Users/you/Code/estimator  # symlink - won't work!
```

## Hot-Reload Development

For hot-reload during active development:
```bash
docker exec -it local-user-apps bash
cd /srv/dev-apps/estimator && npm run dev
```

## How It Works

When DEV_APPS_DIR is configured:
1. The Makefile reads it from `.busibox-state`
2. Docker mounts it at `/srv/dev-apps/` in containers
3. Deploy-api checks `/srv/dev-apps/{app-id}/` for local source
4. If found, it uses local source instead of git cloning
5. Changes to source files are immediately visible in the container
