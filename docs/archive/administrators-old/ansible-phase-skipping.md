---
title: "Ansible Phase Skipping During Reinstall"
category: "administrator"
order: 40
description: "Skip completed phases when reinstalling from an unhealthy service"
published: true
---

# Ansible Phase Skipping During Reinstall

## Problem

When `make install` detected an unhealthy service in a later installation phase (e.g., `core-apps` in the `frontend` phase) and attempted to reinstall from that point, Ansible would run through **all previous phases** even though the script specified `--tags frontend`.

### Example Scenario

1. Installation phases: `infrastructure` → `apis` → `frontend`
2. Services deployed: `postgres` (infrastructure), `authz-api` (apis), `deploy-api` (apis), `core-apps` (frontend)
3. Problem: `core-apps` container fails or is unhealthy
4. User runs: `make install` to fix it
5. **Expected**: Only reinstall `frontend` phase (core-apps)
6. **Actual**: Reinstalls all phases: infrastructure → apis → frontend

This happened because:
- Ansible's `--tags` option **includes** tasks with no tags or the `always` tag
- Many setup tasks in earlier phases don't have specific tags or use `always`
- These tasks would run even when targeting a specific later phase

## Solution

Modified the `run_ansible()` function in `scripts/make/install.sh` to use Ansible's `--skip-tags` option to explicitly skip completed phases.

### How It Works

The script now:

1. **Detects unhealthy phase**: `validate_install_health()` sets `FIRST_UNHEALTHY_PHASE`
2. **Builds skip list**: `run_ansible()` determines which phases to skip based on the unhealthy phase
3. **Applies skip-tags**: Passes `--skip-tags` to Ansible along with `--tags`

### Skip Logic

```bash
if [[ -n "$FIRST_UNHEALTHY_PHASE" ]]; then
    case "$FIRST_UNHEALTHY_PHASE" in
        infrastructure)
            # Need to run infrastructure, no skips
            skip_tags=""
            ;;
        apis)
            # Skip infrastructure phase
            skip_tags="infrastructure"
            ;;
        frontend)
            # Skip infrastructure and apis phases
            skip_tags="infrastructure,apis"
            ;;
    esac
fi
```

### User-Visible Output

When reinstalling, users now see:

```
[INFO] Running ansible with tags: frontend
[INFO] Skipping already-healthy phases: infrastructure,apis
```

This makes it clear that:
1. Only the `frontend` phase is being deployed
2. The `infrastructure` and `apis` phases are being skipped

## Benefits

### Performance Improvement

Reinstalling only the unhealthy phase is significantly faster:

- **Before**: 5-10 minutes (all phases)
- **After**: 1-2 minutes (single phase)

Actual time savings depend on:
- Number of skipped phases
- Complexity of each phase
- Network/disk speed

### Reduced Risk

By skipping healthy phases:
- No unnecessary restarts of working services
- Lower chance of introducing new issues
- Preserves existing service state and connections

### Better User Experience

- Faster reinstalls mean less waiting
- Clear logging shows what's being skipped
- Matches user expectations ("fix only what's broken")

## Phase Definitions

The installation has three phases:

1. **infrastructure**: PostgreSQL database
   - Tag: `infrastructure`
   - Services: `postgres`

2. **apis**: Core API services
   - Tag: `apis`
   - Services: `authz-api`, `deploy-api`

3. **frontend**: Web applications and proxy
   - Tag: `frontend`
   - Services: `core-apps` (includes nginx, ai-portal, agent-manager)

## Testing

To test the skip behavior:

```bash
# 1. Install normally (all phases run)
make install

# 2. Stop a frontend service to simulate failure
docker stop prod-core-apps

# 3. Run install again - should skip infrastructure and apis
make install

# Expected output:
# [INFO] Running ansible with tags: frontend
# [INFO] Skipping already-healthy phases: infrastructure,apis
```

## Ansible Tag Behavior

### Understanding --tags

The `--tags` option in Ansible:
- **Includes** tasks with the specified tag
- **Also includes** tasks with no tags
- **Also includes** tasks with the `always` tag
- **Does not skip** these untagged/always tasks

This is why `--skip-tags` is necessary.

### Understanding --skip-tags

The `--skip-tags` option:
- **Excludes** tasks with the specified tag(s)
- **Takes precedence** over `--tags`
- Works for comma-separated lists: `--skip-tags infrastructure,apis`

### Combined Behavior

When using both:
```bash
ansible-playbook --tags frontend --skip-tags infrastructure,apis site.yml
```

Ansible will:
1. Include tasks tagged with `frontend`
2. Exclude tasks tagged with `infrastructure` or `apis`
3. Include untagged tasks (but we skip them via phase tags)
4. Include `always` tasks only if not in skip list

## Related Files

- `scripts/make/install.sh` - Main installation script with `run_ansible()` function
- `provision/ansible/roles/*/tasks/*.yml` - Ansible task files with phase tags
- `provision/ansible/docker.yml` - Main Docker deployment playbook
- `provision/ansible/site.yml` - Main Proxmox/LXC deployment playbook

## Backward Compatibility

The fix is fully backward compatible:

- **Fresh installs**: No `FIRST_UNHEALTHY_PHASE` set → no skip-tags → runs all phases normally
- **Reinstalls**: `FIRST_UNHEALTHY_PHASE` set → skip-tags applied → skips healthy phases
- **Manual calls**: Works with or without the health check variables

## Future Enhancements

Potential improvements:

1. **Service-level skipping**: Skip individual healthy services within a phase
2. **Dependency detection**: Auto-detect service dependencies and only run required phases
3. **Parallel phases**: Run independent phases in parallel
4. **Progress tracking**: Show which services are being skipped vs. reinstalled

## Commit

Fixed in commit: b4fdc005dd25aee62ea98b31ddc6709ea218aa23

## See Also

- Ansible Documentation: [Tags](https://docs.ansible.com/ansible/latest/user_guide/playbooks_tags.html)
- `docs/configuration/make-commands.md` - Make command reference
- `TESTING.md` - Testing procedures
