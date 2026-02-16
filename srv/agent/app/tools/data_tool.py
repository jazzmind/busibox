"""
Data management tools for AI agents.

Provides tools for creating, querying, and managing structured data documents.
These tools enable agents to maintain persistent, queryable data storage similar
to Notion/Coda databases.

Use cases:
- Agents tracking task lists, project data, or research notes
- Workflows storing intermediate results
- Long-running processes maintaining state
- User data collection and organization

All operations respect RLS policies for security.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Tool, RunContext

from app.agents.core import BusiboxDeps


# =============================================================================
# Input/Output Schemas
# =============================================================================

class CreateDataDocumentInput(BaseModel):
    """Input for creating a data document."""
    name: str = Field(description="Name of the data document (like a database name)")
    doc_schema: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional schema definition with field types and validation"
    )
    initial_records: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Optional initial records to insert"
    )
    visibility: str = Field(
        default="personal",
        description="Visibility: 'personal' (user only) or 'shared' (role-based)"
    )


class CreateDataDocumentOutput(BaseModel):
    """Output from creating a data document."""
    success: bool = Field(description="Whether creation succeeded")
    document_id: Optional[str] = Field(description="UUID of the created document")
    name: str = Field(description="Name of the created document")
    record_count: int = Field(default=0, description="Number of records inserted")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class QueryDataInput(BaseModel):
    """Input for querying data records."""
    document_id: str = Field(description="UUID of the data document to query")
    select: Optional[List[str]] = Field(
        default=None,
        description="Fields to return (default: all)"
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Filter conditions using {field, op, value} or {and: [...]} / {or: [...]}"
    )
    order_by: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Sort order, e.g., [{'field': 'name', 'direction': 'asc'}]"
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max records to return (keep low to avoid context overflow)")
    offset: int = Field(default=0, ge=0, description="Pagination offset")


class QueryDataOutput(BaseModel):
    """Output from querying data records."""
    success: bool = Field(description="Whether query succeeded")
    records: List[Dict[str, Any]] = Field(default_factory=list, description="Query results")
    total: int = Field(default=0, description="Total matching records")
    limit: int = Field(description="Limit used")
    offset: int = Field(description="Offset used")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class InsertRecordsInput(BaseModel):
    """Input for inserting records."""
    document_id: str = Field(description="UUID of the data document")
    records: List[Dict[str, Any]] = Field(
        description="Records to insert (each is a dict of field values)"
    )


class InsertRecordsOutput(BaseModel):
    """Output from inserting records."""
    success: bool = Field(description="Whether insertion succeeded")
    count: int = Field(default=0, description="Number of records inserted")
    record_ids: List[str] = Field(default_factory=list, description="IDs of inserted records")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class UpdateRecordsInput(BaseModel):
    """Input for updating records."""
    document_id: str = Field(description="UUID of the data document")
    updates: Dict[str, Any] = Field(
        description="Field updates to apply (e.g., {'status': 'done', 'completed_at': '2024-01-01'})"
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Filter for which records to update (updates all if not provided)"
    )


class UpdateRecordsOutput(BaseModel):
    """Output from updating records."""
    success: bool = Field(description="Whether update succeeded")
    count: int = Field(default=0, description="Number of records updated")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class DeleteRecordsInput(BaseModel):
    """Input for deleting records."""
    document_id: str = Field(description="UUID of the data document")
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Filter for which records to delete"
    )
    record_ids: Optional[List[str]] = Field(
        default=None,
        description="Specific record IDs to delete"
    )


class DeleteRecordsOutput(BaseModel):
    """Output from deleting records."""
    success: bool = Field(description="Whether deletion succeeded")
    count: int = Field(default=0, description="Number of records deleted")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class ListDataDocumentsInput(BaseModel):
    """Input for listing data documents."""
    visibility: Optional[str] = Field(
        default=None,
        description="Filter by visibility: 'personal' or 'shared'"
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max documents to return")


class ListDataDocumentsOutput(BaseModel):
    """Output from listing data documents."""
    success: bool = Field(description="Whether listing succeeded")
    documents: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="List of data documents with metadata"
    )
    total: int = Field(default=0, description="Total documents found")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class GetDocumentInput(BaseModel):
    """Input for getting a single document."""
    document_id: str = Field(description="UUID of the data document")
    include_records: bool = Field(default=True, description="Include records in response")


class GetDocumentOutput(BaseModel):
    """Output from getting a document."""
    success: bool = Field(description="Whether get succeeded")
    document: Optional[Dict[str, Any]] = Field(default=None, description="Document data")
    error: Optional[str] = Field(default=None, description="Error message if failed")


# =============================================================================
# Tool Implementations
# =============================================================================

async def create_data_document(
    ctx: RunContext[BusiboxDeps],
    name: str,
    schema: Optional[Dict[str, Any]] = None,
    initial_records: Optional[List[Dict[str, Any]]] = None,
    visibility: str = "personal",
    source_app: Optional[str] = None,
) -> CreateDataDocumentOutput:
    """
    Create a new structured data document for storing records.
    
    Think of this as creating a new database table or Notion database.
    The document can optionally have a schema that validates records.
    
    Args:
        ctx: RunContext with authenticated client
        name: Name for the data document (like "Tasks", "Research Notes", etc.)
        schema: Optional schema definition for validation
        initial_records: Optional list of initial records to insert
        visibility: "personal" (default, user only) or "shared" (team access)
        source_app: Optional app identifier (e.g. "busibox-projects") for filtering
    
    Returns:
        CreateDataDocumentOutput with document_id if successful
    
    Example schema:
        {
            "fields": {
                "name": {"type": "string", "required": true},
                "status": {"type": "enum", "values": ["pending", "done"]},
                "priority": {"type": "integer", "min": 1, "max": 5}
            }
        }
    """
    try:
        body: Dict[str, Any] = {
            "name": name,
            "schema": schema,
            "initialRecords": initial_records,
            "visibility": visibility,
            "enableCache": False,  # Agents don't need caching by default
        }
        if source_app:
            body["sourceApp"] = source_app
        response = await ctx.deps.busibox_client.request(
            method="POST",
            path="/data",
            json=body,
        )
        
        return CreateDataDocumentOutput(
            success=True,
            document_id=response.get("id"),
            name=response.get("name", name),
            record_count=response.get("recordCount", len(initial_records or [])),
        )
    except Exception as e:
        return CreateDataDocumentOutput(
            success=False,
            document_id=None,
            name=name,
            error=str(e),
        )


async def query_data(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    select: Optional[List[str]] = None,
    where: Optional[Dict[str, Any]] = None,
    order_by: Optional[List[Dict[str, str]]] = None,
    limit: int = 20,
    offset: int = 0,
) -> QueryDataOutput:
    """
    Query records from a data document with filtering and sorting.
    
    Supports SQL-like queries with WHERE, ORDER BY, LIMIT, and OFFSET.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        select: List of fields to return (default: all)
        where: Filter conditions
        order_by: Sort specification
        limit: Max records to return (default: 20)
        offset: Pagination offset (default: 0)
    
    Returns:
        QueryDataOutput with matching records
    
    Example where clause:
        Simple: {"field": "status", "op": "eq", "value": "pending"}
        Complex: {"and": [
            {"field": "status", "op": "eq", "value": "pending"},
            {"field": "priority", "op": "gte", "value": 3}
        ]}
    
    Supported operators: eq, ne, gt, gte, lt, lte, in, nin, contains, startswith, endswith
    """
    try:
        response = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json={
                "select": select,
                "where": where,
                "orderBy": order_by,
                "limit": limit,
                "offset": offset,
            },
        )
        
        return QueryDataOutput(
            success=True,
            records=response.get("records", []),
            total=response.get("total", 0),
            limit=response.get("limit", limit),
            offset=response.get("offset", offset),
        )
    except Exception as e:
        return QueryDataOutput(
            success=False,
            records=[],
            total=0,
            limit=limit,
            offset=offset,
            error=str(e),
        )


async def insert_records(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    records: List[Dict[str, Any]],
) -> InsertRecordsOutput:
    """
    Insert records into a data document.
    
    Each record is a dictionary of field values. Records will be validated
    against the document's schema if one is defined.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        records: List of records to insert
    
    Returns:
        InsertRecordsOutput with count and IDs of inserted records
    
    Example:
        records = [
            {"name": "Review PR", "status": "pending", "priority": 3},
            {"name": "Write docs", "status": "pending", "priority": 2}
        ]
    """
    try:
        response = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/records",
            json={
                "records": records,
                "validate": True,
            },
        )
        
        return InsertRecordsOutput(
            success=True,
            count=response.get("count", 0),
            record_ids=response.get("recordIds", []),
        )
    except Exception as e:
        return InsertRecordsOutput(
            success=False,
            error=str(e),
        )


async def update_records(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    updates: Dict[str, Any],
    where: Optional[Dict[str, Any]] = None,
) -> UpdateRecordsOutput:
    """
    Update records in a data document.
    
    Updates all matching records with the provided field values.
    If no where clause is provided, updates ALL records.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        updates: Field values to update
        where: Filter for which records to update
    
    Returns:
        UpdateRecordsOutput with count of updated records
    
    Example:
        # Mark all pending tasks as done
        updates = {"status": "done", "completed_at": "2024-01-01"}
        where = {"field": "status", "op": "eq", "value": "pending"}
    """
    try:
        response = await ctx.deps.busibox_client.request(
            method="PUT",
            path=f"/data/{document_id}/records",
            json={
                "updates": updates,
                "where": where,
                "validate": True,
            },
        )
        
        return UpdateRecordsOutput(
            success=True,
            count=response.get("count", 0),
        )
    except Exception as e:
        return UpdateRecordsOutput(
            success=False,
            error=str(e),
        )


async def delete_records(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    where: Optional[Dict[str, Any]] = None,
    record_ids: Optional[List[str]] = None,
) -> DeleteRecordsOutput:
    """
    Delete records from a data document.
    
    Can delete by filter (where clause) or by specific record IDs.
    At least one of where or record_ids must be provided.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        where: Filter for which records to delete
        record_ids: Specific record IDs to delete
    
    Returns:
        DeleteRecordsOutput with count of deleted records
    """
    try:
        response = await ctx.deps.busibox_client.request(
            method="DELETE",
            path=f"/data/{document_id}/records",
            json={
                "where": where,
                "recordIds": record_ids,
            },
        )
        
        return DeleteRecordsOutput(
            success=True,
            count=response.get("count", 0),
        )
    except Exception as e:
        return DeleteRecordsOutput(
            success=False,
            error=str(e),
        )


async def list_data_documents(
    ctx: RunContext[BusiboxDeps],
    visibility: Optional[str] = None,
    limit: int = 20,
) -> ListDataDocumentsOutput:
    """
    List available data documents.
    
    Returns data documents accessible to the user with basic metadata.
    
    Args:
        ctx: RunContext with authenticated client
        visibility: Optional filter ("personal" or "shared")
        limit: Max documents to return
    
    Returns:
        ListDataDocumentsOutput with list of document summaries
    """
    try:
        params = {"limit": limit}
        if visibility:
            params["visibility"] = visibility
        
        response = await ctx.deps.busibox_client.request(
            method="GET",
            path="/data",
            params=params,
        )
        
        return ListDataDocumentsOutput(
            success=True,
            documents=response.get("documents", []),
            total=response.get("total", 0),
        )
    except Exception as e:
        return ListDataDocumentsOutput(
            success=False,
            error=str(e),
        )


async def get_data_document(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    include_records: bool = True,
) -> GetDocumentOutput:
    """
    Get a data document by ID.
    
    Retrieves the full document including schema, metadata, and optionally records.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        include_records: Whether to include all records (default: True)
    
    Returns:
        GetDocumentOutput with document data
    """
    try:
        response = await ctx.deps.busibox_client.request(
            method="GET",
            path=f"/data/{document_id}",
            params={"includeRecords": str(include_records).lower()},
        )
        
        return GetDocumentOutput(
            success=True,
            document=response,
        )
    except Exception as e:
        return GetDocumentOutput(
            success=False,
            error=str(e),
        )


# =============================================================================
# Tool Definitions
# =============================================================================

create_data_document_tool = Tool(
    create_data_document,
    takes_ctx=True,
    name="create_data_document",
    description="""Create a new structured data document for storing records.

