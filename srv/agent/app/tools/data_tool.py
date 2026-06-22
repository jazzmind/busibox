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
    aggregate: Optional[Dict[str, str]] = Field(
        default=None,
        description="Aggregation specification, e.g., {'count': '*', 'avg': 'salary'}. Supported: count, sum, avg, min, max"
    )
    group_by: Optional[List[str]] = Field(
        default=None,
        description="Fields to group by when aggregating, e.g., ['department', 'status']"
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
    aggregations: Optional[List[Dict[str, Any]]] = Field(default=None, description="Aggregation results when aggregate/group_by used")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class AggregateDataInput(BaseModel):
    """Input for aggregating data records."""
    document_id: str = Field(description="UUID of the data document to query")
    aggregate: Dict[str, str] = Field(
        description="Aggregation functions to apply, e.g., {'count': '*', 'avg': 'salary', 'sum': 'revenue'}. Supported: count, sum, avg, min, max"
    )
    group_by: Optional[List[str]] = Field(
        default=None,
        description="Fields to group results by, e.g., ['department', 'status']. Returns one row per group."
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Filter conditions (same syntax as query_data)"
    )


class AggregateDataOutput(BaseModel):
    """Output from aggregating data records."""
    success: bool = Field(description="Whether aggregation succeeded")
    results: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Aggregation results. Each item has the group_by field values (if grouped) plus aggregation values."
    )
    total_groups: int = Field(default=0, description="Number of groups returned")
    error: Optional[str] = Field(default=None, description="Error message if failed")


class GetFacetsInput(BaseModel):
    """Input for getting distinct values and counts for fields."""
    document_id: str = Field(description="UUID of the data document")
    fields: List[str] = Field(
        description="Field names to get distinct values for, e.g., ['department', 'status', 'employeeType']"
    )
    where: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Optional filter to scope the facet computation"
    )


class GetFacetsOutput(BaseModel):
    """Output from getting facets."""
    success: bool = Field(description="Whether facet retrieval succeeded")
    facets: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="Map of field name to list of {value, count} entries, sorted by count descending"
    )
    total_records: int = Field(default=0, description="Total records in the document (before filtering)")
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
# Filter Sanitization
# =============================================================================

# Operators that expect a single scalar value
_SCALAR_OPS = {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "startswith", "endswith", "regex"}
# Operators that expect a list value
_LIST_OPS = {"in", "nin"}


def _sanitize_where(where: Any) -> Optional[Dict[str, Any]]:
    """
    Sanitize a where clause generated by an LLM.

    Handles common LLM mistakes:
    - Passing a list where a scalar is expected (picks first element or converts to 'in')
    - Passing a scalar where a list is expected (wraps in list)
    - Passing numeric strings for comparison operators
    - Nested and/or with non-list values
    """
    if not isinstance(where, dict):
        return None

    # Logical operators
    for logical_key in ("and", "or"):
        if logical_key in where:
            conditions = where[logical_key]
            if not isinstance(conditions, list):
                conditions = [conditions] if isinstance(conditions, dict) else []
            sanitized = [_sanitize_where(c) for c in conditions]
            sanitized = [c for c in sanitized if c is not None]
            if not sanitized:
                return None
            if len(sanitized) == 1:
                return sanitized[0]
            return {logical_key: sanitized}

    if "not" in where:
        inner = _sanitize_where(where["not"])
        return {"not": inner} if inner else None

    # Field condition
    field = where.get("field")
    if not field or not isinstance(field, str):
        return None

    op = where.get("op", "eq")
    if not isinstance(op, str):
        op = "eq"
    value = where.get("value")

    if op in _SCALAR_OPS:
        if isinstance(value, list):
            if len(value) == 0:
                return None
            if len(value) == 1:
                value = value[0]
            else:
                # LLM meant "in" but used a scalar op — convert
                op = "in"
    elif op in _LIST_OPS:
        if not isinstance(value, list):
            value = [value]

    if op == "isnull":
        if not isinstance(value, bool):
            value = bool(value)

    return {"field": field, "op": op, "value": value}


