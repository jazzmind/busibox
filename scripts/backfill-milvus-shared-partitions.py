#!/usr/bin/env python3
"""
Full remediation: fix personal-visibility documents in shared libraries, then
backfill their Milvus partitions so non-owner users can search them.

PROBLEM: Documents uploaded to shared libraries before the upload-path fix were
stored as visibility=personal in Postgres AND indexed into personal_{owner_id}
Milvus partitions instead of the correct role_{role_id} partitions.

Two-phase fix:
  Phase 0  --fix-postgres   Query authz DB for library→role bindings.  For
                            every non-personal library that still has
                            visibility=personal documents, update them to
                            visibility=shared and insert document_roles rows.
  Phase 1  (always runs)   For each visibility=shared/authenticated document,
                            copy vectors into the correct Milvus role_{} /
                            authenticated partitions.

USAGE (run from Proxmox host or inside data-lxc):

  # See what is broken without changing anything:
  python3 backfill-milvus-shared-partitions.py --diagnose

  # Fix only Postgres (dry-run first):
  python3 backfill-milvus-shared-partitions.py --fix-postgres --dry-run
  python3 backfill-milvus-shared-partitions.py --fix-postgres --apply

  # Fix Postgres AND Milvus in one pass:
  python3 backfill-milvus-shared-partitions.py --fix-postgres --apply --clean-personal

  # Fix only Milvus (if Postgres already correct):
  python3 backfill-milvus-shared-partitions.py --dry-run
  python3 backfill-milvus-shared-partitions.py --apply

  # Only process a single file (useful for testing):
  python3 backfill-milvus-shared-partitions.py --apply --file-id <uuid>

Environment variables:
  POSTGRES_HOST      (default: localhost)
  POSTGRES_PORT      (default: 5432)
  POSTGRES_DB        (default: data)          — data-api database
  AUTHZ_DB           (default: authz)         — authz database (same host)
  POSTGRES_USER      (default: busibox_user)
  POSTGRES_PASSWORD  (required)
  MILVUS_HOST        (default: milvus)
  MILVUS_PORT        (default: 19530)
  MILVUS_COLLECTION  (default: documents)

Requires: psycopg2-binary, pymilvus
"""

import argparse
import os
import sys
import time

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("Missing psycopg2. Install with: pip install psycopg2-binary")
    sys.exit(1)

try:
    from pymilvus import Collection, connections, utility
except ImportError:
    print("Missing pymilvus. Install with: pip install pymilvus")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

PG_HOST    = os.environ.get("POSTGRES_HOST", "localhost")
PG_PORT    = int(os.environ.get("POSTGRES_PORT", "5432"))
PG_DB      = os.environ.get("POSTGRES_DB", "data")
AUTHZ_DB   = os.environ.get("AUTHZ_DB", "authz")
PG_USER    = os.environ.get("POSTGRES_USER", "busibox_user")
PG_PASS    = os.environ.get("POSTGRES_PASSWORD", "")

MILVUS_HOST       = os.environ.get("MILVUS_HOST", "milvus")
MILVUS_PORT       = int(os.environ.get("MILVUS_PORT", "19530"))
MILVUS_COLLECTION = os.environ.get("MILVUS_COLLECTION", "documents")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def pg_connect(db=None, admin_bypass=False):
    conn = psycopg2.connect(
        host=PG_HOST, port=PG_PORT, dbname=db or PG_DB,
        user=PG_USER, password=PG_PASS,
    )
    if admin_bypass:
        # Satisfy the admin_docs_select / admin_docs_update / admin_docs_delete
        # RLS policies so this connection can see all rows without user filtering.
        with conn.cursor() as cur:
            cur.execute("SET app.is_admin = 'true'")
        conn.commit()
    return conn


def milvus_connect() -> Collection:
    connections.connect("default", host=MILVUS_HOST, port=MILVUS_PORT, timeout=10)
    if not utility.has_collection(MILVUS_COLLECTION):
        print(f"ERROR: Milvus collection '{MILVUS_COLLECTION}' does not exist.")
        sys.exit(1)
    col = Collection(MILVUS_COLLECTION)
    col.load()
    return col


