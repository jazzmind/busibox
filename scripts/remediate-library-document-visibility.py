#!/usr/bin/env python3
"""
Remediate documents stuck as visibility=personal in shared libraries.

Run from the admin workstation against a running Busibox environment.
Requires connectivity to the data-api and authz-api (or direct DB access
via SSH tunnel / Ansible exec).

Usage (with API access):
    python3 scripts/remediate-library-document-visibility.py \
        --library-name "Company Documents" \
        --authz-url http://10.96.200.210:8010 \
        --data-api-url http://10.96.200.206:8002 \
        --admin-token <your-admin-token>

Usage (direct PostgreSQL — requires DB access from admin workstation or
SSH tunnel to pg-lxc):
    POSTGRES_HOST=10.96.200.203 POSTGRES_PASSWORD=<pass> \
    python3 scripts/remediate-library-document-visibility.py \
        --library-name "Company Documents" \
        --authz-url http://10.96.200.210:8010 \
        --data-api-url http://10.96.200.206:8002 \
        --admin-token <your-admin-token> \
        --dry-run    # Print what would change without writing

The script:
  1. Looks up the library by name via data-api to get its ID.
  2. Fetches the library's authz role bindings (role IDs bound to the library).
  3. Counts documents in the library that are visibility=personal (stuck docs).
  4. Updates each stuck document:
       - visibility → shared
       - inserts document_roles rows for each library role (idempotent ON CONFLICT)
  5. Reports affected document count.

Requires: psycopg2, httpx
    pip install psycopg2-binary httpx
"""

import argparse
import os
import sys

try:
    import httpx
    import psycopg2
    import psycopg2.extras
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install with: pip install psycopg2-binary httpx")
    sys.exit(1)


# =============================================================================
# CLI args
# =============================================================================


def parse_args():
    p = argparse.ArgumentParser(description="Fix stuck personal documents in shared libraries")
    p.add_argument("--library-name", required=True, help="Name of the shared library to fix")
    p.add_argument("--authz-url", required=True, help="AuthZ service base URL")
    p.add_argument("--data-api-url", required=True, help="Data-API base URL")
    p.add_argument("--admin-token", required=True, help="Admin JWT (must have authz.roles.read + data.read)")
    p.add_argument("--dry-run", action="store_true", help="Print what would change without modifying the DB")
    # DB connection (for direct SQL path)
    p.add_argument("--pg-host", default=os.getenv("POSTGRES_HOST", "localhost"))
    p.add_argument("--pg-port", type=int, default=int(os.getenv("POSTGRES_PORT", "5432")))
    p.add_argument("--pg-db", default=os.getenv("POSTGRES_DB", "files"))
    p.add_argument("--pg-user", default=os.getenv("POSTGRES_USER", "busibox_user"))
    p.add_argument("--pg-password", default=os.getenv("POSTGRES_PASSWORD", ""))
    return p.parse_args()


# =============================================================================
# API helpers
# =============================================================================


def get_library_by_name(data_api_url: str, headers: dict, name: str) -> dict | None:
    """Find a library by name using data-api list endpoint."""
    resp = httpx.get(f"{data_api_url}/libraries", headers=headers, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    libraries = body.get("data", {}).get("libraries") or body.get("libraries") or []
    for lib in libraries:
        lib_name = lib.get("name") or lib.get("Name")
        if lib_name == name:
            return lib
    return None


def get_library_authz_roles(authz_url: str, headers: dict, library_id: str) -> list[dict]:
    """Fetch roles bound to this library from authz."""
    resp = httpx.get(
        f"{authz_url}/resources/library/{library_id}/roles",
        headers=headers,
        timeout=15,
    )
    if resp.status_code == 404:
        return []
    resp.raise_for_status()
    body = resp.json()
    # Authz returns a list of role objects: [{id, name, ...}]
    if isinstance(body, list):
        return body
    return body.get("roles") or body.get("data") or []


# =============================================================================
# DB operations
# =============================================================================


def get_db_conn(args):
    return psycopg2.connect(
        host=args.pg_host,
        port=args.pg_port,
        dbname=args.pg_db,
        user=args.pg_user,
        password=args.pg_password,
        connect_timeout=10,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )


def find_stuck_documents(conn, library_id: str) -> list[dict]:
    """Return data_files rows in the library that are still visibility=personal."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT file_id, filename, owner_id, visibility
            FROM data_files
            WHERE library_id = %s::uuid
              AND visibility = 'personal'
              AND deleted_at IS NULL
            ORDER BY created_at
            """,
            (library_id,),
        )
        return cur.fetchall()


