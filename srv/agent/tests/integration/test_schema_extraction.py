"""
Integration tests for schema extraction structured output.

These tests exercise the structured-output pipeline directly against the
real LLM backend (via LiteLLM):
  BaseStreamingAgent._call_structured_output -> LiteLLM -> MLX/vLLM
  BaseStreamingAgent._run_native_structured_output -> PydanticAI NativeOutput
  BaseStreamingAgent.run() with response_schema context

Tests marked with @pytest.mark.integration require:
  - LiteLLM running and reachable
  - MLX or vLLM backend active

Tests are skipped (not failed) when the LLM backend is unreachable.
"""

import json
import asyncio

import pytest

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    ToolStrategy,
)
from app.config.settings import get_settings
from app.schemas.auth import Principal
from app.schemas.streaming import StreamEvent


SAMPLE_RESUME_TEXT = (
    "John Doe\n"
    "Software Engineer\n"
    "Email: john.doe@example.com\n"
    "Phone: (555) 123-4567\n\n"
    "Experience:\n"
    "- Senior Engineer at Acme Corp (2020-2024)\n"
    "- Engineer at Widgets Inc (2017-2020)\n\n"
    "Education:\n"
    "- BS Computer Science, MIT (2017)\n\n"
    "Skills: Python, TypeScript, PostgreSQL, Docker\n"
)

SIMPLE_PERSON_SCHEMA = {
    "name": "person_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "required": ["name", "email"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "email": {"type": "string"},
        },
    },
}

EXTRACTION_SCHEMA = {
    "name": "extraction_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "required": ["schemaName", "displayName", "itemLabel", "fields"],
        "additionalProperties": False,
        "properties": {
            "schemaName": {"type": "string"},
            "displayName": {"type": "string"},
            "itemLabel": {"type": "string"},
            "fields": {
                "type": "object",
                "additionalProperties": {
                    "type": "object",
                    "required": ["type", "description"],
                    "additionalProperties": False,
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "string", "integer", "number",
                                "boolean", "array", "enum", "datetime",
                            ],
                        },
                        "required": {"type": "boolean"},
                        "description": {"type": "string"},
                        "display_order": {"type": "integer"},
                        "search": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["keyword", "embed", "graph"],
                            },
                        },
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {"type": {"type": "string"}},
                            "required": ["type"],
                        },
                    },
                },
            },
        },
    },
}

RECORDS_RESPONSE_SCHEMA = {
    "name": "extraction_records",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["records"],
        "properties": {
            "records": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string", "maxLength": 500},
                        "email": {"type": "string", "maxLength": 500},
                        "phone": {"type": "string", "maxLength": 500},
                        "skills": {
                            "type": "array",
                            "maxItems": 20,
                            "items": {"type": "string", "maxLength": 200},
                        },
                        "_provenance": {
                            "type": "object",
                            "additionalProperties": True,
                        },
                    },
                },
            }
        },
    },
}


# -- Helpers ------------------------------------------------------------------


class _TestExtractorAgent(BaseStreamingAgent):
    """Minimal concrete agent for structured output integration tests."""

    def __init__(self):
        config = AgentConfig(
            name="test-extractor",
            display_name="Test Extractor",
            instructions=(
                "You are a structured data extraction assistant. "
                "Extract the requested data from the provided text. "
                "Return ONLY valid JSON matching the required schema."
            ),
            tools=[],
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
        )
        super().__init__(config)

    def pipeline_steps(self, query, context):
        return []


def _check_llm_reachable():
    """Skip test if LiteLLM is not reachable."""
    try:
        import httpx

        settings = get_settings()
        base_url = str(settings.litellm_base_url).rstrip("/")
        resp = httpx.get(f"{base_url}/health", timeout=5.0)
        if resp.status_code >= 500:
            pytest.skip(f"LiteLLM unhealthy: {resp.status_code}")
    except Exception as e:
        pytest.skip(f"LiteLLM not reachable: {e}")