def list_partitions(col: Collection) -> set:
    return {p.name for p in col.partitions}


def vectors_in_partition(col: Collection, file_id: str, partition: str) -> list:
    try:
        return col.query(
            expr=f'file_id == "{file_id}"',
            partition_names=[partition],
            output_fields=["*"],
        )
    except Exception:
        return []


def copy_vectors(col: Collection, records: list, target_partition: str, dry_run: bool) -> int:
    if not records:
        return 0
    if dry_run:
        print(f"      [DRY-RUN] Would insert {len(records)} vectors → {target_partition}")
        return len(records)
    try:
        col.insert(records, partition_name=target_partition)
        col.flush()
        return len(records)
    except Exception as e:
        print(f"      ERROR copying to {target_partition}: {e}")
        return 0


def delete_from_partition(col: Collection, file_id: str, partition: str, dry_run: bool):
    if dry_run:
        print(f"      [DRY-RUN] Would delete vectors from {partition}")
        return
    try:
        col.delete(f'file_id == "{file_id}"', partition_name=partition)
        col.flush()
    except Exception as e:
        print(f"      ERROR deleting from {partition}: {e}")


def ensure_partition(col: Collection, partition_name: str, dry_run: bool):
    existing = list_partitions(col)
    if partition_name not in existing:
        if dry_run:
            print(f"      [DRY-RUN] Would create partition {partition_name}")
        else:
            col.create_partition(partition_name)
            print(f"      Created partition {partition_name}")


# ---------------------------------------------------------------------------
# Phase 0: fix Postgres
# ---------------------------------------------------------------------------

def get_library_role_ids(authz_conn, library_id: str) -> list[tuple[str, str]]:
    """Return [(role_id, role_name), ...] bound to library_id in authz DB."""
    with authz_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute(
            """
            SELECT r.id::text AS role_id, r.name AS role_name
            FROM authz_role_bindings rb
            JOIN authz_roles r ON r.id = rb.role_id
            WHERE rb.resource_type = 'library'
              AND rb.resource_id = %s
            """,
            (library_id,),
        )
        return [(row["role_id"], row["role_name"]) for row in cur.fetchall()]


