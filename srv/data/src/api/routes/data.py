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
- POST /data/index-from-extraction: Index extracted fields into Milvus
- GET /data/{id}/cache: Get cache status
- POST /data/{id}/cache: Activate caching
- DELETE /data/{id}/cache: Deactivate caching

All endpoints respect RLS policies for security.
"""

import json
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
    description: Optional[str] = Field(None, description="Human-readable field description")
    values: Optional[List[Any]] = Field(None, description="Allowed values for enum type")
    min: Optional[float] = Field(None, description="Minimum value for numeric types")
    max: Optional[float] = Field(None, description="Maximum value for numeric types")
    items: Optional[Dict] = Field(None, description="Item schema for array type")
    auto: Optional[str] = Field(None, description="Auto-fill: 'now' for datetime, 'uuid' for string")
    search: Optional[List[str]] = Field(None, description="Search/indexing modes: keyword, embed, graph")


class GraphRelationshipDef(BaseModel):
    """Defines how records map to graph relationships."""
    source_label: str = Field(..., description="Label for source node (e.g., 'Task')")
    target_field: str = Field(..., description="Field containing target node ID (e.g., 'projectId')")
    target_label: str = Field(..., description="Label for target node (e.g., 'Project')")
    relationship: str = Field(..., description="Relationship type (e.g., 'BELONGS_TO')")


class DataSchema(BaseModel):
    """Data document schema."""
    fields: Dict[str, SchemaFieldDef] = Field(default_factory=dict, description="Field definitions")
    indexes: List[str] = Field(default_factory=list, description="Fields to index")
    embedFields: List[str] = Field(default_factory=list, description="Fields to generate embeddings for")
    graphNode: Optional[str] = Field(None, description="Graph node label for records (e.g., 'Task'). If set, records are auto-synced to graph DB.")
    graphRelationships: Optional[List[GraphRelationshipDef]] = Field(None, description="Graph relationship definitions mapping record fields to graph edges")


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
    sourceApp: Optional[str] = Field(None, description="Source app identifier (e.g., 'busibox-projects') for app data libraries")
    
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


class UpdateDocumentRolesRequest(BaseModel):
    """Request to replace role assignments for a data document."""
    roleIds: List[str] = Field(default_factory=list, description="Role IDs assigned to the document")
    visibility: Optional[str] = Field(None, description="Optional visibility override: personal or shared")


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


async def _sync_graph(request: Request, document_id: str, document_name: str, schema: Optional[Dict], records: list, visibility: str = "personal"):
    """
    Sync data document records to graph database (best-effort, non-blocking).
    
    Only syncs if schema has graphNode defined and Neo4j is available.
    Failures are logged but never block the API response.
    """
    try:
        graph_service = getattr(request.app.state, "graph_service", None)
        if not graph_service or not graph_service.available:
            return
        
        if not schema or not schema.get("graphNode"):
            return
        
        owner_id = getattr(request.state, "user_id", "")
        await graph_service.sync_data_document_records(
            document_id=document_id,
            document_name=document_name,
            schema=schema,
            records=records,
            owner_id=owner_id,
            visibility=visibility,
        )
    except Exception as e:
        logger.warning(
            "[DATA API] Graph sync failed (non-blocking)",
            document_id=document_id,
            error=str(e),
        )


async def _delete_graph(request: Request, document_id: str):
    """Delete graph data for a document (best-effort, non-blocking)."""
    try:
        graph_service = getattr(request.app.state, "graph_service", None)
        if not graph_service or not graph_service.available:
            return
        await graph_service.delete_document_graph(document_id)
    except Exception as e:
        logger.warning(
            "[DATA API] Graph delete failed (non-blocking)",
            document_id=document_id,
            error=str(e),
        )


async def _delete_graph_nodes(request: Request, record_ids: List[str]):
    """Delete graph nodes for specific record IDs (best-effort, non-blocking)."""
    if not record_ids:
        return
    try:
        graph_service = getattr(request.app.state, "graph_service", None)
        if not graph_service or not graph_service.available:
            return
        for node_id in record_ids:
            try:
                await graph_service.delete_node(node_id)
            except Exception as node_err:
                logger.warning(
                    "[DATA API] Graph node delete failed (non-blocking)",
                    node_id=node_id,
                    error=str(node_err),
                )
    except Exception as e:
        logger.warning(
            "[DATA API] Graph nodes delete failed (non-blocking)",
            record_count=len(record_ids),
            error=str(e),
        )


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
    sourceApp: Optional[str] = Query(None, description="Filter by source app (e.g., 'busibox-projects')"),
    metadataType: Optional[str] = Query(None, alias="type", description="Filter by metadata type (e.g., 'extraction_schema')"),
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
        metadata_type=metadataType,
    )
    
    try:
        documents = await data_service.list_documents(
            request,
            library_id=libraryId,
            visibility=visibility,
            source_app=sourceApp,
            metadata_type=metadataType,
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
        
        # Sync to graph database (non-blocking)
        await _sync_graph(
            request,
            document_id=document.get("id", ""),
            document_name=body.name,
            schema=body.schema_def,
            records=body.initialRecords or [],
            visibility=body.visibility,
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
        
        # Clean up graph data (non-blocking)
        await _delete_graph(request, document_id)
        
        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to delete data document", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/{document_id}/roles",
    summary="Get document role assignments",
    dependencies=[Depends(require_data_read)],
)
async def get_data_document_roles(
    request: Request,
    document_id: str,
    data_service: DataService = Depends(get_data_service),
):
    """Get role assignments for a data document."""
    validate_uuid(document_id, "document_id")

    try:
        roles = await data_service.get_document_roles(request, document_id)
        document = await data_service.get_document(request, document_id, include_records=False)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return {
            "documentId": document_id,
            "visibility": document.get("visibility", "personal"),
            "roles": roles,
            "roleIds": [role["role_id"] for role in roles],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get document roles", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put(
    "/{document_id}/roles",
    summary="Replace document role assignments",
    dependencies=[Depends(require_data_write)],
)
async def update_data_document_roles(
    request: Request,
    document_id: str,
    body: UpdateDocumentRolesRequest,
    data_service: DataService = Depends(get_data_service),
):
    """Replace role assignments for a data document and optionally update visibility."""
    validate_uuid(document_id, "document_id")

    try:
        result = await data_service.set_document_roles(
            request,
            document_id=document_id,
            role_ids=body.roleIds,
            visibility=body.visibility,
        )
        return result
    except PermissionError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to update document roles", document_id=document_id, error=str(e))
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
        
        # Sync new records to graph (non-blocking, re-reads full doc for schema)
        try:
            doc = await data_service.get_document(request, document_id, include_records=True)
            if doc:
                await _sync_graph(
                    request,
                    document_id=document_id,
                    document_name=doc.get("name", ""),
                    schema=doc.get("schema"),
                    records=doc.get("records", []),
                    visibility=doc.get("visibility", "personal"),
                )
        except Exception as graph_err:
            logger.warning("[DATA API] Graph sync after insert failed", error=str(graph_err))
        
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
        
        # Sync updated records to graph (non-blocking, re-reads full doc for schema)
        try:
            doc = await data_service.get_document(request, document_id, include_records=True)
            if doc:
                await _sync_graph(
                    request,
                    document_id=document_id,
                    document_name=doc.get("name", ""),
                    schema=doc.get("schema"),
                    records=doc.get("records", []),
                    visibility=doc.get("visibility", "personal"),
                )
        except Exception as graph_err:
            logger.warning("[DATA API] Graph sync after update failed", error=str(graph_err))
        
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
        count, deleted_ids = await data_service.delete_records(
            request,
            document_id,
            where=body.where,
            record_ids=body.recordIds,
        )
        
        # Delete graph nodes for removed records (non-blocking)
        if deleted_ids:
            try:
                await _delete_graph_nodes(request, deleted_ids)
            except Exception as graph_err:
                logger.warning("[DATA API] Graph node delete after record delete failed", error=str(graph_err))
        
        # Sync remaining records to graph (non-blocking, re-reads full doc for schema)
        try:
            doc = await data_service.get_document(request, document_id, include_records=True)
            if doc:
                await _sync_graph(
                    request,
                    document_id=document_id,
                    document_name=doc.get("name", ""),
                    schema=doc.get("schema"),
                    records=doc.get("records", []),
                    visibility=doc.get("visibility", "personal"),
                )
        except Exception as graph_err:
            logger.warning("[DATA API] Graph sync after delete failed", error=str(graph_err))
        
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

    from services.embedding_client import EmbeddingClient
    from shared.config import Config

    config = Config().to_dict()
    embedding_client = EmbeddingClient(config)

    data_service: DataService = await get_data_service(request)
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])

    doc = data_service.get(document_id, user_id=user_id, role_ids=role_ids, include_records=True)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    records = doc.get("records", [])
    if not records:
        return {"indexed": 0, "message": "No records to embed"}

    texts: List[str] = []
    for record in records:
        for field_name in body.fields:
            val = record.get("data", {}).get(field_name)
            if val is not None:
                text = ", ".join(val) if isinstance(val, list) else str(val)
                if text.strip():
                    texts.append(text)

    if not texts:
        return {"indexed": 0, "message": "No non-empty field values to embed"}

    embeddings = await embedding_client.embed_chunks(texts)
    embedding_client.close()

    return {
        "indexed": len(embeddings),
        "dimension": config.get("embedding_dimension", 1024),
    }


# =============================================================================
# Field Indexing from Extraction
# =============================================================================

def _field_value_to_text(value: Any) -> str:
    """Convert a field value to a text representation for indexing."""
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, default=str)
    return str(value)


@router.post(
    "/index-from-extraction",
    summary="Index extracted fields into Milvus",
    dependencies=[Depends(require_data_write)],
)
async def index_from_extraction(
    request: Request,
    file_id: str = Query(..., description="Source document file_id"),
    schema_document_id: str = Query(..., description="Schema data-document ID"),
):
    """
    Index extracted field values into Milvus based on schema search tags.

    Fields tagged with ``keyword`` get BM25 indexing, ``embed`` get semantic
    vector indexing. Fields tagged ``graph`` are handled by the graph endpoint.
    """
    validate_uuid(schema_document_id, "schema_document_id")

    from services.milvus_service import MilvusService, MilvusConnectionError
    from services.embedding_client import EmbeddingClient
    from shared.config import Config

    config = Config().to_dict()
    data_service: DataService = await get_data_service(request)
    user_id = getattr(request.state, "user_id", None)
    role_ids = getattr(request.state, "role_ids", [])

    schema_doc = data_service.get(
        schema_document_id, user_id=user_id, role_ids=role_ids, include_records=False,
    )
    if not schema_doc:
        raise HTTPException(status_code=404, detail="Schema document not found")

    schema = schema_doc.get("schema") or schema_doc.get("data_schema") or {}
    fields_def = schema.get("fields", {})
    if not fields_def:
        raise HTTPException(status_code=400, detail="Schema has no field definitions")

    keyword_fields: List[str] = []
    embed_fields_list: List[str] = []

    for fname, fdef in fields_def.items():
        if not isinstance(fdef, dict):
            continue
        search_tags = fdef.get("search") or []
        # Accept legacy "index" as alias for "keyword"
        normalised = [("keyword" if t == "index" else t) for t in search_tags]
        if "keyword" in normalised:
            keyword_fields.append(fname)
        if "embed" in normalised:
            embed_fields_list.append(fname)

    if not keyword_fields and not embed_fields_list:
        return {"indexed_count": 0, "message": "No fields have keyword or embed search tags"}

    # Load extraction records for this file_id (stored in the schema doc records)
    records_doc = data_service.get(
        schema_document_id, user_id=user_id, role_ids=role_ids, include_records=True,
    )
    all_records = records_doc.get("records", []) if records_doc else []

    file_records = [
        r for r in all_records
        if r.get("data", {}).get("_file_id") == file_id
        or r.get("metadata", {}).get("file_id") == file_id
        or r.get("_sourceFileId") == file_id
    ]

    if not file_records:
        return {"indexed_count": 0, "message": "No extraction records found for this file"}

    # Build Milvus entries
    embedding_client: Optional[EmbeddingClient] = None
    embed_dim = config.get("embedding_dimension", 1024)

    texts_to_embed: List[str] = []
    entries_needing_embed: List[int] = []

    field_entries: List[Dict] = []

    for record_idx, record in enumerate(file_records):
        record_data = record.get("data", record)
        all_tagged = set(keyword_fields) | set(embed_fields_list)

        for fname in all_tagged:
            value = record_data.get(fname)
            text = _field_value_to_text(value)
            if not text.strip():
                continue

            entry: Dict[str, Any] = {
                "id": f"{file_id}-field-{fname}-{record_idx}",
                "text": text[:65000],
                "text_dense": [0.0] * embed_dim,
                "metadata": {
                    "field_name": fname,
                    "schema_document_id": schema_document_id,
                    "record_index": record_idx,
                },
            }

            if fname in embed_fields_list:
                texts_to_embed.append(text[:8000])
                entries_needing_embed.append(len(field_entries))

            field_entries.append(entry)

    if not field_entries:
        return {"indexed_count": 0, "message": "All field values are empty"}

    # Generate embeddings for embed-tagged fields
    if texts_to_embed:
        embedding_client = EmbeddingClient(config)
        try:
            embeddings = await embedding_client.embed_chunks(texts_to_embed)
            for i, embed_idx in enumerate(entries_needing_embed):
                if i < len(embeddings):
                    field_entries[embed_idx]["text_dense"] = embeddings[i]
        finally:
            embedding_client.close()

    # Determine visibility from the file metadata
    from api.main import pg_service
    conn = pg_service.pool.getconn()
    visibility = "personal"
    file_role_ids: List[str] = []
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT visibility, role_ids FROM data_files WHERE file_id = %s",
            (file_id,),
        )
        row = cur.fetchone()
        if row:
            visibility = row[0] or "personal"
            file_role_ids = row[1] or []
    finally:
        pg_service.pool.putconn(conn)

    # Insert into Milvus
    milvus = MilvusService(config)
    try:
        milvus.connect()
        milvus.delete_extracted_fields(file_id)
        inserted = milvus.insert_extracted_fields(
            file_id=file_id,
            user_id=user_id,
            field_entries=field_entries,
            visibility=visibility,
            role_ids=file_role_ids if visibility == "shared" else None,
        )
    except MilvusConnectionError as e:
        logger.warning("Milvus unavailable for field indexing", error=str(e))
        return {"indexed_count": 0, "error": "Milvus unavailable"}
    finally:
        milvus.close()

    logger.info(
        "Field indexing complete",
        file_id=file_id,
        keyword_fields=keyword_fields,
        embed_fields=embed_fields_list,
        total_entries=len(field_entries),
        inserted=inserted,
    )

    return {
        "indexed_count": len(field_entries),
        "keyword_fields": keyword_fields,
        "embed_fields": embed_fields_list,
    }


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


@router.post(
    "/{document_id}/graph-sync",
    summary="Re-sync document records to graph database",
    dependencies=[Depends(require_data_write)],
)
async def sync_document_to_graph(
    request: Request,
    document_id: str,
    data_service: DataService = Depends(get_data_service),
):
    """
    Re-sync all records in a data document to the graph database (Neo4j).
    
    This reads all records from the document and pushes them to the graph,
    creating/updating nodes and relationships based on the document's schema
    (graphNode and graphRelationships fields).
    
    Useful for:
    - Populating the graph with pre-existing records
    - Recovering from graph database issues
    - Re-syncing after schema changes
    """
    validate_uuid(document_id, "document_id")
    
    graph_service = getattr(request.app.state, "graph_service", None)
    if not graph_service or not graph_service.available:
        raise HTTPException(
            status_code=503,
            detail="Graph database not available. Ensure Neo4j is running and NEO4J_URI is configured.",
        )
    
    try:
        # Get the document with records
        document = await data_service.get_document(
            request,
            document_id,
            include_records=True,
        )
        
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        
        schema = document.get("schema")
        if not schema or not schema.get("graphNode"):
            raise HTTPException(
                status_code=400,
                detail="Document schema does not have graphNode defined. Graph sync requires a graphNode label in the schema.",
            )
        
        records = document.get("records", [])
        document_name = document.get("name", "")
        visibility = document.get("visibility", "personal")
        owner_id = getattr(request.state, "user_id", "")
        
        # Sync to graph
        count = await graph_service.sync_data_document_records(
            document_id=document_id,
            document_name=document_name,
            schema=schema,
            records=records,
            owner_id=owner_id,
            visibility=visibility,
        )
        
        logger.info(
            "[DATA API] Graph sync completed",
            document_id=document_id,
            document_name=document_name,
            graph_node=schema.get("graphNode"),
            record_count=len(records),
            synced_count=count,
        )
        
        return {
            "documentId": document_id,
            "documentName": document_name,
            "graphNode": schema.get("graphNode"),
            "recordCount": len(records),
            "syncedCount": count,
            "message": f"Synced {count} records to graph database",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to sync graph", document_id=document_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Default extraction schemas
# =============================================================================

DEFAULT_EXTRACTION_SCHEMAS = [
    {
        "name": "General Entity Extraction",
        "metadata": {
            "type": "extraction_schema",
            "builtin": True,
            "description": "Extract common entity types from any document for knowledge graph population.",
        },
        "schema": {
            "displayName": "General Entities",
            "itemLabel": "Entity Set",
            "fields": {
                "people": {
                    "type": "array",
                    "description": "People mentioned in the document (names of individuals)",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 1,
                },
                "organizations": {
                    "type": "array",
                    "description": "Organizations, companies, or institutions mentioned",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 2,
                },
                "technologies": {
                    "type": "array",
                    "description": "Technologies, tools, frameworks, or platforms mentioned",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 3,
                },
                "locations": {
                    "type": "array",
                    "description": "Geographic locations, cities, countries, or regions",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 4,
                },
                "keywords": {
                    "type": "array",
                    "description": "Key topics, tags, or subject matter keywords",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 5,
                },
                "concepts": {
                    "type": "array",
                    "description": "Abstract concepts, themes, or ideas discussed",
                    "search": ["embed", "graph"],
                    "items": {"type": "string"},
                    "display_order": 6,
                },
            },
        },
    },
    {
        "name": "People & Organizations",
        "metadata": {
            "type": "extraction_schema",
            "builtin": True,
            "description": "Focused extraction of people and organizations with context.",
        },
        "schema": {
            "displayName": "People & Organizations",
            "itemLabel": "Entity",
            "fields": {
                "person": {
                    "type": "string",
                    "description": "Name of a person mentioned",
                    "search": ["keyword", "graph"],
                    "display_order": 1,
                },
                "role": {
                    "type": "string",
                    "description": "Role or title of the person",
                    "search": ["keyword"],
                    "display_order": 2,
                },
                "organization": {
                    "type": "string",
                    "description": "Organization the person is associated with",
                    "search": ["keyword", "graph"],
                    "display_order": 3,
                },
                "context": {
                    "type": "string",
                    "description": "Context or relevance of the person/organization in the document",
                    "search": ["embed"],
                    "display_order": 4,
                },
                "keywords": {
                    "type": "array",
                    "description": "Related keywords or topics",
                    "search": ["keyword", "graph"],
                    "items": {"type": "string"},
                    "display_order": 5,
                },
            },
        },
    },
]


@router.post(
    "/seed-default-schemas",
    summary="Seed default extraction schemas",
    dependencies=[Depends(require_data_write)],
)
async def seed_default_schemas(
    request: Request,
    data_service: DataService = Depends(get_data_service),
):
    """
    Create built-in extraction schemas if they don't already exist.

    Idempotent: skips schemas that already exist (matched by name + builtin flag).
    """
    from api.main import pg_service

    created = []
    skipped = []

    for schema_def in DEFAULT_EXTRACTION_SCHEMAS:
        name = schema_def["name"]

        # Check if a schema with this name and builtin flag already exists
        async with pg_service.acquire(request) as conn:
            existing = await conn.fetchrow(
                "SELECT file_id FROM data_files "
                "WHERE filename = $1 AND doc_type = 'data' "
                "AND metadata->>'builtin' = 'true' "
                "AND metadata->>'type' = 'extraction_schema'",
                name,
            )

        if existing:
            skipped.append(name)
            continue

        try:
            result = await data_service.create_document(
                request=request,
                name=name,
                schema=schema_def["schema"],
                metadata=schema_def["metadata"],
                visibility="personal",
            )
            created.append({"name": name, "id": result.get("id", "")})
        except Exception as e:
            logger.error(f"Failed to seed schema '{name}'", error=str(e))

    return {
        "created": created,
        "skipped": skipped,
        "total": len(DEFAULT_EXTRACTION_SCHEMAS),
    }
