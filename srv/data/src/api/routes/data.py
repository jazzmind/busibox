"""
Data document API endpoints.

Handles structured data document operations:
- GET /data: List data documents
- POST /data: Create a new data document
- GET /data/{id}: Get data document by ID
- PUT /data/{id}: Update data document metadata
- DELETE /data/{id}: Delete data document
- POST /data/{id}/records: Insert records
- PUT /data/{id}/records: Update records
- DELETE /data/{id}/records: Delete records
- POST /data/{id}/query: Query records
- GET /data/{id}/schema: Get schema
- PUT /data/{id}/schema: Update schema
- POST /data/{id}/embed: Generate embeddings for fields
- GET /data/{id}/cache: Get cache status
- POST /data/{id}/cache: Activate caching
- DELETE /data/{id}/cache: Deactivate caching

All endpoints respect RLS policies for security.
"""

import uuid
from typing import Any, Dict, List, Optional

import structlog
from fastapi import APIRouter, Depends, Request, Query, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette import status as http_status

from api.middleware.jwt_auth import ScopeChecker
from api.services.data_service import DataService
from api.services.query_engine import QueryEngine

logger = structlog.get_logger()

router = APIRouter()

# Scope dependencies
require_data_read = ScopeChecker("data.read")  # Reuse data scopes for now
require_data_write = ScopeChecker("data.write")


# =============================================================================
# Request/Response Models
# =============================================================================

class SchemaFieldDef(BaseModel):
    """Schema field definition."""
    type: str = Field(..., description="Field type: string, integer, number, boolean, array, object, enum, datetime")
    required: bool = Field(default=False, description="Whether field is required")
    values: Optional[List[Any]] = Field(None, description="Allowed values for enum type")
    min: Optional[float] = Field(None, description="Minimum value for numeric types")
    max: Optional[float] = Field(None, description="Maximum value for numeric types")
    items: Optional[Dict] = Field(None, description="Item schema for array type")
    auto: Optional[str] = Field(None, description="Auto-fill: 'now' for datetime, 'uuid' for string")


class DataSchema(BaseModel):
    """Data document schema."""
    fields: Dict[str, SchemaFieldDef] = Field(default_factory=dict, description="Field definitions")
    indexes: List[str] = Field(default_factory=list, description="Fields to index")
    embedFields: List[str] = Field(default_factory=list, description="Fields to generate embeddings for")


class CreateDataDocumentRequest(BaseModel):
    """Request to create a data document."""
    name: str = Field(..., description="Document name")
    schema_def: Optional[Dict] = Field(None, alias="schema", description="Optional schema definition")
    initialRecords: Optional[List[Dict]] = Field(None, description="Optional initial records")
    metadata: Optional[Dict] = Field(None, description="Optional document metadata")
    visibility: str = Field(default="personal", description="Visibility: personal or shared")
    roleIds: Optional[List[str]] = Field(None, description="Role IDs for shared documents")
    libraryId: Optional[str] = Field(None, description="Library to place document in")
    enableCache: bool = Field(default=False, description="Enable Redis caching")
    sourceApp: Optional[str] = Field(None, description="Source app identifier (e.g., 'status-report') for app data libraries")
    
    class Config:
        populate_by_name = True


class UpdateDataDocumentRequest(BaseModel):
    """Request to update a data document."""
    name: Optional[str] = Field(None, description="New document name")
    schema_def: Optional[Dict] = Field(None, alias="schema", description="New schema")
    metadata: Optional[Dict] = Field(None, description="New metadata")
    expectedVersion: Optional[int] = Field(None, description="Expected version for optimistic locking")
    
    class Config:
        populate_by_name = True


class InsertRecordsRequest(BaseModel):
    """Request to insert records."""
    records: List[Dict] = Field(..., description="Records to insert")
    validate_schema: bool = Field(default=True, alias="validate", description="Whether to validate against schema")
    
    class Config:
        populate_by_name = True


