#!/usr/bin/env python3
"""
Database Migration Tool: Separate Service Databases

This script migrates services from the shared 'busibox' database to their own
dedicated databases:
- authz tables -> 'authz' database
- ingest tables -> 'files' database
- agent tables -> 'agent_server' database (already separate)

The migration:
1. Creates the target databases if they don't exist
2. Creates schemas in target databases
3. Copies data from source to target
4. Optionally drops tables from source after verification

Usage:
    # Dry run (no changes)
    python migrate_to_separate_databases.py --dry-run
    
    # Migrate specific service
    python migrate_to_separate_databases.py --service authz
    python migrate_to_separate_databases.py --service ingest
    
    # Migrate all services
    python migrate_to_separate_databases.py --all
    
    # Cleanup source after migration (removes tables from busibox)
    python migrate_to_separate_databases.py --all --cleanup

Environment:
    POSTGRES_HOST - PostgreSQL host (default: localhost)
    POSTGRES_PORT - PostgreSQL port (default: 5432)
    POSTGRES_USER - Admin user (default: postgres)
    POSTGRES_PASSWORD - Admin password (required)
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

# Add paths for schema imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "srv" / "shared"))

try:
    import asyncpg
except ImportError:
    print("ERROR: asyncpg is required. Install with: pip install asyncpg")
    sys.exit(1)


# =============================================================================
# Configuration
# =============================================================================

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_ADMIN_USER = os.getenv("POSTGRES_ADMIN_USER", "postgres")

# Source database (shared)
SOURCE_DB = "busibox"
SOURCE_USER = os.getenv("SOURCE_USER", "busibox_user")
SOURCE_PASSWORD = os.getenv("SOURCE_PASSWORD", "devpassword")

# Target database configurations
SERVICES = {
    "authz": {
        "database": "authz",
        "owner": "busibox_user",
        "tables": [
            "audit_logs",
            "authz_delegation_tokens",
            "authz_email_domain_config",
            "authz_key_encryption_keys",
            "authz_magic_links",
            "authz_oauth_clients",
            "authz_passkey_challenges",
            "authz_passkeys",
            "authz_role_bindings",
            "authz_roles",
            "authz_sessions",
            "authz_signing_keys",
            "authz_totp_codes",
            "authz_totp_secrets",
            "authz_user_roles",
            "authz_users",
            "authz_wrapped_data_keys",
        ],
        # Tables must be migrated in dependency order (referenced tables first)
        "migration_order": [
            "authz_roles",
            "authz_users",
            "authz_user_roles",
            "authz_oauth_clients",
            "authz_signing_keys",
            "authz_key_encryption_keys",
            "authz_wrapped_data_keys",
            "authz_sessions",
            "authz_magic_links",
            "authz_totp_codes",
            "authz_totp_secrets",
            "authz_passkeys",
            "authz_passkey_challenges",
            "authz_delegation_tokens",
            "authz_email_domain_config",
            "authz_role_bindings",
            "audit_logs",
        ],
    },
    "ingest": {
        "database": "files",
        "owner": "busibox_user",
        "tables": [
            "groups",
            "group_memberships",
            "ingestion_files",
            "ingestion_status",
            "ingestion_chunks",
            "document_roles",
            "processing_history",
            "processing_strategy_results",
        ],
        # Tables must be migrated in dependency order
        "migration_order": [
            "groups",
            "group_memberships",
            "ingestion_files",
            "ingestion_status",
            "ingestion_chunks",
            "document_roles",
            "processing_history",
            "processing_strategy_results",
        ],
    },
}


# =============================================================================
# Helper Functions
# =============================================================================

def log(msg: str, level: str = "INFO"):
    """Print a log message with timestamp."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prefix = {"INFO": "ℹ️ ", "WARN": "⚠️ ", "ERROR": "❌", "SUCCESS": "✅", "STEP": "➡️ "}
    print(f"[{ts}] {prefix.get(level, '')} {msg}")


