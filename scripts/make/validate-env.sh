#!/bin/bash
# Validate container environment variables match vault secrets.
# Resolves vault password using the same fallback chain as service-deploy.sh:
#   1. ANSIBLE_VAULT_PASSWORD env var
#   2. VAULT_PASS_FILE env var
#   3. ~/.vault_pass file
#
# Execution context: admin workstation (via make validate-env)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_SCRIPT="${REPO_ROOT}/scripts/lib/vault-pass-from-env.sh"

VAULT_PASS_ARG=""
if [[ -n "${ANSIBLE_VAULT_PASSWORD:-}" ]]; then
    [[ -x "$ENV_SCRIPT" ]] || chmod +x "$ENV_SCRIPT"
    VAULT_PASS_ARG="--vault-password-file ${ENV_SCRIPT}"
elif [[ -n "${VAULT_PASS_FILE:-}" && -f "${VAULT_PASS_FILE}" ]]; then
    VAULT_PASS_ARG="--vault-password-file ${VAULT_PASS_FILE}"
elif [[ -f "$HOME/.vault_pass" ]]; then
    VAULT_PASS_ARG="--vault-password-file $HOME/.vault_pass"
else
    echo "⚠ Skipping environment validation (no vault password available)"
    exit 0
fi

cd "${REPO_ROOT}/provision/ansible"
# shellcheck disable=SC2086
ansible-playbook -i inventory/docker docker.yml --tags validate_env ${VAULT_PASS_ARG}
