"""
Library management endpoints.

Handles:
- GET /libraries: List user's libraries
- POST /libraries: Create a new library
- GET /libraries/{id}: Get library by ID
- GET /libraries/by-folder: Resolve folder name to library
- PUT /libraries/{id}: Update library
- DELETE /libraries/{id}: Delete library (soft delete)
"""

import uuid
from typing import Optional, List

import structlog
from fastapi import APIRouter, Depends, Request, Query
from starlette import status as http_status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.middleware.jwt_auth import ScopeChecker
from api.services.library_service import (
    LibraryService,
    PersonalLibraryTypes,
    PERSONAL_LIBRARY_NAMES,
    FIXED_PERSONAL_LIBRARY_TYPES,
    library_to_response,
)

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_data_read = ScopeChecker("data.read")
require_data_write = ScopeChecker("data.write")


# =============================================================================
# Request/Response Models
# =============================================================================

class CreateLibraryRequest(BaseModel):
    """Request body for creating a library."""
    id: Optional[str] = Field(None, description="Optional explicit library ID (for syncing from AI Portal)")
    name: str = Field(..., description="Library name")
    is_personal: bool = Field(default=False, alias="isPersonal", description="Whether this is a personal library")
    user_id: Optional[str] = Field(None, alias="userId", description="User ID (for personal libraries from AI Portal sync)")
    library_type: Optional[str] = Field(None, alias="libraryType", description="Personal library type (DOCS, RESEARCH, TASKS)")
    created_by: Optional[str] = Field(None, alias="createdBy", description="Creator user ID (from AI Portal sync)")
    
    class Config:
        populate_by_name = True  # Allow both snake_case and camelCase


class UpdateLibraryRequest(BaseModel):
    """Request body for updating a library."""
    name: Optional[str] = Field(None, description="New library name")


class LibraryResponse(BaseModel):
    """Library response model."""
    id: str
    name: str
    isPersonal: bool
    userId: Optional[str]
    libraryType: Optional[str]
    createdBy: str
    deletedAt: Optional[str]
    createdAt: Optional[str]
    updatedAt: Optional[str]


# =============================================================================
# Helper Functions
# =============================================================================

def validate_uuid(id_str: str, field_name: str = "ID") -> tuple[Optional[uuid.UUID], Optional[JSONResponse]]:
    """
    Validate a string as a UUID.
    
    Returns:
        tuple: (uuid.UUID, None) if valid, or (None, JSONResponse) with 400 error if invalid
    """
    try:
        return uuid.UUID(id_str), None
    except ValueError:
        return None, JSONResponse(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            content={"error": f"Invalid {field_name} format", "details": f"{field_name} must be a valid UUID"}
        )


async def get_library_service(request: Request) -> LibraryService:
    """Get library service using the shared database connection pool."""
    # Use the singleton pg_service from main to avoid creating new pools
    from api.main import pg_service
    
    # Ensure the pool is connected
    if not pg_service.pool:
        await pg_service.connect()
    
    return LibraryService(pg_service.pool)


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/by-folder")
async def get_library_by_folder(
    request: Request,
    folder: str = Query(..., description="Folder name to resolve (e.g., 'personal-tasks', 'research')"),
):
    """
    Resolve a folder name to a library.
    
    Supported folder names:
    - "personal", "personal-docs", "docs" -> Personal DOCS library
    - "personal-research", "research" -> Personal RESEARCH library  
    - "personal-tasks", "tasks" -> Personal TASKS library
    
    For other folder names, attempts to find a shared library by name.
    
    This endpoint auto-creates personal libraries if they don't exist.
    
    Authentication: Accepts either Bearer token (from authenticated services) or
    X-User-Id header (from AI Portal proxy).
    """
    # Get user_id from either authenticated session or header
    user_id = getattr(request.state, "user_id", None)
    
    # If no user_id from auth, check for header (from AI Portal proxy)
    if not user_id:
        user_id = request.headers.get("X-User-Id")
    
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    try:
        library_service = await get_library_service(request)
        library = await library_service.get_library_by_folder(user_id, folder)
        
        if not library:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": f"Library not found for folder: {folder}"}
            )
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": {
                    "library": library_to_response(library)
                }
            }
        )
        
    except Exception as e:
        logger.error("Failed to resolve library by folder", folder=folder, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to resolve library", "details": str(e)}
        )


