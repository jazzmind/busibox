"""
Library Migration Service - Migrates libraries from Busibox Portal to data-api.

This service runs on container startup to automatically migrate library data
from Busibox Portal's database to data-api's database.

The migration is idempotent - it only imports libraries that don't already exist.
"""

import os
from typing import Dict, List, Optional, Tuple

import asyncpg
import structlog

logger = structlog.get_logger()


def get_ai_portal_db_url() -> Optional[str]:
    """
    Get Busibox Portal database connection URL from environment.
    
    Returns None if not configured (migration won't run).
    """
    if url := os.environ.get("BUSIBOX_PORTAL_DB_URL"):
        return url
    
    # Check for individual components
    host = os.environ.get("BUSIBOX_PORTAL_DB_HOST")
    if not host:
        return None  # Not configured
    
    port = os.environ.get("BUSIBOX_PORTAL_DB_PORT", "5432")
    dbname = os.environ.get("BUSIBOX_PORTAL_DB_NAME", "ai_portal")
    user = os.environ.get("BUSIBOX_PORTAL_DB_USER", "ai_portal")
    password = os.environ.get("BUSIBOX_PORTAL_DB_PASSWORD", "")
    
    return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"


async def check_ai_portal_has_libraries(portal_conn: asyncpg.Connection) -> bool:
    """Check if Busibox Portal has the Library table with data."""
    try:
        # Check if table exists and has data
        result = await portal_conn.fetchval(
            'SELECT EXISTS(SELECT 1 FROM "Library" LIMIT 1)'
        )
        return result
    except asyncpg.UndefinedTableError:
        return False
    except Exception as e:
        logger.warning("Could not check Busibox Portal libraries", error=str(e))
        return False


async def check_migration_needed(
    portal_conn: asyncpg.Connection,
    data_conn: asyncpg.Connection,
) -> bool:
    """
    Check if migration is needed by comparing library counts.
    
    Migration is needed if Busibox Portal has libraries that don't exist in data.
    """
    try:
        # Get count from Busibox Portal
        portal_count = await portal_conn.fetchval('SELECT COUNT(*) FROM "Library"')
        
        # Get count from ingest
        data_count = await data_conn.fetchval('SELECT COUNT(*) FROM libraries')
        
        if portal_count == 0:
            logger.info("No libraries in Busibox Portal, skipping migration")
            return False
        
        if data_count >= portal_count:
            logger.info(
                "Libraries already migrated",
                portal_count=portal_count,
                data_count=data_count,
            )
            return False
        
        logger.info(
            "Migration needed",
            portal_count=portal_count,
            data_count=data_count,
            to_migrate=portal_count - data_count,
        )
        return True
        
    except Exception as e:
        logger.warning("Could not check migration status", error=str(e))
        return False


