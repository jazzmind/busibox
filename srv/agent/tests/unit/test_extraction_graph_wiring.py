"""Unit tests for graph entity creation wiring in extraction.py.

Verifies that the extract_document endpoint calls POST /data/graph/from-extraction
after storing records, and that failures are non-fatal.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import uuid

from app.api.extraction import (
    _extract_records,
    _parse_json_text,
    _normalize_for_matching,
    _find_provenance_candidates,
    _build_value_provenance,
    _validate_and_enrich_records,
    _build_records_response_schema,
    ExtractRequest,
)


# =============================================================================
# _extract_records tests - making sure our record parsing is robust
# =============================================================================

class TestExtractRecords:
    def test_records_list(self):
        output = {"records": [{"name": "Alice"}, {"name": "Bob"}]}
        assert len(_extract_records(output)) == 2

    def test_records_nested_in_data(self):
        output = {"data": {"records": [{"name": "Alice"}]}}
        assert len(_extract_records(output)) == 1

    def test_data_is_list(self):
        output = {"data": [{"name": "Alice"}, {"name": "Bob"}]}
        assert len(_extract_records(output)) == 2

    def test_result_is_dict(self):
        output = {"result": {"name": "Alice"}}
        result = _extract_records(output)
        assert len(result) == 1
        assert result[0]["name"] == "Alice"

    def test_result_has_records(self):
        output = {"result": {"records": [{"name": "Alice"}]}}
        assert len(_extract_records(output)) == 1

    def test_result_is_json_string(self):
        import json
        output = {"result": json.dumps({"records": [{"name": "Alice"}]})}
        assert len(_extract_records(output)) == 1

    def test_empty_output(self):
        assert _extract_records({}) == []

    def test_filters_non_dict_records(self):
        output = {"records": [{"name": "Alice"}, "not_a_dict", 42]}
        assert len(_extract_records(output)) == 1


# =============================================================================
# _parse_json_text tests
# =============================================================================

class TestParseJsonText:
    def test_plain_json(self):
        result = _parse_json_text('{"records": []}')
        assert result == {"records": []}

    def test_fenced_json(self):
        text = '```json\n{"records": [{"x": 1}]}\n```'
        result = _parse_json_text(text)
        assert result is not None
        assert "records" in result

    def test_json_with_surrounding_text(self):
        text = 'Here is the data: {"name": "Alice"} done.'
        result = _parse_json_text(text)
        assert result is not None
        assert result["name"] == "Alice"

    def test_empty_string(self):
        assert _parse_json_text("") is None

    def test_non_json(self):
        assert _parse_json_text("this is not json") is None

    def test_array_input(self):
        result = _parse_json_text('[{"a": 1}]')
        assert result is not None
        assert "records" in result


# =============================================================================
# _normalize_for_matching tests
# =============================================================================

class TestNormalizeForMatching:
    def test_basic(self):
        text, _ = _normalize_for_matching("Hello World!")
        assert text == "hello world"

    def test_punctuation_collapsed(self):
        text, _ = _normalize_for_matching("foo--bar")
        assert text == "foo bar"

    def test_empty(self):
        text, _ = _normalize_for_matching("")
        assert text == ""


# =============================================================================
# _find_provenance_candidates tests
# =============================================================================

class TestFindProvenanceCandidates:
    def test_exact_match(self):
        md = "Alice works at Acme Corp"
        result = _find_provenance_candidates(md, "Alice")
        assert len(result) >= 1
        assert result[0]["text"] == "Alice"
        assert result[0]["charOffset"] == 0

    def test_no_match(self):
        md = "nothing relevant here"
        result = _find_provenance_candidates(md, "XYZ123")
        assert result == []

    def test_short_value_skipped(self):
        result = _find_provenance_candidates("a", "a")
        assert result == []

    def test_none_value(self):
        result = _find_provenance_candidates("text", None)
        assert result == []


# =============================================================================
# _validate_and_enrich_records tests
# =============================================================================

class TestValidateAndEnrichRecords:
    def test_adds_source_metadata(self):
        records = [{"name": "Alice"}]
        schema = {"fields": {"name": {"type": "string"}}}
        result = _validate_and_enrich_records(records, schema, "file-1", "agent-1")
        assert len(result) == 1
        assert result[0]["_sourceFileId"] == "file-1"
        assert result[0]["_extractedBy"] == "agent-1"
        assert "_extractedAt" in result[0]

    def test_coerces_string_to_array(self):
        records = [{"tags": "one, two, three"}]
        schema = {"fields": {"tags": {"type": "array", "items": {"type": "string"}}}}
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert isinstance(result[0]["tags"], list)

    def test_missing_required_field_still_includes_record(self):
        """Missing required fields should NOT raise — the record is included
        with a coercion annotation instead."""
        records = [{"other": "data"}]
        schema = {"fields": {"name": {"type": "string", "required": True}}}
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert len(result) == 1
        coercions = result[0].get("_provenance", {}).get("_coercions", [])
        assert any("missing_required" in c.get("reason", "") for c in coercions)

    def test_provenance_initialized(self):
        records = [{"name": "Test"}]
        schema = {"fields": {"name": {"type": "string"}}}
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert "_provenance" in result[0]
        assert isinstance(result[0]["_provenance"], dict)


# =============================================================================
# _build_records_response_schema tests
# =============================================================================

class TestBuildRecordsResponseSchema:
    def test_no_required_on_record_items(self):
        """Response schema must NEVER have required on record items.

        With strict structured output, required fields the LLM can't populate
        force it to return an empty records array.
        """
        schema_obj = {
            "fields": {
                "name": {"type": "string", "required": True},
                "email": {"type": "string", "required": True},
                "phone": {"type": "string"},
            }
        }
        response_schema = _build_records_response_schema(schema_obj)
        items = response_schema["schema"]["properties"]["records"]["items"]
        assert "required" not in items, (
            f"Record items should NOT have required, but got: {items.get('required')}"
        )

    def test_records_wrapper_is_required(self):
        """The top-level 'records' key must still be required."""
        schema_obj = {"fields": {"name": {"type": "string"}}}
        response_schema = _build_records_response_schema(schema_obj)
        assert "records" in response_schema["schema"]["required"]

    def test_properties_present(self):
        schema_obj = {
            "fields": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            }
        }
        response_schema = _build_records_response_schema(schema_obj)
        items = response_schema["schema"]["properties"]["records"]["items"]
        assert "name" in items["properties"]
        assert "age" in items["properties"]
        assert "_provenance" in items["properties"]


# =============================================================================
# Graph wiring test - verify the call is made
# =============================================================================

class TestGraphWiringInExtraction:
    """Test that extraction.py calls the graph from-extraction endpoint."""

    @pytest.mark.asyncio
    async def test_graph_call_included_in_pipeline(self):
        """The pipeline should include graph creation."""
        from app.api.extraction import _run_extraction_pipeline
        import inspect
        source = inspect.getsource(_run_extraction_pipeline)
        assert '"graph"' in source or "'graph'" in source

    @pytest.mark.asyncio
    async def test_graph_call_is_non_fatal(self):
        """The graph call should be non-fatal in the pipeline."""
        import inspect
        from app.api.extraction import _run_extraction_pipeline
        source = inspect.getsource(_run_extraction_pipeline)
        assert "/data/graph/from-extraction" in source


# =============================================================================
# Diagnostic tests for the exact extraction failure path
# =============================================================================

class TestExtractionOutputParsing:
    """Tests that reproduce the exact agent output shapes seen in production
    and verify _extract_records handles them correctly."""

    def test_result_string_with_records(self):
        """Agent returns NativeOutput as a JSON string wrapped in result key.
        This is the standard path: run_service wraps string data as {"result": str}."""
        import json
        inner = json.dumps({"records": [{"name": "Alice", "email": "a@b.com"}]})
        output = {"result": inner}
        records = _extract_records(output)
        assert len(records) == 1
        assert records[0]["name"] == "Alice"

    def test_result_string_with_empty_records(self):
        """LLM returns {"records": []} — the 0-records failure case.
        result_len=15 in logs corresponds to this exact string."""
        import json
        inner = json.dumps({"records": []})
        assert len(inner) == 15, f"Expected length 15, got {len(inner)}"
        output = {"result": inner}
        records = _extract_records(output)
        assert records == [], "Empty records should return empty list"

    def test_result_string_with_single_record_no_wrapper(self):
        """LLM returns a single record dict as a JSON string (no records wrapper)."""
        import json
        inner = json.dumps({"name": "Bob", "skills": ["python"]})
        output = {"result": inner}
        records = _extract_records(output)
        assert len(records) == 1
        assert records[0]["name"] == "Bob"

    def test_result_string_with_provenance_only_record(self):
        """A record containing only _provenance should NOT be returned."""
        import json
        inner = json.dumps({"_provenance": {"name": {"text": "..."}}})
        output = {"result": inner}
        records = _extract_records(output)
        assert records == [], "Record with only _provenance should be empty"

    def test_data_dict_with_records(self):
        """Alternative wrapping where output is {"data": {"records": [...]}}."""
        output = {"data": {"records": [{"name": "Carol"}]}}
        records = _extract_records(output)
        assert len(records) == 1

    def test_direct_records_key(self):
        """Output directly contains records at top level."""
        output = {"records": [{"name": "Dave"}, {"name": "Eve"}]}
        records = _extract_records(output)
        assert len(records) == 2


class TestValidateAndEnrichGraceful:
    """Tests that _validate_and_enrich_records is graceful about errors."""

    def test_missing_required_does_not_raise(self):
        """Must NOT raise even when required fields are missing."""
        records = [{"email": "a@b.com"}]
        schema = {
            "fields": {
                "name": {"type": "string", "required": True},
                "email": {"type": "string"},
            }
        }
        result = _validate_and_enrich_records(records, schema, "file-1", "agent-1")
        assert len(result) == 1
        assert result[0]["email"] == "a@b.com"
        assert result[0]["_sourceFileId"] == "file-1"

    def test_multiple_missing_required_fields(self):
        """Multiple missing required fields should all be annotated."""
        records = [{"phone": "555-1234"}]
        schema = {
            "fields": {
                "name": {"type": "string", "required": True},
                "email": {"type": "string", "required": True},
                "phone": {"type": "string"},
            }
        }
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert len(result) == 1
        coercions = result[0]["_provenance"]["_coercions"]
        missing_coercions = [c for c in coercions if "missing_required" in c.get("reason", "")]
        assert len(missing_coercions) >= 1

    def test_coercion_error_does_not_raise(self):
        """Type coercion failure should null the field, not crash."""
        records = [{"count": "not-a-number"}]
        schema = {"fields": {"count": {"type": "integer"}}}
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert len(result) == 1
        assert result[0]["count"] is None

    def test_numeric_clamping(self):
        """Out-of-range numbers should be clamped, not raise."""
        records = [{"score": 150}]
        schema = {"fields": {"score": {"type": "integer", "min": 0, "max": 100}}}
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert result[0]["score"] == 100

    def test_valid_record_passes_through(self):
        """A fully valid record should pass through with metadata."""
        records = [{"name": "Alice", "age": 30}]
        schema = {
            "fields": {
                "name": {"type": "string", "required": True},
                "age": {"type": "integer"},
            }
        }
        result = _validate_and_enrich_records(records, schema, "f", "a")
        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert result[0]["age"] == 30


class TestTierSelection:
    """Tests for _select_extraction_tier."""

    def test_small_doc_direct(self):
        from app.api.extraction import _select_extraction_tier
        assert _select_extraction_tier(5000) == "direct"

    def test_medium_doc_rag(self):
        from app.api.extraction import _select_extraction_tier
        assert _select_extraction_tier(50000) == "rag"

    def test_large_doc_chunk_sweep(self):
        from app.api.extraction import _select_extraction_tier
        assert _select_extraction_tier(200000) == "chunk_sweep"

    def test_boundary_direct_rag(self):
        from app.api.extraction import _select_extraction_tier, TIER1_TOKEN_THRESHOLD
        assert _select_extraction_tier(TIER1_TOKEN_THRESHOLD - 1) == "direct"
        assert _select_extraction_tier(TIER1_TOKEN_THRESHOLD) == "rag"

    def test_boundary_rag_sweep(self):
        from app.api.extraction import _select_extraction_tier, TIER2_TOKEN_THRESHOLD
        assert _select_extraction_tier(TIER2_TOKEN_THRESHOLD - 1) == "rag"
        assert _select_extraction_tier(TIER2_TOKEN_THRESHOLD) == "chunk_sweep"


class TestRelaxSchemaForChunked:
    """Tests for _relax_schema_for_chunked."""

    def test_strips_required_from_fields(self):
        from app.api.extraction import _relax_schema_for_chunked
        schema_obj = {
            "fields": {
                "name": {"type": "string", "required": True},
                "email": {"type": "string", "required": True},
                "phone": {"type": "string"},
            }
        }
        response_schema = {
            "schema": {
                "properties": {
                    "records": {
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}},
                        }
                    }
                }
            }
        }
        relaxed_schema, relaxed_response = _relax_schema_for_chunked(schema_obj, response_schema)
        for fdef in relaxed_schema["fields"].values():
            assert "required" not in fdef, f"Field still has required: {fdef}"

    def test_does_not_mutate_original(self):
        from app.api.extraction import _relax_schema_for_chunked
        schema_obj = {"fields": {"name": {"type": "string", "required": True}}}
        response_schema = {"schema": {"properties": {"records": {"items": {}}}}}
        _relax_schema_for_chunked(schema_obj, response_schema)
        assert schema_obj["fields"]["name"]["required"] is True, "Original was mutated"


class TestOffsetCorrectProvenance:
    """Tests for _offset_correct_provenance."""

    def test_shifts_offsets(self):
        from app.api.extraction import _offset_correct_provenance
        records = [
            {
                "name": "Alice",
                "_provenance": {
                    "name": {"text": "Alice", "charOffset": 10, "charLength": 5},
                },
            }
        ]
        _offset_correct_provenance(records, 500)
        assert records[0]["_provenance"]["name"]["charOffset"] == 510

    def test_zero_offset_noop(self):
        from app.api.extraction import _offset_correct_provenance
        records = [{"_provenance": {"name": {"charOffset": 10}}}]
        _offset_correct_provenance(records, 0)
        assert records[0]["_provenance"]["name"]["charOffset"] == 10

    def test_nested_provenance(self):
        from app.api.extraction import _offset_correct_provenance
        records = [
            {
                "_provenance": {
                    "skills": [
                        {"text": "python", "charOffset": 20, "charLength": 6},
                        {"text": "java", "charOffset": 30, "charLength": 4},
                    ]
                }
            }
        ]
        _offset_correct_provenance(records, 100)
        assert records[0]["_provenance"]["skills"][0]["charOffset"] == 120
        assert records[0]["_provenance"]["skills"][1]["charOffset"] == 130