Use this tool to create a persistent data store - like a database table or Notion database.
Data documents support:
- Optional schema validation
- CRUD operations on records
- Filtering and querying
- Personal or shared visibility

Example use cases:
- Create a "Tasks" document to track work items
- Create a "Research Notes" document to store findings
- Create a "Project Data" document for workflow state

The document persists between conversations and can be queried/updated later.""",
)

query_data_tool = Tool(
    query_data,
    takes_ctx=True,
    name="query_data",
    description="""Query records from a data document with filtering and sorting.

Supports SQL-like queries with:
- Field selection (select specific fields)
- WHERE clauses with AND/OR/NOT
- Comparison operators (eq, ne, gt, gte, lt, lte, in, contains, etc.)
- ORDER BY with multiple fields
- LIMIT and OFFSET for pagination

Use this to:
- Find specific records by criteria
- List all records with sorting
- Check if records exist
- Get counts or filtered views

Example: Find all high-priority pending tasks.""",
)

insert_records_tool = Tool(
    insert_records,
    takes_ctx=True,
    name="insert_records",
    description="""Insert one or more records into a data document.

Records are validated against the document's schema (if defined).
Each record gets a unique ID automatically.

Use this to:
- Add new items to a list
- Log events or findings
- Store workflow results
- Save user inputs