@router.get("")
async def list_libraries(
    request: Request,
    include_shared: bool = Query(True, description="Include shared libraries"),
    _: dict = Depends(require_data_read),
):
    """
    List libraries accessible to the current user.
    
    Returns personal libraries and optionally shared libraries.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    try:
        library_service = await get_library_service(request)
        
        # Ensure all personal libraries exist
        await library_service.ensure_all_personal_libraries(user_id)
        
        # List libraries
        libraries = await library_service.list_user_libraries(user_id, include_shared)
        
        # Get document counts for each library (using RLS)
        libraries_with_counts = []
        for lib in libraries:
            count = await library_service.get_library_document_count(str(lib["id"]), request)
            response = library_to_response(lib)
            response["documentCount"] = count
            libraries_with_counts.append(response)
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": libraries_with_counts,
                "total": len(libraries_with_counts),
            }
        )
        
    except Exception as e:
        logger.error("Failed to list libraries", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to list libraries", "details": str(e)}
        )


@router.post("")
async def create_library(
    request: Request,
    body: CreateLibraryRequest,
    _: dict = Depends(require_data_write),
):
    """
    Create a new library.
    
    For personal libraries (is_personal=true), the user_id is automatically set
    to the current user. Personal libraries require a library_type (DOCS, RESEARCH, TASKS).
    
    For shared libraries (is_personal=false), only the name is required.
    
    An explicit ID can be provided for syncing from AI Portal (id field).
    When syncing, also provide userId, libraryType, and createdBy.
    """
    # Check for internal service header (from AI Portal sync)
    internal_service = request.headers.get("X-Internal-Service")
    header_user_id = request.headers.get("X-User-Id")
    
    # Get user_id from either the authenticated session or the header (for internal sync)
    user_id = getattr(request.state, "user_id", None) or header_user_id
    
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    # For internal sync requests with explicit ID, use provided values
    is_sync_request = body.id is not None and internal_service == "ai-portal"
    
    # Validate personal library requirements (unless it's a sync request)
    if body.is_personal and not is_sync_request:
        if not body.library_type:
            return JSONResponse(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                content={"error": "library_type is required for personal libraries"}
            )
        valid_types = [PersonalLibraryTypes.DOCS, PersonalLibraryTypes.RESEARCH, PersonalLibraryTypes.TASKS, PersonalLibraryTypes.MEDIA, PersonalLibraryTypes.CUSTOM]
        if body.library_type not in valid_types:
            return JSONResponse(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                content={"error": f"Invalid library_type. Must be one of: DOCS, RESEARCH, TASKS, MEDIA, CUSTOM"}
            )
        if body.library_type == PersonalLibraryTypes.CUSTOM and (not body.name or not body.name.strip()):
            return JSONResponse(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                content={"error": "Library name is required for custom personal libraries"}
            )
    
    try:
        library_service = await get_library_service(request)
        
        if is_sync_request:
            # Sync request from AI Portal - use all provided fields
            logger.info(
                "Syncing library from AI Portal",
                library_id=body.id,
                name=body.name,
                is_personal=body.is_personal,
                library_type=body.library_type,
            )
            library = await library_service.create_library(
                name=body.name,
                created_by=body.created_by or user_id,
                is_personal=body.is_personal,
                user_id=body.user_id,
                library_type=body.library_type,
                library_id=body.id,
            )
        elif body.is_personal:
            if body.library_type == PersonalLibraryTypes.CUSTOM:
                library = await library_service.create_custom_personal_library(user_id, body.name)
            else:
                # Use get_or_create to handle existing libraries for fixed types
                library = await library_service.get_or_create_personal_library(user_id, body.library_type)
        else:
            library = await library_service.create_library(
                name=body.name,
                created_by=user_id,
                is_personal=False,
            )
        
        return JSONResponse(
            status_code=http_status.HTTP_201_CREATED,
            content={
                "data": library_to_response(library)
            }
        )
        
    except Exception as e:
        logger.error("Failed to create library", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to create library", "details": str(e)}
        )


@router.get("/app-data")
async def list_app_data_libraries(
    request: Request,
    sourceApp: Optional[str] = Query(None, description="Filter by source app (e.g., 'status-report')"),
    _: dict = Depends(require_data_read),
):
    """
    List app data libraries (data documents with sourceApp metadata).
    
    App data libraries are structured data created by apps like status-report.
    They are exposed as "libraries" for browsing in the document manager.
    
    Each data document with a sourceApp becomes a browseable library entry.
    The response includes schema information for rendering the data.
    
    Returns:
        List of app data "libraries" grouped by sourceApp
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    try:
        # Import data service to query data documents
        from api.services.data_service import DataService
        from api.main import pg_service
        
        data_service = DataService(pg_service.pool)
        
        # Query data documents with sourceApp in metadata
        # If sourceApp is specified, filter by it; otherwise get all app data documents
        from api.middleware.jwt_auth import set_rls_session_vars
        
        async with pg_service.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            
            # Build query for data documents with sourceApp
            query = """
                SELECT 
                    file_id,
                    filename as name,
                    owner_id,
                    visibility,
                    metadata,
                    data_schema,
                    data_record_count,
                    data_version,
                    data_modified_at,
                    library_id,
                    created_at,
                    updated_at
                FROM data_files
                WHERE doc_type = 'data'
                  AND metadata->>'sourceApp' IS NOT NULL
            """
            params = []
            param_idx = 1
            
            if sourceApp:
                query += f" AND metadata->>'sourceApp' = ${param_idx}"
                params.append(sourceApp)
                param_idx += 1
            
            query += " ORDER BY metadata->>'sourceApp', filename"
            
            rows = await conn.fetch(query, *params)
        
        # Transform to app data library format, grouped by sourceApp
        import json
        app_data_libraries = []
        
        for row in rows:
            metadata = json.loads(row["metadata"]) if row["metadata"] else {}
            schema = json.loads(row["data_schema"]) if row["data_schema"] else None
            
            app_data_libraries.append({
                "id": str(row["file_id"]),
                "documentId": str(row["file_id"]),  # Same as id for data documents
                "name": row["name"],
                "sourceApp": metadata.get("sourceApp"),
                "displayName": schema.get("displayName", row["name"]) if schema else row["name"],
                "itemLabel": schema.get("itemLabel", "Item") if schema else "Item",
                "recordCount": row["data_record_count"] or 0,
                "visibility": row["visibility"],
                "schema": schema,
                "allowSharing": schema.get("allowSharing", True) if schema else True,
                "createdAt": row["created_at"].isoformat() if row["created_at"] else None,
                "updatedAt": row["updated_at"].isoformat() if row["updated_at"] else None,
            })
        
        # Group by sourceApp for easier consumption
        grouped = {}
        for lib in app_data_libraries:
            app = lib["sourceApp"]
            if app not in grouped:
                grouped[app] = {
                    "sourceApp": app,
                    "documents": [],
                    "totalRecords": 0,
                }
            grouped[app]["documents"].append(lib)
            grouped[app]["totalRecords"] += lib["recordCount"]
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": app_data_libraries,
                "grouped": list(grouped.values()),
                "total": len(app_data_libraries),
            }
        )
        
    except Exception as e:
        logger.error("Failed to list app data libraries", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to list app data libraries", "details": str(e)}
        )


