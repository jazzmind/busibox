#!/bin/bash
# Vault decryption testing and upgrade tool.
#
# Usage:
#   test-vault-decrypt.sh <vault_file>                              # Decrypt test
#   test-vault-decrypt.sh <vault_file> --check-templates            # Check for Jinja2 templates
#   test-vault-decrypt.sh <vault_file> --strip-legacy               # Remove legacy keys
#   test-vault-decrypt.sh <vault_file> --upgrade <example_vault>    # Validate & upgrade vault
#
# Expects: ANSIBLE_VAULT_PASSWORD env var
set -euo pipefail

# Ensure ansible-vault is on PATH — busibox installs it into its own venv.
[ -x "$HOME/.busibox/venv/bin/ansible-vault" ] && export PATH="$HOME/.busibox/venv/bin:$PATH"

VAULT_FILE="${1:?Usage: test-vault-decrypt.sh <vault_file> [--check-templates|--strip-legacy|--upgrade <example>]}"
MODE="${2:-decrypt}"
PW="${ANSIBLE_VAULT_PASSWORD:-}"
PW_LEN="${#PW}"

make_pw_script() {
    local tmp
    tmp=$(mktemp)
    printf '#!/bin/sh\necho "%s"\n' "$PW" > "$tmp"
    chmod 700 "$tmp"
    echo "$tmp"
}

# Ensure pyyaml is available — ansible may use a separate Python that already has it,
# but the system python3 often does not on macOS.
if ! python3 -c "import yaml" 2>/dev/null; then
    python3 -m pip install --quiet --break-system-packages pyyaml 2>/dev/null \
        || python3 -m pip install --quiet pyyaml 2>/dev/null \
        || { echo '{"status":"error","message":"pyyaml_not_installed"}'; exit 1; }
fi

if [ "$MODE" = "--upgrade" ]; then
    EXAMPLE_FILE="${3:?--upgrade requires <example_vault_file> as third argument}"
    if [ "$PW_LEN" -eq 0 ]; then
        echo '{"status":"error","message":"vault_password_not_set"}'
        exit 1
    fi
    if [ ! -f "$EXAMPLE_FILE" ]; then
        echo '{"status":"error","message":"example_vault_not_found"}'
        exit 1
    fi

    TMP_PW=$(make_pw_script)
    TMP_PLAIN=$(mktemp)
    TMP_UPGRADED=$(mktemp)
    CREATED_NEW="false"

    if [ -f "$VAULT_FILE" ]; then
        HEAD=$(head -1 "$VAULT_FILE")
        if echo "$HEAD" | grep -q '^\$ANSIBLE_VAULT'; then
            ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP_PW" > "$TMP_PLAIN" 2>/dev/null || {
                echo '{"status":"error","message":"vault_decrypt_failed"}'
                rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_UPGRADED"
                exit 1
            }
        else
            cp "$VAULT_FILE" "$TMP_PLAIN"
        fi
    else
        CREATED_NEW="true"
        cp "$EXAMPLE_FILE" "$TMP_PLAIN"
    fi

    python3 -c "
import yaml, sys, json, secrets, string, os

with open('$TMP_PLAIN') as f:
    data = yaml.safe_load(f) or {}

with open('$EXAMPLE_FILE') as f:
    example = yaml.safe_load(f) or {}

created_new = '$CREATED_NEW' == 'true'
added = []
removed = []
copied = []
issues = []

# --- Strip legacy keys ---
LEGACY_SECRETS = ['ai_portal', 'agent_manager', 'openai_api_key']
LEGACY_TOP = ['site_domain']

if isinstance(data.get('secrets'), dict):
    for key in LEGACY_SECRETS:
        if key in data['secrets']:
            del data['secrets'][key]
            removed.append('secrets.' + key)

for key in LEGACY_TOP:
    if key in data:
        del data[key]
        removed.append(key)

