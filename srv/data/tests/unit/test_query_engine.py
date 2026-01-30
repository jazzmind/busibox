"""
Unit tests for the QueryEngine class.

Tests the SQL-like query parsing and execution against in-memory data.
No database or network access required.
"""

import pytest
from api.services.query_engine import QueryEngine, SortKey


@pytest.fixture
def query_engine():
    """Create a QueryEngine instance."""
    return QueryEngine()


@pytest.fixture
def sample_records():
    """Sample records for testing."""
    return [
        {"id": "1", "name": "Task A", "status": "pending", "priority": 3, "tags": ["urgent"]},
        {"id": "2", "name": "Task B", "status": "done", "priority": 1, "tags": ["low"]},
        {"id": "3", "name": "Task C", "status": "pending", "priority": 5, "tags": ["urgent", "important"]},
        {"id": "4", "name": "Task D", "status": "in_progress", "priority": 2, "tags": []},
        {"id": "5", "name": "Task E", "status": "pending", "priority": 4, "tags": ["medium"]},
    ]


class TestQueryValidation:
    """Test query validation."""
    
    def test_valid_simple_query(self, query_engine):
        query = {
            "select": ["name", "status"],
            "where": {"field": "status", "op": "eq", "value": "pending"},
            "limit": 10,
            "offset": 0,
        }
        errors = query_engine.validate_query(query)
        assert errors == []
    
    def test_valid_complex_query(self, query_engine):
        query = {
            "select": ["name"],
            "where": {
                "and": [
                    {"field": "status", "op": "eq", "value": "pending"},
                    {"field": "priority", "op": "gte", "value": 3},
                ]
            },
            "orderBy": [{"field": "priority", "direction": "desc"}],
            "limit": 50,
            "offset": 0,
            "aggregate": {"count": "*"},
        }
        errors = query_engine.validate_query(query)
        assert errors == []
    
    def test_invalid_select_type(self, query_engine):
        query = {"select": "name"}  # Should be list
        errors = query_engine.validate_query(query)
        assert any("select" in e.lower() for e in errors)
    
    def test_invalid_operator(self, query_engine):
        query = {
            "where": {"field": "status", "op": "invalid_op", "value": "pending"}
        }
        errors = query_engine.validate_query(query)
        assert any("invalid_op" in e for e in errors)
    
    def test_invalid_order_direction(self, query_engine):
        query = {
            "orderBy": [{"field": "name", "direction": "sideways"}]
        }
        errors = query_engine.validate_query(query)
        assert any("direction" in e.lower() for e in errors)
    
    def test_invalid_limit(self, query_engine):
        query = {"limit": -5}
        errors = query_engine.validate_query(query)
        assert any("limit" in e.lower() for e in errors)
    
    def test_invalid_aggregation(self, query_engine):
        query = {"aggregate": {"unknown_agg": "field"}}
        errors = query_engine.validate_query(query)
        assert any("unknown" in e.lower() for e in errors)
    
    def test_missing_value_for_comparison(self, query_engine):
        query = {
            "where": {"field": "status", "op": "eq"}  # Missing value
        }
        errors = query_engine.validate_query(query)
        assert any("value" in e.lower() for e in errors)
    
    def test_isnull_without_value_is_valid(self, query_engine):
        # isnull doesn't require a value in the same way
        query = {
            "where": {"field": "tags", "op": "isnull", "value": True}
        }
        errors = query_engine.validate_query(query)
        assert errors == []