@router.get("/{library_id}")
async def get_library(
    request: Request,
    library_id: str,
    _: dict = Depends(require_data_read),
):
    """
    Get a library by ID.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    # Validate UUID
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    try:
        library_service = await get_library_service(request)
        library = await library_service.get_library_by_id(library_id, user_id)
        
        if not library:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Library not found"}
            )
        
        # Get document count (using RLS)
        count = await library_service.get_library_document_count(library_id, request)
        response = library_to_response(library)
        response["documentCount"] = count
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": response
            }
        )
        
    except Exception as e:
        logger.error("Failed to get library", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to get library", "details": str(e)}
        )


@router.put("/{library_id}")
async def update_library(
    request: Request,
    library_id: str,
    body: UpdateLibraryRequest,
    _: dict = Depends(require_data_write),
):
    """
    Update a library.
    
    Only the name can be updated. Personal libraries cannot have their
    library_type changed.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    # Validate UUID
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    try:
        library_service = await get_library_service(request)
        
        # Check if library exists and user has access
        existing = await library_service.get_library_by_id(library_id, user_id)
        if not existing:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Library not found"}
            )
        
        # Check ownership for personal libraries
        if existing["is_personal"] and str(existing["user_id"]) != user_id:
            return JSONResponse(
                status_code=http_status.HTTP_403_FORBIDDEN,
                content={"error": "Cannot modify another user's personal library"}
            )
        
        library = await library_service.update_library(library_id, name=body.name)
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": library_to_response(library)
            }
        )
        
    except Exception as e:
        logger.error("Failed to update library", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to update library", "details": str(e)}
        )


