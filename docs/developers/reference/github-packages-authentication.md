---
title: "GitHub Packages Authentication"
category: "developer"
order: 110
description: "npm authentication for private GitHub Packages deployment"
published: true
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

There are two requirements for npm to authenticate with GitHub Packages:

1. **The `.npmrc` configuration file** must be present in the project root
2. **The `GITHUB_TOKEN` environment variable** must be set when running npm

The `.npmrc` file tells npm how to authenticate:

```
@jazzmind:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
```

### The Problem

The `.npmrc` file was **gitignored** in the busibox-portal repository, so it wasn't included when GitHub created the deployment tarball. Without this file, npm had no way to know it should use the GITHUB_TOKEN for authentication, even when the token was available.

## Solution

The deployment scripts now automatically create the `.npmrc` file and set the `GITHUB_TOKEN` environment variable:

### 1. Create `.npmrc` During Deployment

The deployment scripts create the `.npmrc` file in the deployment directory:

**Branch Deployment (deploy-branch.yml)**:
```yaml
- name: Create .npmrc for GitHub Packages authentication
  copy:
    content: |
      @jazzmind:registry=https://npm.pkg.github.com
      //npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
    dest: "{{ app_to_deploy.deploy_path }}/.npmrc"
    mode: '0600'

- name: Install dependencies
  shell: |
    cd {{ app_to_deploy.deploy_path }}
    if [ -f ~/.github_token ]; then
      export GITHUB_TOKEN=$(cat ~/.github_token)
    fi
    npm install --production=false
```

**Release Deployment (deploywatch-app.sh.j2)**:
```bash
install_dependencies() {
    cd "${DEPLOY_PATH}"
    
    # Export GitHub token
    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        export GITHUB_TOKEN
    fi
    
    # Create .npmrc for GitHub Packages authentication
    if [[ -f "package.json" ]]; then
        cat > .npmrc << 'EOF'
@jazzmind:registry=https://npm.pkg.github.com
//npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
EOF
    fi
    
    # ... rest of npm install logic
}
```

This approach is simpler and more reliable than depending on the `.npmrc` file being in the repository.

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
make deploy-busibox-portal INV=staging  # Or whichever app needs it
make deploy-busibox-portal  # Production
```

### Updating Token Scopes (403 Errors)

If you see `403 Forbidden` or `permission_denied: The token provided does not match expected scopes`, the token lacks the `read:packages` scope:

1. Go to [GitHub Settings → Developer settings → Personal access tokens](https://github.com/settings/tokens)
2. Edit your token and ensure these scopes are checked:
   - `repo` (Full control of private repositories)
   - `read:packages` (Download packages from GitHub Package Registry)
3. Update the token in vault (see above) and redeploy

## Verification

### 1. Check Token is Deployed

SSH into the container and verify the token file exists:

```bash
ssh root@<container-ip>
cat ~/.github_token
# Should show your token (starts with ghp_ or github_pat_)
```

### 2. Check `.npmrc` is Created

After deployment, verify the `.npmrc` file was created:

```bash
ssh root@<container-ip>
cat /srv/apps/busibox-portal/.npmrc
# Should show:
# @jazzmind:registry=https://npm.pkg.github.com
# //npm.pkg.github.com/:_authToken=${GITHUB_TOKEN}
```

### 3. Test npm Authentication Manually

On the container, test if npm can authenticate:

```bash
cd /srv/apps/busibox-portal
export GITHUB_TOKEN=$(cat ~/.github_token)
npm install --dry-run
```

If it works, you should see packages being resolved without 401 errors.

### 4. Test Full Deployment

Deploy the application:

```bash
cd /path/to/busibox/provision/ansible
make deploy-busibox-portal INV=staging
```

Watch for the "Install dependencies" task - it should complete without 401 errors.

## Related Files

- **Ansible vault**: `provision/ansible/roles/secrets/vars/vault.yml`
- **Token deployment**: `provision/ansible/roles/app_deployer/tasks/main.yml` (lines 77-89)
- **Branch deployment**: `provision/ansible/roles/app_deployer/tasks/deploy-branch.yml` (lines 134-144)
- **Release deployment**: `provision/ansible/roles/app_deployer/templates/deploywatch-app.sh.j2` (line 324)

## Common Issues

### `.npmrc` File Not Created

**Symptom**: 401 errors, `.npmrc` not found in deployment directory

**Solution**: 
The deployment scripts should automatically create the `.npmrc` file. If it's not being created, check that the deployment script has been updated with the latest changes.

### Token Has Wrong Permissions

**Symptom**: 401 errors persist even after `.npmrc` is present

**Solution**: 
1. Check token permissions on GitHub (needs `read:packages`)
2. Generate new token if needed
3. Update vault and redeploy

### Token Not Deployed to Container

**Symptom**: `~/.github_token` file doesn't exist on container

**Solution**: Redeploy the app (deploys token via app_deployer role):
```bash
cd provision/ansible
make deploy-busibox-portal INV=staging
```

### GITHUB_TOKEN Not Set

**Symptom**: `.npmrc` exists but npm still can't authenticate

**Solution**: Ensure the `GITHUB_TOKEN` environment variable is being set during npm install. Check the deployment script exports the token from `~/.github_token`.

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
