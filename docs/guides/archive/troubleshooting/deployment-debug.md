# Debugging Deployment Failures

Based on your output, all 4 apps failed to deploy. Let's debug systematically.

## Step 1: Check Deploywatch Logs

```bash
# Check the deploywatch logs for each app
tail -100 /var/log/deploywatch/ai-portal.log
tail -100 /var/log/deploywatch/agent-client.log
tail -100 /var/log/deploywatch/doc-intel.log
tail -100 /var/log/deploywatch/innovation.log

# Or check all at once
for app in ai-portal agent-client doc-intel innovation; do
  echo "=== $app ==="
  tail -50 /var/log/deploywatch/$app.log
  echo ""
done
```

## Step 2: Check Service Status

```bash
systemctl list-units --type=service --state=running | grep -E '(ai-portal|agent-client|doc-intel|innovation)'
journalctl -u ai-portal.service -n 50 --no-pager
```

## Step 3: Check GitHub Access

The deploywatch scripts need to access GitHub. Check if the container can reach GitHub:

```bash
# Test GitHub connectivity
curl -I https://api.github.com

# Try to access one of the repos
curl -I https://api.github.com/repos/jazzmind/ai-portal

# Check if git is installed
which git
git --version
```

## Step 4: Manual Deployment Test

Try running one of the deploywatch scripts manually to see the full error:

```bash
# Run ai-portal deployment manually (verbose)
bash -x /srv/deploywatch/apps/ai-portal.sh 2>&1 | tee /tmp/ai-portal-manual.log

# Check the output
cat /tmp/ai-portal-manual.log
```

## Step 5: Check Environment Files

```bash
# Check if .env files were created
ls -la /srv/apps/ai-portal/.env
ls -la /srv/apps/agent-client/.env
ls -la /srv/apps/doc-intel/.env
ls -la /srv/apps/innovation/.env

# Verify environment files have content (don't cat them, they contain secrets)
wc -l /srv/apps/*/.env
```

## Step 6: Check Directory Permissions

```bash
# Check ownership and permissions
ls -la /srv/apps/
ls -la /srv/deploywatch/apps/
```

## Common Issues

### Issue 1: GitHub Private Repos

If the repos are private, deploywatch needs a GitHub token:

```bash
# Check if GITHUB_TOKEN is set
echo $GITHUB_TOKEN

# If not, add to deploywatch script or environment
```

### Issue 2: Missing Dependencies

```bash
# Check if required tools are installed
which node npm git curl jq

# Check Node.js version
node --version
npm --version
```

### Issue 3: Network/DNS Issues

```bash
# Check DNS resolution
nslookup api.github.com
nslookup github.com

# Check network connectivity
ping -c 3 github.com
```

### Issue 4: Disk Space

```bash
# Check available disk space
df -h /srv/apps
```

## Quick Fix: Force Clean Redeployment

If you want to start fresh:

```bash
# Stop all services
systemctl stop ai-portal.service agent-client.service doc-intel.service innovation.service

# Remove all app directories
rm -rf /srv/apps/ai-portal
rm -rf /srv/apps/agent-client
rm -rf /srv/apps/doc-intel
rm -rf /srv/apps/innovation

# Re-run ansible to recreate directories and .env files
cd ~/busibox/provision/ansible
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer

# Try manual deployment of one app first
bash -x /srv/deploywatch/apps/ai-portal.sh
```

## Expected Deployment Flow

1. **Download**: Clone or download release from GitHub
2. **Extract**: Unzip release to temp directory
3. **Move**: Copy files to `/srv/apps/{app-name}/`
4. **Install**: Run `npm install --production`
5. **Build**: Run build command (e.g., `npm run build`)
6. **Start**: Start with systemd (`systemctl start {app-name}.service`)
7. **Health Check**: Verify `/api/health` responds with 200

Any of these steps can fail. The logs will show which step failed.