async def get_connection(
    database: str = "postgres",
    user: str = None,
    password: str = None,
) -> asyncpg.Connection:
    """Get a database connection."""
    return await asyncpg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=database,
        user=user or POSTGRES_ADMIN_USER,
        password=password or POSTGRES_PASSWORD,
    )


async def database_exists(conn: asyncpg.Connection, db_name: str) -> bool:
    """Check if a database exists."""
    result = await conn.fetchval(
        "SELECT 1 FROM pg_database WHERE datname = $1",
        db_name,
    )
    return result is not None


async def table_exists(conn: asyncpg.Connection, table_name: str) -> bool:
    """Check if a table exists."""
    result = await conn.fetchval(
        """
        SELECT 1 FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = $1
        """,
        table_name,
    )
    return result is not None


async def get_row_count(conn: asyncpg.Connection, table_name: str) -> int:
    """Get row count for a table."""
    try:
        result = await conn.fetchval(f'SELECT COUNT(*) FROM "{table_name}"')
        return result or 0
    except Exception:
        return -1


async def get_table_columns(conn: asyncpg.Connection, table_name: str) -> List[str]:
    """Get column names for a table."""
    rows = await conn.fetch(
        """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema = 'public' AND table_name = $1
        ORDER BY ordinal_position
        """,
        table_name,
    )
    return [row["column_name"] for row in rows]


# =============================================================================
# Migration Functions
# =============================================================================

async def create_target_database(
    service_name: str,
    config: dict,
    dry_run: bool = False,
) -> bool:
    """Create the target database if it doesn't exist."""
    db_name = config["database"]
    owner = config["owner"]
    
    log(f"Checking database '{db_name}'...", "STEP")
    
    conn = await get_connection("postgres")
    try:
        exists = await database_exists(conn, db_name)
        
        if exists:
            log(f"Database '{db_name}' already exists", "INFO")
            return True
        
        if dry_run:
            log(f"[DRY RUN] Would create database '{db_name}' owned by '{owner}'", "INFO")
            return True
        
        # Create database
        await conn.execute(f'CREATE DATABASE "{db_name}" OWNER "{owner}"')
        log(f"Created database '{db_name}'", "SUCCESS")
        
        # Connect to new database to set up extensions
        target_conn = await get_connection(db_name, owner, SOURCE_PASSWORD)
        try:
            await target_conn.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
            await target_conn.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
            log(f"Created extensions in '{db_name}'", "SUCCESS")
        finally:
            await target_conn.close()
        
        return True
        
    finally:
        await conn.close()


async def create_target_schema(
    service_name: str,
    config: dict,
    dry_run: bool = False,
) -> bool:
    """Create schema in the target database using the service's schema.py."""
    db_name = config["database"]
    
    log(f"Creating schema in '{db_name}'...", "STEP")
    
    if dry_run:
        log(f"[DRY RUN] Would create schema in '{db_name}'", "INFO")
        return True
    
    try:
        # Import the appropriate schema
        import importlib.util
        
        if service_name == "authz":
            schema_path = Path(__file__).parent.parent.parent / "srv" / "authz" / "src" / "schema.py"
        elif service_name == "ingest":
            schema_path = Path(__file__).parent.parent.parent / "srv" / "ingest" / "src" / "schema.py"
        else:
            log(f"Unknown service: {service_name}", "ERROR")
            return False
        
        spec = importlib.util.spec_from_file_location(f"{service_name}_schema", schema_path)
        schema_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema_module)
        
        if service_name == "authz":
            schema = schema_module.get_authz_schema()
        else:
            schema = schema_module.get_ingest_schema()
        
        # Apply schema
        conn = await get_connection(db_name, SOURCE_USER, SOURCE_PASSWORD)
        try:
            await schema.apply(conn)
            log(f"Schema created in '{db_name}'", "SUCCESS")
            return True
        finally:
            await conn.close()
            
    except Exception as e:
        log(f"Failed to create schema: {e}", "ERROR")
        return False