@router.delete("/{library_id}")
async def delete_library(
    request: Request,
    library_id: str,
    hard_delete: bool = Query(False, description="Permanently delete instead of soft delete"),
    _: dict = Depends(require_data_write),
):
    """
    Delete a library.
    
    By default, performs a soft delete (sets deleted_at timestamp).
    Documents in the library are NOT deleted - they become orphaned.
    
    Use hard_delete=true to permanently delete the library.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    # Validate UUID
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    try:
        library_service = await get_library_service(request)
        
        # Check if library exists and user has access
        existing = await library_service.get_library_by_id(library_id, user_id)
        if not existing:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Library not found"}
            )
        
        # Check ownership for personal libraries
        if existing["is_personal"] and str(existing["user_id"]) != user_id:
            return JSONResponse(
                status_code=http_status.HTTP_403_FORBIDDEN,
                content={"error": "Cannot delete another user's personal library"}
            )

        # Only CUSTOM personal libraries can be deleted; fixed types (DOCS, RESEARCH, TASKS, MEDIA) cannot
        if existing["is_personal"] and existing.get("library_type") in FIXED_PERSONAL_LIBRARY_TYPES:
            return JSONResponse(
                status_code=http_status.HTTP_403_FORBIDDEN,
                content={"error": "Cannot delete default personal libraries (Personal, Research, Tasks, Media). Create a custom library to organize documents."}
            )
        
        deleted = await library_service.delete_library(library_id, soft_delete=not hard_delete)
        
        if deleted:
            return JSONResponse(
                status_code=http_status.HTTP_200_OK,
                content={"message": "Library deleted", "id": library_id}
            )
        else:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Library not found"}
            )
        
    except Exception as e:
        logger.error("Failed to delete library", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to delete library", "details": str(e)}
        )


@router.get("/{library_id}/documents")
async def get_library_documents(
    request: Request,
    library_id: str,
    sortBy: str = "createdAt",
    sortOrder: str = "desc",
    status: Optional[str] = None,
    search: Optional[str] = None,
    tag: Optional[str] = None,
    tags: Optional[str] = None,
    _: dict = Depends(require_data_read),
):
    """
    Get documents in a library.
    
    Returns files from data_files filtered by library_id.
    Requires JWT authentication for RLS enforcement.
    
    Note: Internal services (AI Portal) must exchange tokens to get
    audience-bound JWT for data-api. Zero trust architecture.
    """
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])
    print(f"[get_library_documents] user_id={user_id}, role_ids={role_ids}, library_id={library_id}")
    
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    # Validate UUID
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    try:
        library_service = await get_library_service(request)
        
        # First verify user has access to the library
        library = await library_service.get_library_by_id(library_id, user_id)
        print(f"[get_library_documents] library lookup result: {library}")
        if not library:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Library not found or access denied"}
            )
        
        # Parse tags (comma-separated) for multi-tag filter
        tags_list: Optional[List[str]] = None
        if tags:
            tags_list = [t.strip() for t in tags.split(",") if t.strip()]
        elif tag:
            tags_list = [tag]

        # Get documents from data_files (uses RLS)
        documents = await library_service.get_library_documents(
            library_id=library_id,
            request=request,  # Pass request for RLS context
            sort_by=sortBy,
            sort_order=sortOrder,
            status_filter=status,
            search=search,
            tag=tag,
            tags=tags_list,
        )
        print(f"[get_library_documents] returning {len(documents)} documents")
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "documents": documents,
                "count": len(documents),
            }
        )
        
    except Exception as e:
        logger.error("Failed to get library documents", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to get library documents", "details": str(e)}
        )


# =============================================================================
# Library Trigger Models
# =============================================================================

class CreateTriggerRequest(BaseModel):
    """Request body for creating a library trigger."""
    name: str = Field(..., description="Trigger name")
    description: Optional[str] = Field(None, description="Trigger description")
    agent_id: Optional[str] = Field(None, alias="agentId", description="Agent ID to execute")
    prompt: Optional[str] = Field(None, description="Prompt for the agent")
    schema_document_id: Optional[str] = Field(None, alias="schemaDocumentId", description="Data document ID containing extraction schema")
    delegation_token: Optional[str] = Field(None, alias="delegationToken", description="Pre-authorized token for agent execution")
    delegation_scopes: Optional[List[str]] = Field(None, alias="delegationScopes", description="Scopes for delegation token")
    
    class Config:
        populate_by_name = True


class UpdateTriggerRequest(BaseModel):
    """Request body for updating a library trigger."""
    name: Optional[str] = Field(None, description="New trigger name")
    description: Optional[str] = Field(None, description="New description")
    is_active: Optional[bool] = Field(None, alias="isActive", description="Enable/disable trigger")
    prompt: Optional[str] = Field(None, description="New prompt")
    schema_document_id: Optional[str] = Field(None, alias="schemaDocumentId", description="New schema document ID")
    agent_id: Optional[str] = Field(None, alias="agentId", description="New agent ID")
    
    class Config:
        populate_by_name = True


def trigger_to_response(trigger: dict) -> dict:
    """Convert a trigger DB row to API response format."""
    return {
        "id": str(trigger["id"]),
        "libraryId": str(trigger["library_id"]),
        "name": trigger["name"],
        "description": trigger.get("description"),
        "agentId": str(trigger["agent_id"]) if trigger.get("agent_id") else None,
        "prompt": trigger.get("prompt"),
        "schemaDocumentId": str(trigger["schema_document_id"]) if trigger.get("schema_document_id") else None,
        "isActive": trigger.get("is_active", True),
        "createdBy": str(trigger["created_by"]),
        "executionCount": trigger.get("execution_count", 0),
        "lastExecutionAt": trigger["last_execution_at"].isoformat() if trigger.get("last_execution_at") else None,
        "lastError": trigger.get("last_error"),
        "createdAt": trigger["created_at"].isoformat() if trigger.get("created_at") else None,
        "updatedAt": trigger["updated_at"].isoformat() if trigger.get("updated_at") else None,
    }


# =============================================================================
# Library Trigger Endpoints
# =============================================================================

@router.get("/{library_id}/triggers")
async def list_library_triggers(
    request: Request,
    library_id: str,
    active_only: bool = Query(False, alias="activeOnly", description="Only return active triggers"),
    _: dict = Depends(require_data_read),
):
    """List triggers configured for a library."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    try:
        from api.main import pg_service
        triggers = await pg_service.list_library_triggers(library_id, active_only=active_only)
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": [trigger_to_response(t) for t in triggers],
                "total": len(triggers),
            }
        )
    except Exception as e:
        logger.error("Failed to list library triggers", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to list library triggers", "details": str(e)}
        )


