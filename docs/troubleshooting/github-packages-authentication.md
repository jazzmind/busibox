---
created: 2025-12-11
updated: 2025-12-11
status: active
category: troubleshooting
---

# GitHub Packages Authentication for npm

## Problem

When deploying applications that depend on private GitHub Packages (like `@jazzmind/busibox-ui`), npm fails with:

```
npm error code E401
npm error 401 Unauthorized - GET https://npm.pkg.github.com/download/@jazzmind/busibox-ui/...
npm error authentication token not provided
```

This occurs even though:
- The GitHub token is stored in Ansible vault
- The token is deployed to `~/.github_token` on the container
- The token works for cloning private repositories

## Root Cause

The GitHub token is available in the `~/.github_token` file, but npm needs it as an **environment variable** named `GITHUB_TOKEN` to authenticate with GitHub Packages.

Applications using GitHub Packages have a `.npmrc` file like:

```
@jazzmind:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
```

The `${GITHUB_TOKEN}` placeholder requires the environment variable to be set.

## Solution

The deployment scripts now export the `GITHUB_TOKEN` environment variable before running `npm install`:

### 1. Branch Deployment (deploy-branch.yml)

```yaml
- name: Install dependencies
  shell: |
    cd {{ app_to_deploy.deploy_path }}
    # Export GitHub token for npm authentication to GitHub Packages
    if [ -f ~/.github_token ]; then
      export GITHUB_TOKEN=$(cat ~/.github_token)
    fi
    npm install --production=false
```

### 2. Release Deployment (deploywatch-app.sh.j2)

```bash
install_dependencies() {
    cd "${DEPLOY_PATH}"
    
    # Export GitHub token for npm authentication to GitHub Packages
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        export GITHUB_TOKEN
        log_info "GitHub token available for npm authentication"
    fi
    
    # ... rest of npm install logic
}
```

## GitHub Token Permissions

For npm to access GitHub Packages, the GitHub Personal Access Token needs:

1. **`read:packages`** - Download packages from GitHub Packages
2. **`repo`** - Access private repositories (already required for cloning)

### Verifying Token Permissions

1. Go to GitHub Settings → Developer settings → Personal access tokens
2. Find your token (or create a new one)
3. Ensure these scopes are checked:
   - ✅ `repo` (Full control of private repositories)
   - ✅ `read:packages` (Download packages from GitHub Package Registry)

### Updating Token in Ansible Vault

If you need to update the token:

```bash
cd /path/to/busibox/provision/ansible
ansible-vault edit roles/secrets/vars/vault.yml
```

Update the `github_token` value:

```yaml
secrets:
  github_token: "ghp_your_new_token_here"
```

Then redeploy to update the token on containers:

```bash
make deploy-ai-portal  # Or whichever app needs it
```

## Verification

### 1. Check Token is Deployed

SSH into the container and verify the token file exists:

```bash
ssh root@<container-ip>
cat ~/.github_token
# Should show your token (starts with ghp_)
```

### 2. Test npm Authentication Manually

On the container, test if npm can authenticate:

```bash
cd /srv/apps/ai-portal
export GITHUB_TOKEN=$(cat ~/.github_token)
npm install --dry-run
```

If it works, you should see packages being resolved without 401 errors.

### 3. Test Full Deployment

Deploy the application:

```bash
cd /path/to/busibox/provision/ansible
make deploy-ai-portal INV=inventory/test
```

Watch for the "Install dependencies" task - it should complete without 401 errors.

## Related Files

- **Ansible vault**: `provision/ansible/roles/secrets/vars/vault.yml`
- **Token deployment**: `provision/ansible/roles/app_deployer/tasks/main.yml` (lines 77-89)
- **Branch deployment**: `provision/ansible/roles/app_deployer/tasks/deploy-branch.yml` (line 127)
- **Release deployment**: `provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2` (line 324)

## Common Issues

### Token Has Wrong Permissions

**Symptom**: 401 errors persist even after fix

**Solution**: 
1. Check token permissions on GitHub (needs `read:packages`)
2. Generate new token if needed
3. Update vault and redeploy

### Token Not Deployed to Container

**Symptom**: `~/.github_token` file doesn't exist on container

**Solution**:
```bash
cd provision/ansible
# Redeploy app_deployer role to deploy token
ansible-playbook -i inventory/test/hosts.yml site.yml --tags app_deployer
```

### .npmrc File Missing or Incorrect

**Symptom**: npm doesn't try to use GitHub Packages

**Solution**: Ensure the application has a `.npmrc` file in its root:

```
@jazzmind:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
```

## Prevention

When creating new applications that use GitHub Packages:

1. ✅ Add `.npmrc` file to repository root
2. ✅ Ensure deployment scripts export `GITHUB_TOKEN`
3. ✅ Test deployment on test environment first
4. ✅ Verify token has `read:packages` permission

## References

- [GitHub Packages Documentation](https://docs.github.com/en/packages)
- [npm Authentication with GitHub Packages](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-npm-registry)
- [Personal Access Token Scopes](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
