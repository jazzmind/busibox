#!/usr/bin/env python3
"""
Sync vault with example while preserving comments and structure.

This script:
1. Reads vault.example.yml line by line (preserving all comments/structure)
2. For each YAML value line, checks if we have a current value
3. Substitutes current values where they exist
4. Outputs the result with all comments preserved
"""

import sys
import re
import yaml

def parse_yaml_path(line):
    """
    Extract the YAML path from a line like:
      api_key: "CHANGE_ME"
    Returns: (indent_level, key, value) or None
    """
    # Match YAML key: value lines
    match = re.match(r'^(\s*)([a-z_][a-z0-9_-]*)\s*:\s*(.+)$', line, re.IGNORECASE)
    if match:
        indent = match.group(1)
        key = match.group(2)
        value = match.group(3).strip()
        return (len(indent), key, value)
    return None

def build_path_stack(indent_level, key, stack):
    """
    Maintain a stack of keys representing current path in YAML.
    Returns the full dotted path like "secrets.openai.api_key"
    """
    # Pop items from stack until we're at the right level
    while stack and stack[-1][0] >= indent_level:
        stack.pop()
    
    # Add current key
    stack.append((indent_level, key))
    
    # Build dot path
    return '.'.join([k for _, k in stack])

def main():
    if len(sys.argv) != 3:
        print("Usage: preserve_comments.py current_vault.yml example.yml", file=sys.stderr)
        sys.exit(1)
    
    current_file = sys.argv[1]
    example_file = sys.argv[2]
    
    # Load current vault as flat dict
    with open(current_file, 'r') as f:
        current_data = yaml.safe_load(f)
    
    def flatten_dict(d, parent_key='', sep='.'):
        items = []
        for k, v in d.items():
            new_key = f'{parent_key}{sep}{k}' if parent_key else k
            if isinstance(v, dict):
                items.extend(flatten_dict(v, new_key, sep=sep).items())
            else:
                items.append((new_key, v))
        return dict(items)
    
    current_flat = flatten_dict(current_data) if current_data else {}
    
    # Process example file line by line
    stack = []
    with open(example_file, 'r') as f:
        for line in f:
            # Check if this is a YAML value line
            parsed = parse_yaml_path(line.rstrip('\n'))
            
            if parsed:
                indent_level, key, value = parsed
                path = build_path_stack(indent_level, key, stack)
                
                # Check if this is a value line (not a dict key)
                # Value lines have quotes or are template variables
                is_value = (
                    value.startswith('"') or 
                    value.startswith("'") or
                    value.startswith('{{') or
                    value == 'true' or 
                    value == 'false' or
                    re.match(r'^\d+$', value) or
                    value.startswith('---')  # Multi-line string
                )
                
                if is_value and path in current_flat:
                    # Substitute with current value, preserving format
                    current_val = current_flat[path]
                    
                    # Format the value
                    if isinstance(current_val, str):
                        if current_val.startswith('{{'):
                            # Template variable - no quotes
                            formatted_val = current_val
                        else:
                            # String - add quotes if original had them
                            if value.startswith('"'):
                                formatted_val = f'"{current_val}"'
                            elif value.startswith("'"):
                                formatted_val = f"'{current_val}'"
                            else:
                                formatted_val = current_val
                    elif isinstance(current_val, bool):
                        formatted_val = 'true' if current_val else 'false'
                    elif isinstance(current_val, (int, float)):
                        formatted_val = str(current_val)
                    else:
                        formatted_val = value
                    
                    # Reconstruct line with new value
                    indent = ' ' * indent_level
                    print(f"{indent}{key}: {formatted_val}")
                else:
                    # No current value or this is a dict key - keep original
                    print(line.rstrip('\n'))
            else:
                # Comment, empty line, or other non-value line - keep as-is
                print(line.rstrip('\n'))

if __name__ == '__main__':
    main()
