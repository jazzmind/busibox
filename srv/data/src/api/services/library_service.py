"""
Library Service for managing document libraries.

Handles library CRUD operations, personal library management, and folder resolution.
This service consolidates library management that was previously split between
Busibox Portal and data-api.

RLS (Row-Level Security):
- Libraries table does NOT have RLS (application-level filtering)
- data_files table HAS RLS - requires app.user_id session variable
- All queries to data_files must go through acquire_with_rls() context manager
"""

import uuid
from datetime import datetime
from typing import Dict, List, Optional, Literal
from contextlib import asynccontextmanager

import asyncpg
import structlog

# Import RLS utilities from busibox_common
from busibox_common.auth import set_rls_session_vars, WorkerRLSContext

logger = structlog.get_logger()


# Personal library type constants
class PersonalLibraryTypes:
    DOCS = "DOCS"
    RESEARCH = "RESEARCH"
    TASKS = "TASKS"
    MEDIA = "MEDIA"
    CUSTOM = "CUSTOM"  # User-created personal libraries with custom names


# Library display names by type (CUSTOM uses user-provided name)
PERSONAL_LIBRARY_NAMES: Dict[str, str] = {
    PersonalLibraryTypes.DOCS: "Personal",
    PersonalLibraryTypes.RESEARCH: "Research",
    PersonalLibraryTypes.TASKS: "Tasks",
    PersonalLibraryTypes.MEDIA: "Media",
    PersonalLibraryTypes.CUSTOM: "Custom",  # Placeholder; actual name comes from user
}

# Fixed types that cannot be deleted by users (only CUSTOM can be deleted)
FIXED_PERSONAL_LIBRARY_TYPES = {
    PersonalLibraryTypes.DOCS,
    PersonalLibraryTypes.RESEARCH,
    PersonalLibraryTypes.TASKS,
    PersonalLibraryTypes.MEDIA,
}

# Folder name to library type mapping
FOLDER_TO_LIBRARY_TYPE: Dict[str, str] = {
    "personal": PersonalLibraryTypes.DOCS,
    "personal-docs": PersonalLibraryTypes.DOCS,
    "docs": PersonalLibraryTypes.DOCS,
    "personal-research": PersonalLibraryTypes.RESEARCH,
    "research": PersonalLibraryTypes.RESEARCH,
    "personal-tasks": PersonalLibraryTypes.TASKS,
    "tasks": PersonalLibraryTypes.TASKS,
    "personal-media": PersonalLibraryTypes.MEDIA,
    "media": PersonalLibraryTypes.MEDIA,
}