def phase0_fix_postgres(data_conn, authz_conn, dry_run: bool, file_id_filter: str | None) -> int:
    """
    Find personal-visibility documents that live in non-personal libraries and
    fix them: set visibility=shared, insert document_roles from authz bindings.
    Returns the number of documents fixed (or that would be fixed in dry-run).
    """
    print("\n" + "=" * 70)
    print("PHASE 0 — Fix Postgres: personal docs in shared libraries")
    print("=" * 70)

    # Find all non-personal libraries that have any personal docs
    with data_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        query = """
            SELECT
                l.id::text      AS library_id,
                l.name          AS library_name,
                l.is_personal,
                l.user_id::text AS library_owner,
                COUNT(f.file_id) AS stuck_count
            FROM libraries l
            JOIN data_files f
              ON f.library_id = l.id
             AND f.visibility = 'personal'
            WHERE l.is_personal = false
              AND l.deleted_at IS NULL
        """
        params = []
        if file_id_filter:
            query += " AND f.file_id = %s"
            params.append(file_id_filter)
        query += " GROUP BY l.id, l.name, l.is_personal, l.user_id ORDER BY l.name"
        cur.execute(query, params)
        libraries = cur.fetchall()

    if not libraries:
        print("  No non-personal libraries have personal-visibility documents — Postgres is clean.")
        return 0

    print(f"  Found {len(libraries)} non-personal library/libraries with stuck docs:")
    for lib in libraries:
        print(f"    - '{lib['library_name']}' ({lib['library_id'][:8]}…): "
              f"{lib['stuck_count']} stuck doc(s)")

    total_fixed = 0

    for lib in libraries:
        library_id   = lib["library_id"]
        library_name = lib["library_name"]

        print(f"\n  Library: '{library_name}' ({library_id})")

        # Get role bindings from authz DB
        role_pairs = get_library_role_ids(authz_conn, library_id)
        if not role_pairs:
            print("    WARNING: No role bindings in authz for this library — skipping.")
            print("    Assign access roles to the library in the Admin UI, then re-run.")
            continue

        print(f"    Authz roles ({len(role_pairs)}):")
        for rid, rname in role_pairs:
            print(f"      - {rname} ({rid[:8]}…)")

        role_ids = [rid for rid, _ in role_pairs]

        # Fetch stuck docs for this library
        with data_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            q2 = """
                SELECT file_id::text, filename, owner_id::text
                FROM data_files
                WHERE library_id = %s::uuid
                  AND visibility = 'personal'
            """
            p2 = [library_id]
            if file_id_filter:
                q2 += " AND file_id = %s"
                p2.append(file_id_filter)
            q2 += " ORDER BY created_at"
            cur.execute(q2, p2)
            stuck_docs = cur.fetchall()

        print(f"    Stuck documents: {len(stuck_docs)}")

        for doc in stuck_docs:
            file_id  = doc["file_id"]
            filename = doc["filename"] or "<unknown>"

            if dry_run:
                print(f"    [DRY-RUN] Would fix: {filename} ({file_id[:8]}…)")
                for rid in role_ids:
                    print(f"              + document_roles row: role_id={rid[:8]}…")
                total_fixed += 1
                continue

            try:
                with data_conn:
                    with data_conn.cursor() as cur:
                        cur.execute(
                            "UPDATE data_files SET visibility = 'shared' WHERE file_id = %s::uuid",
                            (file_id,),
                        )
                        for rid in role_ids:
                            cur.execute(
                                """
                                INSERT INTO document_roles (file_id, role_id, role_name, added_by)
                                VALUES (%s::uuid, %s::uuid, %s, NULL)
                                ON CONFLICT (file_id, role_id) DO NOTHING
                                """,
                                (file_id, rid, f"backfill-{rid[:8]}"),
                            )
                print(f"    Fixed: {filename} ({file_id[:8]}…)")
                total_fixed += 1
            except Exception as e:
                print(f"    ERROR fixing {file_id}: {e}")

    print(f"\n  Phase 0 complete — {total_fixed} document(s) {'would be fixed' if dry_run else 'fixed'}.")
    return total_fixed


# ---------------------------------------------------------------------------
# Phase 1: backfill Milvus partitions
# ---------------------------------------------------------------------------

