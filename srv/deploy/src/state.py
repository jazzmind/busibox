"""
Busibox State File Management

Reads and writes the .busibox-state file for installation state tracking.
"""

import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Default state file path
DEFAULT_STATE_FILE = os.environ.get("BUSIBOX_STATE_FILE", "/app/busibox/.busibox-state-dev")


async def read_state(state_file: Optional[str] = None) -> Dict[str, str]:
    """
    Read state from .busibox-state file.
    
    The file format is KEY="value" or KEY=value, one per line.
    Lines starting with # are treated as comments.
    
    Args:
        state_file: Path to state file. Defaults to BUSIBOX_STATE_FILE env var.
    
    Returns:
        Dictionary of state key-value pairs.
    """
    state: Dict[str, str] = {}
    path = Path(state_file or DEFAULT_STATE_FILE)
    
    if not path.exists():
        logger.debug(f"State file does not exist: {path}")
        return state
    
    try:
        content = path.read_text()
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                # Remove surrounding quotes if present
                value = value.strip()
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                state[key.strip()] = value
        logger.debug(f"Read {len(state)} state entries from {path}")
    except Exception as e:
        logger.error(f"Error reading state file {path}: {e}")
    
    return state


async def write_state(updates: Dict[str, Any], state_file: Optional[str] = None) -> None:
    """
    Update state file with new values.
    
    Reads existing state, merges with updates, and writes back.
    Values are stored as KEY="value" format.
    
    Args:
        updates: Dictionary of key-value pairs to update.
        state_file: Path to state file. Defaults to BUSIBOX_STATE_FILE env var.
    """
    path = Path(state_file or DEFAULT_STATE_FILE)
    
    # Read existing state
    state = await read_state(state_file)
    
    # Merge updates
    for key, value in updates.items():
        if value is None:
            # Remove key if value is None
            state.pop(key, None)
        else:
            state[key] = str(value)
    
    # Write back
    try:
        # Ensure parent directory exists
        path.parent.mkdir(parents=True, exist_ok=True)
        
        lines = []
        for k, v in sorted(state.items()):
            # Escape any quotes in value
            v_escaped = v.replace('"', '\\"')
            lines.append(f'{k}="{v_escaped}"')
        
        path.write_text("\n".join(lines) + "\n")
        logger.info(f"Wrote {len(state)} state entries to {path}")
    except Exception as e:
        logger.error(f"Error writing state file {path}: {e}")
        raise


async def get_state_value(key: str, state_file: Optional[str] = None) -> Optional[str]:
    """
    Get a single value from state file.
    
    Args:
        key: State key to retrieve.
        state_file: Path to state file.
    
    Returns:
        Value if found, None otherwise.
    """
    state = await read_state(state_file)
    return state.get(key)


async def set_state_value(key: str, value: Any, state_file: Optional[str] = None) -> None:
    """
    Set a single value in state file.
    
    Args:
        key: State key to set.
        value: Value to set (will be converted to string).
        state_file: Path to state file.
    """
    await write_state({key: value}, state_file)