@router.post("/{library_id}/triggers")
async def create_library_trigger(
    request: Request,
    library_id: str,
    body: CreateTriggerRequest,
    _: dict = Depends(require_data_write),
):
    """Create a new trigger on a library. When a document completes processing in this library, the trigger fires."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    lib_uuid, error_response = validate_uuid(library_id, "Library ID")
    if error_response:
        return error_response
    
    if not body.agent_id:
        return JSONResponse(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            content={"error": "agent_id is required"}
        )
    
    try:
        from api.main import pg_service
        trigger = await pg_service.create_library_trigger(
            library_id=library_id,
            name=body.name,
            created_by=user_id,
            agent_id=body.agent_id,
            prompt=body.prompt,
            schema_document_id=body.schema_document_id,
            description=body.description,
            delegation_token=body.delegation_token,
            delegation_scopes=body.delegation_scopes,
        )
        return JSONResponse(
            status_code=http_status.HTTP_201_CREATED,
            content={"data": trigger_to_response(trigger)}
        )
    except Exception as e:
        logger.error("Failed to create library trigger", library_id=library_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to create library trigger", "details": str(e)}
        )


@router.get("/{library_id}/triggers/{trigger_id}")
async def get_library_trigger(
    request: Request,
    library_id: str,
    trigger_id: str,
    _: dict = Depends(require_data_read),
):
    """Get a specific library trigger."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    _, error_response = validate_uuid(trigger_id, "Trigger ID")
    if error_response:
        return error_response
    
    try:
        from api.main import pg_service
        trigger = await pg_service.get_library_trigger(trigger_id)
        if not trigger or str(trigger["library_id"]) != library_id:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Trigger not found"}
            )
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={"data": trigger_to_response(trigger)}
        )
    except Exception as e:
        logger.error("Failed to get library trigger", trigger_id=trigger_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to get library trigger", "details": str(e)}
        )