class TestInMemoryFiltering:
    """Test in-memory record filtering."""
    
    def test_filter_eq(self, query_engine, sample_records):
        query = {"where": {"field": "status", "op": "eq", "value": "pending"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 3
        assert all(r["status"] == "pending" for r in result["records"])
    
    def test_filter_ne(self, query_engine, sample_records):
        query = {"where": {"field": "status", "op": "ne", "value": "pending"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
        assert all(r["status"] != "pending" for r in result["records"])
    
    def test_filter_gt(self, query_engine, sample_records):
        query = {"where": {"field": "priority", "op": "gt", "value": 3}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
        assert all(r["priority"] > 3 for r in result["records"])
    
    def test_filter_gte(self, query_engine, sample_records):
        query = {"where": {"field": "priority", "op": "gte", "value": 3}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 3
        assert all(r["priority"] >= 3 for r in result["records"])
    
    def test_filter_lt(self, query_engine, sample_records):
        query = {"where": {"field": "priority", "op": "lt", "value": 3}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
        assert all(r["priority"] < 3 for r in result["records"])
    
    def test_filter_lte(self, query_engine, sample_records):
        query = {"where": {"field": "priority", "op": "lte", "value": 3}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 3
        assert all(r["priority"] <= 3 for r in result["records"])
    
    def test_filter_in(self, query_engine, sample_records):
        query = {"where": {"field": "status", "op": "in", "value": ["pending", "done"]}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 4
        assert all(r["status"] in ["pending", "done"] for r in result["records"])
    
    def test_filter_nin(self, query_engine, sample_records):
        query = {"where": {"field": "status", "op": "nin", "value": ["pending", "done"]}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 1
        assert result["records"][0]["status"] == "in_progress"
    
    def test_filter_contains_string(self, query_engine, sample_records):
        query = {"where": {"field": "name", "op": "contains", "value": "Task"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 5  # All records contain "Task"
    
    def test_filter_contains_array(self, query_engine, sample_records):
        query = {"where": {"field": "tags", "op": "contains", "value": "urgent"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
    
    def test_filter_startswith(self, query_engine, sample_records):
        query = {"where": {"field": "name", "op": "startswith", "value": "Task"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 5
    
    def test_filter_endswith(self, query_engine, sample_records):
        query = {"where": {"field": "name", "op": "endswith", "value": "A"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 1
        assert result["records"][0]["id"] == "1"
    
    def test_filter_isnull_true(self, query_engine):
        records = [
            {"id": "1", "name": "Test", "optional": None},
            {"id": "2", "name": "Test 2", "optional": "value"},
        ]
        query = {"where": {"field": "optional", "op": "isnull", "value": True}}
        result = query_engine.execute_in_memory(records, query)
        assert result["total"] == 1
        assert result["records"][0]["id"] == "1"
    
    def test_filter_isnull_false(self, query_engine):
        records = [
            {"id": "1", "name": "Test", "optional": None},
            {"id": "2", "name": "Test 2", "optional": "value"},
        ]
        query = {"where": {"field": "optional", "op": "isnull", "value": False}}
        result = query_engine.execute_in_memory(records, query)
        assert result["total"] == 1
        assert result["records"][0]["id"] == "2"
    
    def test_filter_regex(self, query_engine, sample_records):
        query = {"where": {"field": "name", "op": "regex", "value": r"Task [A-C]"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 3


class TestLogicalOperators:
    """Test AND/OR/NOT logical operators."""
    
    def test_and_condition(self, query_engine, sample_records):
        query = {
            "where": {
                "and": [
                    {"field": "status", "op": "eq", "value": "pending"},
                    {"field": "priority", "op": "gte", "value": 4},
                ]
            }
        }
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
        assert all(r["status"] == "pending" and r["priority"] >= 4 for r in result["records"])
    
    def test_or_condition(self, query_engine, sample_records):
        query = {
            "where": {
                "or": [
                    {"field": "status", "op": "eq", "value": "done"},
                    {"field": "priority", "op": "eq", "value": 5},
                ]
            }
        }
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2  # Task B (done) and Task C (priority 5)
    
    def test_not_condition(self, query_engine, sample_records):
        query = {
            "where": {
                "not": {"field": "status", "op": "eq", "value": "pending"}
            }
        }
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 2
        assert all(r["status"] != "pending" for r in result["records"])
    
    def test_nested_conditions(self, query_engine, sample_records):
        query = {
            "where": {
                "or": [
                    {
                        "and": [
                            {"field": "status", "op": "eq", "value": "pending"},
                            {"field": "priority", "op": "gte", "value": 4},
                        ]
                    },
                    {"field": "status", "op": "eq", "value": "done"},
                ]
            }
        }
        result = query_engine.execute_in_memory(sample_records, query)
        # (pending AND priority >= 4) OR done = Task C, Task E, Task B
        assert result["total"] == 3


class TestSorting:
    """Test sorting functionality."""
    
    def test_sort_ascending(self, query_engine, sample_records):
        query = {"orderBy": [{"field": "priority", "direction": "asc"}]}
        result = query_engine.execute_in_memory(sample_records, query)
        priorities = [r["priority"] for r in result["records"]]
        assert priorities == sorted(priorities)
    
    def test_sort_descending(self, query_engine, sample_records):
        query = {"orderBy": [{"field": "priority", "direction": "desc"}]}
        result = query_engine.execute_in_memory(sample_records, query)
        priorities = [r["priority"] for r in result["records"]]
        assert priorities == sorted(priorities, reverse=True)
    
    def test_sort_by_string(self, query_engine, sample_records):
        query = {"orderBy": [{"field": "name", "direction": "asc"}]}
        result = query_engine.execute_in_memory(sample_records, query)
        names = [r["name"] for r in result["records"]]
        assert names == sorted(names)
    
    def test_multi_field_sort(self, query_engine, sample_records):
        query = {
            "orderBy": [
                {"field": "status", "direction": "asc"},
                {"field": "priority", "direction": "desc"},
            ]
        }
        result = query_engine.execute_in_memory(sample_records, query)
        # Verify primary sort by status, secondary by priority desc
        records = result["records"]
        for i in range(len(records) - 1):
            if records[i]["status"] == records[i + 1]["status"]:
                assert records[i]["priority"] >= records[i + 1]["priority"]
    
    def test_sort_with_nulls(self, query_engine):
        records = [
            {"id": "1", "name": "A", "value": 1},
            {"id": "2", "name": "B", "value": None},
            {"id": "3", "name": "C", "value": 3},
        ]
        query = {"orderBy": [{"field": "value", "direction": "asc"}]}
        result = query_engine.execute_in_memory(records, query)
        # Nulls should be last
        assert result["records"][-1]["value"] is None


class TestPagination:
    """Test pagination with limit and offset."""
    
    def test_limit(self, query_engine, sample_records):
        query = {"limit": 2}
        result = query_engine.execute_in_memory(sample_records, query)
        assert len(result["records"]) == 2
        assert result["total"] == 5
    
    def test_offset(self, query_engine, sample_records):
        query = {"limit": 2, "offset": 2}
        result = query_engine.execute_in_memory(sample_records, query)
        assert len(result["records"]) == 2
        assert result["offset"] == 2
    
    def test_offset_beyond_results(self, query_engine, sample_records):
        query = {"limit": 10, "offset": 100}
        result = query_engine.execute_in_memory(sample_records, query)
        assert len(result["records"]) == 0
        assert result["total"] == 5


class TestFieldSelection:
    """Test field selection."""
    
    def test_select_specific_fields(self, query_engine, sample_records):
        query = {"select": ["name", "status"]}
        result = query_engine.execute_in_memory(sample_records, query)
        for record in result["records"]:
            assert set(record.keys()) == {"name", "status"}
    
    def test_select_all(self, query_engine, sample_records):
        query = {}  # No select = all fields
        result = query_engine.execute_in_memory(sample_records, query)
        # Records should have all original fields
        assert "id" in result["records"][0]
        assert "tags" in result["records"][0]


class TestAggregations:
    """Test aggregation functions."""
    
    def test_count_all(self, query_engine, sample_records):
        query = {"aggregate": {"count": "*"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["count"] == 5
    
    def test_count_field(self, query_engine):
        records = [
            {"id": "1", "value": 10},
            {"id": "2", "value": None},
            {"id": "3", "value": 30},
        ]
        query = {"aggregate": {"count": "value"}}
        result = query_engine.execute_in_memory(records, query)
        assert result["aggregations"]["count_value"] == 2  # Excludes null
    
    def test_sum(self, query_engine, sample_records):
        query = {"aggregate": {"sum": "priority"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["sum_priority"] == 15  # 3+1+5+2+4
    
    def test_avg(self, query_engine, sample_records):
        query = {"aggregate": {"avg": "priority"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["avg_priority"] == 3.0  # 15/5
    
    def test_min(self, query_engine, sample_records):
        query = {"aggregate": {"min": "priority"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["min_priority"] == 1
    
    def test_max(self, query_engine, sample_records):
        query = {"aggregate": {"max": "priority"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["max_priority"] == 5
    
    def test_multiple_aggregations(self, query_engine, sample_records):
        query = {
            "aggregate": {
                "count": "*",
                "sum": "priority",
                "avg": "priority",
            }
        }
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["count"] == 5
        assert result["aggregations"]["sum_priority"] == 15
        assert result["aggregations"]["avg_priority"] == 3.0
    
    def test_aggregation_with_filter(self, query_engine, sample_records):
        query = {
            "where": {"field": "status", "op": "eq", "value": "pending"},
            "aggregate": {"count": "*", "avg": "priority"},
        }
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["aggregations"]["count"] == 3  # 3 pending
        assert result["aggregations"]["avg_priority"] == 4.0  # (3+5+4)/3


class TestGroupBy:
    """Test GROUP BY functionality."""
    
    def test_group_by_single_field(self, query_engine, sample_records):
        query = {
            "aggregate": {"count": "*"},
            "groupBy": ["status"],
        }
        result = query_engine.execute_in_memory(sample_records, query)
        aggs = result["aggregations"]
        assert isinstance(aggs, list)
        status_counts = {a["status"]: a["count"] for a in aggs}
        assert status_counts["pending"] == 3
        assert status_counts["done"] == 1
        assert status_counts["in_progress"] == 1
    
    def test_group_by_with_sum(self, query_engine, sample_records):
        query = {
            "aggregate": {"sum": "priority"},
            "groupBy": ["status"],
        }
        result = query_engine.execute_in_memory(sample_records, query)
        aggs = result["aggregations"]
        status_sums = {a["status"]: a["sum_priority"] for a in aggs}
        assert status_sums["pending"] == 12  # 3+5+4
        assert status_sums["done"] == 1
        assert status_sums["in_progress"] == 2


class TestNestedFields:
    """Test nested field access with dot notation."""
    
    def test_access_nested_field(self, query_engine):
        records = [
            {"id": "1", "meta": {"author": "Alice", "version": 1}},
            {"id": "2", "meta": {"author": "Bob", "version": 2}},
        ]
        query = {"where": {"field": "meta.author", "op": "eq", "value": "Alice"}}
        result = query_engine.execute_in_memory(records, query)
        assert result["total"] == 1
        assert result["records"][0]["id"] == "1"
    
    def test_sort_by_nested_field(self, query_engine):
        records = [
            {"id": "1", "meta": {"version": 3}},
            {"id": "2", "meta": {"version": 1}},
            {"id": "3", "meta": {"version": 2}},
        ]
        query = {"orderBy": [{"field": "meta.version", "direction": "asc"}]}
        result = query_engine.execute_in_memory(records, query)
        versions = [r["meta"]["version"] for r in result["records"]]
        assert versions == [1, 2, 3]
    
    def test_select_nested_field(self, query_engine):
        records = [
            {"id": "1", "meta": {"author": "Alice"}, "name": "Doc 1"},
        ]
        query = {"select": ["id", "meta.author"]}
        result = query_engine.execute_in_memory(records, query)
        assert result["records"][0] == {"id": "1", "meta.author": "Alice"}


class TestEdgeCases:
    """Test edge cases and error handling."""
    
    def test_empty_records(self, query_engine):
        result = query_engine.execute_in_memory([], {"limit": 10})
        assert result["records"] == []
        assert result["total"] == 0
    
    def test_empty_query(self, query_engine, sample_records):
        result = query_engine.execute_in_memory(sample_records, {})
        assert result["total"] == 5
        assert len(result["records"]) == 5
    
    def test_filter_nonexistent_field(self, query_engine, sample_records):
        query = {"where": {"field": "nonexistent", "op": "eq", "value": "test"}}
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 0  # No records have this field
    
    def test_empty_where_clause(self, query_engine, sample_records):
        query = {"where": {}}  # Empty where = match all
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 5
    
    def test_no_field_in_where(self, query_engine, sample_records):
        query = {"where": {"op": "eq", "value": "test"}}  # Missing field
        result = query_engine.execute_in_memory(sample_records, query)
        assert result["total"] == 5  # Should match all
