"""Unit tests for default extraction schema definitions.

Validates the structure and content of DEFAULT_EXTRACTION_SCHEMAS
and their alignment with the entity normalization mapping.
"""

import pytest

from api.routes.data import DEFAULT_EXTRACTION_SCHEMAS
from api.routes.graph import _normalize_entity_type


# =============================================================================
# Schema structure validation
# =============================================================================

class TestDefaultSchemaStructure:
    def test_at_least_two_schemas(self):
        assert len(DEFAULT_EXTRACTION_SCHEMAS) >= 2

    @pytest.mark.parametrize("idx", range(len(DEFAULT_EXTRACTION_SCHEMAS)))
    def test_schema_has_required_keys(self, idx):
        schema_def = DEFAULT_EXTRACTION_SCHEMAS[idx]
        assert "name" in schema_def
        assert "metadata" in schema_def
        assert "schema" in schema_def
        assert isinstance(schema_def["name"], str)
        assert len(schema_def["name"]) > 0

    @pytest.mark.parametrize("idx", range(len(DEFAULT_EXTRACTION_SCHEMAS)))
    def test_metadata_is_extraction_schema(self, idx):
        meta = DEFAULT_EXTRACTION_SCHEMAS[idx]["metadata"]
        assert meta.get("type") == "extraction_schema"
        assert meta.get("builtin") is True

    @pytest.mark.parametrize("idx", range(len(DEFAULT_EXTRACTION_SCHEMAS)))
    def test_schema_has_fields(self, idx):
        schema = DEFAULT_EXTRACTION_SCHEMAS[idx]["schema"]
        assert "fields" in schema
        assert isinstance(schema["fields"], dict)
        assert len(schema["fields"]) > 0

    @pytest.mark.parametrize("idx", range(len(DEFAULT_EXTRACTION_SCHEMAS)))
    def test_schema_has_display_metadata(self, idx):
        schema = DEFAULT_EXTRACTION_SCHEMAS[idx]["schema"]
        assert "displayName" in schema
        assert "itemLabel" in schema


# =============================================================================
# Graph-tagged field validation
# =============================================================================

class TestGraphTaggedFields:
    def _get_graph_fields(self):
        """Collect all fields across all schemas that have search: ["graph"]."""
        graph_fields = []
        for schema_def in DEFAULT_EXTRACTION_SCHEMAS:
            schema = schema_def["schema"]
            for field_name, field_def in schema.get("fields", {}).items():
                search_modes = field_def.get("search", [])
                if "graph" in search_modes:
                    graph_fields.append({
                        "schema_name": schema_def["name"],
                        "field_name": field_name,
                        "field_def": field_def,
                    })
        return graph_fields

    def test_has_graph_tagged_fields(self):
        fields = self._get_graph_fields()
        assert len(fields) > 0, "Default schemas should have at least one graph-tagged field"

    def test_graph_fields_normalize_to_known_types(self):
        """All graph-tagged fields should map to recognized entity types."""
        known_types = {"Person", "Organization", "Technology", "Location",
                      "Keyword", "Concept", "Project"}
        for field_info in self._get_graph_fields():
            entity_type = _normalize_entity_type(field_info["field_name"])
            assert entity_type in known_types, (
                f"Field '{field_info['field_name']}' in schema "
                f"'{field_info['schema_name']}' normalizes to unknown type "
                f"'{entity_type}'"
            )

    def test_graph_fields_have_descriptions(self):
        for field_info in self._get_graph_fields():
            desc = field_info["field_def"].get("description", "")
            assert len(desc) > 0, (
                f"Graph-tagged field '{field_info['field_name']}' in "
                f"'{field_info['schema_name']}' should have a description"
            )


# =============================================================================
# Search mode validation
# =============================================================================

class TestSearchModes:
    VALID_SEARCH_MODES = {"index", "embed", "graph"}

    @pytest.mark.parametrize("idx", range(len(DEFAULT_EXTRACTION_SCHEMAS)))
    def test_all_search_modes_are_valid(self, idx):
        schema = DEFAULT_EXTRACTION_SCHEMAS[idx]["schema"]
        for field_name, field_def in schema.get("fields", {}).items():
            modes = field_def.get("search", [])
            for mode in modes:
                assert mode in self.VALID_SEARCH_MODES, (
                    f"Unknown search mode '{mode}' in field '{field_name}' "
                    f"of schema '{DEFAULT_EXTRACTION_SCHEMAS[idx]['name']}'"
                )


# =============================================================================
# General Entity Extraction schema specifics
# =============================================================================

class TestGeneralEntitySchema:
    @pytest.fixture
    def schema(self):
        return next(
            s for s in DEFAULT_EXTRACTION_SCHEMAS
            if s["name"] == "General Entity Extraction"
        )

    def test_has_core_entity_fields(self, schema):
        fields = schema["schema"]["fields"]
        expected = {"people", "organizations", "technologies", "locations", "keywords", "concepts"}
        assert expected.issubset(set(fields.keys()))

    def test_array_fields_have_string_items(self, schema):
        fields = schema["schema"]["fields"]
        for fname, fdef in fields.items():
            if fdef.get("type") == "array":
                assert fdef.get("items", {}).get("type") == "string", (
                    f"Array field '{fname}' should have string items"
                )

    def test_all_entity_fields_are_graph_tagged(self, schema):
        fields = schema["schema"]["fields"]
        for fname, fdef in fields.items():
            search = fdef.get("search", [])
            assert "graph" in search, (
                f"Field '{fname}' in General Entity Extraction should be graph-tagged"
            )

    def test_fields_have_display_order(self, schema):
        fields = schema["schema"]["fields"]
        for fname, fdef in fields.items():
            assert "display_order" in fdef, (
                f"Field '{fname}' should have display_order"
            )


# =============================================================================
# People & Organizations schema specifics
# =============================================================================

class TestPeopleOrgSchema:
    @pytest.fixture
    def schema(self):
        return next(
            s for s in DEFAULT_EXTRACTION_SCHEMAS
            if s["name"] == "People & Organizations"
        )

    def test_has_person_and_org_fields(self, schema):
        fields = schema["schema"]["fields"]
        assert "person" in fields
        assert "organization" in fields

    def test_has_role_field(self, schema):
        fields = schema["schema"]["fields"]
        assert "role" in fields

    def test_context_field_is_embed_not_graph(self, schema):
        context = schema["schema"]["fields"].get("context", {})
        search = context.get("search", [])
        assert "embed" in search
        assert "graph" not in search

    def test_person_and_org_are_graph_tagged(self, schema):
        fields = schema["schema"]["fields"]
        assert "graph" in fields["person"].get("search", [])
        assert "graph" in fields["organization"].get("search", [])