def fix_documents(conn, docs: list[dict], library_id: str, role_ids: list[str], dry_run: bool):
    """
    For each stuck document:
      - Update visibility to 'shared'
      - Insert document_roles rows for each library role (idempotent)
    """
    if not docs:
        print("  No stuck documents found — nothing to do.")
        return

    for doc in docs:
        file_id = str(doc["file_id"])
        filename = doc["filename"]

        if dry_run:
            print(f"  [DRY-RUN] Would fix: {filename} ({file_id})")
            for rid in role_ids:
                print(f"             + document_roles: role_id={rid}")
            continue

        with conn:
            cur = conn.cursor()
            try:
                # Update visibility
                cur.execute(
                    "UPDATE data_files SET visibility = 'shared' WHERE file_id = %s::uuid",
                    (file_id,),
                )
                # Insert document_roles (idempotent)
                for rid in role_ids:
                    cur.execute(
                        """
                        INSERT INTO document_roles (file_id, role_id, role_name, added_by)
                        VALUES (%s::uuid, %s::uuid, %s, NULL)
                        ON CONFLICT (file_id, role_id) DO NOTHING
                        """,
                        (file_id, rid, f"remediation-{rid[:8]}"),
                    )
                print(f"  Fixed: {filename} ({file_id})")
            except Exception as e:
                conn.rollback()
                print(f"  ERROR fixing {file_id}: {e}")
                raise
            finally:
                cur.close()


# =============================================================================
# Main
# =============================================================================


def main():
    args = parse_args()
    admin_headers = {"Authorization": f"Bearer {args.admin_token}"}

    print(f"\n=== Library Document Visibility Remediation ===")
    print(f"Target library: {args.library_name}")
    print(f"Dry run: {args.dry_run}\n")

    # 1. Find the library
    print(f"[1] Looking up library '{args.library_name}' via data-api...")
    library = get_library_by_name(args.data_api_url, admin_headers, args.library_name)
    if not library:
        print(f"ERROR: Library '{args.library_name}' not found. Available libraries:")
        resp = httpx.get(f"{args.data_api_url}/libraries", headers=admin_headers, timeout=15)
        body = resp.json()
        libs = body.get("data", {}).get("libraries") or body.get("libraries") or []
        for lib in libs:
            print(f"  - {lib.get('name')} (id={lib.get('id')})")
        sys.exit(1)

    library_id = library.get("id")
    is_personal = library.get("isPersonal") or library.get("is_personal")
    print(f"  Found: id={library_id}, isPersonal={is_personal}")

    if is_personal:
        print("ERROR: This is a personal library. Remediation only applies to shared libraries.")
        sys.exit(1)

    # 2. Get library role bindings from authz
    print(f"\n[2] Fetching authz role bindings for library {library_id}...")
    role_bindings = get_library_authz_roles(args.authz_url, admin_headers, library_id)
    if not role_bindings:
        print("ERROR: No role bindings found for this library in authz.")
        print("       The library must have access roles assigned before remediation.")
        print("       Use the Admin UI to assign roles to the library, then re-run.")
        sys.exit(1)

    role_ids = [str(r["id"]) for r in role_bindings]
    print(f"  Library roles ({len(role_ids)}):")
    for rb in role_bindings:
        print(f"    - {rb.get('name')} ({rb.get('id')})")

    # 3. Connect to DB
    print(f"\n[3] Connecting to PostgreSQL ({args.pg_host}:{args.pg_port}/{args.pg_db})...")
    try:
        conn = get_db_conn(args)
        print("  Connected.")
    except Exception as e:
        print(f"ERROR: Could not connect to PostgreSQL: {e}")
        print("       Ensure POSTGRES_HOST/PORT/DB/USER/PASSWORD are set, or pass --pg-host etc.")
        sys.exit(1)

    try:
        # 4. Find stuck documents
        print(f"\n[4] Finding documents in library with visibility=personal...")
        stuck_docs = find_stuck_documents(conn, library_id)
        print(f"  Found {len(stuck_docs)} stuck document(s)")

        # 5. Fix them
        print(f"\n[5] Fixing stuck documents (dry_run={args.dry_run})...")
        fix_documents(conn, stuck_docs, library_id, role_ids, args.dry_run)

    finally:
        conn.close()

    if args.dry_run:
        print(f"\n[DRY-RUN] No changes made. Remove --dry-run to apply fixes.")
    else:
        print(f"\nDone. {len(stuck_docs)} document(s) remediated.")
        print("Documents are now visibility=shared with the library's role bindings.")


if __name__ == "__main__":
    main()