class UpdateRecordsRequest(BaseModel):
    """Request to update records."""
    updates: Dict[str, Any] = Field(..., description="Field updates to apply")
    where: Optional[Dict] = Field(None, description="Filter for which records to update")
    validate_schema: bool = Field(default=True, alias="validate", description="Whether to validate against schema")
    
    class Config:
        populate_by_name = True


class DeleteRecordsRequest(BaseModel):
    """Request to delete records."""
    where: Optional[Dict] = Field(None, description="Filter for which records to delete")
    recordIds: Optional[List[str]] = Field(None, description="Specific record IDs to delete")


class QueryRequest(BaseModel):
    """Request to query records."""
    select: Optional[List[str]] = Field(None, description="Fields to select (default: all)")
    where: Optional[Dict] = Field(None, description="Filter conditions")
    orderBy: Optional[List[Dict]] = Field(None, description="Sort specification")
    limit: int = Field(default=100, ge=1, le=1000, description="Max records to return")
    offset: int = Field(default=0, ge=0, description="Pagination offset")
    aggregate: Optional[Dict[str, str]] = Field(None, description="Aggregation specification")
    groupBy: Optional[List[str]] = Field(None, description="Group by fields")
    useJsonbQuery: bool = Field(default=False, description="Use PostgreSQL JSONB query (for large documents)")


class UpdateSchemaRequest(BaseModel):
    """Request to update schema."""
    schema_def: Dict = Field(..., alias="schema", description="New schema definition")
    validateExisting: bool = Field(default=False, description="Validate existing records against new schema")
    
    class Config:
        populate_by_name = True


class EmbedFieldsRequest(BaseModel):
    """Request to generate embeddings for fields."""
    fields: List[str] = Field(..., description="Fields to embed")
    regenerate: bool = Field(default=False, description="Regenerate existing embeddings")


class DataDocumentResponse(BaseModel):
    """Data document response."""
    id: str
    name: str
    ownerId: Optional[str]
    visibility: str
    metadata: Dict
    schema_def: Optional[Dict] = Field(None, alias="schema")
    recordCount: int
    version: int
    modifiedAt: Optional[str]
    libraryId: Optional[str]
    createdAt: Optional[str]
    updatedAt: Optional[str]
    records: Optional[List[Dict]] = None
    
    class Config:
        populate_by_name = True


class QueryResponse(BaseModel):
    """Query response."""
    records: Optional[List[Dict]] = None
    total: int
    limit: int
    offset: int
    aggregations: Optional[Dict] = None


class RecordOperationResponse(BaseModel):
    """Response for record insert/update/delete."""
    success: bool
    count: int
    recordIds: Optional[List[str]] = None
    message: Optional[str] = None


# =============================================================================
# Helper Functions
# =============================================================================

def validate_uuid(id_str: str, field_name: str = "ID") -> uuid.UUID:
    """Validate a string as a UUID."""
    try:
        return uuid.UUID(id_str)
    except ValueError:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid {field_name} format: must be a valid UUID"
        )


async def get_data_service(request: Request) -> DataService:
    """Get data service using the shared database connection pool."""
    from api.main import pg_service, redis_service
    from api.services.cache_manager import CacheManager
    
    # Create cache manager if Redis is available
    cache_manager = None
    if redis_service and redis_service.client:
        # Create flush callback that writes to database
        async def flush_callback(document_id: str, data: Dict):
            async with pg_service.acquire(request) as conn:
                import json
                await conn.execute("""
                    UPDATE data_files
                    SET data_content = $2,
                        data_schema = $3,
                        data_version = $4,
                        data_record_count = $5,
                        data_modified_at = NOW(),
                        updated_at = NOW()
                    WHERE file_id = $1 AND doc_type = 'data'
                """,
                    uuid.UUID(document_id),
                    json.dumps(data.get("records", [])),
                    json.dumps(data.get("schema")) if data.get("schema") else None,
                    data.get("version", 1),
                    len(data.get("records", [])),
                )
        
        cache_manager = CacheManager(redis_service.client, flush_callback=flush_callback)
    
    return DataService(pg_service.pool, cache_manager=cache_manager)