async def migrate_libraries(
    portal_conn: asyncpg.Connection,
    data_conn: asyncpg.Connection,
) -> Tuple[int, int]:
    """
    Migrate libraries from Busibox Portal to data.
    
    Returns:
        Tuple of (libraries_migrated, tag_caches_migrated)
    """
    # Fetch libraries from Busibox Portal
    libraries = await portal_conn.fetch("""
        SELECT 
            id,
            name,
            "isPersonal" as is_personal,
            "userId" as user_id,
            "libraryType" as library_type,
            "createdBy" as created_by,
            "deletedAt" as deleted_at,
            "createdAt" as created_at,
            "updatedAt" as updated_at
        FROM "Library"
        ORDER BY "createdAt" ASC
    """)
    
    migrated_libs = 0
    for lib in libraries:
        # Check if already exists
        exists = await data_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM libraries WHERE id = $1)",
            lib['id']
        )
        
        if exists:
            continue
        
        # Insert library
        try:
            await data_conn.execute(
                """
                INSERT INTO libraries (
                    id, name, is_personal, user_id, library_type,
                    created_by, deleted_at, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                """,
                lib['id'],
                lib['name'],
                lib['is_personal'],
                lib['user_id'],
                lib['library_type'],
                lib['created_by'],
                lib['deleted_at'],
                lib['created_at'],
                lib['updated_at']
            )
            migrated_libs += 1
            logger.debug(
                "Migrated library",
                library_id=str(lib['id']),
                name=lib['name'],
            )
        except asyncpg.UniqueViolationError:
            # Race condition - already inserted
            pass
        except Exception as e:
            logger.warning(
                "Failed to migrate library",
                library_id=str(lib['id']),
                error=str(e),
            )
    
    # Fetch and migrate tag caches
    tag_caches = await portal_conn.fetch("""
        SELECT 
            id,
            "libraryId" as library_id,
            version,
            groups,
            "generatedAt" as generated_at
        FROM "LibraryTagCache"
    """)
    
    import json
    migrated_caches = 0
    for cache in tag_caches:
        # Check if library exists in ingest
        lib_exists = await data_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM libraries WHERE id = $1)",
            cache['library_id']
        )
        if not lib_exists:
            continue
        
        # Check if cache already exists
        cache_exists = await data_conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM library_tag_cache WHERE id = $1)",
            cache['id']
        )
        if cache_exists:
            continue
        
        try:
            # Handle JSON encoding
            groups_json = cache['groups']
            if isinstance(groups_json, str):
                groups_json = json.loads(groups_json)
            
            await data_conn.execute(
                """
                INSERT INTO library_tag_cache (id, library_id, version, groups, generated_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                cache['id'],
                cache['library_id'],
                cache['version'],
                json.dumps(groups_json) if groups_json else '[]',
                cache['generated_at']
            )
            migrated_caches += 1
        except asyncpg.UniqueViolationError:
            pass
        except Exception as e:
            logger.warning(
                "Failed to migrate tag cache",
                cache_id=str(cache['id']),
                error=str(e),
            )
    
    return migrated_libs, migrated_caches


async def update_file_library_ids(
    portal_conn: asyncpg.Connection,
    data_conn: asyncpg.Connection,
) -> int:
    """
    Update library_id column in data_files based on Busibox Portal Document records.
    
    Returns count of files updated.
    """
    # Get document-library mappings from Busibox Portal
    documents = await portal_conn.fetch("""
        SELECT id, "libraryId" as library_id
        FROM "Document"
        WHERE "libraryId" IS NOT NULL
    """)
    
    updated = 0
    for doc in documents:
        file_id = doc['id']
        library_id = doc['library_id']
        
        # Update the data_files record if it exists and doesn't have library_id
        result = await data_conn.execute(
            """
            UPDATE data_files
            SET library_id = $1
            WHERE file_id = $2::uuid AND library_id IS NULL
            """,
            library_id,
            file_id
        )
        
        if result and result.split()[-1] != '0':
            updated += 1
    
    return updated


async def run_migration_if_needed(data_pool: asyncpg.Pool) -> None:
    """
    Run library migration from Busibox Portal if needed.
    
    This function is called on container startup after schema is applied.
    It's safe to call multiple times (idempotent).
    """
    # Get Busibox Portal connection URL
    portal_url = get_ai_portal_db_url()
    if not portal_url:
        print("[library-migration] Busibox Portal DB not configured, skipping migration")
        logger.debug("Busibox Portal DB not configured, skipping library migration")
        return
    
    print("[library-migration] Checking for library migration...")
    
    portal_conn = None
    data_conn = None
    
    try:
        # Connect to Busibox Portal
        try:
            portal_conn = await asyncpg.connect(portal_url)
        except Exception as e:
            logger.debug(
                "Could not connect to Busibox Portal DB, skipping migration",
                error=str(e)
            )
            return
        
        # Check if Busibox Portal has libraries
        has_libraries = await check_ai_portal_has_libraries(portal_conn)
        if not has_libraries:
            logger.debug("Busibox Portal has no libraries, skipping migration")
            return
        
        # Get data connection
        data_conn = await data_pool.acquire()
        
        # Check if migration is needed
        needed = await check_migration_needed(portal_conn, data_conn)
        if not needed:
            print("[library-migration] Libraries already migrated, skipping")
            return
        
        # Run migration
        print("[library-migration] Starting library migration from Busibox Portal...")
        logger.info("Starting library migration from Busibox Portal...")
        
        libs, caches = await migrate_libraries(portal_conn, data_conn)
        files = await update_file_library_ids(portal_conn, data_conn)
        
        print(f"[library-migration] Completed: {libs} libraries, {caches} tag caches, {files} files updated")
        logger.info(
            "Library migration completed",
            libraries_migrated=libs,
            tag_caches_migrated=caches,
            files_updated=files,
        )
        
    except Exception as e:
        logger.warning(
            "Library migration failed (non-fatal)",
            error=str(e),
        )
    finally:
        if portal_conn:
            await portal_conn.close()
        if data_conn:
            await data_pool.release(data_conn)
