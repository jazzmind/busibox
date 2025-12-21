#!/usr/bin/env python3
"""
update-vault.py - Update Ansible vault with missing secrets

EXECUTION CONTEXT: Admin workstation
DIRECTORY: scripts/
RUN FROM: Repository root

DESCRIPTION:
  Compares the encrypted vault.yml with vault.example.yml and prompts
  for missing secrets or values that still have CHANGE_ME placeholders.
  Automatically handles Jinja2 templates without prompting.

USAGE:
  python3 scripts/update-vault.py
  python3 scripts/update-vault.py --vault-password-file ~/.vault_pass

REQUIREMENTS:
  - Python 3.7+
  - PyYAML: pip3 install pyyaml
  - ansible-vault command

EXAMPLES:
  # Interactive mode
  python3 scripts/update-vault.py

  # Using password file
  python3 scripts/update-vault.py --vault-password-file ~/.vault_pass

EXIT CODES:
  0 - Success
  1 - General error
  2 - Missing dependencies
  3 - Vault decryption failed

AUTHOR: Busibox Team
CREATED: 2025-11-06
"""

import os
import sys
import argparse
import subprocess
import tempfile
import shutil
import re
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is not installed")
    print("Install with: pip3 install pyyaml")
    sys.exit(2)

# ANSI color codes
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color

def log_info(msg: str):
    print(f"{Colors.BLUE}[INFO]{Colors.NC} {msg}")

def log_success(msg: str):
    print(f"{Colors.GREEN}[SUCCESS]{Colors.NC} {msg}")

def log_warn(msg: str):
    print(f"{Colors.YELLOW}[WARN]{Colors.NC} {msg}")

def log_error(msg: str):
    print(f"{Colors.RED}[ERROR]{Colors.NC} {msg}")

def is_jinja_template(value: str) -> bool:
    """Check if value is a Jinja2 template"""
    if not isinstance(value, str):
        return False
    return '{{' in value and '}}' in value

def needs_change(value: str) -> bool:
    """Check if value starts with CHANGE_ME"""
    if not isinstance(value, str):
        return False
    return value.startswith('CHANGE_ME')

def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
    """Flatten nested dictionary into dot-notation keys"""
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def unflatten_dict(d: Dict[str, Any], sep: str = '.') -> Dict[str, Any]:
    """Convert dot-notation keys back to nested dictionary"""
    result = {}
    for key, value in d.items():
        parts = key.split(sep)
        current = result
        for part in parts[:-1]:
            if part not in current:
                current[part] = {}
            current = current[part]
        current[parts[-1]] = value
    return result

def get_nested_value(d: Dict[str, Any], key: str, sep: str = '.') -> Any:
    """Get value from nested dict using dot notation"""
    keys = key.split(sep)
    value = d
    for k in keys:
        if isinstance(value, dict) and k in value:
            value = value[k]
        else:
            return None
    return value

def set_nested_value(d: Dict[str, Any], key: str, value: Any, sep: str = '.'):
    """Set value in nested dict using dot notation"""
    keys = key.split(sep)
    current = d
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value

def get_key_description(example_content: str, key: str) -> str:
    """Extract comment description for a key from the example file"""
    # Convert dot notation to YAML path
    parts = key.split('.')
    indent_level = len(parts) - 1
    key_name = parts[-1]
    
    # Search for the key in the file
    lines = example_content.split('\n')
    for i, line in enumerate(lines):
        # Check if this line contains our key
        if re.search(rf'^\s*{re.escape(key_name)}:', line):
            # Look for comments above this line
            comments = []
            j = i - 1
            while j >= 0:
                prev_line = lines[j]
                if re.match(r'^\s*#', prev_line):
                    comment = re.sub(r'^\s*#\s*', '', prev_line)
                    comments.insert(0, comment)
                    j -= 1
                elif prev_line.strip() == '':
                    j -= 1
                else:
                    break
            return ' '.join(comments)
    return ''

def prompt_for_value(key: str, current_value: Any, description: str) -> Optional[str]:
    """Prompt user for a value"""
    print()
    print(f"{Colors.BLUE}{'━' * 70}{Colors.NC}")
    print(f"{Colors.YELLOW}Key:{Colors.NC} {key}")
    if description:
        print(f"{Colors.YELLOW}Description:{Colors.NC} {description}")
    if current_value is not None and not (isinstance(current_value, str) and needs_change(current_value)):
        print(f"{Colors.YELLOW}Current value:{Colors.NC} {current_value}")
    print(f"{Colors.BLUE}{'━' * 70}{Colors.NC}")
    
    try:
        value = input("Enter value (or press Enter to skip): ").strip()
        if not value:
            if current_value is not None and not (isinstance(current_value, str) and needs_change(current_value)):
                return str(current_value)
            return None
        return value
    except (KeyboardInterrupt, EOFError):
        print()
        return None

