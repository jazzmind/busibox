#!/usr/bin/env python3
"""
Parse psql pipe-separated output from config_entries and decrypt llm-keys.

Two modes:
  1. Single-value mode (used for testing):
       python3 decrypt_config_value.py <base64_blob> <passphrase>
       Outputs the decrypted plaintext to stdout.

  2. Batch mode (used by Ansible):
       echo "<psql output>" | python3 decrypt_config_value.py --batch
       Reads pipe-separated "key|value|encrypted" rows from stdin (psql -t -A -F'|').
       Outputs a JSON dict of {env_var: plaintext_value}.
       CONFIG_ENCRYPTION_KEY env var is used for decryption.

Encryption scheme mirrors srv/config/src/services/encryption.py:
  key = SHA-256(passphrase)
  ciphertext = AES-256-GCM(key, nonce=urandom(12), plaintext)
  stored = base64(nonce + ciphertext)
"""
import base64
import hashlib
import json
import os
import sys


def decrypt(stored: str, passphrase: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = hashlib.sha256(passphrase.encode()).digest()
    raw = base64.b64decode(stored)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


def batch_mode():
    enc_key = os.environ.get("CONFIG_ENCRYPTION_KEY", "")
    result = {}
    for line in sys.stdin:
        line = line.rstrip("\n")
        if not line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        k, v, encrypted = parts[0], parts[1], parts[2]
        if not k or not v:
            continue
        if encrypted == "t":
            if not enc_key:
                continue
            try:
                result[k] = decrypt(v, enc_key)
            except Exception:
                pass  # skip rows we can't decrypt
        else:
            result[k] = v
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--batch":
        batch_mode()
    elif len(sys.argv) == 3:
        try:
            print(decrypt(sys.argv[1], sys.argv[2]), end="")
        except Exception as e:
            print(f"Decryption failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage:", file=sys.stderr)
        print("  decrypt_config_value.py <base64_blob> <passphrase>", file=sys.stderr)
        print("  echo '<psql output>' | CONFIG_ENCRYPTION_KEY=<key> decrypt_config_value.py --batch", file=sys.stderr)
        sys.exit(1)
