---
title: "Removing Vault Files from Git History"
category: "developer"
order: 90
description: "Process for removing sensitive vault files from Git history"
published: true
---

# Removing Vault Files from Git History

## Overview

This guide documents the process of removing sensitive vault files from the entire Git history of the busibox repository. This is necessary when sensitive files were accidentally committed without encryption or with weak encryption.

## Files Removed

The following files have been removed from Git history:

- `provision/ansible/roles/secrets/vars/vault.yml`
- `provision/ansible/roles/secrets/vars/vault.prod.yml`
- `provision/ansible/roles/secrets/vars/vault.yml.cashman`
- `provision/ansible/roles/secrets/vars/vault.yml.new`

## Why This Was Necessary

These vault files contained sensitive credentials and secrets that should never have been in Git history:

1. **Database passwords**
2. **API keys and tokens**
3. **Encryption keys**
4. **Service credentials**

Even if encrypted with Ansible Vault, having the encrypted files in public history is a security risk.

## Process Used

### Tool: git-filter-repo

We used `git-filter-repo` instead of the older `git filter-branch` because it's:
- **Faster**: 10-50x faster than filter-branch
- **Safer**: Better at handling edge cases
- **Recommended**: Official Git documentation recommends it
- **Comprehensive**: Properly handles all refs, tags, and branches

### Automated Script

A script was created at `scripts/remove-vault-history.sh` that:

1. **Checks for git-filter-repo** and installs it if needed
2. **Creates a backup** of the repository before making changes
3. **Verifies no uncommitted changes** exist
4. **Fetches all remote branches** to ensure complete history rewrite
5. **Removes the files** from all commits in history
6. **Verifies removal** by checking git log
7. **Updates .gitignore** to prevent re-adding these files
8. **Provides next steps** for force-pushing and notifying collaborators

### Manual Steps Performed

```bash
# 1. Ran the removal script
cd /Users/wsonnenreich/Code/busibox
bash scripts/remove-vault-history.sh

# 2. Verified removal
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.yml
# (should show no results)

# 3. Force-pushed to remote (DESTRUCTIVE)
git push origin --force --all
git push origin --force --tags

# 4. Notified all collaborators
```

## Impact

### Repository Changes

- **History rewritten**: All commit SHAs have changed
- **Size reduced**: Repository size decreased by removing vault files from all commits
- **Remote force-pushed**: All remote branches updated with new history

### Required Actions for Collaborators

All developers with clones of this repository must:

1. **Save any uncommitted work**
2. **Delete their local clone**
3. **Re-clone the repository**

**DO NOT** try to pull or rebase - this will not work with rewritten history.

## Prevention

### .gitignore Updates

The following patterns were added to `.gitignore`:

```
vault.yml
vault.prod.yml
vault.yml.cashman
vault.yml.new
```

### Best Practices Going Forward

1. **Never commit vault files directly**
   - Use `vault.example.yml` as a template
   - Keep actual vault files only locally or in secure storage

2. **Use Ansible Vault for secrets**
   - Encrypt all sensitive data: `ansible-vault encrypt vault.yml`
   - Use vault password file: `--vault-password-file`

3. **Use .gitignore proactively**
   - Add new vault files to `.gitignore` immediately
   - Review `.gitignore` before committing new files

4. **Use pre-commit hooks**
   - Install hooks that check for unencrypted secrets
   - Use tools like `detect-secrets` or `git-secrets`

5. **Rotate compromised secrets**
   - Any secrets in the removed files should be rotated
   - Update passwords, API keys, and tokens
   - Generate new encryption keys

## Security Checklist

After removing vault files from history:

- [ ] Force-pushed to all remotes
- [ ] Notified all collaborators to re-clone
- [ ] Verified vault files don't appear in history
- [ ] Confirmed .gitignore includes vault files
- [ ] Rotated all exposed credentials
- [ ] Updated database passwords
- [ ] Regenerated API keys
- [ ] Created new encryption keys
- [ ] Updated Ansible vault with new secrets
- [ ] Tested deployments with new credentials
- [ ] Documented credential rotation in secure location

