---
created: 2025-12-11
updated: 2025-12-11
status: active
category: guides
---

# Update GitHub Token for Package Access

## When You Need This

If you see this error during deployment:

```
npm error 403 403 Forbidden - GET https://npm.pkg.github.com/...
npm error 403 Permission permission_denied: The token provided does not match expected scopes.
```

Your GitHub Personal Access Token doesn't have the `read:packages` scope.

## Required Token Scopes

Your GitHub token needs these permissions:

- ✅ **`repo`** - Full control of private repositories (for cloning repos)
- ✅ **`read:packages`** - Download packages from GitHub Package Registry (for npm packages)

## Steps to Update Token

### 1. Create or Update GitHub Token

1. Go to [GitHub Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens)
2. Click "Generate new token" (classic) or edit your existing token
3. Set a descriptive name: `Busibox Deployment Token`
4. Select these scopes:
   - ✅ `repo` (all sub-scopes)
   - ✅ `read:packages`
5. Click "Generate token" or "Update token"
6. **Copy the token** (starts with `ghp_` or `github_pat_`)

### 2. Update Ansible Vault

```bash
cd /path/to/busibox/provision/ansible

# Edit the vault (you'll be prompted for the vault password)
ansible-vault edit roles/secrets/vars/vault.yml
```

Find the `github_token` line and update it:

```yaml
secrets:
  github_token: "ghp_your_new_token_here"
  # ... rest of secrets
```

Save and exit (`:wq` in vim).

### 3. Redeploy to Update Token on Containers

The token needs to be deployed to the containers:

```bash
# Deploy to test environment
make deploy-ai-portal INV=inventory/test

# Or deploy to production
make deploy-ai-portal
```

This will:
1. Deploy the new token to `~/.github_token` on the container
2. Use it during npm install
3. Complete the deployment successfully

## Verification

After updating the token, verify it works:

```bash
# SSH into the container
ssh root@<container-ip>

# Check the token file exists and starts with the right prefix
head -c 20 ~/.github_token
# Should show: ghp_... or github_pat_...

# Test npm authentication
cd /srv/apps/ai-portal
export GITHUB_TOKEN=$(cat ~/.github_token)
npm install --dry-run
```

If the token is correct, npm should download packages without 403 errors.

## Troubleshooting

### Token Still Doesn't Work

1. **Verify token scopes on GitHub**:
   - Go to [GitHub Settings → Personal access tokens](https://github.com/settings/tokens)
   - Click on your token
   - Verify `repo` and `read:packages` are checked

2. **Check token is deployed**:
   ```bash
   ssh root@<container-ip>
   cat ~/.github_token
   ```

3. **Test token manually**:
   ```bash
   # Test with curl
   TOKEN=$(cat ~/.github_token)
   curl -H "Authorization: token $TOKEN" https://api.github.com/user
   # Should show your GitHub user info
   ```

### Token Expired

GitHub tokens can expire. If your token expired:

1. Generate a new token (follow steps above)
2. Update the vault
3. Redeploy

### Wrong Token in Vault

If you accidentally put the wrong token in the vault:

1. Edit the vault again: `ansible-vault edit roles/secrets/vars/vault.yml`
2. Update with the correct token
3. Redeploy

## Security Notes

- ✅ **Never commit tokens to git** - Always use Ansible vault
- ✅ **Use fine-grained tokens** - Only grant necessary permissions
- ✅ **Rotate tokens regularly** - Update tokens every 6-12 months
- ✅ **Use separate tokens** - Consider different tokens for different environments

## Related Documentation

- [GitHub Packages Authentication](../troubleshooting/github-packages-authentication.md)
- [Ansible Vault Usage](../reference/ansible-vault.md)