class LibraryService:
    """
    Service for library management operations.
    
    RLS Note:
    - Libraries table has no RLS (application-level access control)
    - data_files has RLS - use acquire_with_rls() for those queries
    """
    
    def __init__(self, pool: asyncpg.Pool):
        """
        Initialize the library service.
        
        Args:
            pool: AsyncPG connection pool
        """
        self.pool = pool
    
    @asynccontextmanager
    async def acquire_with_rls(self, request):
        """
        Get a connection with RLS session variables set.
        
        Use this for any queries against tables with RLS enabled
        (e.g., data_files, data_status).
        
        Args:
            request: FastAPI Request object with user_id in state,
                    or WorkerRLSContext for background workers
        """
        user_id = getattr(request.state, "user_id", None)
        role_ids = getattr(request.state, "role_ids", [])
        print(f"[acquire_with_rls] Setting RLS for user_id={user_id}, role_ids={role_ids}")
        
        async with self.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            yield conn
    
    async def get_or_create_personal_library(
        self,
        user_id: str,
        library_type: str = PersonalLibraryTypes.DOCS,
    ) -> Dict:
        """
        Get or create a personal library for a user.
        
        Args:
            user_id: The user's UUID
            library_type: Type of personal library (DOCS, RESEARCH, TASKS)
            
        Returns:
            Library record as dict
        """
        user_uuid = uuid.UUID(user_id)
        
        async with self.pool.acquire() as conn:
            # First, try to find existing library
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type, 
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE user_id = $1 
                  AND library_type = $2 
                  AND is_personal = true
                  AND deleted_at IS NULL
                """,
                user_uuid,
                library_type,
            )
            
            if library:
                logger.debug(
                    "Found existing personal library",
                    library_id=str(library["id"]),
                    library_type=library_type,
                    user_id=user_id,
                )
                return dict(library)
            
            # For DOCS type, check for legacy library with null type and migrate
            if library_type == PersonalLibraryTypes.DOCS:
                legacy_library = await conn.fetchrow(
                    """
                    SELECT id, name, is_personal, user_id, library_type,
                           created_by, deleted_at, created_at, updated_at
                    FROM libraries
                    WHERE user_id = $1
                      AND library_type IS NULL
                      AND is_personal = true
                      AND deleted_at IS NULL
                    """,
                    user_uuid,
                )
                
                if legacy_library:
                    # Migrate legacy library to DOCS type
                    await conn.execute(
                        """
                        UPDATE libraries
                        SET library_type = $1, updated_at = NOW()
                        WHERE id = $2
                        """,
                        library_type,
                        legacy_library["id"],
                    )
                    logger.info(
                        "Migrated legacy personal library to DOCS type",
                        library_id=str(legacy_library["id"]),
                        user_id=user_id,
                    )
                    
                    # Re-fetch to get updated record
                    library = await conn.fetchrow(
                        """
                        SELECT id, name, is_personal, user_id, library_type,
                               created_by, deleted_at, created_at, updated_at
                        FROM libraries
                        WHERE id = $1
                        """,
                        legacy_library["id"],
                    )
                    return dict(library)
            
            # Create new personal library
            library_name = PERSONAL_LIBRARY_NAMES.get(library_type, "Personal")
            library_id = uuid.uuid4()
            
            await conn.execute(
                """
                INSERT INTO libraries (id, name, is_personal, user_id, library_type, created_by, created_at, updated_at)
                VALUES ($1, $2, true, $3, $4, $5, NOW(), NOW())
                """,
                library_id,
                library_name,
                user_uuid,
                library_type,
                user_uuid,
            )
            
            logger.info(
                "Created personal library",
                library_id=str(library_id),
                library_type=library_type,
                user_id=user_id,
            )
            
            # Fetch and return the created library
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type,
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE id = $1
                """,
                library_id,
            )
            
            return dict(library)

    async def create_custom_personal_library(
        self,
        user_id: str,
        name: str,
    ) -> Dict:
        """
        Create a user-named personal library (CUSTOM type).

        Args:
            user_id: The user's UUID
            name: Custom library name

        Returns:
            Created library record
        """
        return await self.create_library(
            name=name.strip() or "New Library",
            created_by=user_id,
            is_personal=True,
            user_id=user_id,
            library_type=PersonalLibraryTypes.CUSTOM,
        )
    
    async def get_library_by_folder(
        self,
        user_id: str,
        folder_name: str,
    ) -> Optional[Dict]:
        """
        Resolve a folder name to a library.
        
        Handles personal library aliases:
        - "personal", "personal-docs", "docs" -> DOCS library
        - "personal-research", "research" -> RESEARCH library
        - "personal-tasks", "tasks" -> TASKS library
        
        For other folder names, attempts to find a shared library by name.
        
        Args:
            user_id: The user's UUID
            folder_name: Folder name to resolve
            
        Returns:
            Library record as dict, or None if not found
        """
        normalized_folder = folder_name.lower().strip()
        
        # Check if it's a personal library alias
        library_type = FOLDER_TO_LIBRARY_TYPE.get(normalized_folder)
        
        if library_type:
            # Get or create the personal library
            return await self.get_or_create_personal_library(user_id, library_type)
        
        # Try to find a shared library by name
        user_uuid = uuid.UUID(user_id)
        
        async with self.pool.acquire() as conn:
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type,
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE LOWER(name) = LOWER($1)
                  AND deleted_at IS NULL
                  AND (is_personal = false OR user_id = $2)
                """,
                folder_name,
                user_uuid,
            )
            
            if library:
                return dict(library)
        
        return None
    
    async def get_library_by_id(
        self,
        library_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Get a library by its ID.
        
        Args:
            library_id: The library's UUID
            user_id: Optional user ID for access control
            
        Returns:
            Library record as dict, or None if not found
        """
        lib_uuid = uuid.UUID(library_id)
        
        async with self.pool.acquire() as conn:
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type,
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE id = $1 AND deleted_at IS NULL
                """,
                lib_uuid,
            )
            
            if library:
                return dict(library)
        
        return None
    
    async def list_user_libraries(
        self,
        user_id: str,
        include_shared: bool = True,
    ) -> List[Dict]:
        """
        List libraries accessible to a user.
        
        Args:
            user_id: The user's UUID
            include_shared: Whether to include shared libraries
            
        Returns:
            List of library records
        """
        user_uuid = uuid.UUID(user_id)
        
        async with self.pool.acquire() as conn:
            # Custom sort order for personal library types:
            # DOCS first (main personal), then RESEARCH, TASKS, MEDIA, then CUSTOM
            personal_type_order = """
                CASE library_type
                    WHEN 'DOCS' THEN 1
                    WHEN 'RESEARCH' THEN 2
                    WHEN 'TASKS' THEN 3
                    WHEN 'MEDIA' THEN 4
                    WHEN 'CUSTOM' THEN 5
                    ELSE 6
                END
            """
            
            if include_shared:
                libraries = await conn.fetch(
                    f"""
                    SELECT id, name, is_personal, user_id, library_type,
                           created_by, deleted_at, created_at, updated_at
                    FROM libraries
                    WHERE ((is_personal = true AND user_id = $1)
                       OR is_personal = false)
                    AND deleted_at IS NULL
                    ORDER BY is_personal DESC, {personal_type_order}, name ASC
                    """,
                    user_uuid,
                )
            else:
                libraries = await conn.fetch(
                    f"""
                    SELECT id, name, is_personal, user_id, library_type,
                           created_by, deleted_at, created_at, updated_at
                    FROM libraries
                    WHERE is_personal = true 
                      AND user_id = $1
                      AND deleted_at IS NULL
                    ORDER BY {personal_type_order}
                    """,
                    user_uuid,
                )
            
            return [dict(lib) for lib in libraries]
    
    async def create_library(
        self,
        name: str,
        created_by: str,
        is_personal: bool = False,
        user_id: Optional[str] = None,
        library_type: Optional[str] = None,
        library_id: Optional[str] = None,
    ) -> Dict:
        """
        Create a new library.
        
        Args:
            name: Library name
            created_by: User ID who created it
            is_personal: Whether this is a personal library
            user_id: Owner user ID (for personal libraries)
            library_type: Type of personal library (DOCS, RESEARCH, TASKS)
            library_id: Optional explicit library ID (for syncing from Busibox Portal)
            
        Returns:
            Created library record
        """
        # Use explicit ID if provided, otherwise generate new one
        lib_uuid = uuid.UUID(library_id) if library_id else uuid.uuid4()
        created_by_uuid = uuid.UUID(created_by)
        user_uuid = uuid.UUID(user_id) if user_id else None
        
        async with self.pool.acquire() as conn:
            # Check if library already exists (for sync operations)
            if library_id:
                existing = await conn.fetchrow(
                    "SELECT id FROM libraries WHERE id = $1",
                    lib_uuid,
                )
                if existing:
                    logger.info(
                        "Library already exists, skipping create",
                        library_id=library_id,
                    )
                    # Return the existing library
                    library = await conn.fetchrow(
                        """
                        SELECT id, name, is_personal, user_id, library_type,
                               created_by, deleted_at, created_at, updated_at
                        FROM libraries
                        WHERE id = $1
                        """,
                        lib_uuid,
                    )
                    return dict(library)
            
            await conn.execute(
                """
                INSERT INTO libraries (id, name, is_personal, user_id, library_type, created_by, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, NOW(), NOW())
                """,
                lib_uuid,
                name,
                is_personal,
                user_uuid,
                library_type,
                created_by_uuid,
            )
            
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type,
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE id = $1
                """,
                lib_uuid,
            )
            
            logger.info(
                "Created library",
                library_id=str(lib_uuid),
                name=name,
                is_personal=is_personal,
                created_by=created_by,
            )
            
            return dict(library)
    
    async def update_library(
        self,
        library_id: str,
        name: Optional[str] = None,
    ) -> Optional[Dict]:
        """
        Update a library.
        
        Args:
            library_id: The library's UUID
            name: New name (optional)
            
        Returns:
            Updated library record, or None if not found
        """
        lib_uuid = uuid.UUID(library_id)
        
        async with self.pool.acquire() as conn:
            # Check if library exists
            existing = await conn.fetchrow(
                "SELECT id FROM libraries WHERE id = $1 AND deleted_at IS NULL",
                lib_uuid,
            )
            
            if not existing:
                return None
            
            if name:
                await conn.execute(
                    """
                    UPDATE libraries
                    SET name = $1, updated_at = NOW()
                    WHERE id = $2
                    """,
                    name,
                    lib_uuid,
                )
            
            library = await conn.fetchrow(
                """
                SELECT id, name, is_personal, user_id, library_type,
                       created_by, deleted_at, created_at, updated_at
                FROM libraries
                WHERE id = $1
                """,
                lib_uuid,
            )
            
            return dict(library) if library else None
    
    async def delete_library(
        self,
        library_id: str,
        soft_delete: bool = True,
    ) -> bool:
        """
        Delete a library.
        
        Args:
            library_id: The library's UUID
            soft_delete: If True, soft delete (set deleted_at). If False, hard delete.
            
        Returns:
            True if deleted, False if not found
        """
        lib_uuid = uuid.UUID(library_id)
        
        async with self.pool.acquire() as conn:
            if soft_delete:
                result = await conn.execute(
                    """
                    UPDATE libraries
                    SET deleted_at = NOW(), updated_at = NOW()
                    WHERE id = $1 AND deleted_at IS NULL
                    """,
                    lib_uuid,
                )
            else:
                result = await conn.execute(
                    "DELETE FROM libraries WHERE id = $1",
                    lib_uuid,
                )
            
            deleted = result.split()[-1] != "0"
            
            if deleted:
                logger.info(
                    "Deleted library",
                    library_id=library_id,
                    soft_delete=soft_delete,
                )
            
            return deleted
    
    async def get_library_document_count(
        self,
        library_id: str,
        request=None,
    ) -> int:
        """
        Get the number of documents in a library.
        
        Note: This queries data_files which has RLS enabled.
        If request is provided, RLS will filter by user_id.
        If no request (e.g., for admin operations), counts all documents.
        
        Args:
            library_id: The library's UUID
            request: Optional FastAPI request for RLS context
            
        Returns:
            Document count
        """
        lib_uuid = uuid.UUID(library_id)
        
        if request:
            # Use RLS-enabled connection
            async with self.acquire_with_rls(request) as conn:
                result = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM data_files
                    WHERE library_id = $1
                    """,
                    lib_uuid,
                )
                return result or 0
        else:
            # No RLS - count all (for internal operations)
            async with self.pool.acquire() as conn:
                result = await conn.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM data_files
                    WHERE library_id = $1
                    """,
                    lib_uuid,
                )
                return result or 0
    
    async def get_library_documents(
        self,
        library_id: str,
        request,
        sort_by: str = "createdAt",
        sort_order: str = "desc",
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        tag: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> List[Dict]:
        """
        Get documents in a library from data_files table.
        
        Args:
            library_id: The library's UUID
            request: FastAPI Request object with RLS context (user_id, role_ids in state)
            sort_by: Sort field (createdAt, name, size)
            sort_order: asc or desc
            status_filter: Filter by processing status
            search: Search in filename
            tag: Filter by tag (future)
            
        Returns:
            List of document records
            
        Note:
            This queries data_files which has RLS enabled.
            The request must have user_id set in request.state for RLS to work.
        """
        lib_uuid = uuid.UUID(library_id)
        
        # Map API sort fields to DB columns
        sort_column_map = {
            "createdAt": "created_at",
            "name": "filename",
            "size": "size_bytes",
        }
        sort_column = sort_column_map.get(sort_by, "created_at")
        order = "DESC" if sort_order.lower() == "desc" else "ASC"
        
        # Use RLS-enabled connection for data_files query
        async with self.acquire_with_rls(request) as conn:
            # Debug: verify RLS session variable
            rls_check = await conn.fetchval("SELECT current_setting('app.user_id', true)")
            print(f"[get_library_documents] RLS app.user_id = {rls_check}")
            
            # Build query with optional filters
            query = """
                SELECT 
                    f.file_id as id,
                    f.filename as name,
                    f.original_filename as "originalFilename",
                    f.mime_type as "mimeType",
                    f.size_bytes as "sizeBytes",
                    f.storage_path as "storagePath",
                    f.content_hash as "contentHash",
                    f.extracted_keywords as "extractedKeywords",
                    f.metadata,
                    f.visibility,
                    f.library_id as "libraryId",
                    f.owner_id as "ownerId",
                    f.created_at as "createdAt",
                    f.updated_at as "updatedAt",
                    s.stage as status,
                    s.progress as "processingProgress",
                    s.error_message as "errorMessage"
                FROM data_files f
                LEFT JOIN data_status s ON f.file_id = s.file_id
                WHERE f.library_id = $1
            """
            params = [lib_uuid]
            print(f"[get_library_documents] Query params: library_id={lib_uuid}")
            param_idx = 2
            
            # Add status filter
            if status_filter:
                query += f" AND s.stage = ${param_idx}"
                params.append(status_filter)
                param_idx += 1
            
            # Add search filter
            if search:
                query += f" AND (f.filename ILIKE ${param_idx} OR f.original_filename ILIKE ${param_idx})"
                params.append(f"%{search}%")
                param_idx += 1

            # Add tags filter: documents where extracted_keywords overlaps with any tag
            tags_to_use = tags if tags else ([tag] if tag else None)
            if tags_to_use:
                query += f" AND f.extracted_keywords && ${param_idx}::text[]"
                params.append(tags_to_use)
                param_idx += 1
            
            # Add ordering
            query += f" ORDER BY f.{sort_column} {order}"
            
            rows = await conn.fetch(query, *params)
            
            documents = []
            for row in rows:
                doc = dict(row)
                # Convert UUIDs to strings
                doc["id"] = str(doc["id"])
                if doc.get("libraryId"):
                    doc["libraryId"] = str(doc["libraryId"])
                if doc.get("ownerId"):
                    doc["ownerId"] = str(doc["ownerId"])
                # Convert timestamps
                if doc.get("createdAt"):
                    doc["createdAt"] = doc["createdAt"].isoformat()
                if doc.get("updatedAt"):
                    doc["updatedAt"] = doc["updatedAt"].isoformat()
                # Parse metadata if JSON string
                if doc.get("metadata") and isinstance(doc["metadata"], str):
                    import json
                    try:
                        doc["metadata"] = json.loads(doc["metadata"])
                    except json.JSONDecodeError:
                        pass
                # Ensure extractedKeywords is a list (PostgreSQL returns array)
                if doc.get("extractedKeywords") is None:
                    doc["extractedKeywords"] = []
                doc["extractedKeywords"] = list(doc["extractedKeywords"]) if doc["extractedKeywords"] else []
                documents.append(doc)
            
            return documents
    
    async def ensure_all_personal_libraries(
        self,
        user_id: str,
    ) -> List[Dict]:
        """
        Ensure all personal library types exist for a user.
        
        Creates DOCS, RESEARCH, TASKS, and MEDIA libraries if they don't exist.
        
        Args:
            user_id: The user's UUID
            
        Returns:
            List of all personal libraries for the user
        """
        libraries = []
        
        for library_type in [PersonalLibraryTypes.DOCS, PersonalLibraryTypes.RESEARCH, PersonalLibraryTypes.TASKS, PersonalLibraryTypes.MEDIA]:
            try:
                library = await self.get_or_create_personal_library(user_id, library_type)
                libraries.append(library)
            except Exception as e:
                logger.error(
                    "Failed to ensure personal library",
                    library_type=library_type,
                    user_id=user_id,
                    error=str(e),
                )
        
        return libraries


def library_to_response(library: Dict) -> Dict:
    """
    Convert a library record to API response format.
    
    Args:
        library: Library record from database
        
    Returns:
        API response dict with camelCase keys
    """
    return {
        "id": str(library["id"]),
        "name": library["name"],
        "isPersonal": library["is_personal"],
        "userId": str(library["user_id"]) if library["user_id"] else None,
        "libraryType": library["library_type"],
        "createdBy": str(library["created_by"]),
        "deletedAt": library["deleted_at"].isoformat() if library["deleted_at"] else None,
        "createdAt": library["created_at"].isoformat() if library["created_at"] else None,
        "updatedAt": library["updated_at"].isoformat() if library["updated_at"] else None,
    }