# --- Remove Jinja2 template references ---
def has_jinja(val):
    if isinstance(val, str):
        return '{{' in val and '}}' in val
    if isinstance(val, dict):
        return any(has_jinja(v) for v in val.values())
    if isinstance(val, list):
        return any(has_jinja(v) for v in val)
    return False

def strip_jinja_values(d, path=''):
    if not isinstance(d, dict):
        return
    keys_to_remove = []
    for k, v in list(d.items()):
        full = f'{path}.{k}' if path else k
        if isinstance(v, dict):
            strip_jinja_values(v, full)
            if not v:
                keys_to_remove.append(k)
        elif has_jinja(v):
            keys_to_remove.append(k)
            removed.append(full)
    for k in keys_to_remove:
        del d[k]

if isinstance(data.get('secrets'), dict):
    strip_jinja_values(data['secrets'], 'secrets')

# --- Generate random values ---
def gen_random(placeholder):
    if not isinstance(placeholder, str):
        return placeholder
    p = placeholder.upper()
    if 'PASSWORD' in p or 'SECRET' in p:
        return secrets.token_urlsafe(24)
    if 'KEY' in p and 'API' in p:
        return 'sk-' + secrets.token_hex(24)
    if 'KEY' in p:
        return secrets.token_hex(32)
    if 'TOKEN' in p:
        return secrets.token_hex(24)
    return secrets.token_hex(32)

def is_placeholder(val):
    if not isinstance(val, str):
        return False
    return 'CHANGE_ME' in val

# --- Walk example schema, add missing keys ---
def ensure_keys(actual, example_data, path=''):
    if not isinstance(example_data, dict):
        return
    if not isinstance(actual, dict):
        return
    for key, example_val in example_data.items():
        full = f'{path}.{key}' if path else key
        if isinstance(example_val, dict):
            if key not in actual:
                actual[key] = {}
                added.append(full)
            ensure_keys(actual[key], example_val, full)
        elif key not in actual:
            if is_placeholder(example_val):
                actual[key] = gen_random(example_val)
                added.append(full)
            elif example_val == '' or example_val is None:
                actual[key] = ''
                added.append(full)
            else:
                actual[key] = example_val
                added.append(full)
        elif created_new and is_placeholder(actual.get(key, '')):
            actual[key] = gen_random(actual[key])

# Top-level keys from example (ssl_email etc)
for key, val in example.items():
    if key == 'secrets':
        continue
    if key not in data:
        if is_placeholder(val):
            # Top-level placeholders are usually user-specific (email, domain)
            # Mark as issue rather than generating random
            data[key] = val
            issues.append({'key': key, 'reason': 'placeholder_needs_user_input'})
        else:
            data[key] = val
            added.append(key)

# Ensure secrets structure
if 'secrets' not in data:
    data['secrets'] = {}

# --- Fallback copies (before ensure_keys so copies take priority over random generation) ---
s = data['secrets']

# litellm_salt_key: copy from master if missing or placeholder
salt = s.get('litellm_salt_key', '')
if (not salt or is_placeholder(str(salt))) and s.get('litellm_master_key') and not is_placeholder(str(s['litellm_master_key'])):
    s['litellm_salt_key'] = s['litellm_master_key']
    copied.append('litellm_salt_key <- litellm_master_key')
elif (not salt or is_placeholder(str(salt))) and s.get('litellm_api_key') and not is_placeholder(str(s['litellm_api_key'])):
    s['litellm_salt_key'] = s['litellm_api_key']
    copied.append('litellm_salt_key <- litellm_api_key')

# encryption_key: ensure config_api.encryption_key exists if encryption_key does
if s.get('encryption_key') and isinstance(s.get('config_api'), dict):
    if not s['config_api'].get('encryption_key'):
        s['config_api']['encryption_key'] = s['encryption_key']
        copied.append('config_api.encryption_key <- encryption_key')
elif isinstance(s.get('config_api'), dict) and s['config_api'].get('encryption_key') and not s.get('encryption_key'):
    s['encryption_key'] = s['config_api']['encryption_key']
    copied.append('encryption_key <- config_api.encryption_key')

