"""
Port Allocator for User Apps
=============================

Manages dynamic port assignment for user (sandboxed) apps to prevent
port conflicts when multiple apps declare the same defaultPort in their
manifest (common since the template defaults to 3002).

Resolution strategy (in order):
1. Reuse persisted port if this app was previously assigned one.
2. Use the manifest's preferred port if no other app has claimed it.
3. Allocate the next free port from the dynamic range (4100-4999).

Persistence:
  Port assignments are stored in /tmp/busibox_port_assignments.json
  alongside the existing running-apps registry.  Assignments survive
  deploy-api restarts; they are released on undeploy.
"""

import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PORT_ASSIGNMENTS_FILE = "/tmp/busibox_port_assignments.json"

DYNAMIC_PORT_MIN = 4100
DYNAMIC_PORT_MAX = 4999

# Ports used by core apps and infrastructure -- never allocate these.
RESERVED_PORTS = frozenset({
    # Core Next.js apps (core_app_executor.py CORE_APPS)
    3000, 3001, 3002, 3003, 3004, 3005, 3006,
    # Infrastructure services
    4000,   # litellm
    5432,   # postgres
    6379,   # redis
    8000,   # agent-api
    8002,   # data-api
    8003,   # search-api
    8010,   # authz-api
    9000,   # minio
})


def get_port_assignments() -> Dict[str, int]:
    """Read the persisted app-id -> port map."""
    try:
        with open(PORT_ASSIGNMENTS_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {k: int(v) for k, v in data.items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return {}


def save_port_assignments(assignments: Dict[str, int]) -> None:
    """Write the app-id -> port map to disk."""
    try:
        with open(PORT_ASSIGNMENTS_FILE, "w") as f:
            json.dump(assignments, f)
    except Exception as e:
        logger.error(f"Failed to persist port assignments: {e}")


def get_assigned_port(app_id: str) -> Optional[int]:
    """Look up the currently assigned port for an app, if any."""
    return get_port_assignments().get(app_id)


def allocate_port(
    app_id: str,
    preferred_port: Optional[int],
    logs: List[str],
) -> int:
    """Resolve a conflict-free port for *app_id*.

    Returns the port number that should be used for this deployment.
    The assignment is persisted so that re-deploys of the same app
    get the same port.

    If the manifest's defaultPort differs from the persisted assignment,
    the old assignment is released and the new port is claimed.  This
    ensures that edits to busibox.json (e.g. changing defaultPort) are
    picked up on the next deploy.

    Raises ``RuntimeError`` if no free port can be found.
    """
    assignments = get_port_assignments()

    # 1. Already assigned — reuse unless the manifest changed the preferred port.
    if app_id in assignments:
        persisted = assignments[app_id]
        if preferred_port is not None and preferred_port != persisted:
            # Manifest port changed — release old assignment and fall through
            # so the new preferred port gets allocated below.
            logs.append(
                f"🔌 Manifest port changed {persisted} → {preferred_port} for {app_id}; "
                f"releasing old assignment"
            )
            logger.info(
                f"Port changed for {app_id}: {persisted} → {preferred_port}; releasing"
            )
            del assignments[app_id]
            save_port_assignments(assignments)
            # Fall through to normal allocation with new preferred_port.
        else:
            logs.append(f"🔌 Reusing persisted port {persisted} for {app_id}")
            logger.info(f"Reusing persisted port {persisted} for {app_id}")
            return persisted

    claimed_ports = set(assignments.values()) | RESERVED_PORTS

    # 2. Preferred port from manifest is available.
    if preferred_port and preferred_port not in claimed_ports:
        assignments[app_id] = preferred_port
        save_port_assignments(assignments)
        logs.append(f"🔌 Using manifest port {preferred_port} for {app_id}")
        logger.info(f"Using manifest port {preferred_port} for {app_id}")
        return preferred_port

    if preferred_port and preferred_port in claimed_ports:
        # Find who owns it for a helpful log message.
        owner = next(
            (aid for aid, p in assignments.items() if p == preferred_port),
            "a reserved service",
        )
        logs.append(
            f"🔌 Port {preferred_port} requested by manifest is already "
            f"claimed by {owner} -- finding an available port"
        )
        logger.info(
            f"Port {preferred_port} for {app_id} conflicts with {owner}"
        )

    # 3. Scan the dynamic range for the first unclaimed port.
    for candidate in range(DYNAMIC_PORT_MIN, DYNAMIC_PORT_MAX + 1):
        if candidate not in claimed_ports:
            assignments[app_id] = candidate
            save_port_assignments(assignments)
            logs.append(f"🔌 Assigned dynamic port {candidate} for {app_id}")
            logger.info(f"Assigned dynamic port {candidate} for {app_id}")
            return candidate

    raise RuntimeError(
        f"No free ports in range {DYNAMIC_PORT_MIN}-{DYNAMIC_PORT_MAX} "
        f"for app {app_id}"
    )


def release_port(app_id: str) -> None:
    """Remove a port assignment (called on undeploy)."""
    assignments = get_port_assignments()
    if app_id in assignments:
        port = assignments.pop(app_id)
        save_port_assignments(assignments)
        logger.info(f"Released port {port} for {app_id}")
