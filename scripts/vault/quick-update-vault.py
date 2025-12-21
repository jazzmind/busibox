#!/usr/bin/env python3
"""
Quick script to update Bedrock credentials in vault
"""
import sys
import yaml
import subprocess
import tempfile
import os

vault_file = sys.argv[1]
api_key = sys.argv[2]
region = sys.argv[3] if len(sys.argv) > 3 else "us-east-1"

# Decrypt vault
result = subprocess.run(
    ['ansible-vault', 'decrypt', '--output', '-', vault_file],
    capture_output=True,
    text=True
)

if result.returncode != 0:
    print(f"Error decrypting vault: {result.stderr}")
    sys.exit(1)

# Parse YAML
try:
    vault_data = yaml.safe_load(result.stdout)
except Exception as e:
    print(f"Error parsing vault YAML: {e}")
    sys.exit(1)

# Update Bedrock credentials
if 'secrets' not in vault_data:
    vault_data['secrets'] = {}

# Store the full API key (it's base64 encoded bearer token)
vault_data['secrets']['bedrock_api_key'] = api_key
vault_data['secrets']['bedrock_region'] = region

# Add to litellm section
if 'litellm' not in vault_data['secrets']:
    vault_data['secrets']['litellm'] = {}

vault_data['secrets']['litellm']['bedrock_api_key'] = api_key

# Write to temp file
with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.yml') as tmp:
    yaml.dump(vault_data, tmp, default_flow_style=False, sort_keys=False)
    tmp_path = tmp.name

# Re-encrypt vault
result = subprocess.run(
    ['ansible-vault', 'encrypt', '--output', vault_file, tmp_path],
    capture_output=True,
    text=True
)

# Clean up
os.unlink(tmp_path)

if result.returncode != 0:
    print(f"Error encrypting vault: {result.stderr}")
    sys.exit(1)

print("✓ Vault updated successfully")