# --- Add missing keys from example schema ---
example_secrets = example.get('secrets', {})
ensure_keys(data['secrets'], example_secrets, 'secrets')

# --- Check for remaining issues ---
# Required keys that must be non-empty and non-placeholder
REQUIRED = [
    'secrets.postgresql.password',
    'secrets.minio.root_user',
    'secrets.minio.root_password',
    'secrets.neo4j.password',
    'secrets.jwt_secret',
    'secrets.session_secret',
    'secrets.authz_master_key',
    'secrets.litellm_master_key',
    'secrets.litellm_salt_key',
    'secrets.admin_emails',
]

for req in REQUIRED:
    parts = req.split('.')
    val = data
    for p in parts:
        if isinstance(val, dict):
            val = val.get(p)
        else:
            val = None
            break
    if val is None or val == '' or is_placeholder(str(val)):
        if not any(i['key'] == req for i in issues):
            issues.append({'key': req, 'reason': 'required_empty_or_placeholder'})

changed = len(added) > 0 or len(removed) > 0 or len(copied) > 0

if changed:
    with open('$TMP_UPGRADED', 'w') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, width=120)

non_placeholder_issues = [i for i in issues if i['reason'] != 'placeholder_needs_user_input' or created_new]

result = {
    'status': 'created' if created_new else ('upgraded' if changed else 'clean'),
    'added': added,
    'removed': removed,
    'copied': copied,
    'issues': non_placeholder_issues,
    'changed': changed,
}
print(json.dumps(result))
" 2>&1

    PYTHON_RC=$?
    if [ $PYTHON_RC -ne 0 ]; then
        echo '{"status":"error","message":"python_failed"}'
        rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_UPGRADED"
        exit 1
    fi

    if [ -s "$TMP_UPGRADED" ]; then
        if [ -f "$VAULT_FILE" ]; then
            cp "$VAULT_FILE" "${VAULT_FILE}.bak"
        fi
        cp "$TMP_UPGRADED" "$VAULT_FILE"
        ansible-vault encrypt "$VAULT_FILE" --vault-password-file "$TMP_PW" 2>/dev/null || {
            echo '{"status":"error","message":"re_encrypt_failed"}'
            if [ -f "${VAULT_FILE}.bak" ]; then
                cp "${VAULT_FILE}.bak" "$VAULT_FILE"
            fi
            rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_UPGRADED"
            exit 1
        }
    fi

    rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_UPGRADED"
    exit 0