## Backup and Recovery

### Backup Location

Before running the script, a full backup was created at:
```
../busibox-backup-YYYYMMDD-HHMMSS/
```

### Recovery Process

If something went wrong and you need to restore:

```bash
# 1. Remove the modified .git directory
rm -rf /Users/wsonnenreich/Code/busibox/.git

# 2. Restore from backup
cp -r /path/to/backup/.git /Users/wsonnenreich/Code/busibox/

# 3. Verify restoration
git log --oneline | head -10
```

**Note**: Only restore from backup if you haven't force-pushed to remote yet!

## Verification

### Check Local History

```bash
# Should return no results
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.yml
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.prod.yml

# Should show files are not tracked
git ls-files | grep vault.yml
```

### Check Remote History

```bash
# After force-push, verify on GitHub/remote
# Go to repository on GitHub
# Search for: "vault.yml path:provision/ansible/roles/secrets/vars/"
# Should show no results in commit history
```

### Check File System

```bash
# Files should still exist locally (if not removed manually)
ls -la provision/ansible/roles/secrets/vars/

# But should not be tracked by git
git status | grep vault
```

## Troubleshooting

### "git-filter-repo not found"

Install git-filter-repo:
```bash
# Using pip
pip3 install git-filter-repo

# Using Homebrew (macOS)
brew install git-filter-repo
```

### "Cannot push - rejected"

If force-push is rejected:
```bash
# Ensure you have write access
git remote -v

# Use --force-with-lease for safety (checks remote hasn't changed)
git push origin --force-with-lease --all

# Or use --force if you're certain
git push origin --force --all
```

### "Collaborator can't sync after re-clone"

Collaborators must:
```bash
# Delete local clone
rm -rf busibox/

# Re-clone from remote
git clone <repository-url>

# DO NOT try to pull or fetch in old clone
```

### "Repository size didn't decrease"

Git keeps old objects for 90 days by default:
```bash
# Force garbage collection immediately
git reflog expire --expire=now --all
git gc --prune=now --aggressive

# Check size
du -sh .git/
```

## Related Documentation

- `scripts/remove-vault-history.sh` - Automated removal script
- `provision/ansible/roles/secrets/vars/vault.example.yml` - Vault template
- `.gitignore` - Git ignore patterns
- `SECURITY.md` - Security policies and procedures

## Timeline

- **2026-02-01**: Vault files removed from Git history
- **2026-02-01**: Script created for automated removal
- **2026-02-01**: .gitignore updated to prevent re-adding
- **2026-02-01**: Force-pushed to remote repository
- **2026-02-01**: Collaborators notified to re-clone

## Lessons Learned

1. **Prevent is better than cure**: Add files to `.gitignore` before first commit
2. **Review before commit**: Always review `git status` and `git diff` before committing
3. **Use pre-commit hooks**: Automate secret detection
4. **Encrypt immediately**: Use Ansible Vault encryption before committing sensitive files
5. **Regular audits**: Periodically audit repository for accidentally committed secrets

## Future Improvements

1. **Implement pre-commit hooks**: Add `detect-secrets` or `git-secrets`
2. **CI/CD secret scanning**: Add secret scanning to GitHub Actions
3. **Vault file validation**: Create script to verify vault files are encrypted
4. **Automated .gitignore checks**: Validate .gitignore includes all vault patterns
5. **Secret rotation automation**: Automate credential rotation process

## References

- [git-filter-repo documentation](https://github.com/newren/git-filter-repo)
- [GitHub: Removing sensitive data](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [Ansible Vault documentation](https://docs.ansible.com/ansible/latest/user_guide/vault.html)
- [Git filter-branch vs filter-repo](https://git-scm.com/docs/git-filter-branch#_warning)