def phase1_backfill_milvus(data_conn, col, existing_partitions, dry_run: bool,
                            clean_personal: bool, file_id_filter: str | None):
    print("\n" + "=" * 70)
    print("PHASE 1 — Backfill Milvus partitions for shared/authenticated docs")
    print("=" * 70)

    with data_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        query = """
            SELECT
                f.file_id::text   AS file_id,
                f.owner_id::text  AS owner_id,
                f.visibility,
                f.filename,
                array_agg(dr.role_id::text) FILTER (WHERE dr.role_id IS NOT NULL) AS role_ids
            FROM data_files f
            LEFT JOIN document_roles dr ON dr.file_id = f.file_id
            WHERE f.visibility IN ('shared', 'authenticated')
        """
        params = []
        if file_id_filter:
            query += " AND f.file_id = %s"
            params.append(file_id_filter)
        query += " GROUP BY f.file_id, f.owner_id, f.visibility, f.filename"
        cur.execute(query, params)
        rows = cur.fetchall()

    print(f"\n  Found {len(rows)} shared/authenticated document(s)")

    if not rows:
        print("  Nothing to backfill in Milvus.")
        return

    total_docs      = 0
    docs_ok         = 0
    docs_fixed      = 0
    docs_no_vectors = 0
    docs_no_roles   = 0
    errors          = 0

    for row in rows:
        file_id    = row["file_id"]
        owner_id   = row["owner_id"]
        role_ids   = row["role_ids"] or []
        filename   = row["filename"] or "<unknown>"
        visibility = row["visibility"]
        total_docs += 1

        print(f"\n  [{total_docs}/{len(rows)}] {filename} ({file_id[:8]}…)")
        print(f"    Owner:    {owner_id[:8]}…")
        print(f"    Roles:    {role_ids}")

        if visibility == "authenticated":
            target_partitions = {"authenticated"}
        elif role_ids:
            target_partitions = {f"role_{rid}" for rid in role_ids}
        else:
            print("    SKIP: visibility=shared but no document_roles — cannot determine partitions")
            docs_no_roles += 1
            continue

        personal_partition = f"personal_{owner_id}"

        # Find which existing partitions hold this file's vectors
        partitions_with_vectors = set()
        source_records = None

        for pname in existing_partitions:
            if pname.startswith("personal_") or pname.startswith("role_") or pname == "authenticated":
                records = vectors_in_partition(col, file_id, pname)
                if records:
                    partitions_with_vectors.add(pname)
                    if source_records is None:
                        source_records = records

        print(f"    Partitions with vectors: {partitions_with_vectors or '(none)'}")
        print(f"    Target partitions:       {target_partitions}")

        if not partitions_with_vectors and source_records is None:
            print("    WARNING: No vectors found — document may not be indexed yet")
            docs_no_vectors += 1
            continue

        missing_targets = target_partitions - partitions_with_vectors
        wrong_personal  = personal_partition in partitions_with_vectors

        if not missing_targets and not (wrong_personal and clean_personal):
            print("    OK: already in correct partitions")
            docs_ok += 1
            continue

        docs_fixed += 1

        if missing_targets:
            if source_records is None:
                source_records = vectors_in_partition(col, file_id, personal_partition)
            if not source_records:
                print("    ERROR: No source vectors available to copy from")
                errors += 1
                continue

            for target in sorted(missing_targets):
                print(f"    Copying {len(source_records)} vectors → {target}")
                ensure_partition(col, target, dry_run)
                copied = copy_vectors(col, source_records, target, dry_run)
                if copied:
                    print(f"        ✓ {copied} vectors inserted")

        if wrong_personal and clean_personal:
            print(f"    Removing vectors from {personal_partition}")
            delete_from_partition(col, file_id, personal_partition, dry_run)
            if not dry_run:
                print(f"        ✓ Deleted from {personal_partition}")

    print("\n" + "-" * 40)
    print(f"  Total docs examined:   {total_docs}")
    print(f"  Already correct:       {docs_ok}")
    print(f"  Fixed (vectors copied):{docs_fixed}")
    print(f"  No vectors yet:        {docs_no_vectors}")
    print(f"  No roles in DB:        {docs_no_roles}")
    print(f"  Errors:                {errors}")


# ---------------------------------------------------------------------------
# Diagnose
# ---------------------------------------------------------------------------