elif [ "$MODE" = "--check-templates" ]; then
    if [ ! -f "$VAULT_FILE" ]; then
        echo "vault_file_missing=true"
        exit 1
    fi
    if [ "$PW_LEN" -eq 0 ]; then
        echo "vault_password_not_set=true"
        exit 1
    fi

    TMP_PW=$(make_pw_script)
    CONTENT=$(ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP_PW" 2>/dev/null) || {
        echo "vault_decrypt_failed=true"
        rm -f "$TMP_PW"
        exit 1
    }
    rm -f "$TMP_PW"

    LEGACY_KEYS=()
    TEMPLATE_REFS=()

    while IFS= read -r line; do
        if echo "$line" | grep -qE '\{\{[^}]+\}\}'; then
            TEMPLATE_REFS+=("$line")
        fi
    done <<< "$CONTENT"

    for key in ai_portal agent_manager openai_api_key; do
        if echo "$CONTENT" | grep -qE "^  ${key}:"; then
            LEGACY_KEYS+=("$key")
        fi
    done

    if [ ${#TEMPLATE_REFS[@]} -eq 0 ] && [ ${#LEGACY_KEYS[@]} -eq 0 ]; then
        echo "vault_clean=true"
        exit 0
    fi

    if [ ${#LEGACY_KEYS[@]} -gt 0 ]; then
        echo "legacy_keys_found=${LEGACY_KEYS[*]}"
    fi
    if [ ${#TEMPLATE_REFS[@]} -gt 0 ]; then
        echo "template_refs_found=${#TEMPLATE_REFS[@]}"
        for ref in "${TEMPLATE_REFS[@]}"; do
            echo "  template_ref: $ref"
        done
    fi
    exit 1

elif [ "$MODE" = "--strip-legacy" ]; then
    if [ ! -f "$VAULT_FILE" ]; then
        echo "vault_file_missing=true"
        exit 1
    fi
    if [ "$PW_LEN" -eq 0 ]; then
        echo "vault_password_not_set=true"
        exit 1
    fi

    TMP_PW=$(make_pw_script)
    TMP_PLAIN=$(mktemp)
    TMP_CLEAN=$(mktemp)

    ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP_PW" > "$TMP_PLAIN" 2>/dev/null || {
        echo "vault_decrypt_failed=true"
        rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_CLEAN"
        exit 1
    }

    python3 -c "
import yaml, sys

with open('$TMP_PLAIN') as f:
    data = yaml.safe_load(f)

legacy_removed = []
if isinstance(data.get('secrets'), dict):
    for key in ['ai_portal', 'agent_manager', 'openai_api_key']:
        if key in data['secrets']:
            del data['secrets'][key]
            legacy_removed.append(key)

for key in ['site_domain']:
    if key in data:
        del data[key]
        legacy_removed.append(key)

if not legacy_removed:
    print('no_legacy_keys_found=true')
    sys.exit(1)

with open('$TMP_CLEAN', 'w') as f:
    yaml.dump(data, f, default_flow_style=False, allow_unicode=True, width=120)

for k in legacy_removed:
    print(f'removed_key={k}')
"

    STRIP_RC=$?
    if [ $STRIP_RC -ne 0 ]; then
        rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_CLEAN"
        exit 0
    fi

    cp "$VAULT_FILE" "${VAULT_FILE}.bak"

    cp "$TMP_CLEAN" "$VAULT_FILE"
    ansible-vault encrypt "$VAULT_FILE" --vault-password-file "$TMP_PW" 2>/dev/null || {
        echo "re_encrypt_failed=true"
        cp "${VAULT_FILE}.bak" "$VAULT_FILE"
        rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_CLEAN"
        exit 1
    }

    echo "vault_cleaned=true"
    echo "backup_at=${VAULT_FILE}.bak"
    rm -f "$TMP_PW" "$TMP_PLAIN" "$TMP_CLEAN"
    exit 0

else
    # Default: decrypt test mode
    if [ "$PW_LEN" -gt 8 ]; then
        echo "vault_password_length=$PW_LEN"
        echo "vault_password_preview=${PW:0:4}...${PW: -4}"
    elif [ "$PW_LEN" -gt 0 ]; then
        echo "vault_password_length=$PW_LEN"
        echo "vault_password_preview=TOO_SHORT"
    else
        echo "vault_password_length=0"
        echo "vault_password_preview=NOT_SET"
    fi

    if [ ! -f "$VAULT_FILE" ]; then
        echo "vault_file_exists=false"
        exit 0
    fi
    echo "vault_file_exists=true"

    HEAD=$(head -1 "$VAULT_FILE")
    echo "vault_file_header=$HEAD"

    if echo "$HEAD" | grep -q '^\$ANSIBLE_VAULT'; then
        echo "vault_encrypted=true"
        TMP=$(make_pw_script)
        if ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP" > /dev/null 2>&1; then
            echo "vault_decrypt_test=SUCCESS"
            KEYS=$(ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP" 2>/dev/null | grep -E '^[a-z_]+:' | head -5)
            echo "vault_top_keys=$KEYS"
        else
            echo "vault_decrypt_test=FAILED"
            ansible-vault view "$VAULT_FILE" --vault-password-file "$TMP" 2>&1 | head -3
        fi
        rm -f "$TMP"
    else
        echo "vault_encrypted=false"
        KEYS=$(grep -E '^[a-z_]+:' "$VAULT_FILE" | head -5)
        echo "vault_top_keys=$KEYS"
    fi
fi