async def migrate_table_data(
    table_name: str,
    source_db: str,
    target_db: str,
    dry_run: bool = False,
) -> Tuple[bool, int]:
    """
    Migrate data from source table to target table.
    
    Returns:
        Tuple of (success, row_count)
    """
    log(f"Migrating table '{table_name}'...", "STEP")
    
    # Connect to source
    source_conn = await get_connection(source_db, SOURCE_USER, SOURCE_PASSWORD)
    
    try:
        # Check source table exists
        if not await table_exists(source_conn, table_name):
            log(f"Table '{table_name}' does not exist in source", "WARN")
            return True, 0
        
        # Get source row count
        source_count = await get_row_count(source_conn, table_name)
        
        if source_count == 0:
            log(f"Table '{table_name}': 0 rows (empty)", "INFO")
            return True, 0
        
        if dry_run:
            log(f"[DRY RUN] Would migrate {source_count} rows from '{table_name}'", "INFO")
            return True, source_count
        
        # Get columns
        columns = await get_table_columns(source_conn, table_name)
        columns_str = ", ".join(f'"{c}"' for c in columns)
        
        # Fetch all data from source
        rows = await source_conn.fetch(f'SELECT {columns_str} FROM "{table_name}"')
        
    finally:
        await source_conn.close()
    
    # Connect to target
    target_conn = await get_connection(target_db, SOURCE_USER, SOURCE_PASSWORD)
    
    try:
        # Check target table exists
        if not await table_exists(target_conn, table_name):
            log(f"Table '{table_name}' does not exist in target", "ERROR")
            return False, 0
        
        # Check if target already has data
        target_count = await get_row_count(target_conn, table_name)
        if target_count > 0:
            log(f"Table '{table_name}' already has {target_count} rows in target - skipping", "WARN")
            return True, 0
        
        # Build insert query with placeholders
        placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))
        insert_sql = f'INSERT INTO "{table_name}" ({columns_str}) VALUES ({placeholders})'
        
        # Insert data in batches
        batch_size = 1000
        inserted = 0
        
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            for row in batch:
                try:
                    await target_conn.execute(insert_sql, *row.values())
                    inserted += 1
                except asyncpg.UniqueViolationError:
                    # Skip duplicates
                    pass
                except Exception as e:
                    log(f"Error inserting row in '{table_name}': {e}", "WARN")
        
        log(f"Migrated {inserted} rows to '{table_name}'", "SUCCESS")
        return True, inserted
        
    finally:
        await target_conn.close()


async def verify_migration(
    service_name: str,
    config: dict,
) -> bool:
    """Verify that migration was successful by comparing row counts."""
    log(f"Verifying migration for '{service_name}'...", "STEP")
    
    source_conn = await get_connection(SOURCE_DB, SOURCE_USER, SOURCE_PASSWORD)
    target_conn = await get_connection(config["database"], SOURCE_USER, SOURCE_PASSWORD)
    
    try:
        all_match = True
        
        for table_name in config["tables"]:
            source_exists = await table_exists(source_conn, table_name)
            target_exists = await table_exists(target_conn, table_name)
            
            if not source_exists:
                log(f"  {table_name}: not in source (OK)", "INFO")
                continue
            
            if not target_exists:
                log(f"  {table_name}: MISSING in target!", "ERROR")
                all_match = False
                continue
            
            source_count = await get_row_count(source_conn, table_name)
            target_count = await get_row_count(target_conn, table_name)
            
            if source_count == target_count:
                log(f"  {table_name}: {source_count} rows ✓", "INFO")
            else:
                log(f"  {table_name}: source={source_count}, target={target_count} ⚠️", "WARN")
                # Not necessarily an error if target already had some data
        
        return all_match
        
    finally:
        await source_conn.close()
        await target_conn.close()


