---
title: "Git History Cleanup - Vault Files Removal Plan"
category: "developer"
order: 91
description: "Plan for removing vault files from Git history"
published: true
---

# Vault Files Git History Removal Plan

## Current Situation

### Files Currently Tracked in Git
- `provision/ansible/roles/secrets/vars/vault.prod.yml` ✓ (tracked, has history)
- `provision/ansible/roles/secrets/vars/vault.yml` (not currently in working tree)
- `provision/ansible/roles/secrets/vars/vault.yml.cashman` (deleted, staged)
- `provision/ansible/roles/secrets/vars/vault.yml.new` (deleted, staged)

### Current Git Status
```
On branch refactor
Changes to be committed:
  modified:   .gitignore
  modified:   provision/ansible/roles/secrets/vars/vault.prod.yml
  deleted:    provision/ansible/roles/secrets/vars/vault.yml.cashman
  deleted:    provision/ansible/roles/secrets/vars/vault.yml.new

Untracked files:
  docs/security/
  scripts/remove-vault-history.sh
```

### Files in History
All 4 files have extensive history in Git:
```bash
git log --all --full-history --oneline -- vault.yml | head -5
dfb304da feat: Enhance runtime installation...
1c5a9094 feat: Update Docker Compose...
9c8e0fcd feat: Rename Ingest API...
092e0176 feat(embedding): Introduce dedicated...
1b8e982b Merge pull request #2...
```

## Execution Plan

### Phase 1: Commit Current Changes (BEFORE history rewrite)

Since there are already staged changes, we need to commit them first:

```bash
cd /Users/wsonnenreich/Code/busibox

# Commit the current changes
git add docs/security/ scripts/remove-vault-history.sh
git commit -m "chore: prepare for vault history removal

- Delete vault.yml.cashman and vault.yml.new from tracking
- Update .gitignore patterns
- Add removal script and documentation"
```

### Phase 2: Remove vault.prod.yml from Tracking

Before rewriting history, remove the file from current tracking:

```bash
# Remove from git tracking but keep local file
git rm --cached provision/ansible/roles/secrets/vars/vault.prod.yml

# Commit the removal
git commit -m "chore: stop tracking vault.prod.yml"
```

### Phase 3: Rewrite Git History

Run the automated script:

```bash
bash scripts/remove-vault-history.sh
```

This will:
1. Install git-filter-repo if needed
2. Create backup at `../busibox-backup-YYYYMMDD-HHMMSS/`
3. Remove all 4 files from entire history
4. Verify removal
5. Update .gitignore if needed

### Phase 4: Verify Removal

```bash
# Should return NO results
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.yml
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.prod.yml
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.yml.cashman
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/vault.yml.new

# Should show files are not tracked
git ls-files | grep vault
```

### Phase 5: Force Push to Remote

⚠️ **DESTRUCTIVE - Point of No Return**

```bash
# Push to refactor branch
git push origin refactor --force

# If you want to clean main/master as well:
git checkout main
git pull origin main
bash scripts/remove-vault-history.sh  # Run again on main
git push origin main --force

# Push all branches
git push origin --force --all

# Push tags
git push origin --force --tags
```

### Phase 6: Notify Collaborators

Send notification to all team members:

```
URGENT: busibox Repository History Rewrite

The busibox repository history has been rewritten to remove sensitive vault files.

ACTION REQUIRED:
1. Save any uncommitted work
2. Delete your local busibox clone
3. Re-clone from GitHub

DO NOT try to pull or rebase in your existing clone - it will not work.

Files removed from history:
- vault.yml
- vault.prod.yml
- vault.yml.cashman
- vault.yml.new

Questions? Contact [admin]
```

### Phase 7: Security Follow-up

After history is cleaned:

- [ ] Rotate all database passwords
- [ ] Regenerate API keys
- [ ] Create new encryption keys
- [ ] Update Ansible vault with new secrets
- [ ] Test deployments with new credentials
- [ ] Document credential rotation

## Alternative: Manual git-filter-repo Command

If you prefer to run git-filter-repo manually instead of using the script:

```bash
# Install git-filter-repo
pip3 install git-filter-repo

# Remove files from history
git filter-repo --invert-paths \
  --path provision/ansible/roles/secrets/vars/vault.yml \
  --path provision/ansible/roles/secrets/vars/vault.prod.yml \
  --path provision/ansible/roles/secrets/vars/vault.yml.cashman \
  --path provision/ansible/roles/secrets/vars/vault.yml.new \
  --force

# Verify
git log --all --full-history --oneline -- provision/ansible/roles/secrets/vars/
```

## Rollback Plan

If something goes wrong BEFORE force-pushing:

```bash
# Restore from backup
BACKUP_DIR="../busibox-backup-YYYYMMDD-HHMMSS"
rm -rf .git
cp -r $BACKUP_DIR/.git .
git status
```

If something goes wrong AFTER force-pushing:
- You CANNOT undo the force push
- All collaborators must deal with the new history
- Only option is to restore from backup and NOT force-push

## Risks and Considerations

### High Risk Items
1. **Destructive operation** - Cannot be undone after force-push
2. **All collaborators affected** - Everyone must re-clone
3. **Active development disruption** - Coordinate timing carefully
4. **Forks may be affected** - External forks will have old history

### Mitigation
1. **Backup created automatically** by script
2. **Test on refactor branch first** before cleaning main
3. **Notify collaborators in advance**
4. **Schedule during low-activity period**
5. **Keep backup for 30 days** minimum

## Timeline Recommendation

1. **T-24h**: Notify collaborators of planned maintenance
2. **T-1h**: Freeze commits to repository
3. **T-0**: Execute phases 1-4 (commit, remove, rewrite, verify)
4. **T+15min**: Review and test locally
5. **T+30min**: Force push to remote (phase 5)
6. **T+35min**: Notify collaborators to re-clone
7. **T+1day**: Verify all collaborators have updated
8. **T+7days**: Begin credential rotation (phase 7)

## Success Criteria

- [ ] No vault files appear in `git log --all`
- [ ] Files still exist locally (not deleted from disk)
- [ ] .gitignore prevents re-adding files
- [ ] All branches force-pushed successfully
- [ ] All collaborators have re-cloned
- [ ] Deployments work with existing secrets
- [ ] No git errors or corruption

## Resources

- Script: `scripts/remove-vault-history.sh`
- Documentation: `docs/security/vault-history-removal.md`
- Backup: Will be at `../busibox-backup-YYYYMMDD-HHMMSS/`
- git-filter-repo: https://github.com/newren/git-filter-repo

## Questions to Answer Before Proceeding

1. **Are all collaborators aware?** ⚠️
2. **Is now a good time?** (low development activity) ⚠️
3. **Do you have repository write access?** ⚠️
4. **Are there any open pull requests?** (will be invalidated) ⚠️
5. **Are CI/CD pipelines paused?** (may fail during rewrite) ⚠️
6. **Do you have a backup plan?** ✓ (script creates automatic backup)

## Next Steps

1. Review this plan
2. Answer questions above
3. Notify collaborators
4. Execute Phase 1 (commit current changes)
5. Execute Phase 2 (remove vault.prod.yml from tracking)
6. Execute Phase 3 (run removal script)
7. Verify thoroughly before force-push
8. Coordinate force-push with team
9. Monitor collaborator re-clones
10. Plan credential rotation