def diagnose(data_conn, authz_conn):
    """Print a read-only summary of the current state."""
    print("\n" + "=" * 70)
    print("DIAGNOSE — current document visibility state")
    print("=" * 70)

    with data_conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
        cur.execute("""
            SELECT
                l.name          AS library_name,
                l.is_personal   AS lib_personal,
                f.visibility,
                COUNT(*)        AS doc_count
            FROM data_files f
            JOIN libraries l ON l.id = f.library_id
            WHERE l.deleted_at IS NULL
            GROUP BY l.name, l.is_personal, f.visibility
            ORDER BY l.name, f.visibility
        """)
        rows = cur.fetchall()

    if not rows:
        print("  No documents found in data_files.")
        return

    print(f"\n  {'Library':<35} {'Personal?':<10} {'Visibility':<14} {'Count':>6}")
    print(f"  {'-'*35} {'-'*10} {'-'*14} {'-'*6}")
    for row in rows:
        print(f"  {row['library_name']:<35} {str(row['lib_personal']):<10} "
              f"{row['visibility']:<14} {row['doc_count']:>6}")

    # Highlight problem cases
    problems = [r for r in rows if not r["lib_personal"] and r["visibility"] == "personal"]
    if problems:
        print(f"\n  ⚠  STUCK docs (personal visibility in non-personal library):")
        for r in problems:
            print(f"     - '{r['library_name']}': {r['doc_count']} doc(s)")
        print(f"\n  Run with --fix-postgres --dry-run to preview the fix.")
        print(f"  Run with --fix-postgres --apply   to apply it.")
    else:
        print("\n  ✓ No stuck documents found — Postgres visibility is correct.")
        print("  If Milvus search still doesn't work, run --dry-run to check Milvus partitions.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        description="Fix shared-library docs in Postgres and backfill Milvus partitions"
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diagnose",   action="store_true",
                      help="Print current state (read-only, no Milvus connection needed)")
    mode.add_argument("--dry-run",    action="store_true",
                      help="Print what would change without writing anything")
    mode.add_argument("--apply",      action="store_true",
                      help="Apply changes to both Postgres (if --fix-postgres) and Milvus")

    p.add_argument("--fix-postgres", action="store_true",
                   help="Phase 0: fix personal-visibility docs in shared libraries before Milvus backfill")
    p.add_argument("--clean-personal", action="store_true",
                   help="After copying vectors to role partitions, delete from personal partition")
    p.add_argument("--file-id", help="Only process this specific file UUID")
    return p.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("Busibox — shared-document remediation + Milvus backfill")
    print(f"  Data Postgres:  {PG_USER}@{PG_HOST}:{PG_PORT}/{PG_DB}")
    print(f"  Authz Postgres: {PG_USER}@{PG_HOST}:{PG_PORT}/{AUTHZ_DB}")
    print(f"  Milvus:         {MILVUS_HOST}:{MILVUS_PORT}/{MILVUS_COLLECTION}")
    if args.file_id:
        print(f"  File filter:    {args.file_id}")
    print("=" * 70)

    # Connect to data DB with admin RLS bypass (always needed)
    print("\nConnecting to Postgres (data)…")
    try:
        data_conn = pg_connect(PG_DB, admin_bypass=True)
    except Exception as e:
        print(f"ERROR: Cannot connect to data DB: {e}")
        sys.exit(1)
    print("  OK")

    # Connect to authz DB (needed for diagnose / fix-postgres)
    authz_conn = None
    if args.diagnose or args.fix_postgres:
        print(f"Connecting to Postgres (authz)…")
        try:
            authz_conn = pg_connect(AUTHZ_DB)
            print("  OK")
        except Exception as e:
            print(f"ERROR: Cannot connect to authz DB: {e}")
            sys.exit(1)

    # Diagnose mode — no Milvus needed
    if args.diagnose:
        diagnose(data_conn, authz_conn)
        data_conn.close()
        authz_conn.close()
        return

    # Phase 0: fix Postgres if requested
    if args.fix_postgres:
        if authz_conn is None:
            # Already set above, but guard just in case
            print("ERROR: authz DB connection required for --fix-postgres")
            sys.exit(1)
        phase0_fix_postgres(data_conn, authz_conn, dry_run=args.dry_run,
                            file_id_filter=args.file_id)
        authz_conn.close()

    # Connect to Milvus for Phase 1
    print("\nConnecting to Milvus…")
    try:
        col = milvus_connect()
    except Exception as e:
        print(f"ERROR: Cannot connect to Milvus: {e}")
        sys.exit(1)

    existing_partitions = list_partitions(col)
    print(f"  Milvus has {len(existing_partitions)} partitions")

    # Phase 1: backfill Milvus
    phase1_backfill_milvus(
        data_conn, col, existing_partitions,
        dry_run=args.dry_run,
        clean_personal=args.clean_personal,
        file_id_filter=args.file_id,
    )

    data_conn.close()

    print("\n" + "=" * 70)
    if args.dry_run:
        print("[DRY-RUN] No changes were written.")
    else:
        print("[APPLY] Done.")
    print("=" * 70)


if __name__ == "__main__":
    main()