def decrypt_vault(vault_file: Path, output_file: Path, vault_password_args: List[str]) -> bool:
    """Decrypt the vault file"""
    cmd = ['ansible-vault', 'decrypt'] + vault_password_args + ['--output', str(output_file), str(vault_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

def encrypt_vault(input_file: Path, output_file: Path, vault_password_args: List[str]) -> bool:
    """Encrypt the vault file"""
    cmd = ['ansible-vault', 'encrypt'] + vault_password_args + ['--output', str(output_file), str(input_file)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0

def main():
    parser = argparse.ArgumentParser(description='Update Ansible vault with missing secrets')
    parser.add_argument('--vault-password-file', help='Path to vault password file')
    args = parser.parse_args()
    
    # Setup paths
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    vault_file = repo_root / 'provision/ansible/roles/secrets/vars/vault.yml'
    example_file = repo_root / 'provision/ansible/roles/secrets/vars/vault.example.yml'
    
    log_info("Busibox Vault Update Script")
    print()
    
    # Check if ansible-vault is available
    if shutil.which('ansible-vault') is None:
        log_error("ansible-vault command not found")
        log_info("Install with: pip3 install ansible")
        sys.exit(2)
    
    log_success("Dependencies check passed")
    
    # Check if files exist
    if not vault_file.exists():
        log_error(f"Vault file not found: {vault_file}")
        sys.exit(1)
    
    if not example_file.exists():
        log_error(f"Example file not found: {example_file}")
        sys.exit(1)
    
    # Prepare vault password arguments
    vault_password_args = []
    if args.vault_password_file:
        vault_password_args = ['--vault-password-file', args.vault_password_file]
    
    # Create temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        decrypted_vault = temp_path / 'vault.yml'
        updated_vault = temp_path / 'vault_updated.yml'
        
        # Decrypt vault
        log_info("Decrypting vault file...")
        if not decrypt_vault(vault_file, decrypted_vault, vault_password_args):
            log_error("Failed to decrypt vault file")
            log_info("Make sure you have the correct vault password")
            sys.exit(3)
        log_success("Vault decrypted")
        
        # Load YAML files
        log_info("Loading vault files...")
        with open(example_file, 'r') as f:
            example_data = yaml.safe_load(f)
            example_content = f.read()
        
        with open(decrypted_vault, 'r') as f:
            current_data = yaml.safe_load(f)
        
        # Reset file pointer to read content
        with open(example_file, 'r') as f:
            example_content = f.read()
        
        # Flatten dictionaries for easier comparison
        example_flat = flatten_dict(example_data)
        current_flat = flatten_dict(current_data)
        
        log_info("Analyzing vault structure...")
        print()
        log_info("Checking for missing or CHANGE_ME values...")
        
        changes_made = 0
        keys_processed = 0
        
        # Iterate through example keys
        for key, example_value in sorted(example_flat.items()):
            keys_processed += 1
            
            # Skip if example value is None
            if example_value is None:
                continue
            
            current_value = current_flat.get(key)
            
            # If it's a Jinja template in example, handle it
            if isinstance(example_value, str) and is_jinja_template(example_value):
                if current_value is None:
                    log_info(f"Adding Jinja template: {key}")
                    current_flat[key] = example_value
                    changes_made += 1
                continue
            
            # Check if value needs to be changed
            should_prompt = False
            
            if current_value is None:
                should_prompt = True
            elif isinstance(current_value, str) and needs_change(current_value):
                should_prompt = True
            
            if should_prompt:
                description = get_key_description(example_content, key)
                new_value = prompt_for_value(key, current_value, description)
                
                if new_value is not None:
                    log_info(f"Updating: {key}")
                    current_flat[key] = new_value
                    changes_made += 1
                else:
                    log_warn(f"Skipped: {key}")
        
        print()
        log_info(f"Processed {keys_processed} keys")
        
        if changes_made > 0:
            log_success(f"{changes_made} values updated")
            
            # Ask for confirmation
            print()
            try:
                confirm = input("Do you want to save these changes? (y/N): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print()
                confirm = 'n'
            
            if confirm in ['y', 'yes']:
                # Unflatten the dictionary back to nested structure
                updated_data = unflatten_dict(current_flat)
                
                # Write updated vault
                with open(updated_vault, 'w') as f:
                    yaml.dump(updated_data, f, default_flow_style=False, sort_keys=False)
                
                # Encrypt the updated vault
                log_info("Encrypting updated vault...")
                if encrypt_vault(updated_vault, vault_file, vault_password_args):
                    log_success("Vault updated and encrypted successfully")
                    
                    # Create backup
                    from datetime import datetime
                    backup_file = vault_file.parent / f"vault.yml.backup.{datetime.now().strftime('%Y%m%d-%H%M%S')}"
                    try:
                        shutil.copy2(vault_file, backup_file)
                        log_info(f"Backup saved to: {backup_file}")
                    except Exception as e:
                        log_warn(f"Could not create backup: {e}")
                else:
                    log_error("Failed to encrypt vault")
                    sys.exit(1)
            else:
                log_info("Changes discarded")
        else:
            log_info("No changes needed - vault is up to date")
    
    print()
    log_success("Vault update complete")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print()
        log_info("Operation cancelled by user")
        sys.exit(0)
    except Exception as e:
        log_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