Records can contain any JSON-compatible data.""",
)

update_records_tool = Tool(
    update_records,
    takes_ctx=True,
    name="update_records",
    description="""Update records in a data document.

Applies field updates to all records matching the filter.
If no filter provided, updates ALL records (use with caution).

Use this to:
- Mark tasks as complete
- Update status fields
- Modify multiple records at once
- Add timestamps or notes

Always verify your filter before updating.""",
)

delete_records_tool = Tool(
    delete_records,
    takes_ctx=True,
    name="delete_records",
    description="""Delete records from a data document.

Can delete by filter (where clause) or specific record IDs.
Deleted records cannot be recovered.

Use this to:
- Remove completed items
- Clean up old data
- Delete specific records by ID

Be careful - deletions are permanent.""",
)

list_data_documents_tool = Tool(
    list_data_documents,
    takes_ctx=True,
    name="list_data_documents",
    description="""List available data documents.

Returns summaries of data documents accessible to the user.
Use this to discover existing data stores before creating new ones.

Shows: name, ID, record count, visibility, and timestamps.""",
)

get_data_document_tool = Tool(
    get_data_document,
    takes_ctx=True,
    name="get_data_document",
    description="""Get a data document with its full details.

Retrieves schema, metadata, and optionally all records.
Use this to understand the structure of a data document
or to get all data at once.

Set include_records=False for just metadata/schema.""",
)


# Convenience list of all data tools for registration
DATA_TOOLS = [
    create_data_document_tool,
    query_data_tool,
    insert_records_tool,
    update_records_tool,
    delete_records_tool,
    list_data_documents_tool,
    get_data_document_tool,
]