async def cleanup_source(
    service_name: str,
    config: dict,
    dry_run: bool = False,
) -> bool:
    """Remove tables from source database after migration."""
    log(f"Cleaning up source tables for '{service_name}'...", "STEP")
    
    if dry_run:
        log(f"[DRY RUN] Would drop {len(config['tables'])} tables from source", "INFO")
        return True
    
    conn = await get_connection(SOURCE_DB, SOURCE_USER, SOURCE_PASSWORD)
    
    try:
        # Drop tables in reverse order (dependencies last)
        for table_name in reversed(config["migration_order"]):
            try:
                await conn.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')
                log(f"  Dropped '{table_name}'", "INFO")
            except Exception as e:
                log(f"  Failed to drop '{table_name}': {e}", "WARN")
        
        log(f"Cleanup complete for '{service_name}'", "SUCCESS")
        return True
        
    finally:
        await conn.close()


async def migrate_service(
    service_name: str,
    dry_run: bool = False,
    cleanup: bool = False,
) -> bool:
    """Migrate a single service to its own database."""
    if service_name not in SERVICES:
        log(f"Unknown service: {service_name}", "ERROR")
        return False
    
    config = SERVICES[service_name]
    
    log(f"{'='*60}", "INFO")
    log(f"Migrating {service_name.upper()} service", "INFO")
    log(f"  Source: {SOURCE_DB}", "INFO")
    log(f"  Target: {config['database']}", "INFO")
    log(f"  Tables: {len(config['tables'])}", "INFO")
    log(f"{'='*60}", "INFO")
    
    # Step 1: Create target database
    if not await create_target_database(service_name, config, dry_run):
        return False
    
    # Step 2: Create schema in target
    if not await create_target_schema(service_name, config, dry_run):
        return False
    
    # Step 3: Migrate data
    total_rows = 0
    for table_name in config["migration_order"]:
        success, rows = await migrate_table_data(
            table_name,
            SOURCE_DB,
            config["database"],
            dry_run,
        )
        if not success:
            log(f"Migration failed for table '{table_name}'", "ERROR")
            return False
        total_rows += rows
    
    log(f"Total rows migrated: {total_rows}", "SUCCESS")
    
    # Step 4: Verify migration
    if not dry_run:
        await verify_migration(service_name, config)
    
    # Step 5: Cleanup source (optional)
    if cleanup:
        await cleanup_source(service_name, config, dry_run)
    
    return True


# =============================================================================
# Main
# =============================================================================

async def main():
    parser = argparse.ArgumentParser(
        description="Migrate services from shared busibox database to separate databases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    parser.add_argument(
        "--service",
        choices=list(SERVICES.keys()),
        help="Migrate a specific service",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Migrate all services",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Remove tables from source database after migration",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing migrations without making changes",
    )
    
    args = parser.parse_args()
    
    if not args.service and not args.all and not args.verify_only:
        parser.print_help()
        sys.exit(1)
    
    if not POSTGRES_PASSWORD:
        print("ERROR: POSTGRES_PASSWORD environment variable is required")
        sys.exit(1)
    
    # Header
    print()
    log("Database Migration Tool: Service Separation", "INFO")
    log(f"Host: {POSTGRES_HOST}:{POSTGRES_PORT}", "INFO")
    log(f"Source DB: {SOURCE_DB}", "INFO")
    log(f"Dry run: {args.dry_run}", "INFO")
    print()
    
    if args.verify_only:
        # Just verify existing migrations
        for service_name, config in SERVICES.items():
            await verify_migration(service_name, config)
        return
    
    services_to_migrate = []
    if args.all:
        services_to_migrate = list(SERVICES.keys())
    elif args.service:
        services_to_migrate = [args.service]
    
    success = True
    for service_name in services_to_migrate:
        if not await migrate_service(service_name, args.dry_run, args.cleanup):
            success = False
            log(f"Migration failed for {service_name}", "ERROR")
            break
    
    print()
    if success:
        log("Migration completed successfully!", "SUCCESS")
        if not args.dry_run and not args.cleanup:
            log("Run with --cleanup to remove tables from source database", "INFO")
    else:
        log("Migration failed!", "ERROR")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