@router.put("/{library_id}/triggers/{trigger_id}")
async def update_library_trigger(
    request: Request,
    library_id: str,
    trigger_id: str,
    body: UpdateTriggerRequest,
    _: dict = Depends(require_data_write),
):
    """Update a library trigger (enable/disable, change settings)."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    _, error_response = validate_uuid(trigger_id, "Trigger ID")
    if error_response:
        return error_response
    
    try:
        from api.main import pg_service
        
        # Verify trigger belongs to this library
        existing = await pg_service.get_library_trigger(trigger_id)
        if not existing or str(existing["library_id"]) != library_id:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Trigger not found"}
            )
        
        update_kwargs = {}
        if body.name is not None:
            update_kwargs["name"] = body.name
        if body.description is not None:
            update_kwargs["description"] = body.description
        if body.is_active is not None:
            update_kwargs["is_active"] = body.is_active
        if body.prompt is not None:
            update_kwargs["prompt"] = body.prompt
        if body.schema_document_id is not None:
            update_kwargs["schema_document_id"] = body.schema_document_id
        if body.agent_id is not None:
            update_kwargs["agent_id"] = body.agent_id
        
        trigger = await pg_service.update_library_trigger(trigger_id, **update_kwargs)
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={"data": trigger_to_response(trigger)}
        )
    except Exception as e:
        logger.error("Failed to update library trigger", trigger_id=trigger_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to update library trigger", "details": str(e)}
        )


@router.delete("/{library_id}/triggers/{trigger_id}")
async def delete_library_trigger(
    request: Request,
    library_id: str,
    trigger_id: str,
    _: dict = Depends(require_data_write),
):
    """Delete a library trigger."""
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    _, error_response = validate_uuid(trigger_id, "Trigger ID")
    if error_response:
        return error_response
    
    try:
        from api.main import pg_service
        
        # Verify trigger belongs to this library
        existing = await pg_service.get_library_trigger(trigger_id)
        if not existing or str(existing["library_id"]) != library_id:
            return JSONResponse(
                status_code=http_status.HTTP_404_NOT_FOUND,
                content={"error": "Trigger not found"}
            )
        
        deleted = await pg_service.delete_library_trigger(trigger_id)
        if deleted:
            return JSONResponse(
                status_code=http_status.HTTP_200_OK,
                content={"message": "Trigger deleted", "id": trigger_id}
            )
        return JSONResponse(
            status_code=http_status.HTTP_404_NOT_FOUND,
            content={"error": "Trigger not found"}
        )
    except Exception as e:
        logger.error("Failed to delete library trigger", trigger_id=trigger_id, error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to delete library trigger", "details": str(e)}
        )


@router.post("/ensure-personal")
async def ensure_personal_libraries(
    request: Request,
    _: dict = Depends(require_data_write),
):
    """
    Ensure all personal library types exist for the current user.
    
    Creates DOCS, RESEARCH, and TASKS libraries if they don't exist.
    This is called automatically when listing libraries, but can be
    called explicitly to pre-create libraries.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            content={"error": "User ID not found in request"}
        )
    
    try:
        library_service = await get_library_service(request)
        libraries = await library_service.ensure_all_personal_libraries(user_id)
        
        return JSONResponse(
            status_code=http_status.HTTP_200_OK,
            content={
                "data": [library_to_response(lib) for lib in libraries],
                "message": f"Ensured {len(libraries)} personal libraries exist",
            }
        )
        
    except Exception as e:
        logger.error("Failed to ensure personal libraries", error=str(e), exc_info=True)
        return JSONResponse(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Failed to ensure personal libraries", "details": str(e)}
        )
