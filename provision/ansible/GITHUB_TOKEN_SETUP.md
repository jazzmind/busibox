# GitHub Token Setup for Private Repositories

## Problem

The `jazzmind` repositories are **private**, which means:
- GitHub API returns 404 without authentication
- Cannot list releases or check for updates
- Cannot download source code or releases
- Deploywatch scripts fail

## Solution

Use a GitHub Personal Access Token (PAT) for authentication.

## Step 1: Create GitHub Personal Access Token

1. Go to **GitHub.com** → Your profile → **Settings**
2. Scroll down to **Developer settings** (bottom of left sidebar)
3. Click **Personal access tokens** → **Tokens (classic)**
4. Click **Generate new token** → **Generate new token (classic)**
5. Fill in the form:
   - **Note**: "Busibox Deployment Server"
   - **Expiration**: Choose appropriate duration (90 days, 1 year, or no expiration)
   - **Select scopes**:
     - ✅ **repo** (Full control of private repositories)
       - This includes: repo:status, repo_deployment, public_repo, repo:invite, security_events
6. Click **Generate token**
7. **COPY THE TOKEN NOW** - you won't see it again!
   - Format: `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

## Step 2: Install Token on Server

### Option A: File-based (Recommended)

```bash
# SSH into the container
ssh TEST-apps-lxc  # or appropriate container

# Create token file in root's home directory
echo "ghp_your_token_here" > ~/.github_token

# Secure the file (important!)
chmod 600 ~/.github_token

# Verify
ls -la ~/.github_token
# Should show: -rw------- 1 root root 41 Oct 29 17:00 /root/.github_token
```

### Option B: Environment Variable

```bash
# Add to ~/.bashrc or ~/.profile
echo 'export GITHUB_TOKEN="ghp_your_token_here"' >> ~/.bashrc
source ~/.bashrc

# Verify
echo $GITHUB_TOKEN
```

## Step 3: Test Authentication

```bash
# Test API access
curl -H "Authorization: token $(cat ~/.github_token)" \
  https://api.github.com/repos/jazzmind/ai-portal/releases/latest | jq -r '.tag_name'

# Should return: v0.0.1 (or the latest release tag)
# NOT: null or 404 error
```

## Step 4: Deploy Applications

```bash
cd ~/busibox/provision/ansible

# Pull latest changes (includes GitHub auth support)
git pull origin 002-deploy-app-servers

# Regenerate deploywatch scripts
ansible-playbook -i inventory/test site.yml --ask-vault-pass --tags app_deployer -e deploy_apps=false

# Test deployment
bash /srv/deploywatch/apps/ai-portal.sh

# Expected output:
# [INFO] Checking for new release
# [INFO] Current version: none
# [INFO] Latest version: v0.0.1
# [INFO] New version available: v0.0.1
# [INFO] Downloading source archive from https://github.com/jazzmind/ai-portal/archive/refs/tags/v0.0.1.tar.gz
# [SUCCESS] Downloaded source archive
# ...
```

## How It Works

The deploywatch script checks for authentication in this order:

1. **`GITHUB_TOKEN` environment variable** - If set, uses this
2. **`~/.github_token` file** - If exists, reads token from file
3. **No authentication** - Falls back to public access (will fail for private repos)

All GitHub API calls and git clones use the token if available:

```bash
# API calls
curl -H "Authorization: token $TOKEN" https://api.github.com/...

# Git clones
git clone https://$TOKEN@github.com/jazzmind/ai-portal.git
```

## Security Considerations

### Token Permissions

The token has **full repo access**, which means it can:
- ✅ Read all private repositories
- ✅ Clone and download releases
- ⚠️ Push commits (but deploywatch doesn't do this)
- ⚠️ Delete repositories (but deploywatch doesn't do this)

### Token Storage

- **File location**: `/root/.github_token`
- **Permissions**: `600` (read/write by owner only)
- **Owner**: `root`
- **Not in version control**: Never commit this file!

### Token Rotation

Tokens can expire. To rotate:

```bash
# 1. Generate new token on GitHub (same process as above)
# 2. Update token on server
echo "ghp_new_token_here" > ~/.github_token

# 3. Test
bash /srv/deploywatch/apps/ai-portal.sh
```

### Revocation

If token is compromised:

1. **Revoke on GitHub**:
   - Settings → Developer settings → Personal access tokens
   - Find the token → Delete
   
2. **Remove from server**:
   ```bash
   rm ~/.github_token
   unset GITHUB_TOKEN
   ```

## Multiple Containers

If you have multiple containers that need to deploy apps:

```bash
# Set up token on each container
for container in TEST-apps-lxc TEST-agent-lxc; do
  echo "Setting up token on $container"
  ssh $container "echo 'ghp_your_token_here' > ~/.github_token && chmod 600 ~/.github_token"
done
```

## Troubleshooting

### Still Getting 404 Errors

```bash
# Check if token file exists
ls -la ~/.github_token

# Check if token is being read
cat ~/.github_token

# Test token manually
curl -H "Authorization: token $(cat ~/.github_token)" \
  https://api.github.com/user
# Should return your GitHub user info, not 404
```

### Token Doesn't Work

```bash
# Check token format (should start with ghp_)
cat ~/.github_token

# Check token on GitHub
# Settings → Developer settings → Personal access tokens
# Verify token exists and has 'repo' scope
```

### Deploywatch Still Fails

```bash
# Run with debug output
bash -x /srv/deploywatch/apps/ai-portal.sh 2>&1 | grep -i "authorization\|token\|404"

# Check if curl is using the token
# Look for: curl -H "Authorization: token ghp_..."
```

## Alternative: GitHub App

For better security, consider using a GitHub App instead of PAT:
- More granular permissions
- Can be scoped to specific repositories
- Better audit logging
- Automatic token expiration

This would require modifying the deploywatch script to use GitHub App authentication.

## Summary

1. ✅ Create GitHub token with `repo` scope
2. ✅ Save to `~/.github_token` on server
3. ✅ Set permissions to `600`
4. ✅ Pull latest deploywatch scripts
5. ✅ Test deployment

After setup, deploywatch will automatically use the token for all GitHub operations! 🔐

