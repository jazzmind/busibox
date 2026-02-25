"""Unit tests for schema-driven entity type normalization.

Tests the pure `_normalize_entity_type()` function and `FIELD_NAME_TO_ENTITY_TYPE`
mapping used by POST /data/graph/from-extraction.
"""

import pytest

from api.routes.graph import _normalize_entity_type, FIELD_NAME_TO_ENTITY_TYPE


# =============================================================================
# Person variants
# =============================================================================

class TestPersonNormalization:
    @pytest.mark.parametrize("field_name", [
        "people", "person", "persons", "person_name", "person_names",
        "names", "author", "authors", "speaker", "speakers",
        "employee", "employees", "candidate", "candidates",
    ])
    def test_person_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Person"

    def test_person_with_whitespace(self):
        assert _normalize_entity_type("  people  ") == "Person"

    def test_person_case_insensitive(self):
        assert _normalize_entity_type("People") == "Person"
        assert _normalize_entity_type("PEOPLE") == "Person"
        assert _normalize_entity_type("Person_Name") == "Person"


# =============================================================================
# Organization variants
# =============================================================================

class TestOrganizationNormalization:
    @pytest.mark.parametrize("field_name", [
        "organizations", "organization", "organisations", "organisation",
        "company", "companies", "org", "orgs",
        "employer", "employers", "institution", "institutions",
    ])
    def test_organization_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Organization"


# =============================================================================
# Technology variants
# =============================================================================

class TestTechnologyNormalization:
    @pytest.mark.parametrize("field_name", [
        "technologies", "technology", "tech", "tools", "tool",
        "tech_stack", "software", "framework", "frameworks",
        "platform", "platforms", "language", "languages",
        "programming_languages",
    ])
    def test_technology_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Technology"


# =============================================================================
# Location variants
# =============================================================================

class TestLocationNormalization:
    @pytest.mark.parametrize("field_name", [
        "locations", "location", "place", "places",
        "city", "cities", "country", "countries",
        "region", "regions",
    ])
    def test_location_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Location"


# =============================================================================
# Keyword variants
# =============================================================================

class TestKeywordNormalization:
    @pytest.mark.parametrize("field_name", [
        "keywords", "keyword", "tags", "tag", "topics", "topic",
        "skills", "skill", "competencies", "competency",
    ])
    def test_keyword_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Keyword"


# =============================================================================
# Concept variants
# =============================================================================

class TestConceptNormalization:
    @pytest.mark.parametrize("field_name", [
        "concepts", "concept", "themes", "theme",
    ])
    def test_concept_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Concept"


# =============================================================================
# Project variants
# =============================================================================

class TestProjectNormalization:
    @pytest.mark.parametrize("field_name", [
        "projects", "project", "initiatives", "initiative",
        "programs", "program", "programmes", "programme",
    ])
    def test_project_variants(self, field_name):
        assert _normalize_entity_type(field_name) == "Project"


# =============================================================================
# Fallback behavior
# =============================================================================

class TestFallbackNormalization:
    def test_unknown_field_gets_title_cased(self):
        assert _normalize_entity_type("custom_field") == "Custom_Field"

    def test_single_word_unknown(self):
        assert _normalize_entity_type("vehicles") == "Vehicles"

    def test_hyphenated_field(self):
        """Hyphens are converted to underscores before lookup."""
        assert _normalize_entity_type("tech-stack") == "Technology"

    def test_spaces_in_field(self):
        """Spaces are converted to underscores before lookup."""
        assert _normalize_entity_type("tech stack") == "Technology"

    def test_empty_string(self):
        result = _normalize_entity_type("")
        assert isinstance(result, str)

    def test_numeric_field_fallback(self):
        result = _normalize_entity_type("123")
        assert isinstance(result, str)


# =============================================================================
# Mapping completeness
# =============================================================================

class TestMappingCompleteness:
    def test_all_mapped_types_are_known(self):
        """All values in the mapping should be one of the canonical types."""
        canonical_types = {"Person", "Organization", "Technology", "Location",
                          "Keyword", "Concept", "Project"}
        for field_name, entity_type in FIELD_NAME_TO_ENTITY_TYPE.items():
            assert entity_type in canonical_types, (
                f"Field '{field_name}' maps to unknown type '{entity_type}'"
            )

    def test_no_duplicate_field_names(self):
        """All keys should be lowercase (the normalization lowercases input)."""
        for key in FIELD_NAME_TO_ENTITY_TYPE:
            assert key == key.lower(), f"Key '{key}' should be lowercase"