# =============================================================================
# Direct _call_structured_output against real LLM
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_call_structured_output_simple_person(test_session):
    """Call _call_structured_output directly with a simple person schema.

    Verifies the LLM returns valid JSON that passes schema validation.
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()
    result = await agent._call_structured_output(
        prompt=(
            "Extract the person's name and email from this text:\n\n"
            f"{SAMPLE_RESUME_TEXT}"
        ),
        system_prompt=(
            "You are a structured data extraction assistant. "
            "Return ONLY valid JSON."
        ),
        response_schema=SIMPLE_PERSON_SCHEMA,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"
    assert "name" in parsed, f"Missing 'name' in result: {parsed}"
    assert "email" in parsed, f"Missing 'email' in result: {parsed}"
    assert isinstance(parsed["name"], str) and len(parsed["name"]) > 0
    assert "@" in parsed["email"], f"Invalid email: {parsed['email']}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_call_structured_output_extraction_schema(test_session):
    """Call _call_structured_output with the full extraction schema.

    This is the exact schema shape that was failing in production when
    the Schema Builder agent returned "I couldn't find any relevant
    information" instead of structured JSON.
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()
    result = await agent._call_structured_output(
        prompt=(
            "Analyze this document and generate an extraction schema "
            "for resumes.\n\n"
            f"Document content:\n{SAMPLE_RESUME_TEXT}"
        ),
        system_prompt=(
            "You are a Schema Builder assistant that designs structured "
            "extraction schemas for document processing workflows. "
            "Return ONLY valid JSON."
        ),
        response_schema=EXTRACTION_SCHEMA,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"
    assert "schemaName" in parsed, f"Missing 'schemaName': {parsed}"
    assert "displayName" in parsed, f"Missing 'displayName': {parsed}"
    assert "itemLabel" in parsed, f"Missing 'itemLabel': {parsed}"
    assert "fields" in parsed, f"Missing 'fields': {parsed}"

    fields = parsed["fields"]
    assert isinstance(fields, dict), f"'fields' is not a dict: {type(fields)}"
    assert len(fields) > 0, "Schema returned zero fields"

    for field_name, field_def in fields.items():
        assert "type" in field_def, f"Field '{field_name}' missing 'type'"
        assert field_def["type"] in {
            "string", "integer", "number", "boolean",
            "array", "enum", "datetime",
        }, f"Field '{field_name}' has invalid type: {field_def['type']}"
        assert "description" in field_def, f"Field '{field_name}' missing 'description'"
        assert "search" in field_def, f"Field '{field_name}' missing 'search'"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_call_structured_output_records_extraction(test_session):
    """Call _call_structured_output with the records response schema.

    This is the exact schema used by the extraction API endpoint.
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()
    result = await agent._call_structured_output(
        prompt=(
            "Extract data from this document into records matching the schema.\n\n"
            f"Document markdown:\n{SAMPLE_RESUME_TEXT}"
        ),
        system_prompt=(
            "You are a structured data extraction assistant. "
            "Extract the requested data from the provided text. "
            "Return ONLY valid JSON with shape {\"records\":[...]}."
        ),
        response_schema=RECORDS_RESPONSE_SCHEMA,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"
    assert "records" in parsed, f"Missing 'records': {parsed}"

    records = parsed["records"]
    assert isinstance(records, list), f"'records' is not a list: {type(records)}"
    assert len(records) >= 1, f"Expected >= 1 record, got {len(records)}"


# =============================================================================
# PydanticAI NativeOutput path (_run_native_structured_output)
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_native_structured_output_simple_person(test_session):
    """Test PydanticAI NativeOutput path with a simple person schema.

    This exercises the json_schema_to_pydantic conversion and PydanticAI's
    response_format + validation pipeline.
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()
    context = AgentContext(
        principal=Principal(
            sub="test-user",
            scopes=["read"],
            token="test-token",
        ),
    )
    context.response_schema = SIMPLE_PERSON_SCHEMA

    result = await agent._run_native_structured_output(
        query=(
            "Extract the person's name and email from this text:\n\n"
            f"{SAMPLE_RESUME_TEXT}"
        ),
        context=context,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"
    assert "name" in parsed, f"Missing 'name': {parsed}"
    assert "email" in parsed, f"Missing 'email': {parsed}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_native_structured_output_extraction_schema(test_session):
    """Test PydanticAI NativeOutput with the full extraction schema.

    This is the path used in production for schema building.
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()
    context = AgentContext(
        principal=Principal(
            sub="test-user",
            scopes=["read"],
            token="test-token",
        ),
    )
    context.response_schema = EXTRACTION_SCHEMA

    result = await agent._run_native_structured_output(
        query=(
            "Analyze this document and generate an extraction schema "
            "for resumes.\n\n"
            f"Document content:\n{SAMPLE_RESUME_TEXT}"
        ),
        context=context,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"
    assert "schemaName" in parsed, f"Missing 'schemaName': {parsed}"
    assert "fields" in parsed, f"Missing 'fields': {parsed}"
    assert len(parsed["fields"]) > 0, "Schema returned zero fields"


# =============================================================================
# Full agent run() path with response_schema in context
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.integration
async def test_agent_run_with_structured_output(test_session, mock_auth_context):
    """Test the complete agent.run() path with response_schema.

    Exercises the full pipeline:
    1. run() -> run_with_streaming()
    2. _setup_context() applies response_schema
    3. _execute_llm_driven() detects response_schema, calls structured output
    4. run_with_streaming() returns raw JSON (skips synthesis)
    """
    _check_llm_reachable()

    agent = _TestExtractorAgent()

    context = dict(mock_auth_context)
    context["response_schema"] = SIMPLE_PERSON_SCHEMA

    result = await agent.run(
        query=(
            "Extract the person's name and email from this text:\n\n"
            f"{SAMPLE_RESUME_TEXT}"
        ),
        context=context,
    )

    assert result is not None
    output_str = result.output if hasattr(result, "output") else str(result)
    assert len(output_str) > 0, "Agent returned empty output"

    parsed = json.loads(output_str)
    assert isinstance(parsed, dict), f"Output is not a dict: {type(parsed)}"
    assert "name" in parsed, f"Missing 'name': {parsed}"
    assert "email" in parsed, f"Missing 'email': {parsed}"


# =============================================================================
# Generate-then-extract: schema built from doc, extraction on same doc
# =============================================================================

TINY_DOC = "Name: Alice Smith\nRole: Engineer\nCity: Denver\n"

TINY_SCHEMA_OBJ = {
    "schemaName": "person",
    "displayName": "Person",
    "itemLabel": "person",
    "fields": {
        "name": {"type": "string", "description": "Full name"},
        "role": {"type": "string", "description": "Job role"},
        "city": {"type": "string", "description": "City"},
    },
}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_generate_then_extract_same_document(test_session):
    """Tiny doc + 3-field schema: prove the extract pipeline returns records."""
    _check_llm_reachable()

    from app.api.extraction import (
        _build_records_response_schema,
        _build_extraction_prompt,
        _extract_records,
    )

    response_schema = _build_records_response_schema(TINY_SCHEMA_OBJ, max_records=1)
    prompt = _build_extraction_prompt(
        schema_document_id="test-schema-001",
        file_id="test-file-001",
        schema_obj=TINY_SCHEMA_OBJ,
        markdown=TINY_DOC,
        instructions="Extract data from this document into records matching the schema.",
        compact_mode=True,
    )

    agent = _TestExtractorAgent()
    result = await agent._call_structured_output(
        prompt=prompt,
        system_prompt="You are a data extraction assistant. Return ONLY valid JSON.",
        response_schema=response_schema,
        max_tokens=1024,
    )

    parsed = json.loads(result)
    assert isinstance(parsed, dict), f"Result is not a dict: {type(parsed)}"

    records = _extract_records(parsed)
    assert len(records) >= 1, (
        f"Expected at least 1 record, got {len(records)}. "
        f"Output: {json.dumps(parsed, indent=2)[:300]}"
    )

    rec = records[0]
    assert rec.get("name"), f"Missing 'name' in record: {list(rec.keys())}"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_generate_then_extract_via_agent_run(test_session, mock_auth_context):
    """Same tiny test but via agent.run() — the full production code path."""
    _check_llm_reachable()

    from app.api.extraction import (
        _build_records_response_schema,
        _build_extraction_prompt,
        _extract_records,
    )

    response_schema = _build_records_response_schema(TINY_SCHEMA_OBJ, max_records=1)
    prompt = _build_extraction_prompt(
        schema_document_id="test-schema-001",
        file_id="test-file-001",
        schema_obj=TINY_SCHEMA_OBJ,
        markdown=TINY_DOC,
        instructions="Extract data from this document into records matching the schema.",
        compact_mode=True,
    )

    agent = _TestExtractorAgent()
    context = dict(mock_auth_context)
    context["response_schema"] = response_schema
    context["max_tokens"] = 1024

    result = await agent.run(query=prompt, context=context)

    assert result is not None
    output_str = result.output if hasattr(result, "output") else str(result)
    assert len(output_str) > 0, "Agent returned empty output"

    parsed = json.loads(output_str)
    assert isinstance(parsed, dict), f"Output is not a dict: {type(parsed)}"

    records = _extract_records(parsed)
    assert len(records) >= 1, (
        f"Expected at least 1 record via agent.run(), got {len(records)}. "
        f"Output: {json.dumps(parsed, indent=2)[:300]}"
    )

    rec = records[0]
    assert rec.get("name"), f"Missing 'name' in record: {list(rec.keys())}"