def _sanitize_order_by(order_by: Any) -> Optional[List[Dict[str, str]]]:
    """
    Sanitize an orderBy clause generated by an LLM.

    Handles common LLM mistakes:
    - Missing 'field' key (e.g., {"terminationDate": "desc"} instead of {"field": "terminationDate", "direction": "desc"})
    - Non-list values
    - Invalid direction values
    """
    if not isinstance(order_by, list):
        if isinstance(order_by, dict):
            order_by = [order_by]
        else:
            return None

    sanitized = []
    for item in order_by:
        if not isinstance(item, dict):
            continue

        if "field" in item and isinstance(item["field"], str):
            direction = item.get("direction", "asc")
            if not isinstance(direction, str) or direction.lower() not in ("asc", "desc"):
                direction = "asc"
            sanitized.append({"field": item["field"], "direction": direction.lower()})
        else:
            # LLM may have passed {"terminationDate": "desc"} or {"column": "terminationDate", "order": "desc"}
            field_name = item.get("column") or item.get("name") or item.get("key")
            direction = item.get("order") or item.get("dir") or item.get("sort") or "asc"

            if not field_name:
                # Try interpreting {fieldName: direction} pattern
                for k, v in item.items():
                    if k not in ("field", "direction", "order", "dir", "sort", "column", "name", "key"):
                        field_name = k
                        if isinstance(v, str) and v.lower() in ("asc", "desc"):
                            direction = v
                        break

            if field_name and isinstance(field_name, str):
                if not isinstance(direction, str) or direction.lower() not in ("asc", "desc"):
                    direction = "asc"
                sanitized.append({"field": field_name, "direction": direction.lower()})

    return sanitized if sanitized else None


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
    aggregate: Optional[Dict[str, str]] = None,
    group_by: Optional[List[str]] = None,
    limit: int = 20,
    offset: int = 0,
) -> QueryDataOutput:
    """
    Query records from a data document with filtering, sorting, and optional aggregation.
    
    Supports SQL-like queries with WHERE, ORDER BY, LIMIT, OFFSET, and GROUP BY.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        select: List of fields to return (default: all)
        where: Filter conditions
        order_by: Sort specification
        aggregate: Aggregation spec, e.g., {"count": "*", "avg": "salary"}
        group_by: Fields to group by when aggregating
        limit: Max records to return (default: 20)
        offset: Pagination offset (default: 0)
    
    Returns:
        QueryDataOutput with matching records and optional aggregations
    
    Example where clause:
        Simple: {"field": "status", "op": "eq", "value": "pending"}
        Complex: {"and": [
            {"field": "status", "op": "eq", "value": "pending"},
            {"field": "priority", "op": "gte", "value": 3}
        ]}
    
    Example aggregation:
        aggregate={"count": "*", "avg": "salary"}, group_by=["department"]
    
    Supported operators: eq, ne, gt, gte, lt, lte, in, nin, contains, startswith, endswith, isnull
    - isnull: {"field": "last_checked", "op": "isnull", "value": true} matches NULL; value: false matches NOT NULL
    Supported aggregations: count, sum, avg, min, max
    """
    try:
        if where:
            where = _sanitize_where(where)
        if order_by:
            order_by = _sanitize_order_by(order_by)

        body: Dict[str, Any] = {
            "select": select,
            "where": where,
            "orderBy": order_by,
            "limit": limit,
            "offset": offset,
        }
        if aggregate:
            body["aggregate"] = aggregate
        if group_by:
            body["groupBy"] = group_by

        response = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json=body,
        )
        
        return QueryDataOutput(
            success=True,
            records=response.get("records", []),
            total=response.get("total", 0),
            limit=response.get("limit", limit),
            offset=response.get("offset", offset),
            aggregations=response.get("aggregations"),
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
        if where:
            where = _sanitize_where(where)

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
        if where:
            where = _sanitize_where(where)

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


async def aggregate_data(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    aggregate: Dict[str, str],
    group_by: Optional[List[str]] = None,
    where: Optional[Dict[str, Any]] = None,
) -> AggregateDataOutput:
    """
    Run aggregation queries on a data document for analytics and summaries.
    
    Use this for counting, summing, averaging, or finding min/max values,
    optionally grouped by one or more fields. More efficient than fetching
    all records and computing manually.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        aggregate: Aggregation functions, e.g., {"count": "*", "avg": "salary", "sum": "revenue"}
        group_by: Fields to group results by (e.g., ["department", "status"])
        where: Optional filter conditions (same syntax as query_data)
    
    Returns:
        AggregateDataOutput with grouped results
    
    Examples:
        Count all records: aggregate={"count": "*"}
        Average salary by department: aggregate={"avg": "salary", "count": "*"}, group_by=["department"]
        Total revenue by status: aggregate={"sum": "revenue"}, group_by=["status"]
    
    Supported aggregation functions: count, sum, avg, min, max
    Use "*" as the field for count to count all records.
    """
    try:
        if where:
            where = _sanitize_where(where)

        body: Dict[str, Any] = {
            "aggregate": aggregate,
            "limit": 1000,
        }
        if group_by:
            body["groupBy"] = group_by
        if where:
            body["where"] = where

        response = await ctx.deps.busibox_client.request(
            method="POST",
            path=f"/data/{document_id}/query",
            json=body,
        )
        
        agg_results = response.get("aggregations", [])
        if not isinstance(agg_results, list):
            agg_results = [agg_results] if agg_results else []

        return AggregateDataOutput(
            success=True,
            results=agg_results,
            total_groups=len(agg_results),
        )
    except Exception as e:
        return AggregateDataOutput(
            success=False,
            error=str(e),
        )


async def get_facets(
    ctx: RunContext[BusiboxDeps],
    document_id: str,
    fields: List[str],
    where: Optional[Dict[str, Any]] = None,
) -> GetFacetsOutput:
    """
    Get distinct values and their counts for specified fields in a data document.
    
    Useful for discovering what filter values exist before constructing queries,
    or for building filter dropdowns and category breakdowns.
    
    Args:
        ctx: RunContext with authenticated client
        document_id: UUID of the data document
        fields: Field names to get facets for (e.g., ["department", "status", "type"])
        where: Optional filter to scope the facet computation
    
    Returns:
        GetFacetsOutput with facets map: {field_name: [{value, count}, ...]}
    
    Example:
        fields=["department", "status"] returns:
        {
            "department": [{"value": "Engineering", "count": 45}, {"value": "Sales", "count": 30}],
            "status": [{"value": "Active", "count": 60}, {"value": "Terminated", "count": 15}]
        }
    """
    try:
        facets: Dict[str, List[Dict[str, Any]]] = {}
        total_records = 0

        if where:
            where = _sanitize_where(where)

        for field_name in fields:
            body: Dict[str, Any] = {
                "aggregate": {"count": "*"},
                "groupBy": [field_name],
                "limit": 1000,
            }
            if where:
                body["where"] = where

            response = await ctx.deps.busibox_client.request(
                method="POST",
                path=f"/data/{document_id}/query",
                json=body,
            )

            agg_results = response.get("aggregations", [])
            if not isinstance(agg_results, list):
                agg_results = [agg_results] if agg_results else []

            facet_values = []
            for row in agg_results:
                val = row.get(field_name)
                if val is not None and val != "":
                    facet_values.append({
                        "value": val,
                        "count": row.get("count", 0),
                    })

            facet_values.sort(key=lambda x: x["count"], reverse=True)
            facets[field_name] = facet_values

            if not total_records:
                total_records = response.get("total", 0)

        return GetFacetsOutput(
            success=True,
            facets=facets,
            total_records=total_records,
        )
    except Exception as e:
        return GetFacetsOutput(
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
    description="""Query records from a data document with filtering, sorting, and optional aggregation.

Supports SQL-like queries with:
- Field selection (select specific fields)
- WHERE clauses with AND/OR/NOT
- Comparison operators (eq, ne, gt, gte, lt, lte, in, nin, contains, startswith, endswith)
- ORDER BY with multiple fields
- LIMIT and OFFSET for pagination
- Aggregation (count, sum, avg, min, max) with GROUP BY

Use this to:
- Find specific records by criteria
- List all records with sorting
- Check if records exist
- Get counts or filtered views
- Run simple aggregations inline with record queries

For complex analytics, prefer aggregate_data. For discovering filter values, prefer get_facets.""",
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


aggregate_data_tool = Tool(
    aggregate_data,
    takes_ctx=True,
    name="aggregate_data",
    description="""Run aggregation queries on a data document for analytics and summaries.

Use this for counting, summing, averaging, or finding min/max values,
optionally grouped by one or more fields. More efficient than fetching
all records and computing manually.

Supports: count, sum, avg, min, max
Use "*" as the field for count to count all records.

Examples:
- Count by status: aggregate={"count": "*"}, group_by=["status"]
- Average salary by department: aggregate={"avg": "salary", "count": "*"}, group_by=["department"]
- Total by category: aggregate={"sum": "amount"}, group_by=["category"]

Combine with where filters to scope the aggregation.""",
)

get_facets_tool = Tool(
    get_facets,
    takes_ctx=True,
    name="get_facets",
    description="""Get distinct values and their counts for specified fields.

Returns all unique values for each requested field along with how many
records have that value. Useful for discovering valid filter values
before constructing queries, or for building category breakdowns.

Example: fields=["department", "status"] returns:
{
    "department": [{"value": "Engineering", "count": 45}, ...],
    "status": [{"value": "Active", "count": 60}, ...]
}

Values are sorted by count (most common first).""",
)


# Convenience list of all data tools for registration
DATA_TOOLS = [
    create_data_document_tool,
    query_data_tool,
    aggregate_data_tool,
    get_facets_tool,
    insert_records_tool,
    update_records_tool,
    delete_records_tool,
    list_data_documents_tool,
    get_data_document_tool,
]