async def get_query_engine() -> QueryEngine:
    """Get query engine instance."""
    return QueryEngine()


# =============================================================================
# Document Endpoints
# =============================================================================

@router.get(
    "",
    summary="List data documents",
    dependencies=[Depends(require_data_read)],
)
async def list_data_documents(
    request: Request,
    libraryId: Optional[str] = Query(None, description="Filter by library"),
    visibility: Optional[str] = Query(None, description="Filter by visibility"),
    sourceApp: Optional[str] = Query(None, description="Filter by source app (e.g., 'status-report')"),
    limit: int = Query(50, ge=1, le=100, description="Max documents to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    data_service: DataService = Depends(get_data_service),
):
    """List data documents accessible to the user."""
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])
    
    logger.debug(
        "[DATA API] GET /data - Listing documents",
        user_id=user_id,
        role_ids=role_ids,
        library_id=libraryId,
        visibility_filter=visibility,
        source_app=sourceApp,
    )
    
    try:
        documents = await data_service.list_documents(
            request,
            library_id=libraryId,
            visibility=visibility,
            source_app=sourceApp,
            limit=limit,
            offset=offset,
        )
        
        logger.info(
            "[DATA API] Listed documents",
            count=len(documents),
            user_id=user_id,
            role_count=len(role_ids) if role_ids else 0,
        )
        
        return {
            "documents": documents,
            "total": len(documents),  # TODO: Get actual total count
            "limit": limit,
            "offset": offset,
        }
    except Exception as e:
        logger.error("[DATA API] Failed to list data documents", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "",
    summary="Create a data document",
    dependencies=[Depends(require_data_write)],
    status_code=http_status.HTTP_201_CREATED,
)
async def create_data_document(
    request: Request,
    body: CreateDataDocumentRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Create a new data document."""
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])
    
    logger.info(
        "[DATA API] POST /data - Creating document",
        name=body.name,
        visibility=body.visibility,
        role_ids=body.roleIds,
        user_id=user_id,
        request_role_ids=role_ids,
        has_schema=body.schema_def is not None,
        initial_record_count=len(body.initialRecords) if body.initialRecords else 0,
        source_app=body.sourceApp,
    )
    
    try:
        document = await data_service.create_document(
            request,
            name=body.name,
            schema=body.schema_def,
            initial_records=body.initialRecords,
            metadata=body.metadata,
            visibility=body.visibility,
            role_ids=body.roleIds,
            library_id=body.libraryId,
            enable_cache=body.enableCache,
            source_app=body.sourceApp,
        )
        
        if document is None:
            logger.error(
                "[DATA API] Document creation returned null - RLS issue",
                name=body.name,
                visibility=body.visibility,
                role_ids=body.roleIds,
                user_id=user_id,
            )
            raise HTTPException(
                status_code=500, 
                detail=f"Document created but could not be retrieved. Visibility '{body.visibility}' requires matching role assignments or owner check."
            )
        
        logger.info(
            "[DATA API] Document created successfully",
            document_id=document.get("id"),
            name=body.name,
        )
        
        return document
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[DATA API] Failed to create data document", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{document_id}",
    summary="Get a data document",
    dependencies=[Depends(require_data_read)],
)
async def get_data_document(
    request: Request,
    document_id: str,
    includeRecords: bool = Query(True, description="Include records in response"),
    data_service: DataService = Depends(get_data_service),
):
    """Get a data document by ID."""
    validate_uuid(document_id, "document_id")
    
    try:
        document = await data_service.get_document(
            request,
            document_id,
            include_records=includeRecords,
        )
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return document
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get data document", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{document_id}",
    summary="Update a data document",
    dependencies=[Depends(require_data_write)],
)
async def update_data_document(
    request: Request,
    document_id: str,
    body: UpdateDataDocumentRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Update a data document's metadata or schema."""
    validate_uuid(document_id, "document_id")
    
    try:
        document = await data_service.update_document(
            request,
            document_id,
            name=body.name,
            schema=body.schema_def,
            metadata=body.metadata,
            expected_version=body.expectedVersion,
        )
        
        return document
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))  # Conflict for version mismatch
    except Exception as e:
        logger.error("Failed to update data document", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{document_id}",
    summary="Delete a data document",
    dependencies=[Depends(require_data_write)],
    status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_data_document(
    request: Request,
    document_id: str,
    data_service: DataService = Depends(get_data_service),
):
    """Delete a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        deleted = await data_service.delete_document(request, document_id)
        
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete data document", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Record Endpoints
# =============================================================================

@router.post(
    "/{document_id}/records",
    summary="Insert records",
    dependencies=[Depends(require_data_write)],
    status_code=http_status.HTTP_201_CREATED,
)
async def insert_records(
    request: Request,
    document_id: str,
    body: InsertRecordsRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Insert records into a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        count, record_ids = await data_service.insert_records(
            request,
            document_id,
            records=body.records,
            validate=body.validate_schema,
        )
        
        return RecordOperationResponse(
            success=True,
            count=count,
            recordIds=record_ids,
            message=f"Inserted {count} records",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to insert records", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{document_id}/records",
    summary="Update records",
    dependencies=[Depends(require_data_write)],
)
async def update_records(
    request: Request,
    document_id: str,
    body: UpdateRecordsRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Update records in a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        count = await data_service.update_records(
            request,
            document_id,
            updates=body.updates,
            where=body.where,
            validate=body.validate_schema,
        )
        
        return RecordOperationResponse(
            success=True,
            count=count,
            message=f"Updated {count} records",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update records", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{document_id}/records",
    summary="Delete records",
    dependencies=[Depends(require_data_write)],
)
async def delete_records(
    request: Request,
    document_id: str,
    body: DeleteRecordsRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Delete records from a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        count = await data_service.delete_records(
            request,
            document_id,
            where=body.where,
            record_ids=body.recordIds,
        )
        
        return RecordOperationResponse(
            success=True,
            count=count,
            message=f"Deleted {count} records",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to delete records", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Query Endpoint
# =============================================================================

@router.post(
    "/{document_id}/query",
    summary="Query records",
    dependencies=[Depends(require_data_read)],
)
async def query_records(
    request: Request,
    document_id: str,
    body: QueryRequest,
    data_service: DataService = Depends(get_data_service),
    query_engine: QueryEngine = Depends(get_query_engine),
):
    """Query records in a data document with filtering, sorting, and aggregation."""
    validate_uuid(document_id, "document_id")
    
    # Validate query
    query = {
        "select": body.select,
        "where": body.where,
        "orderBy": body.orderBy,
        "limit": body.limit,
        "offset": body.offset,
        "aggregate": body.aggregate,
        "groupBy": body.groupBy,
    }
    
    errors = query_engine.validate_query(query)
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})
    
    try:
        # Get document and execute query
        from api.main import pg_service
        from api.middleware.jwt_auth import set_rls_session_vars
        
        async with pg_service.pool.acquire() as conn:
            await set_rls_session_vars(conn, request)
            
            result = await query_engine.execute_query(
                conn,
                document_id,
                query,
                use_jsonb_query=body.useJsonbQuery,
            )
        
        return QueryResponse(
            records=result.get("records"),
            total=result.get("total", 0),
            limit=result.get("limit", body.limit),
            offset=result.get("offset", body.offset),
            aggregations=result.get("aggregations"),
        )
    except Exception as e:
        logger.error("Failed to query records", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Schema Endpoints
# =============================================================================

@router.get(
    "/{document_id}/schema",
    summary="Get schema",
    dependencies=[Depends(require_data_read)],
)
async def get_schema(
    request: Request,
    document_id: str,
    data_service: DataService = Depends(get_data_service),
):
    """Get the schema for a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        schema = await data_service.get_schema(request, document_id)
        
        return {
            "documentId": document_id,
            "schema": schema,
            "hasSchema": schema is not None,
        }
    except Exception as e:
        logger.error("Failed to get schema", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{document_id}/schema",
    summary="Update schema",
    dependencies=[Depends(require_data_write)],
)
async def update_schema(
    request: Request,
    document_id: str,
    body: UpdateSchemaRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Update the schema for a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        document = await data_service.update_schema(
            request,
            document_id,
            schema=body.schema_def,
            validate_existing=body.validateExisting,
        )
        
        return document
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update schema", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Embedding Endpoint
# =============================================================================

@router.post(
    "/{document_id}/embed",
    summary="Generate embeddings",
    dependencies=[Depends(require_data_write)],
)
async def embed_fields(
    request: Request,
    document_id: str,
    body: EmbedFieldsRequest,
):
    """Generate embeddings for specified fields in a data document."""
    validate_uuid(document_id, "document_id")
    
    # TODO: Implement embedding generation
    # This will:
    # 1. Extract text from specified fields
    # 2. Generate embeddings using FastEmbed
    # 3. Store embeddings in Milvus
    # 4. Update document metadata with embedding info
    
    raise HTTPException(
        status_code=501,
        detail="Embedding generation not yet implemented"
    )


# =============================================================================
# Cache Endpoints
# =============================================================================

@router.get(
    "/{document_id}/cache",
    summary="Get cache status",
    dependencies=[Depends(require_data_read)],
)
async def get_cache_status(
    request: Request,
    document_id: str,
    data_service: DataService = Depends(get_data_service),
):
    """Get caching status for a data document."""
    validate_uuid(document_id, "document_id")
    
    try:
        if data_service.cache_manager:
            stats = await data_service.cache_manager.get_document_stats(document_id)
            if stats:
                return stats
        
        return {
            "documentId": document_id,
            "cached": False,
            "message": "Document is not cached" if data_service.cache_manager else "Caching not available",
        }
    except Exception as e:
        logger.error("Failed to get cache status", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/{document_id}/cache",
    summary="Activate caching",
    dependencies=[Depends(require_data_write)],
)
async def activate_cache(
    request: Request,
    document_id: str,
    ttl: int = Query(300, ge=60, le=3600, description="Cache TTL in seconds"),
    data_service: DataService = Depends(get_data_service),
):
    """Activate Redis caching for a data document."""
    validate_uuid(document_id, "document_id")
    
    if not data_service.cache_manager:
        raise HTTPException(status_code=503, detail="Caching service not available")
    
    try:
        # Get document data
        document = await data_service.get_document(request, document_id, include_records=True)
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Cache it
        success = await data_service.cache_manager.cache_document(
            document_id,
            {
                "schema": document.get("schema"),
                "records": document.get("records", []),
                "version": document.get("version", 1),
            },
            ttl=ttl,
        )
        
        if success:
            return {
                "documentId": document_id,
                "cached": True,
                "ttl": ttl,
                "message": "Document cached successfully",
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to cache document")
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to activate cache", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete(
    "/{document_id}/cache",
    summary="Deactivate caching",
    dependencies=[Depends(require_data_write)],
)
async def deactivate_cache(
    request: Request,
    document_id: str,
    flush: bool = Query(True, description="Flush dirty data before deactivating"),
    data_service: DataService = Depends(get_data_service),
):
    """Deactivate Redis caching for a data document."""
    validate_uuid(document_id, "document_id")
    
    if not data_service.cache_manager:
        raise HTTPException(status_code=503, detail="Caching service not available")
    
    try:
        if flush:
            await data_service.cache_manager.flush_document(document_id)
        
        await data_service.cache_manager.invalidate_document(document_id)
        
        return {
            "documentId": document_id,
            "cached": False,
            "message": "Cache deactivated",
        }
    except Exception as e:
        logger.error("Failed to deactivate cache", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
