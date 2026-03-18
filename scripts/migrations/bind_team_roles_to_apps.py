#!/usr/bin/env python3
"""
Retroactively bind existing self-service team roles to their source apps.

Self-service roles (created via ensureTeamRole) follow the naming pattern
``app:{appName}:{entityName}-team``.  These roles must be bound to their source
app (via authz_role_bindings) so that the authz token exchange includes them in
app-scoped JWTs.

This script:
  1. Fetches all self-service roles from authz
  2. For each role, checks if an app binding already exists
  3. If not, creates a binding from the role to the app UUID

Usage
-----
Set env vars:

    AUTHZ_URL    - authz service URL (default: http://localhost:8010)
    AUTHZ_TOKEN  - admin access token with authz.roles.read + authz.bindings.* scopes

Then run:

    python bind_team_roles_to_apps.py

The script will discover the app UUIDs by looking at existing bindings for known
standard roles (e.g. "Admin", "HR") that are already bound to each app. If no
existing bindings are found for an app, the script will skip that app and print
a warning.

Alternatively, you can provide explicit mappings via APP_MAP env var:

    APP_MAP='busibox-workforce=<uuid>,busibox-recruiter=<uuid>'

"""

import json
import os
import re
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

AUTHZ_URL = os.environ.get("AUTHZ_URL", "http://localhost:8010")
AUTHZ_TOKEN = os.environ.get("AUTHZ_TOKEN", "")
APP_MAP_RAW = os.environ.get("APP_MAP", "")
DRY_RUN = os.environ.get("DRY_RUN", "0") == "1"

ROLE_PATTERN = re.compile(r"^app:([a-z0-9][a-z0-9._-]*):.+$")


def api(method: str, path: str, body: dict | None = None) -> dict:
    url = f"{AUTHZ_URL}{path}"
    headers = {"Authorization": f"Bearer {AUTHZ_TOKEN}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        text = e.read().decode() if e.fp else ""
        print(f"  HTTP {e.code}: {text[:200]}", file=sys.stderr)
        raise


def discover_app_uuids() -> dict[str, str]:
    """Build app_name -> app_uuid map from existing role bindings."""
    bindings = api("GET", "/admin/bindings?resource_type=app&limit=1000")
    mapping: dict[str, str] = {}

    for b in bindings.get("bindings", []):
        role_id = b["role_id"]
        resource_id = b["resource_id"]
        try:
            role = api("GET", f"/admin/roles/{role_id}")
        except HTTPError:
            continue
        source_app = role.get("source_app")
        if source_app and source_app not in mapping:
            mapping[source_app] = resource_id

    return mapping


def main():
    if not AUTHZ_TOKEN:
        print("ERROR: Set AUTHZ_TOKEN to an admin access token with authz.roles.read + authz.bindings.* scopes", file=sys.stderr)
        sys.exit(1)

    # Build app_name -> app_uuid mapping
    app_map: dict[str, str] = {}
    if APP_MAP_RAW:
        for pair in APP_MAP_RAW.split(","):
            name, uuid = pair.strip().split("=", 1)
            app_map[name.strip()] = uuid.strip()
        print(f"Using explicit APP_MAP: {app_map}")
    else:
        print("Discovering app UUIDs from existing bindings...")
        app_map = discover_app_uuids()
        print(f"Discovered {len(app_map)} apps: {app_map}")

    if not app_map:
        print("No app mappings found. Set APP_MAP or ensure existing role-app bindings exist.", file=sys.stderr)
        sys.exit(1)

    # Fetch all roles (admin scope)
    try:
        roles = api("GET", "/admin/roles?limit=1000")
    except HTTPError:
        print("Failed to list roles. Ensure AUTHZ_TOKEN has authz.roles.read scope.", file=sys.stderr)
        sys.exit(1)

    role_list = roles if isinstance(roles, list) else roles.get("roles", roles.get("data", []))

    created = 0
    skipped = 0
    already_bound = 0

    for role in role_list:
        name = role.get("name", "")
        match = ROLE_PATTERN.match(name)
        if not match:
            continue

        app_name = match.group(1)
        app_uuid = app_map.get(app_name)
        if not app_uuid:
            print(f"  SKIP {name}: no UUID for app '{app_name}'")
            skipped += 1
            continue

        role_id = role["id"]

        # Check if binding exists
        try:
            bindings = api("GET", f"/admin/bindings?role_id={role_id}&resource_type=app&resource_id={app_uuid}")
            existing = bindings.get("bindings", [])
            if existing:
                already_bound += 1
                continue
        except HTTPError:
            pass

        if DRY_RUN:
            print(f"  DRY RUN: Would bind {name} ({role_id}) -> app {app_uuid}")
            created += 1
            continue

        try:
            api("POST", "/admin/bindings", {
                "role_id": role_id,
                "resource_type": "app",
                "resource_id": app_uuid,
            })
            print(f"  BOUND {name} ({role_id}) -> app {app_uuid}")
            created += 1
        except HTTPError as e:
            if hasattr(e, "code") and e.code == 409:
                already_bound += 1
            else:
                print(f"  FAILED to bind {name}: {e}", file=sys.stderr)

    print(f"\nDone. Created: {created}, Already bound: {already_bound}, Skipped (no app UUID): {skipped}")


if __name__ == "__main__":
    main()
