# Ansible Scripts

Scripts in this directory are used directly by Ansible playbooks and the Ansible Makefile.

These are **copies** of scripts that may also exist in the main `scripts/` directory.
The reason for having copies here:

1. **Path Independence**: Ansible playbooks use relative paths from `provision/ansible/`
2. **Deployment Context**: Some scripts are copied to remote containers during deployment
3. **Self-contained**: Ansible operations should work without relying on parent directory structure

## Scripts

| Script | Purpose | Called By |
|--------|---------|-----------|
| `bootstrap-test-credentials.sh` | Creates test users and OAuth clients | `make bootstrap-test-creds` |
| `generate-token-service-keys.sh` | Generates Ed25519 keys for token service | `make generate-token-keys` |
| `generate_jwk_keys.py` | Python key generator (used by above) | generate-token-service-keys.sh |
| `test-signal-bot.sh` | Tests Signal bot integration | `make test-signal-bot` |
| `view-app-logs.sh` | View application logs on containers | Copied to containers via `apps` role |
| `tail-app-logs.sh` | Tail application logs on containers | Copied to containers via `apps` role |

## Updating Scripts

If you need to update these scripts, consider whether the change should also be made
to the corresponding script in the main `scripts/` directory:

- Main `scripts/` versions are used by `make` commands from the repo root
- These Ansible versions are used when running commands from `provision/ansible/`

## Note on Terminology

- Environment names: `local`, `staging`, `production`
- "test" refers only to running tests, not an environment
