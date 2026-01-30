"""
Query Engine for structured data documents.

Provides SQL-like query capabilities that translate to PostgreSQL JSONB queries.
Supports:
- Field selection (SELECT)
- Filtering (WHERE with AND/OR/NOT)
- Sorting (ORDER BY)
- Pagination (LIMIT/OFFSET)
- Aggregations (COUNT, SUM, AVG, MIN, MAX)
- Grouping (GROUP BY)

Query Format:
{
  "select": ["field1", "field2"],  // Optional, defaults to all
  "where": {
    "and": [
      {"field": "status", "op": "eq", "value": "active"},
      {"field": "priority", "op": "gte", "value": 3}
    ]
  },
  "orderBy": [{"field": "created_at", "direction": "desc"}],
  "limit": 50,
  "offset": 0,
  "aggregate": {
    "count": "*",
    "sum": "amount",
    "avg": "rating"
  },
  "groupBy": ["status"]
}

Supported operators:
- eq, ne: Equal, not equal
- gt, gte, lt, lte: Comparison
- in, nin: In list, not in list
- contains: String/array contains
- startswith, endswith: String prefix/suffix
- isnull: Check null
- regex: Regular expression match
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple, Union

import asyncpg
import structlog

logger = structlog.get_logger()


class QueryEngine:
    """
    SQL-like query engine for structured data documents.
    
    Translates JSON query objects into either:
    1. In-memory filtering (for cached documents or small datasets)
    2. PostgreSQL JSONB queries (for large datasets stored in DB)
    """
    
    # Supported comparison operators
    OPERATORS = {
        "eq": "=",
        "ne": "!=",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
        "in": "IN",
        "nin": "NOT IN",
        "contains": "LIKE",
        "startswith": "LIKE",
        "endswith": "LIKE",
        "isnull": "IS NULL",
        "regex": "~",
    }
    
    # Supported aggregation functions
    AGGREGATIONS = {"count", "sum", "avg", "min", "max"}
    
    def __init__(self):
        """Initialize the query engine."""
        pass
    
    # ========================================================================
    # In-Memory Query Execution
    # ========================================================================
    
    def execute_in_memory(
        self,
        records: List[Dict],
        query: Dict,
    ) -> Dict:
        """
        Execute a query against in-memory records.
        
        Args:
            records: List of record dicts
            query: Query specification
            
        Returns:
            Query result with records and/or aggregations
        """
        # Filter
        where = query.get("where")
        if where:
            records = [r for r in records if self._record_matches(r, where)]
        
        # Count before pagination for total
        total_count = len(records)
        
        # Aggregations (before field selection)
        aggregations = {}
        aggregate_spec = query.get("aggregate")
        group_by = query.get("groupBy")
        
        if aggregate_spec:
            if group_by:
                aggregations = self._compute_grouped_aggregations(records, aggregate_spec, group_by)
            else:
                aggregations = self._compute_aggregations(records, aggregate_spec)
        
        # If only aggregating, return early
        if aggregate_spec and not query.get("select"):
            return {
                "aggregations": aggregations,
                "total": total_count,
            }
        
        # Sort
        order_by = query.get("orderBy")
        if order_by:
            records = self._sort_records(records, order_by)
        
        # Pagination
        offset = query.get("offset", 0)
        limit = query.get("limit", 100)
        records = records[offset:offset + limit]
        
        # Field selection
        select = query.get("select")
        if select:
            records = [self._select_fields(r, select) for r in records]
        
        result = {
            "records": records,
            "total": total_count,
            "limit": limit,
            "offset": offset,
        }
        
        if aggregations:
            result["aggregations"] = aggregations
        
        return result
    
    def _record_matches(self, record: Dict, where: Dict) -> bool:
        """Check if a record matches a where clause."""
        # Logical AND
        if "and" in where:
            return all(self._record_matches(record, cond) for cond in where["and"])
        
        # Logical OR
        if "or" in where:
            return any(self._record_matches(record, cond) for cond in where["or"])
        
        # Logical NOT
        if "not" in where:
            return not self._record_matches(record, where["not"])
        
        # Field condition
        field = where.get("field")
        op = where.get("op", "eq")
        value = where.get("value")
        
        if not field:
            return True
        
        # Handle nested field access with dot notation
        record_value = self._get_nested_value(record, field)
        
        return self._compare(record_value, op, value)
    
    def _compare(self, record_value: Any, op: str, value: Any) -> bool:
        """Compare a record value against an operator and value."""
        if op == "eq":
            return record_value == value
        elif op == "ne":
            return record_value != value
        elif op == "gt":
            return record_value is not None and record_value > value
        elif op == "gte":
            return record_value is not None and record_value >= value
        elif op == "lt":
            return record_value is not None and record_value < value
        elif op == "lte":
            return record_value is not None and record_value <= value
        elif op == "in":
            return record_value in (value if isinstance(value, list) else [value])
        elif op == "nin":
            return record_value not in (value if isinstance(value, list) else [value])
        elif op == "contains":
            if isinstance(record_value, str):
                return value in record_value
            elif isinstance(record_value, list):
                return value in record_value
            return False
        elif op == "startswith":
            return isinstance(record_value, str) and record_value.startswith(value)
        elif op == "endswith":
            return isinstance(record_value, str) and record_value.endswith(value)
        elif op == "isnull":
            return (record_value is None) == value
        elif op == "regex":
            return isinstance(record_value, str) and bool(re.search(value, record_value))
        
        return False
    
    def _get_nested_value(self, record: Dict, field: str) -> Any:
        """Get a value from a record, supporting dot notation for nested fields."""
        parts = field.split(".")
        value = record
        for part in parts:
            if isinstance(value, dict):
                value = value.get(part)
            elif isinstance(value, list) and part.isdigit():
                idx = int(part)
                value = value[idx] if 0 <= idx < len(value) else None
            else:
                return None
        return value
    
    def _sort_records(self, records: List[Dict], order_by: List[Dict]) -> List[Dict]:
        """Sort records by multiple fields."""
        def sort_key(record):
            key = []
            for spec in order_by:
                field = spec.get("field")
                direction = spec.get("direction", "asc").lower()
                value = self._get_nested_value(record, field)
                
                # Handle None values (sort them last)
                if value is None:
                    value = (1, None)  # Tuple to sort after real values
                else:
                    value = (0, value)
                
                # Reverse for descending
                if direction == "desc":
                    # For descending, we need to negate or reverse
                    # This is tricky for mixed types, so we use a wrapper
                    key.append(SortKey(value, reverse=True))
                else:
                    key.append(SortKey(value, reverse=False))
            
            return key
        
        return sorted(records, key=sort_key)
    
    def _select_fields(self, record: Dict, fields: List[str]) -> Dict:
        """Select specific fields from a record."""
        result = {}
        for field in fields:
            value = self._get_nested_value(record, field)
            # Handle nested field names - put in flat structure
            result[field] = value
        return result
    
    def _compute_aggregations(self, records: List[Dict], aggregate_spec: Dict) -> Dict:
        """Compute aggregations over records."""
        result = {}
        
        for agg_name, field in aggregate_spec.items():
            agg_type = agg_name.lower()
            
            if agg_type == "count":
                if field == "*":
                    result["count"] = len(records)
                else:
                    # Count non-null values
                    result[f"count_{field}"] = sum(
                        1 for r in records if self._get_nested_value(r, field) is not None
                    )
            
            elif agg_type in ("sum", "avg", "min", "max"):
                values = [
                    self._get_nested_value(r, field)
                    for r in records
                    if self._get_nested_value(r, field) is not None
                    and isinstance(self._get_nested_value(r, field), (int, float))
                ]
                
                if not values:
                    result[f"{agg_type}_{field}"] = None
                elif agg_type == "sum":
                    result[f"sum_{field}"] = sum(values)
                elif agg_type == "avg":
                    result[f"avg_{field}"] = sum(values) / len(values)
                elif agg_type == "min":
                    result[f"min_{field}"] = min(values)
                elif agg_type == "max":
                    result[f"max_{field}"] = max(values)
        
        return result
    
    def _compute_grouped_aggregations(
        self,
        records: List[Dict],
        aggregate_spec: Dict,
        group_by: List[str],
    ) -> List[Dict]:
        """Compute aggregations grouped by fields."""
        # Group records
        groups = {}
        for record in records:
            key = tuple(self._get_nested_value(record, f) for f in group_by)
            if key not in groups:
                groups[key] = []
            groups[key].append(record)
        
        # Compute aggregations for each group
        results = []
        for key, group_records in groups.items():
            row = {}
            # Add group key fields
            for i, field in enumerate(group_by):
                row[field] = key[i]
            # Add aggregations
            aggs = self._compute_aggregations(group_records, aggregate_spec)
            row.update(aggs)
            results.append(row)
        
        return results
    
    # ========================================================================
    # PostgreSQL JSONB Query Generation
    # ========================================================================
    
    def build_jsonb_query(
        self,
        document_id: str,
        query: Dict,
    ) -> Tuple[str, List[Any]]:
        """
        Build a PostgreSQL query for JSONB data.
        
        This generates a query that operates directly on the JSONB column,
        useful for large documents where in-memory filtering would be slow.
        
        Args:
            document_id: UUID of the data document
            query: Query specification
            
        Returns:
            Tuple of (SQL query string, parameters list)
        """
        params = [document_id]
        param_idx = 2
        
        # Base query using jsonb_array_elements to unnest the array
        sql = """
        WITH doc AS (
            SELECT data_content, data_schema
            FROM data_files
            WHERE file_id = $1 AND doc_type = 'data'
        ),
        records AS (
            SELECT 
                elem.value as record,
                elem.ordinality as row_num
            FROM doc, 
            LATERAL jsonb_array_elements(doc.data_content) WITH ORDINALITY AS elem(value, ordinality)
        """
        
        # Add WHERE clause
        where = query.get("where")
        if where:
            where_sql, params, param_idx = self._build_where_clause(where, params, param_idx)
            sql += f"\n            WHERE {where_sql}"
        
        sql += "\n        )"
        
        # Check if we need aggregations
        aggregate_spec = query.get("aggregate")
        group_by = query.get("groupBy")
        
        if aggregate_spec and not query.get("select"):
            # Aggregation-only query
            if group_by:
                sql += self._build_grouped_aggregation_query(aggregate_spec, group_by, params, param_idx)
            else:
                sql += self._build_aggregation_query(aggregate_spec)
        else:
            # Regular query with optional aggregations
            select_fields = query.get("select", ["*"])
            sql += "\n        SELECT "
            
            if "*" in select_fields or not select_fields:
                sql += "record"
            else:
                field_selects = []
                for field in select_fields:
                    field_selects.append(f"record->>'{field}' as \"{field}\"")
                sql += ", ".join(field_selects)
            
            sql += "\n        FROM records"
            
            # ORDER BY
            order_by = query.get("orderBy")
            if order_by:
                order_clauses = []
                for spec in order_by:
                    field = spec.get("field")
                    direction = spec.get("direction", "asc").upper()
                    # Use JSONB extraction for sorting
                    order_clauses.append(f"(record->>'{field}') {direction} NULLS LAST")
                sql += f"\n        ORDER BY {', '.join(order_clauses)}"
            
            # LIMIT and OFFSET
            limit = query.get("limit", 100)
            offset = query.get("offset", 0)
            sql += f"\n        LIMIT ${param_idx} OFFSET ${param_idx + 1}"
            params.extend([limit, offset])
        
        return sql, params
    
    def _build_where_clause(
        self,
        where: Dict,
        params: List[Any],
        param_idx: int,
    ) -> Tuple[str, List[Any], int]:
        """Build WHERE clause for JSONB query."""
        # Logical AND
        if "and" in where:
            clauses = []
            for cond in where["and"]:
                clause, params, param_idx = self._build_where_clause(cond, params, param_idx)
                clauses.append(f"({clause})")
            return " AND ".join(clauses), params, param_idx
        
        # Logical OR
        if "or" in where:
            clauses = []
            for cond in where["or"]:
                clause, params, param_idx = self._build_where_clause(cond, params, param_idx)
                clauses.append(f"({clause})")
            return " OR ".join(clauses), params, param_idx
        
        # Logical NOT
        if "not" in where:
            clause, params, param_idx = self._build_where_clause(where["not"], params, param_idx)
            return f"NOT ({clause})", params, param_idx
        
        # Field condition
        field = where.get("field")
        op = where.get("op", "eq")
        value = where.get("value")
        
        if not field:
            return "TRUE", params, param_idx
        
        # Handle nested fields with -> instead of .
        json_path = self._field_to_jsonb_path(field)
        
        # Build condition based on operator
        if op == "eq":
            params.append(json.dumps(value))
            clause = f"record{json_path} = ${param_idx}::jsonb"
            param_idx += 1
        elif op == "ne":
            params.append(json.dumps(value))
            clause = f"record{json_path} != ${param_idx}::jsonb"
            param_idx += 1
        elif op in ("gt", "gte", "lt", "lte"):
            # Numeric comparison - extract as text and cast
            params.append(value)
            op_symbol = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
            clause = f"(record{json_path})::numeric {op_symbol} ${param_idx}"
            param_idx += 1
        elif op == "in":
            # IN list
            params.append(value)
            clause = f"record{json_path} <@ ${param_idx}::jsonb"  # contained by
            param_idx += 1
        elif op == "nin":
            params.append(value)
            clause = f"NOT (record{json_path} <@ ${param_idx}::jsonb)"
            param_idx += 1
        elif op == "contains":
            params.append(f"%{value}%")
            clause = f"(record->>{json_path[2:]}) LIKE ${param_idx}"
            param_idx += 1
        elif op == "startswith":
            params.append(f"{value}%")
            clause = f"(record->>{json_path[2:]}) LIKE ${param_idx}"
            param_idx += 1
        elif op == "endswith":
            params.append(f"%{value}")
            clause = f"(record->>{json_path[2:]}) LIKE ${param_idx}"
            param_idx += 1
        elif op == "isnull":
            if value:
                clause = f"record{json_path} IS NULL OR record{json_path} = 'null'::jsonb"
            else:
                clause = f"record{json_path} IS NOT NULL AND record{json_path} != 'null'::jsonb"
        elif op == "regex":
            params.append(value)
            clause = f"(record->>{json_path[2:]}) ~ ${param_idx}"
            param_idx += 1
        else:
            clause = "TRUE"
        
        return clause, params, param_idx
    
    def _field_to_jsonb_path(self, field: str) -> str:
        """Convert a field name to JSONB path notation."""
        parts = field.split(".")
        if len(parts) == 1:
            return f"->'{field}'"
        
        path = ""
        for part in parts[:-1]:
            path += f"->'{part}'"
        path += f"->>'{parts[-1]}'"
        return path
    
    def _build_aggregation_query(self, aggregate_spec: Dict) -> str:
        """Build aggregation SELECT clause."""
        selects = []
        
        for agg_name, field in aggregate_spec.items():
            agg_type = agg_name.lower()
            
            if agg_type == "count":
                if field == "*":
                    selects.append("COUNT(*) as count")
                else:
                    selects.append(f"COUNT(record->>'{field}') as count_{field}")
            elif agg_type == "sum":
                selects.append(f"SUM((record->>'{field}')::numeric) as sum_{field}")
            elif agg_type == "avg":
                selects.append(f"AVG((record->>'{field}')::numeric) as avg_{field}")
            elif agg_type == "min":
                selects.append(f"MIN((record->>'{field}')::numeric) as min_{field}")
            elif agg_type == "max":
                selects.append(f"MAX((record->>'{field}')::numeric) as max_{field}")
        
        return f"\n        SELECT {', '.join(selects)} FROM records"
    
    def _build_grouped_aggregation_query(
        self,
        aggregate_spec: Dict,
        group_by: List[str],
        params: List[Any],
        param_idx: int,
    ) -> str:
        """Build grouped aggregation query."""
        # Group fields
        group_selects = [f"record->>'{f}' as \"{f}\"" for f in group_by]
        
        # Aggregation selects
        agg_selects = []
        for agg_name, field in aggregate_spec.items():
            agg_type = agg_name.lower()
            
            if agg_type == "count":
                if field == "*":
                    agg_selects.append("COUNT(*) as count")
                else:
                    agg_selects.append(f"COUNT(record->>'{field}') as count_{field}")
            elif agg_type == "sum":
                agg_selects.append(f"SUM((record->>'{field}')::numeric) as sum_{field}")
            elif agg_type == "avg":
                agg_selects.append(f"AVG((record->>'{field}')::numeric) as avg_{field}")
            elif agg_type == "min":
                agg_selects.append(f"MIN((record->>'{field}')::numeric) as min_{field}")
            elif agg_type == "max":
                agg_selects.append(f"MAX((record->>'{field}')::numeric) as max_{field}")
        
        all_selects = group_selects + agg_selects
        group_clause = ", ".join([f"record->>'{f}'" for f in group_by])
        
        return f"""
        SELECT {', '.join(all_selects)}
        FROM records
        GROUP BY {group_clause}
        """
    
    # ========================================================================
    # Query Execution with Connection
    # ========================================================================
    
    async def execute_query(
        self,
        conn: asyncpg.Connection,
        document_id: str,
        query: Dict,
        use_jsonb_query: bool = False,
    ) -> Dict:
        """
        Execute a query against a data document.
        
        Args:
            conn: Database connection (should have RLS set)
            document_id: Document UUID
            query: Query specification
            use_jsonb_query: If True, use PostgreSQL JSONB query instead of in-memory
            
        Returns:
            Query result
        """
        if use_jsonb_query:
            # Use PostgreSQL JSONB query
            sql, params = self.build_jsonb_query(document_id, query)
            logger.debug("Executing JSONB query", sql=sql[:200], params=params[:3])
            
            rows = await conn.fetch(sql, *params)
            
            # Check if this is an aggregation query
            if query.get("aggregate") and not query.get("select"):
                if query.get("groupBy"):
                    return {
                        "aggregations": [dict(row) for row in rows],
                        "total": len(rows),
                    }
                else:
                    return {
                        "aggregations": dict(rows[0]) if rows else {},
                        "total": 1,
                    }
            
            # Regular query - convert rows to dicts
            records = []
            for row in rows:
                if "record" in row.keys():
                    records.append(json.loads(row["record"]))
                else:
                    records.append(dict(row))
            
            # Get total count (separate query)
            count_sql = f"""
            SELECT jsonb_array_length(data_content) as total
            FROM data_files
            WHERE file_id = $1 AND doc_type = 'data'
            """
            total_row = await conn.fetchrow(count_sql, document_id)
            total = total_row["total"] if total_row else 0
            
            return {
                "records": records,
                "total": total,
                "limit": query.get("limit", 100),
                "offset": query.get("offset", 0),
            }
        else:
            # In-memory query
            row = await conn.fetchrow("""
                SELECT data_content
                FROM data_files
                WHERE file_id = $1 AND doc_type = 'data'
            """, document_id)
            
            if not row or not row["data_content"]:
                return {"records": [], "total": 0, "limit": query.get("limit", 100), "offset": 0}
            
            records = json.loads(row["data_content"])
            return self.execute_in_memory(records, query)
    
    # ========================================================================
    # Query Validation
    # ========================================================================
    
    def validate_query(self, query: Dict) -> List[str]:
        """
        Validate a query specification.
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Validate select
        select = query.get("select")
        if select is not None and not isinstance(select, list):
            errors.append("'select' must be a list of field names")
        
        # Validate where
        where = query.get("where")
        if where is not None:
            errors.extend(self._validate_where(where))
        
        # Validate orderBy
        order_by = query.get("orderBy")
        if order_by is not None:
            if not isinstance(order_by, list):
                errors.append("'orderBy' must be a list")
            else:
                for i, spec in enumerate(order_by):
                    if not isinstance(spec, dict):
                        errors.append(f"orderBy[{i}] must be an object")
                    elif "field" not in spec:
                        errors.append(f"orderBy[{i}] must have 'field'")
                    elif spec.get("direction") and spec["direction"] not in ("asc", "desc"):
                        errors.append(f"orderBy[{i}].direction must be 'asc' or 'desc'")
        
        # Validate limit/offset
        limit = query.get("limit")
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            errors.append("'limit' must be a non-negative integer")
        
        offset = query.get("offset")
        if offset is not None and (not isinstance(offset, int) or offset < 0):
            errors.append("'offset' must be a non-negative integer")
        
        # Validate aggregate
        aggregate = query.get("aggregate")
        if aggregate is not None:
            if not isinstance(aggregate, dict):
                errors.append("'aggregate' must be an object")
            else:
                for agg_name, field in aggregate.items():
                    if agg_name.lower() not in self.AGGREGATIONS:
                        errors.append(f"Unknown aggregation: {agg_name}")
                    if not isinstance(field, str):
                        errors.append(f"Aggregation field must be a string: {agg_name}")
        
        # Validate groupBy
        group_by = query.get("groupBy")
        if group_by is not None:
            if not isinstance(group_by, list):
                errors.append("'groupBy' must be a list of field names")
            elif not all(isinstance(f, str) for f in group_by):
                errors.append("'groupBy' fields must be strings")
        
        return errors
    
    def _validate_where(self, where: Dict, path: str = "where") -> List[str]:
        """Validate a where clause recursively."""
        errors = []
        
        if not isinstance(where, dict):
            errors.append(f"{path} must be an object")
            return errors
        
        # Check for logical operators
        if "and" in where:
            if not isinstance(where["and"], list):
                errors.append(f"{path}.and must be a list")
            else:
                for i, cond in enumerate(where["and"]):
                    errors.extend(self._validate_where(cond, f"{path}.and[{i}]"))
        
        if "or" in where:
            if not isinstance(where["or"], list):
                errors.append(f"{path}.or must be a list")
            else:
                for i, cond in enumerate(where["or"]):
                    errors.extend(self._validate_where(cond, f"{path}.or[{i}]"))
        
        if "not" in where:
            errors.extend(self._validate_where(where["not"], f"{path}.not"))
        
        # Check for field condition
        if "field" in where:
            if not isinstance(where["field"], str):
                errors.append(f"{path}.field must be a string")
            
            op = where.get("op", "eq")
            if op not in self.OPERATORS:
                errors.append(f"{path}.op '{op}' is not a valid operator")
            
            # 'value' is required for most operators
            if op != "isnull" and "value" not in where:
                errors.append(f"{path} requires 'value' for operator '{op}'")
        
        return errors


class SortKey:
    """
    Helper class for sorting with mixed types and reverse direction.
    """
    
    def __init__(self, value: Any, reverse: bool = False):
        self.value = value
        self.reverse = reverse
    
    def __lt__(self, other):
        if self.reverse:
            return self._compare(other.value, self.value)
        return self._compare(self.value, other.value)
    
    def __eq__(self, other):
        return self.value == other.value
    
    def _compare(self, a, b):
        """Compare two values, handling None and different types."""
        # Tuples from our sort key (priority, value)
        if isinstance(a, tuple) and isinstance(b, tuple):
            if a[0] != b[0]:
                return a[0] < b[0]
            if a[1] is None and b[1] is None:
                return False
            if a[1] is None:
                return False  # None sorts last
            if b[1] is None:
                return True
            try:
                return a[1] < b[1]
            except TypeError:
                return str(a[1]) < str(b[1])
        
        # Direct comparison
        if a is None and b is None:
            return False
        if a is None:
            return False
        if b is None:
            return True
        
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)
