"""
Structured extraction API.

Runs an agent against a document + schema and stores extracted records
into the target data document with provenance metadata.

Extraction is asynchronous: the POST endpoint kicks off a background task
and returns a task ID immediately. Clients poll GET /extract/status/{task_id}
or watch the ``extraction.status`` field on file metadata for completion.
"""

import copy
import json
import logging
import math
import asyncio
import re
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.auth.tokens import validate_bearer
from app.auth.tokens import get_service_token
from app.clients.busibox import BusiboxClient
from app.db.session import SessionLocal
from app.schemas.auth import Principal
from app.services.run_service import create_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extract", tags=["extract"])

# In-memory task tracker for background extractions.
# Keys are task_id (str), values are dicts with status/result/error.
_extraction_tasks: Dict[str, Dict[str, Any]] = {}

# Three-tier progressive extraction thresholds
TIER1_TOKEN_THRESHOLD = 12_000     # below this: single-shot direct extraction
TIER2_TOKEN_THRESHOLD = 100_000    # below this: RAG-guided, above: chunk sweep
CHUNK_WINDOW_TOKENS = 8_000        # target tokens per chunk window (tier 3)
CHUNK_OVERLAP_TOKENS = 500         # overlap between adjacent windows (tier 3)
MAX_PARALLEL_CHUNK_RUNS = 4        # parallel LLM calls for tier 3
MAX_RECORDS_PER_CHUNK = 5          # max records the LLM can return per chunk
FIELD_SEARCH_TOP_K = 6             # top-K chunks per field for RAG retrieval
RAG_CONTEXT_MAX_TOKENS = 16_000    # max assembled context for tier 2

# Built-in agent IDs used for extraction routing decisions.
SCHEMA_BUILDER_AGENT_ID = str(
    uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.schema-builder")
)
DEFAULT_EXTRACTION_AGENT_ID = os.getenv("DEFAULT_EXTRACTION_AGENT_ID") or str(
    uuid.uuid5(uuid.NAMESPACE_DNS, "busibox.builtin.record-extractor")
)


class ExtractRequest(BaseModel):
    file_id: str = Field(..., description="Source document file UUID")
    schema_document_id: str = Field(..., description="Schema data document UUID")
    agent_id: Optional[str] = Field(default=None, description="Extraction agent UUID")
    store_results: bool = Field(default=True, description="Persist extracted records")
    prompt_override: Optional[str] = Field(default=None, description="Optional custom extraction instructions")
    user_id: Optional[str] = Field(default=None, description="User ID for delegation-token mode")
    delegation_token: Optional[str] = Field(default=None, description="Delegation token for internal calls")
    record_defaults: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Key-value pairs merged into every extracted record (e.g. campaignId, stage from trigger config)",
    )


def _parse_json_text(text: str) -> Optional[Dict[str, Any]]:
    def _coerce_payload(payload: Any) -> Optional[Dict[str, Any]]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {"records": payload}
        return None

    def _attempt_json_load(candidate: str) -> Optional[Dict[str, Any]]:
        try:
            payload = json.loads(candidate)
            return _coerce_payload(payload)
        except Exception:
            return None

    def _repair_truncated_json(candidate: str) -> Optional[Dict[str, Any]]:
        """Best-effort fix for cut-off JSON by balancing quotes/brackets."""
        s = candidate.strip()
        if not s:
            return None

        # If quoted string appears cut off, close final quote.
        unescaped_quote_count = 0
        escaped = False
        for ch in s:
            if escaped:
                escaped = False
                continue
            if ch == "\\":
                escaped = True
                continue
            if ch == '"':
                unescaped_quote_count += 1
        if unescaped_quote_count % 2 == 1:
            s += '"'

        # Balance open containers.
        stack: List[str] = []
        in_string = False
        escape = False
        for ch in s:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch == "}":
                if stack and stack[-1] == "{":
                    stack.pop()
            elif ch == "]":
                if stack and stack[-1] == "[":
                    stack.pop()

        closers = []
        while stack:
            opener = stack.pop()
            closers.append("}" if opener == "{" else "]")
        repaired = s + "".join(closers)
        return _attempt_json_load(repaired)

    text = text.strip()
    if not text:
        return None
    direct = _attempt_json_load(text)
    if direct:
        return direct

    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\}|\[[\s\S]*\])\s*```", text)
    if fence_match:
        parsed = _attempt_json_load(fence_match.group(1))
        if parsed:
            return parsed

    # Handle truncated fenced blocks without closing ```
    fence_start = text.lower().find("```json")
    if fence_start != -1:
        maybe_json = text[fence_start + len("```json") :].strip()
        repaired = _repair_truncated_json(maybe_json)
        if repaired:
            return repaired

    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        sliced = _attempt_json_load(text[first_brace:last_brace + 1])
        if sliced:
            return sliced

    # Best-effort: find first balanced JSON object in noisy text.
    start = text.find("{")
    if start != -1:
        depth = 0
        in_string = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    parsed = _attempt_json_load(candidate)
                    if parsed:
                        return parsed
                    else:
                        break

    # Last resort: attempt repair from first brace onward.
    if start != -1:
        repaired = _repair_truncated_json(text[start:])
        if repaired:
            return repaired

    return None


def _extract_records(output: Dict[str, Any]) -> List[Dict[str, Any]]:
    output_keys = list(output.keys()) if isinstance(output, dict) else []
    logger.debug("_extract_records called", extra={
        "output_keys": output_keys,
        "output_type": type(output).__name__,
    })

    if isinstance(output.get("records"), list):
        recs = [r for r in output["records"] if isinstance(r, dict)]
        logger.debug("_extract_records: matched top-level records", extra={"count": len(recs)})
        return recs

    data_obj = output.get("data")
    if isinstance(data_obj, dict) and isinstance(data_obj.get("records"), list):
        recs = [r for r in data_obj["records"] if isinstance(r, dict)]
        logger.debug("_extract_records: matched data.records", extra={"count": len(recs)})
        return recs
    if isinstance(data_obj, list):
        recs = [r for r in data_obj if isinstance(r, dict)]
        if recs:
            logger.debug("_extract_records: matched data as list", extra={"count": len(recs)})
            return recs

    result_obj = output.get("result")
    if isinstance(result_obj, dict):
        if isinstance(result_obj.get("records"), list):
            recs = [r for r in result_obj["records"] if isinstance(r, dict)]
            logger.debug("_extract_records: matched result.records (dict)", extra={"count": len(recs)})
            return recs
        keys = [k for k in result_obj if k not in ("_provenance", "records")]
        if keys:
            logger.debug("_extract_records: treating result dict as single record", extra={"keys": keys})
            return [result_obj]

    if isinstance(result_obj, str):
        logger.debug("_extract_records: result is string", extra={"result_len": len(result_obj), "preview": result_obj[:200]})
        parsed = _parse_json_text(result_obj)
        if parsed and isinstance(parsed.get("records"), list):
            recs = [r for r in parsed["records"] if isinstance(r, dict)]
            logger.debug("_extract_records: matched parsed result.records", extra={"count": len(recs)})
            return recs
        if parsed and isinstance(parsed.get("record"), dict):
            logger.debug("_extract_records: matched parsed result.record (singular)")
            return [parsed["record"]]
        if parsed:
            keys = [k for k in parsed if k not in ("_provenance", "records")]
            if keys:
                logger.debug("_extract_records: treating parsed result as single record", extra={"keys": keys})
                return [parsed]

    for key, val in output.items():
        if isinstance(val, str) and val.strip().startswith(("{", "[")):
            parsed = _parse_json_text(val)
            if parsed and isinstance(parsed.get("records"), list):
                recs = [r for r in parsed["records"] if isinstance(r, dict)]
                if recs:
                    logger.debug("_extract_records: matched via last-resort string parse", extra={"key": key, "count": len(recs)})
                    return recs

    logger.warning("_extract_records: no records found in output", extra={"output_keys": output_keys})
    return []


def _normalize_for_matching(text: str) -> Tuple[str, List[int]]:
    """
    Normalize text for resilient matching and keep a map back to original indices.
    - lowercases
    - treats punctuation/whitespace as separators
    - collapses repeated separators
    """
    normalized_chars: List[str] = []
    index_map: List[int] = []
    last_was_sep = False

    for idx, ch in enumerate(text):
        if ch.isalnum():
            normalized_chars.append(ch.lower())
            index_map.append(idx)
            last_was_sep = False
            continue

        if not last_was_sep and normalized_chars:
            normalized_chars.append(" ")
            index_map.append(idx)
            last_was_sep = True

    # Trim trailing separator from normalized view
    if normalized_chars and normalized_chars[-1] == " ":
        normalized_chars.pop()
        index_map.pop()

    return "".join(normalized_chars), index_map


def _find_provenance_candidates(markdown: str, value: Any, max_matches: int = 5) -> List[Dict[str, Any]]:
    if value is None:
        return []
    needle_raw = str(value).strip()
    if len(needle_raw) < 2:
        return []

    candidates: List[Dict[str, Any]] = []
    haystack_lower = markdown.lower()
    needle_lower = needle_raw.lower()

    # Pass 1: direct case-insensitive substring match.
    start = 0
    while len(candidates) < max_matches:
        idx = haystack_lower.find(needle_lower, start)
        if idx == -1:
            break
        snippet = markdown[idx : idx + len(needle_raw)]
        candidates.append(
            {
                "text": snippet,
                "charOffset": idx,
                "charLength": len(snippet),
            }
        )
        start = idx + max(1, len(needle_raw))

    if candidates:
        return candidates

    # Pass 2: normalized matching (ignores punctuation/spacing differences).
    normalized_haystack, haystack_map = _normalize_for_matching(markdown)
    normalized_needle, _ = _normalize_for_matching(needle_raw)
    if len(normalized_needle) < 2:
        return []

    norm_start = 0
    while len(candidates) < max_matches:
        norm_idx = normalized_haystack.find(normalized_needle, norm_start)
        if norm_idx == -1:
            break

        if norm_idx >= len(haystack_map):
            break
        norm_end = norm_idx + len(normalized_needle) - 1
        if norm_end >= len(haystack_map):
            break

        orig_start = haystack_map[norm_idx]
        orig_end = haystack_map[norm_end]
        if orig_end < orig_start:
            norm_start = norm_idx + 1
            continue

        snippet = markdown[orig_start : orig_end + 1]
        candidates.append(
            {
                "text": snippet,
                "charOffset": orig_start,
                "charLength": max(1, len(snippet)),
            }
        )
        norm_start = norm_idx + max(1, len(normalized_needle))

    # Deduplicate by offset/length.
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for cand in candidates:
        key = (cand.get("charOffset"), cand.get("charLength"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cand)
    return deduped


def _build_value_provenance(value: Any, markdown: str) -> Any:
    """
    Build provenance in a shape that mirrors the record value:
    - scalar -> provenance node or {"candidates": [...]}
    - list -> list of per-item provenance
    - object -> dict of per-key provenance
    """
    if value is None:
        return None

    if isinstance(value, (str, int, float, bool)):
        candidates = _find_provenance_candidates(markdown, value)
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        return {"candidates": candidates}

    if isinstance(value, list):
        items = [_build_value_provenance(item, markdown) for item in value]
        return items if any(item is not None for item in items) else None

    if isinstance(value, dict):
        obj: Dict[str, Any] = {}
        for key, child in value.items():
            child_prov = _build_value_provenance(child, markdown)
            if child_prov is not None:
                obj[str(key)] = child_prov
        return obj or None

    # Fallback for uncommon types
    candidates = _find_provenance_candidates(markdown, str(value))
    if not candidates:
        return None
    return candidates[0] if len(candidates) == 1 else {"candidates": candidates}


def _populate_provenance_from_markdown(
    records: List[Dict[str, Any]],
    markdown: str,
    schema: Dict[str, Any],
) -> None:
    fields = schema.get("fields", {}) if isinstance(schema, dict) else {}
    field_names = list(fields.keys()) if isinstance(fields, dict) else []

    for row in records:
        existing = row.get("_provenance")
        provenance: Dict[str, Any] = dict(existing) if isinstance(existing, dict) else {}
        for field_name in field_names:
            if field_name not in row:
                continue
            existing_field = provenance.get(field_name)
            if existing_field not in (None, {}, []):
                continue
            generated = _build_value_provenance(row.get(field_name), markdown)
            if generated is not None:
                provenance[field_name] = generated
        row["_provenance"] = provenance


def _merge_provenance(existing: Any, incoming: Any) -> Any:
    if incoming in (None, {}, []):
        return existing
    if existing in (None, {}, []):
        return incoming

    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(existing)
        for key, value in incoming.items():
            merged[key] = _merge_provenance(merged.get(key), value)
        return merged

    if isinstance(existing, list) and isinstance(incoming, list):
        max_len = max(len(existing), len(incoming))
        merged_list: List[Any] = []
        for i in range(max_len):
            ex = existing[i] if i < len(existing) else None
            inc = incoming[i] if i < len(incoming) else None
            merged_list.append(_merge_provenance(ex, inc))
        return merged_list

    return existing


def _map_field_type_to_json_schema(field_def: Dict[str, Any]) -> Dict[str, Any]:
    field_type = str(field_def.get("type", "string"))
    if field_type == "integer":
        return {"type": "integer"}
    if field_type == "number":
        return {"type": "number"}
    if field_type == "boolean":
        return {"type": "boolean"}
    if field_type == "datetime":
        return {"type": "string", "maxLength": 100}
    if field_type == "enum":
        values = field_def.get("values")
        if isinstance(values, list) and values:
            return {"type": "string", "enum": [str(v) for v in values]}
        return {"type": "string", "maxLength": 120}
    if field_type == "array":
        items_def = field_def.get("items")
        if isinstance(items_def, dict):
            item_schema = _map_field_type_to_json_schema(items_def)
            item_type = str(item_schema.get("type", "string"))
            if item_type in ("integer", "number", "boolean"):
                return {"type": "array", "maxItems": 25, "items": item_schema}
            if item_type == "object":
                return {"type": "array", "maxItems": 10, "items": item_schema}
            return {
                "type": "array",
                "maxItems": 20,
                "items": item_schema,
            }
        return {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 200}}
    if field_type == "object":
        properties_def = field_def.get("properties")
        if isinstance(properties_def, dict) and properties_def:
            properties: Dict[str, Any] = {}
            required: List[str] = []
            for prop_name, prop_def in properties_def.items():
                if not isinstance(prop_name, str) or not isinstance(prop_def, dict):
                    continue
                properties[prop_name] = _map_field_type_to_json_schema(prop_def)
                if bool(prop_def.get("required")):
                    required.append(prop_name)

            object_schema: Dict[str, Any] = {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
            }
            if required:
                object_schema["required"] = required
            return object_schema
        return {"type": "object", "additionalProperties": True}
    # default/string
    return {"type": "string", "maxLength": 500}


def _get_app_only_fields(schema_obj: Dict[str, Any]) -> List[str]:
    """Return field names marked ``appOnly: true`` in the schema."""
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    if not isinstance(fields, dict):
        return []
    return [
        name
        for name, defn in fields.items()
        if isinstance(defn, dict) and defn.get("appOnly")
    ]




def _build_records_response_schema(
    schema_obj: Dict[str, Any],
    max_records: Optional[int] = None,
    field_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build the JSON Schema that constrains the LLM's structured output.

    Args:
        schema_obj: The full extraction schema (with ``fields``, etc.).
        max_records: Override for the maximum number of records.  When *None*
            the value is read from ``schema_obj.get("maxRecords", 5)``.
        field_names: If given, only include these fields in the per-record
            schema (used by grouped / retry extraction).
    """
    if max_records is None:
        max_records = int(schema_obj.get("maxRecords", 5)) if isinstance(schema_obj, dict) else 5

    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    record_properties: Dict[str, Any] = {}

    if isinstance(fields, dict):
        for field_name, field_def in fields.items():
            if not isinstance(field_name, str) or not isinstance(field_def, dict):
                continue
            if field_def.get("appOnly"):
                continue
            if field_names is not None and field_name not in field_names:
                continue
            record_properties[field_name] = _map_field_type_to_json_schema(field_def)

    record_schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": False,
        "properties": record_properties,
    }

    return {
        "name": "extraction_records",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["records"],
            "properties": {
                "records": {
                    "type": "array",
                    "maxItems": max(1, max_records),
                    "items": record_schema,
                }
            },
        },
    }


def _get_max_records(schema_obj: Dict[str, Any]) -> int:
    """Read the expected record cardinality from the schema."""
    if isinstance(schema_obj, dict):
        return int(schema_obj.get("maxRecords", 5))
    return 5


# ---------------------------------------------------------------------------
# Field grouping for tier-2 grouped extraction
# ---------------------------------------------------------------------------
MAX_SCALAR_GROUP_SIZE = 5  # max simple fields per extraction group


def _group_fields_for_extraction(
    schema_obj: Dict[str, Any],
) -> List[List[str]]:
    """Group non-appOnly fields into extraction batches.

    Heuristic:
    * Array-of-object fields get their own group (they are large/complex).
    * Remaining fields are batched by ``display_order`` proximity, with up
      to ``MAX_SCALAR_GROUP_SIZE`` simple fields per group.
    """
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    if not isinstance(fields, dict):
        return []

    complex_groups: List[List[str]] = []
    simple_fields: List[Tuple[int, str]] = []

    for name, fdef in fields.items():
        if not isinstance(fdef, dict) or fdef.get("appOnly"):
            continue
        ftype = fdef.get("type", "string")
        items_def = fdef.get("items", {}) if isinstance(fdef.get("items"), dict) else {}
        is_complex_array = ftype == "array" and items_def.get("type") == "object"

        if is_complex_array:
            complex_groups.append([name])
        else:
            order = int(fdef.get("display_order", 999))
            simple_fields.append((order, name))

    simple_fields.sort()

    groups: List[List[str]] = []
    current_group: List[str] = []
    for _, name in simple_fields:
        current_group.append(name)
        if len(current_group) >= MAX_SCALAR_GROUP_SIZE:
            groups.append(current_group)
            current_group = []
    if current_group:
        groups.append(current_group)

    return groups + complex_groups


def _identify_missing_fields(
    record: Dict[str, Any],
    schema_obj: Dict[str, Any],
) -> List[str]:
    """Return field names that are null / absent in *record* but defined in the schema."""
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    missing: List[str] = []
    for name, fdef in fields.items():
        if not isinstance(fdef, dict) or fdef.get("appOnly"):
            continue
        val = record.get(name)
        if val is None:
            missing.append(name)
        elif isinstance(val, (list, dict)) and not val:
            missing.append(name)
    return missing


def _clean_markdown_for_extraction(markdown: str) -> str:
    """Strip noise from markdown before sending to the LLM for extraction.

    Removes picture placeholders, OCR picture-text blocks, and collapses
    excessive blank lines.  The original markdown should be kept separately
    for provenance text-matching (which needs exact char offsets).
    """
    # Remove picture placeholders: **==> picture ... <==**
    cleaned = re.sub(r"\*\*==>.*?<==\*\*", "", markdown)

    # Remove picture-text blocks (start marker -> content -> end marker)
    cleaned = re.sub(
        r"\*\*-{3,}\s*Start of picture text\s*-{3,}\*\*.*?\*\*-{3,}\s*End of picture text\s*-{3,}\*\*",
        "",
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Collapse 3+ consecutive blank lines to 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def _estimate_markdown_tokens(markdown: str) -> int:
    # Rough token estimate: 1 token ~= 4 characters for mixed prose/JSON.
    return max(1, int(math.ceil(len(markdown) / 4)))


def _select_extraction_tier(markdown_tokens: int) -> str:
    """Return ``"direct"``, ``"rag"``, or ``"chunk_sweep"`` based on doc size."""
    if markdown_tokens < TIER1_TOKEN_THRESHOLD:
        return "direct"
    if markdown_tokens < TIER2_TOKEN_THRESHOLD:
        return "rag"
    return "chunk_sweep"


def _relax_schema_for_chunked(
    schema_obj: Dict[str, Any],
    response_schema: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Clone *schema_obj* and *response_schema*, stripping ``required`` markers.

    Used by Tiers 2 and 3 where the LLM only sees partial document content
    and cannot be expected to populate every field.
    """
    relaxed_schema = copy.deepcopy(schema_obj)
    fields = relaxed_schema.get("fields")
    if isinstance(fields, dict):
        for field_def in fields.values():
            if isinstance(field_def, dict):
                field_def.pop("required", None)

    relaxed_response = copy.deepcopy(response_schema)
    inner = relaxed_response.get("schema", {})
    records_def = (inner.get("properties") or {}).get("records", {})
    items = records_def.get("items")
    if isinstance(items, dict):
        items.pop("required", None)

    return relaxed_schema, relaxed_response


def _offset_correct_provenance(
    records: List[Dict[str, Any]],
    window_char_offset: int,
) -> None:
    """Shift every ``charOffset`` in ``_provenance`` by *window_char_offset*."""
    if window_char_offset == 0:
        return

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            if "charOffset" in node and isinstance(node["charOffset"], (int, float)):
                node["charOffset"] = int(node["charOffset"]) + window_char_offset
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    for record in records:
        prov = record.get("_provenance")
        if prov:
            _walk(prov)


def _identify_field(schema_obj: Dict[str, Any]) -> Optional[str]:
    """
    Find the best identity field for multi-record grouping.

    Prefers the first required string field with keyword search, then falls
    back to the field with the lowest ``display_order``.
    """
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    if not isinstance(fields, dict):
        return None

    best_keyword: Optional[str] = None
    best_order_name: Optional[str] = None
    best_order_val: Optional[int] = None

    for name, fdef in fields.items():
        if not isinstance(fdef, dict):
            continue
        ftype = fdef.get("type", "string")
        if ftype != "string":
            continue

        search = fdef.get("search", [])
        is_keyword = isinstance(search, list) and "keyword" in search
        is_required = bool(fdef.get("required"))
        order = fdef.get("display_order")

        if is_required and is_keyword and best_keyword is None:
            best_keyword = name

        if order is not None:
            try:
                order_int = int(order)
            except (ValueError, TypeError):
                continue
            if best_order_val is None or order_int < best_order_val:
                best_order_val = order_int
                best_order_name = name

    return best_keyword or best_order_name


def _fuzzy_match(a: str, b: str) -> bool:
    """Case-insensitive containment check for grouping records."""
    a_lower = a.strip().lower()
    b_lower = b.strip().lower()
    if not a_lower or not b_lower:
        return False
    return a_lower in b_lower or b_lower in a_lower


def _group_partial_records(
    records: List[Dict[str, Any]],
    identity_field: Optional[str],
) -> List[List[Dict[str, Any]]]:
    """
    Group partial records by *identity_field* using fuzzy matching.

    Returns a list of groups; each group is a list of partial records that
    belong to the same logical entity.
    """
    if not records:
        return []
    if not identity_field:
        return [records]

    groups: List[List[Dict[str, Any]]] = []
    group_keys: List[str] = []

    for rec in records:
        val = rec.get(identity_field)
        if not isinstance(val, str) or not val.strip():
            if groups:
                groups[0].append(rec)
            else:
                groups.append([rec])
                group_keys.append("")
            continue

        matched = False
        for idx, gk in enumerate(group_keys):
            if gk and _fuzzy_match(val, gk):
                groups[idx].append(rec)
                matched = True
                break

        if not matched:
            groups.append([rec])
            group_keys.append(val.strip())

    return groups


def _merge_partial_group(
    group: List[Dict[str, Any]],
    schema_obj: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge a group of partial records into one, collecting conflicting
    scalar values as lists for later LLM reconciliation.
    """
    if len(group) == 1:
        return group[0]

    merged: Dict[str, Any] = {}
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}

    for record in group:
        for key, value in record.items():
            if key == "_provenance":
                merged["_provenance"] = _merge_provenance(merged.get("_provenance"), value)
                continue
            if value is None:
                continue

            field_def = fields.get(key, {}) if isinstance(fields, dict) else {}
            field_type = field_def.get("type") if isinstance(field_def, dict) else None

            if field_type == "array" and isinstance(value, list):
                existing = merged.get(key, [])
                if not isinstance(existing, list):
                    existing = [existing] if existing is not None else []
                for item in value:
                    if item not in existing:
                        existing.append(item)
                max_items = int(field_def.get("maxItems", 50) or 50) if isinstance(field_def, dict) else 50
                merged[key] = existing[:max_items]
            elif key in merged:
                existing = merged[key]
                if existing != value:
                    if isinstance(existing, list):
                        if value not in existing:
                            existing.append(value)
                    else:
                        merged[key] = [existing, value]
            else:
                merged[key] = value

    return merged


async def _reconcile_conflicts(
    record: Dict[str, Any],
    schema_obj: Dict[str, Any],
    principal: "Principal",
    agent_uuid: "uuid.UUID",
) -> Dict[str, Any]:
    """
    Use a lightweight LLM call to resolve scalar fields that have
    multiple conflicting candidate values after merge.
    """
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    conflicts: Dict[str, List[Any]] = {}
    for key, value in record.items():
        if key.startswith("_"):
            continue
        field_def = fields.get(key, {}) if isinstance(fields, dict) else {}
        field_type = field_def.get("type") if isinstance(field_def, dict) else None
        if field_type == "array":
            continue
        if isinstance(value, list) and len(value) > 1:
            conflicts[key] = value

    if not conflicts:
        return record

    conflict_desc_parts = []
    for field_name, candidates in conflicts.items():
        desc = (fields.get(field_name, {}) or {}).get("description", "")
        conflict_desc_parts.append(
            f"Field '{field_name}' (description: {desc}): "
            f"candidates = {json.dumps(candidates, default=str)}"
        )

    prompt = (
        "You are resolving conflicting values extracted from different parts "
        "of the same document. For each field below, select the most accurate "
        "single value or synthesize a combined answer.\n\n"
        + "\n".join(conflict_desc_parts)
        + "\n\nReturn ONLY valid JSON with the resolved field values."
    )

    resolution_props = {}
    for field_name in conflicts:
        resolution_props[field_name] = _map_field_type_to_json_schema(
            fields.get(field_name, {}) if isinstance(fields, dict) else {}
        )

    resolution_schema = {
        "name": "conflict_resolution",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": list(conflicts.keys()),
            "properties": resolution_props,
        },
    }

    try:
        async with SessionLocal() as session:
            reconcile_run = await create_run(
                session=session,
                principal=principal,
                agent_id=agent_uuid,
                payload={
                    "prompt": prompt,
                    "response_schema": resolution_schema,
                    "max_tokens": 2048,
                },
                scopes=["agent.execute"],
                purpose="extraction-conflict-resolution",
                agent_tier="simple",
            )
        if reconcile_run.status == "succeeded":
            resolved = _extract_records(reconcile_run.output or {})
            if resolved:
                for key in conflicts:
                    val = resolved[0].get(key)
                    if val is not None:
                        record[key] = val
            else:
                raw_output = reconcile_run.output or {}
                result_str = raw_output.get("result", "")
                if isinstance(result_str, str):
                    try:
                        parsed = json.loads(result_str)
                        if isinstance(parsed, dict):
                            for key in conflicts:
                                val = parsed.get(key)
                                if val is not None:
                                    record[key] = val
                    except (json.JSONDecodeError, TypeError):
                        pass
    except Exception as exc:
        logger.warning(
            "LLM reconciliation failed, using first-value fallback",
            extra={"error": str(exc)},
        )

    for key, value in record.items():
        if isinstance(value, list) and key not in (
            k for k, fd in fields.items()
            if isinstance(fd, dict) and fd.get("type") == "array"
        ):
            record[key] = value[0]

    return record


async def _merge_multi_record_results(
    all_records: List[Dict[str, Any]],
    schema_obj: Dict[str, Any],
    principal: "Principal",
    agent_uuid: "uuid.UUID",
) -> List[Dict[str, Any]]:
    """
    Two-phase merge for multi-chunk extraction results.

    Phase A: deterministic grouping by identity field + structural merge.
    Phase B: LLM reconciliation for conflicting scalar values.
    """
    if not all_records:
        return []
    if len(all_records) == 1:
        return all_records

    identity_field = _identify_field(schema_obj)
    logger.info(
        "Multi-record merge starting",
        extra={
            "total_partials": len(all_records),
            "identity_field": identity_field,
        },
    )

    groups = _group_partial_records(all_records, identity_field)
    merged_records = [_merge_partial_group(g, schema_obj) for g in groups]

    reconciled = []
    for rec in merged_records:
        reconciled.append(
            await _reconcile_conflicts(rec, schema_obj, principal, agent_uuid)
        )

    return reconciled


def _build_field_query(field_name: str, field_def: Dict[str, Any], schema_name: Optional[str]) -> str:
    description = str(field_def.get("description", "") or "").strip()
    field_type = str(field_def.get("type", "string"))
    schema_part = f" in {schema_name}" if schema_name else ""
    return (
        f"Extract evidence for field '{field_name}'{schema_part}. "
        f"Description: {description}. Type: {field_type}."
    )


def _select_top_chunks(results: List[Dict[str, Any]], limit: int = FIELD_SEARCH_TOP_K) -> str:
    top = results[:limit]
    snippets: List[str] = []
    for idx, item in enumerate(top, 1):
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        chunk_idx = item.get("chunk_index")
        page_no = item.get("page_number")
        source_meta = []
        if chunk_idx is not None:
            source_meta.append(f"chunk {chunk_idx}")
        if page_no is not None:
            source_meta.append(f"page {page_no}")
        source = ", ".join(source_meta) if source_meta else "chunk"
        snippets.append(f"[{idx}] ({source}) {text}")
    return "\n\n".join(snippets)


async def _search_context_for_field(
    *,
    search_client: BusiboxClient,
    file_id: str,
    field_name: str,
    field_def: Dict[str, Any],
    schema_name: Optional[str],
) -> str:
    query = _build_field_query(field_name, field_def, schema_name)
    try:
        response = await search_client.search(
            query=query,
            top_k=FIELD_SEARCH_TOP_K,
            mode="hybrid",
            file_ids=[file_id],
            rerank=True,
        )
        results = response.get("results", []) if isinstance(response, dict) else []
        return _select_top_chunks(results if isinstance(results, list) else [])
    except Exception:
        return ""


def _build_extraction_prompt(
    *,
    schema_document_id: str,
    file_id: str,
    schema_obj: Dict[str, Any],
    markdown: str,
    instructions: str,
    compact_mode: bool,
    max_records: int = 5,
) -> str:
    cardinality_hint = ""
    if max_records == 1:
        cardinality_hint = (
            "This document describes exactly ONE entity. "
            "Return exactly one record in the records array. "
        )

    mode_instructions = (
        "Return ONLY valid JSON with shape {\"records\":[...]} and no markdown/prose. "
        f"{cardinality_hint}"
        "If a field is not present in the document, omit it or set null — do NOT return an empty records array just because some fields are missing. "
        "Always extract at least one record if the document contains ANY relevant data. "
        "Ignore the 'required' flags in the schema; extract whatever you can find. "
        "Do not invent data. "
        "Do not produce exhaustive lists; include only the most salient values. "
        "For array fields, include at most 25 items (compact mode) "
        "to avoid overly large responses."
        if compact_mode
        else (
            "Return strict JSON with shape {\"records\":[...]} and no markdown/prose. "
            f"{cardinality_hint}"
            "If a field is not present in the document, omit it or set null — do NOT return an empty records array just because some fields are missing. "
            "Always extract at least one record if the document contains ANY relevant data. "
            "Ignore the 'required' flags in the schema; extract whatever you can find. "
            "Do not produce exhaustive lists; include only the most salient values "
            "(typically <= 12 items for list fields)."
        )
    )
    return (
        f"{instructions}\n\n"
        f"{mode_instructions}\n\n"
        f"Schema document ID: {schema_document_id}\n"
        f"Source file ID: {file_id}\n\n"
        f"Schema JSON:\n```json\n{json.dumps(schema_obj, indent=2)}\n```\n\n"
        f"Document markdown:\n{markdown}"
    )


def _validate_and_enrich_records(
    records: List[Dict[str, Any]],
    schema: Dict[str, Any],
    file_id: str,
    agent_id: str,
) -> List[Dict[str, Any]]:
    fields = schema.get("fields", {}) if isinstance(schema, dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    validated: List[Dict[str, Any]] = []
    active_coercions: List[Dict[str, Any]] = []

    def _coerce_bool(value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"true", "1", "yes", "y", "on"}:
                return True
            if v in {"false", "0", "no", "n", "off"}:
                return False
        return value

    def _coerce_to_field_type(field_name: str, value: Any, field_def: Dict[str, Any]) -> Any:
        def _type_name(raw_value: Any) -> str:
            if raw_value is None:
                return "null"
            if isinstance(raw_value, list):
                return "array"
            if isinstance(raw_value, dict):
                return "object"
            return type(raw_value).__name__

        def _note_coercion(path: str, expected_type: str, original_value: Any, coerced_value: Any, reason: str) -> None:
            def _preview(raw_value: Any) -> Any:
                if raw_value is None:
                    return None
                if isinstance(raw_value, (int, float, bool)):
                    return raw_value
                if isinstance(raw_value, str):
                    return raw_value[:200]
                if isinstance(raw_value, list):
                    return f"list(len={len(raw_value)})"
                if isinstance(raw_value, dict):
                    return f"object(keys={list(raw_value.keys())[:12]})"
                return str(raw_value)[:200]

            active_coercions.append(
                {
                    "field": path,
                    "expectedType": expected_type,
                    "fromType": _type_name(original_value),
                    "toType": _type_name(coerced_value),
                    "reason": reason,
                    "fromPreview": _preview(original_value),
                    "toPreview": _preview(coerced_value),
                }
            )

        def _to_string(raw_value: Any) -> str:
            if isinstance(raw_value, str):
                return raw_value
            if isinstance(raw_value, (dict, list)):
                return json.dumps(raw_value, ensure_ascii=False)
            return str(raw_value)

        def _flatten_to_array_items(raw_value: Any) -> List[Any]:
            if isinstance(raw_value, list):
                return raw_value

            if isinstance(raw_value, dict):
                flattened: List[Any] = []
                for key, nested_value in raw_value.items():
                    if isinstance(nested_value, list):
                        for item in nested_value:
                            flattened.append(f"{key}: {item}" if not isinstance(item, dict) else item)
                    elif isinstance(nested_value, dict):
                        flattened.append(json.dumps({key: nested_value}, ensure_ascii=False))
                    elif nested_value is None:
                        continue
                    else:
                        flattened.append(f"{key}: {nested_value}")
                return flattened

            if isinstance(raw_value, str):
                try:
                    parsed = json.loads(raw_value)
                    if isinstance(parsed, (list, dict)):
                        return _flatten_to_array_items(parsed)
                except Exception:
                    pass
                # Fallback for plain delimited text
                if "," in raw_value:
                    return [part.strip() for part in raw_value.split(",") if part.strip()]
                if "\n" in raw_value:
                    return [part.strip() for part in raw_value.splitlines() if part.strip()]
                trimmed = raw_value.strip()
                return [trimmed] if trimmed else []

            if raw_value is None:
                return []
            return [raw_value]

        def _coerce_object_value(raw_value: Any, object_def: Dict[str, Any]) -> Dict[str, Any]:
            properties = object_def.get("properties", {}) if isinstance(object_def.get("properties"), dict) else {}
            property_names = [name for name in properties.keys() if isinstance(name, str)]

            if isinstance(raw_value, dict):
                candidate: Dict[str, Any] = dict(raw_value)
            elif isinstance(raw_value, list):
                candidate = {}
                # Merge dict-like list items first.
                for item in raw_value:
                    if isinstance(item, dict):
                        candidate.update(item)

                # Map scalar list items into known properties by order when available.
                scalar_items = [item for item in raw_value if not isinstance(item, dict)]
                if property_names:
                    for idx, item in enumerate(scalar_items):
                        if idx >= len(property_names):
                            break
                        key = property_names[idx]
                        if key not in candidate:
                            candidate[key] = item
                elif scalar_items:
                    candidate["items"] = scalar_items
                _note_coercion(field_name, "object", raw_value, candidate, "array_to_object")
            elif isinstance(raw_value, str):
                trimmed = raw_value.strip()
                parsed: Any = None
                if trimmed:
                    try:
                        parsed = json.loads(trimmed)
                    except Exception:
                        parsed = None

                if isinstance(parsed, dict):
                    candidate = dict(parsed)
                elif isinstance(parsed, list):
                    candidate = _coerce_object_value(parsed, object_def)
                else:
                    if len(property_names) == 1:
                        candidate = {property_names[0]: trimmed}
                    else:
                        candidate = {"value": trimmed}
                    _note_coercion(field_name, "object", raw_value, candidate, "scalar_string_to_object")
            else:
                if len(property_names) == 1:
                    candidate = {property_names[0]: raw_value}
                else:
                    candidate = {"value": raw_value}
                _note_coercion(field_name, "object", raw_value, candidate, "scalar_to_object")

            # If object properties are defined, coerce nested values recursively and
            # ensure required nested keys exist to avoid strict schema write failures.
            if properties:
                coerced_obj: Dict[str, Any] = {}
                for prop_name, prop_def in properties.items():
                    if not isinstance(prop_name, str) or not isinstance(prop_def, dict):
                        continue
                    if prop_name in candidate and candidate.get(prop_name) is not None:
                        coerced_obj[prop_name] = _coerce_to_field_type(
                            f"{field_name}.{prop_name}",
                            candidate.get(prop_name),
                            prop_def,
                        )
                    elif bool(prop_def.get("required")):
                        coerced_obj[prop_name] = None
                        _note_coercion(
                            f"{field_name}.{prop_name}",
                            str(prop_def.get("type", "string")),
                            None,
                            None,
                            "missing_required_nested_field_defaulted_to_null",
                        )

                # Preserve additional object keys the model returned.
                for key, val in candidate.items():
                    if key not in coerced_obj:
                        coerced_obj[key] = val
                return coerced_obj

            return candidate

        if value is None:
            return None

        field_type = field_def.get("type", "string")

        if field_type == "string":
            if isinstance(value, (str, int, float, bool, dict, list)):
                coerced_string = _to_string(value)
                if not isinstance(value, str):
                    _note_coercion(field_name, "string", value, coerced_string, "value_to_string")
                return coerced_string
            raise ValueError(f"Field '{field_name}' must be a string")

        if field_type == "integer":
            if isinstance(value, bool):
                raise ValueError(f"Field '{field_name}' must be an integer")
            if isinstance(value, int):
                return value
            if isinstance(value, float) and value.is_integer():
                return int(value)
            if isinstance(value, str):
                try:
                    return int(value.strip())
                except Exception as exc:
                    raise ValueError(f"Field '{field_name}' must be an integer") from exc
            raise ValueError(f"Field '{field_name}' must be an integer")

        if field_type == "number":
            if isinstance(value, bool):
                raise ValueError(f"Field '{field_name}' must be a number")
            if isinstance(value, (int, float)):
                return value
            if isinstance(value, str):
                try:
                    return float(value.strip())
                except Exception as exc:
                    raise ValueError(f"Field '{field_name}' must be a number") from exc
            raise ValueError(f"Field '{field_name}' must be a number")

        if field_type == "boolean":
            coerced = _coerce_bool(value)
            if isinstance(coerced, bool):
                return coerced
            raise ValueError(f"Field '{field_name}' must be a boolean")

        if field_type == "array":
            if not isinstance(value, list):
                _note_coercion(field_name, "array", value, value, "non_array_input_normalized")
            coerced_items = _flatten_to_array_items(value)
            item_def = field_def.get("items", {}) if isinstance(field_def.get("items"), dict) else {}
            item_type = item_def.get("type", "string")
            normalized_items: List[Any] = []

            for item in coerced_items:
                if item is None:
                    continue

                if item_type == "string":
                    if isinstance(item, (str, int, float, bool)):
                        normalized_items.append(str(item))
                    elif isinstance(item, dict):
                        normalized_items.append(json.dumps(item, ensure_ascii=False))
                    else:
                        normalized_items.append(str(item))
                elif item_type == "integer":
                    if isinstance(item, bool):
                        continue
                    if isinstance(item, int):
                        normalized_items.append(item)
                    elif isinstance(item, float) and item.is_integer():
                        normalized_items.append(int(item))
                    elif isinstance(item, str):
                        try:
                            normalized_items.append(int(item.strip()))
                        except Exception:
                            continue
                elif item_type == "number":
                    if isinstance(item, bool):
                        continue
                    if isinstance(item, (int, float)):
                        normalized_items.append(item)
                    elif isinstance(item, str):
                        try:
                            normalized_items.append(float(item.strip()))
                        except Exception:
                            continue
                elif item_type == "boolean":
                    coerced_item = _coerce_bool(item)
                    if isinstance(coerced_item, bool):
                        normalized_items.append(coerced_item)
                elif item_type == "object":
                    if isinstance(item, dict):
                        normalized_items.append(_coerce_object_value(item, item_def))
                    elif isinstance(item, str):
                        try:
                            parsed_item = json.loads(item)
                            if isinstance(parsed_item, dict):
                                normalized_items.append(_coerce_object_value(parsed_item, item_def))
                            elif isinstance(parsed_item, list):
                                normalized_items.append(_coerce_object_value(parsed_item, item_def))
                            else:
                                normalized_items.append(_coerce_object_value(item, item_def))
                        except Exception:
                            normalized_items.append(_coerce_object_value(item, item_def))
                    elif isinstance(item, list):
                        normalized_items.append(_coerce_object_value(item, item_def))
                    else:
                        normalized_items.append(_coerce_object_value(item, item_def))
                else:
                    normalized_items.append(item)

            if isinstance(value, dict):
                _note_coercion(field_name, "array", value, normalized_items, "object_to_array")
            elif isinstance(value, str):
                _note_coercion(field_name, "array", value, normalized_items, "string_to_array")
            elif not isinstance(value, list):
                _note_coercion(field_name, "array", value, normalized_items, "scalar_to_array")
            return normalized_items

        if field_type == "object":
            if not isinstance(value, dict):
                _note_coercion(field_name, "object", value, value, "non_object_input_normalized")
            return _coerce_object_value(value, field_def)

        if field_type == "enum":
            allowed = field_def.get("values", [])
            if value in allowed:
                return value
            # Best-effort string match (common LLM output variation).
            if isinstance(value, str):
                for candidate in allowed:
                    if isinstance(candidate, str) and candidate.lower() == value.lower():
                        return candidate
            raise ValueError(f"Field '{field_name}' must be one of: {allowed}")

        # Unknown/custom field types pass through as-is.
        return value

    for record_idx, record in enumerate(records):
        active_coercions = []
        row = dict(record)
        missing_required: List[str] = []
        for field_name, field_def in fields.items():
            if not isinstance(field_def, dict):
                continue
            required = bool(field_def.get("required", False))
            current_value = row.get(field_name)

            if required and current_value is None:
                missing_required.append(field_name)
                continue

            if current_value is not None:
                try:
                    row[field_name] = _coerce_to_field_type(field_name, current_value, field_def)
                except (ValueError, TypeError) as coerce_err:
                    logger.warning(
                        "Field coercion failed, setting to null",
                        extra={
                            "record_idx": record_idx,
                            "field_name": field_name,
                            "error": str(coerce_err),
                        },
                    )
                    row[field_name] = None
                    active_coercions.append({
                        "field": field_name,
                        "reason": f"coercion_failed: {coerce_err}",
                    })
                    continue

                field_type = field_def.get("type", "string")
                if field_type in ("integer", "number"):
                    min_val = field_def.get("min")
                    max_val = field_def.get("max")
                    value = row[field_name]
                    if min_val is not None and isinstance(value, (int, float)) and value < min_val:
                        logger.warning(
                            "Field below minimum, clamping",
                            extra={"field_name": field_name, "value": value, "min": min_val},
                        )
                        row[field_name] = min_val
                    if max_val is not None and isinstance(value, (int, float)) and value > max_val:
                        logger.warning(
                            "Field above maximum, clamping",
                            extra={"field_name": field_name, "value": value, "max": max_val},
                        )
                        row[field_name] = max_val

        if missing_required:
            logger.warning(
                "Record missing required fields — including record anyway",
                extra={
                    "record_idx": record_idx,
                    "missing_fields": missing_required,
                },
            )
            active_coercions.append({
                "field": ", ".join(missing_required),
                "reason": "missing_required_field_allowed",
            })

        if "_provenance" not in row or not isinstance(row.get("_provenance"), dict):
            row["_provenance"] = {}
        if active_coercions:
            existing_coercions = row["_provenance"].get("_coercions")
            if isinstance(existing_coercions, list):
                row["_provenance"]["_coercions"] = existing_coercions + active_coercions
            else:
                row["_provenance"]["_coercions"] = active_coercions
        row["_sourceFileId"] = file_id
        row["_extractedAt"] = now_iso
        row["_extractedBy"] = agent_id
        validated.append(row)
    return validated


async def _resolve_principal(
    payload: ExtractRequest,
    authorization: Optional[str],
) -> Principal:
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
        principal = await validate_bearer(token)
        principal.token = token
        return principal

    if payload.delegation_token and payload.user_id:
        return Principal(
            sub=payload.user_id,
            scopes=["agent.execute", "data.read", "data.write", "search.read"],
            token=payload.delegation_token,
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authorization or delegation_token/user_id",
    )


async def _run_tier2_rag_extraction(
    *,
    task_id: str,
    payload: ExtractRequest,
    principal: Principal,
    client: BusiboxClient,
    schema_obj: Dict[str, Any],
    schema_doc: Dict[str, Any],
    agent_uuid: uuid.UUID,
    instructions: str,
    response_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Tier 2: RAG-guided extraction.

    Uses search-api to retrieve the most relevant chunks for ALL fields,
    deduplicates, assembles in document order, then runs one or more LLM
    calls with the full (relaxed) schema.
    """
    _extraction_tasks[task_id]["step"] = "tier2_rag_retrieval"

    try:
        search_api_token = await get_service_token(
            user_token=principal.token,
            user_id=principal.sub,
            target_audience="search-api",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to get search-api token: {exc}") from exc

    search_client = BusiboxClient(search_api_token)
    fields = (schema_obj.get("fields") or {}) if isinstance(schema_obj, dict) else {}
    schema_name = schema_doc.get("name")

    field_names = list(fields.keys()) if isinstance(fields, dict) else []
    search_tasks = []
    for field_name in field_names:
        field_def = fields.get(field_name)
        if isinstance(field_def, dict):
            search_tasks.append(
                _search_context_for_field(
                    search_client=search_client,
                    file_id=payload.file_id,
                    field_name=field_name,
                    field_def=field_def,
                    schema_name=schema_name,
                )
            )
        else:
            search_tasks.append(asyncio.sleep(0, result=""))

    field_contexts = await asyncio.gather(*search_tasks)

    seen_snippets: Dict[str, int] = {}
    ordered_chunks: List[Tuple[int, str]] = []
    for ctx_text in field_contexts:
        if not ctx_text:
            continue
        for line in ctx_text.split("\n\n"):
            line = line.strip()
            if not line or line in seen_snippets:
                continue
            order_key = len(seen_snippets)
            chunk_match = re.search(r"chunk\s+(\d+)", line)
            if chunk_match:
                order_key = int(chunk_match.group(1))
            seen_snippets[line] = order_key
            ordered_chunks.append((order_key, line))

    ordered_chunks.sort(key=lambda x: x[0])
    assembled_context = "\n\n".join(c[1] for c in ordered_chunks)
    context_tokens = _estimate_markdown_tokens(assembled_context)

    relaxed_schema, relaxed_response = _relax_schema_for_chunked(schema_obj, response_schema)

    if context_tokens <= RAG_CONTEXT_MAX_TOKENS:
        _extraction_tasks[task_id]["step"] = "tier2_rag_extraction"
        prompt = _build_extraction_prompt(
            schema_document_id=payload.schema_document_id,
            file_id=payload.file_id,
            schema_obj=relaxed_schema,
            markdown=assembled_context,
            instructions=instructions + "\nThis is a subset of the document (most relevant sections). Extract all records you can find.",
            compact_mode=False,
        )
        async with SessionLocal() as session:
            run = await create_run(
                session=session,
                principal=principal,
                agent_id=agent_uuid,
                payload={
                    "prompt": prompt,
                    "response_schema": relaxed_response,
                },
                scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                purpose="structured-extraction-rag",
                agent_tier="complex",
            )
        if run.status != "succeeded":
            raise RuntimeError(f"RAG extraction run failed: run_id={run.id}, status={run.status}")
        output = run.output or {}
        if not isinstance(output, dict):
            output = {"result": str(output)}
        records = _extract_records(output)
        logger.info("Tier 2 RAG extraction complete", extra={"run_id": str(run.id), "record_count": len(records)})
        return records

    chunk_lines = [c[1] for c in ordered_chunks]
    windows: List[str] = []
    current_window: List[str] = []
    current_tokens = 0
    for line in chunk_lines:
        line_tokens = _estimate_markdown_tokens(line)
        if current_tokens + line_tokens > RAG_CONTEXT_MAX_TOKENS and current_window:
            windows.append("\n\n".join(current_window))
            current_window = []
            current_tokens = 0
        current_window.append(line)
        current_tokens += line_tokens
    if current_window:
        windows.append("\n\n".join(current_window))

    semaphore = asyncio.Semaphore(MAX_PARALLEL_CHUNK_RUNS)
    all_records: List[Dict[str, Any]] = []

    async def _run_rag_window(window_idx: int, window_text: str) -> List[Dict[str, Any]]:
        _extraction_tasks[task_id]["step"] = f"tier2_rag_window_{window_idx + 1}_of_{len(windows)}"
        prompt = _build_extraction_prompt(
            schema_document_id=payload.schema_document_id,
            file_id=payload.file_id,
            schema_obj=relaxed_schema,
            markdown=window_text,
            instructions=instructions + f"\nThis is section {window_idx + 1} of {len(windows)} of the most relevant document sections. Extract all matching records.",
            compact_mode=False,
        )
        async with semaphore:
            async with SessionLocal() as session:
                run = await create_run(
                    session=session,
                    principal=principal,
                    agent_id=agent_uuid,
                    payload={
                        "prompt": prompt,
                        "response_schema": relaxed_response,
                    },
                    scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                    purpose=f"structured-extraction-rag-window-{window_idx}",
                    agent_tier="complex",
                )
        if run.status == "succeeded":
            output = run.output or {}
            if not isinstance(output, dict):
                output = {"result": str(output)}
            return _extract_records(output)
        return []

    window_results = await asyncio.gather(
        *[_run_rag_window(i, w) for i, w in enumerate(windows)]
    )
    for recs in window_results:
        all_records.extend(recs)

    logger.info("Tier 2 RAG multi-window extraction complete", extra={"window_count": len(windows), "total_records": len(all_records)})
    return all_records


async def _run_tier2_grouped_extraction(
    *,
    task_id: str,
    payload: ExtractRequest,
    principal: Principal,
    client: BusiboxClient,
    schema_obj: Dict[str, Any],
    schema_doc: Dict[str, Any],
    agent_uuid: uuid.UUID,
    instructions: str,
    response_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Tier 2 grouped extraction for long documents.

    Groups related fields, retrieves relevant chunks per group via search,
    runs one LLM call per group in parallel, then merges all results into a
    single record.
    """
    _extraction_tasks[task_id]["step"] = "tier2_grouped_retrieval"

    try:
        search_api_token = await get_service_token(
            user_token=principal.token,
            user_id=principal.sub,
            target_audience="search-api",
        )
    except Exception as exc:
        raise RuntimeError(f"Failed to get search-api token: {exc}") from exc

    search_client = BusiboxClient(search_api_token)
    effective_schema = schema_obj if isinstance(schema_obj, dict) else {}
    fields = effective_schema.get("fields", {}) or {}
    schema_name = schema_doc.get("name")
    max_records = _get_max_records(effective_schema)

    groups = _group_fields_for_extraction(effective_schema)
    if not groups:
        return []

    logger.info(
        "Tier 2 grouped extraction: field groups",
        extra={"group_count": len(groups), "groups": [g for g in groups]},
    )

    semaphore = asyncio.Semaphore(MAX_PARALLEL_CHUNK_RUNS)

    async def _extract_group(group_idx: int, field_names: List[str]) -> Dict[str, Any]:
        _extraction_tasks[task_id]["step"] = f"tier2_group_{group_idx + 1}_of_{len(groups)}"

        # Search for relevant context for every field in this group
        search_tasks = []
        for fname in field_names:
            fdef = fields.get(fname)
            if isinstance(fdef, dict):
                search_tasks.append(
                    _search_context_for_field(
                        search_client=search_client,
                        file_id=payload.file_id,
                        field_name=fname,
                        field_def=fdef,
                        schema_name=schema_name,
                    )
                )
            else:
                search_tasks.append(asyncio.sleep(0, result=""))

        field_contexts = await asyncio.gather(*search_tasks)

        # Deduplicate and assemble context
        seen: Dict[str, int] = {}
        ordered: List[Tuple[int, str]] = []
        for ctx_text in field_contexts:
            if not ctx_text:
                continue
            for para in ctx_text.split("\n\n"):
                para = para.strip()
                if not para or para in seen:
                    continue
                order_key = len(seen)
                chunk_match = re.search(r"chunk\s+(\d+)", para)
                if chunk_match:
                    order_key = int(chunk_match.group(1))
                seen[para] = order_key
                ordered.append((order_key, para))

        ordered.sort(key=lambda x: x[0])
        assembled = "\n\n".join(c[1] for c in ordered)

        if not assembled.strip():
            return {}

        group_response_schema = _build_records_response_schema(
            effective_schema, max_records=max_records, field_names=field_names,
        )

        prompt = _build_extraction_prompt(
            schema_document_id=payload.schema_document_id,
            file_id=payload.file_id,
            schema_obj=effective_schema,
            markdown=assembled,
            instructions=(
                f"{instructions}\n"
                f"Extract ONLY these fields: {', '.join(field_names)}. "
                "This is a subset of the document (most relevant sections for these fields). "
                "Ignore all other fields."
            ),
            compact_mode=False,
            max_records=max_records,
        )

        async with semaphore:
            async with SessionLocal() as session:
                run = await create_run(
                    session=session,
                    principal=principal,
                    agent_id=agent_uuid,
                    payload={
                        "prompt": prompt,
                        "response_schema": group_response_schema,
                    },
                    scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                    purpose=f"structured-extraction-group-{group_idx}",
                    agent_tier="complex",
                )

        if run.status == "succeeded":
            out = run.output or {}
            if not isinstance(out, dict):
                out = {"result": str(out)}
            recs = _extract_records(out)
            if recs:
                return recs[0]
        return {}

    group_results = await asyncio.gather(
        *[_extract_group(i, g) for i, g in enumerate(groups)]
    )

    # Merge all group results into a single record
    merged: Dict[str, Any] = {}
    for partial in group_results:
        for k, v in partial.items():
            if v is not None and v != "" and v != []:
                merged[k] = v

    if not merged:
        return []

    logger.info(
        "Tier 2 grouped extraction complete",
        extra={
            "group_count": len(groups),
            "merged_field_count": len(merged),
            "merged_fields": list(merged.keys()),
        },
    )
    return [merged]


async def _run_tier3_chunk_sweep(
    *,
    task_id: str,
    payload: ExtractRequest,
    principal: Principal,
    client: BusiboxClient,
    schema_obj: Dict[str, Any],
    agent_uuid: uuid.UUID,
    instructions: str,
    response_schema: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """
    Tier 3: Progressive chunk sweep for very large documents.

    Fetches all chunks from data-api, groups them into token windows with
    overlap, runs parallel LLM extractions, and offset-corrects provenance.
    """
    _extraction_tasks[task_id]["step"] = "tier3_fetching_chunks"

    chunks: List[Dict[str, Any]] = []
    page = 1
    page_size = 100
    while True:
        try:
            resp = await client.request(
                "GET",
                f"/files/{payload.file_id}/chunks",
                params={"page": page, "pageSize": page_size},
            )
        except Exception as exc:
            logger.warning("Failed to fetch chunks for tier 3", extra={"error": str(exc)})
            break

        page_chunks = resp.get("chunks", []) if isinstance(resp, dict) else []
        if not page_chunks:
            break
        chunks.extend(page_chunks)
        total_pages = resp.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1

    if not chunks:
        raise RuntimeError("Tier 3 chunk sweep: no chunks available for this document")

    chunks.sort(key=lambda c: c.get("chunkIndex", c.get("chunk_index", 0)))

    windows: List[Tuple[str, int]] = []
    current_texts: List[str] = []
    current_tokens = 0
    window_start_offset = chunks[0].get("charOffset", chunks[0].get("char_offset", 0)) if chunks else 0
    overlap_buffer: List[Dict[str, Any]] = []

    for chunk in chunks:
        text = chunk.get("text", "")
        token_count = chunk.get("tokenCount", chunk.get("token_count")) or _estimate_markdown_tokens(text)
        char_offset = chunk.get("charOffset", chunk.get("char_offset", 0))

        if current_tokens + token_count > CHUNK_WINDOW_TOKENS and current_texts:
            windows.append(("\n\n".join(current_texts), window_start_offset))

            overlap_texts: List[str] = []
            overlap_tokens = 0
            for ob in reversed(overlap_buffer):
                ob_text = ob.get("text", "")
                ob_tokens = ob.get("tokenCount", ob.get("token_count")) or _estimate_markdown_tokens(ob_text)
                if overlap_tokens + ob_tokens > CHUNK_OVERLAP_TOKENS:
                    break
                overlap_texts.insert(0, ob_text)
                overlap_tokens += ob_tokens

            current_texts = overlap_texts
            current_tokens = overlap_tokens
            window_start_offset = char_offset - (sum(len(t) for t in overlap_texts)) if overlap_texts else char_offset

        current_texts.append(text)
        current_tokens += token_count
        overlap_buffer.append(chunk)
        if len(overlap_buffer) > 10:
            overlap_buffer = overlap_buffer[-10:]

    if current_texts:
        windows.append(("\n\n".join(current_texts), window_start_offset))

    logger.info(
        "Tier 3 chunk windows prepared",
        extra={"chunk_count": len(chunks), "window_count": len(windows)},
    )

    relaxed_schema, relaxed_response = _relax_schema_for_chunked(schema_obj, response_schema)
    semaphore = asyncio.Semaphore(MAX_PARALLEL_CHUNK_RUNS)
    all_records: List[Dict[str, Any]] = []

    async def _run_chunk_window(window_idx: int, window_text: str, char_offset: int) -> List[Dict[str, Any]]:
        _extraction_tasks[task_id]["step"] = f"chunk_extraction_{window_idx + 1}_of_{len(windows)}"
        prompt = _build_extraction_prompt(
            schema_document_id=payload.schema_document_id,
            file_id=payload.file_id,
            schema_obj=relaxed_schema,
            markdown=window_text,
            instructions=(
                f"{instructions}\n"
                f"This is chunk window {window_idx + 1} of {len(windows)} from a large document. "
                "Extract all records found in this section. Fields not present in this section should be null."
            ),
            compact_mode=False,
        )
        async with semaphore:
            async with SessionLocal() as session:
                run = await create_run(
                    session=session,
                    principal=principal,
                    agent_id=agent_uuid,
                    payload={
                        "prompt": prompt,
                        "response_schema": relaxed_response,
                    },
                    scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                    purpose=f"structured-extraction-chunk-{window_idx}",
                    agent_tier="complex",
                )
        if run.status == "succeeded":
            output = run.output or {}
            if not isinstance(output, dict):
                output = {"result": str(output)}
            records = _extract_records(output)
            _offset_correct_provenance(records, char_offset)
            return records
        logger.warning(
            "Tier 3 chunk window extraction failed",
            extra={"window_idx": window_idx, "run_id": str(run.id), "status": run.status},
        )
        return []

    window_results = await asyncio.gather(
        *[_run_chunk_window(i, text, offset) for i, (text, offset) in enumerate(windows)]
    )
    for recs in window_results:
        all_records.extend(recs)

    logger.info(
        "Tier 3 chunk sweep extraction complete",
        extra={"window_count": len(windows), "total_records": len(all_records)},
    )
    return all_records


async def _run_extraction_pipeline(
    *,
    task_id: str,
    payload: ExtractRequest,
    principal: Principal,
    client: BusiboxClient,
    markdown: str,
    schema_doc: Dict[str, Any],
    schema_obj: Dict[str, Any],
    agent_uuid: uuid.UUID,
    instructions: str,
    extraction_tier: str,
    response_schema: Dict[str, Any],
) -> None:
    """
    Background coroutine that performs LLM extraction, stores records, creates
    graph entities, indexes to Milvus, and updates file metadata.

    Uses a three-tier approach based on document size:
      - **direct** (<12k tokens): single-shot LLM call with full doc
      - **rag** (12k-100k tokens): RAG-guided retrieval + LLM extraction
      - **chunk_sweep** (>100k tokens): progressive chunk-by-chunk extraction

    Progress is tracked via ``_extraction_tasks[task_id]`` and the file's
    ``metadata.extraction.status`` field (visible through normal file-metadata polling).
    """
    try:
        _extraction_tasks[task_id]["status"] = "running"
        _extraction_tasks[task_id]["step"] = f"llm_extraction_tier_{extraction_tier}"

        run = None
        output: Dict[str, Any] = {}
        records: List[Dict[str, Any]] = []
        run_id = "unknown"

        original_markdown = markdown
        markdown = _clean_markdown_for_extraction(markdown)

        logger.info(
            "Extraction pipeline starting",
            extra={
                "task_id": task_id,
                "extraction_tier": extraction_tier,
                "file_id": payload.file_id,
                "markdown_tokens": _estimate_markdown_tokens(markdown),
                "original_markdown_tokens": _estimate_markdown_tokens(original_markdown),
            },
        )

        if extraction_tier == "rag":
            # Try grouped extraction first (parallel per-group with RAG context)
            try:
                records = await _run_tier2_grouped_extraction(
                    task_id=task_id,
                    payload=payload,
                    principal=principal,
                    client=client,
                    schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
                    schema_doc=schema_doc,
                    agent_uuid=agent_uuid,
                    instructions=instructions,
                    response_schema=response_schema,
                )
                if records:
                    output = {"result": "tier2-grouped", "tier": "grouped"}
            except Exception as grouped_exc:
                logger.warning(
                    "Tier 2 grouped extraction failed, falling back to RAG",
                    extra={"error": str(grouped_exc)},
                )
                records = []

            # Fall back to original RAG approach if grouped produced nothing
            if not records:
                try:
                    records = await _run_tier2_rag_extraction(
                        task_id=task_id,
                        payload=payload,
                        principal=principal,
                        client=client,
                        schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
                        schema_doc=schema_doc,
                        agent_uuid=agent_uuid,
                        instructions=instructions,
                        response_schema=response_schema,
                    )
                    if records:
                        records = await _merge_multi_record_results(
                            records, schema_obj if isinstance(schema_obj, dict) else {},
                            principal, agent_uuid,
                        )
                        output = {"result": "tier2-rag", "tier": "rag"}
                except Exception as tier2_exc:
                    logger.warning(
                        "Tier 2 RAG extraction failed, falling back to direct",
                        extra={"error": str(tier2_exc)},
                    )
                    records = []

        elif extraction_tier == "chunk_sweep":
            try:
                records = await _run_tier3_chunk_sweep(
                    task_id=task_id,
                    payload=payload,
                    principal=principal,
                    client=client,
                    schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
                    agent_uuid=agent_uuid,
                    instructions=instructions,
                    response_schema=response_schema,
                )
                if records:
                    records = await _merge_multi_record_results(
                        records, schema_obj if isinstance(schema_obj, dict) else {},
                        principal, agent_uuid,
                    )
                    output = {"result": "tier3-chunk-sweep", "tier": "chunk_sweep"}
            except Exception as tier3_exc:
                logger.warning(
                    "Tier 3 chunk sweep failed, falling back to direct with truncated markdown",
                    extra={"error": str(tier3_exc)},
                )
                records = []
                markdown = markdown[: TIER1_TOKEN_THRESHOLD * 4]

        # Tier 1 (direct) or fallback from failed higher tiers
        max_records = _get_max_records(schema_obj if isinstance(schema_obj, dict) else {})
        if not records:
            _extraction_tasks[task_id]["step"] = "tier1_direct_extraction"
            prompt = _build_extraction_prompt(
                schema_document_id=payload.schema_document_id,
                file_id=payload.file_id,
                schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
                markdown=markdown,
                instructions=instructions,
                compact_mode=False,
                max_records=max_records,
            )
            async with SessionLocal() as bg_session:
                run = await create_run(
                    session=bg_session,
                    principal=principal,
                    agent_id=agent_uuid,
                    payload={
                        "prompt": prompt,
                        "response_schema": response_schema,
                    },
                    scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                    purpose="structured-extraction-direct",
                    agent_tier="complex",
                )

            if run.status != "succeeded":
                raise RuntimeError(
                    f"Extraction run failed: run_id={run.id}, status={run.status}"
                )

            output = run.output or {}
            if not isinstance(output, dict):
                output = {"result": str(output)}
            logger.info(
                "Tier 1 direct extraction output",
                extra={
                    "run_id": str(run.id),
                    "output_keys": list(output.keys()) if isinstance(output, dict) else type(output).__name__,
                    "output_preview": str(output)[:500],
                },
            )
            records = _extract_records(output)
            logger.info(
                "Tier 1 records parsed",
                extra={"run_id": str(run.id), "record_count": len(records)},
            )

            # ---- Tier 1 missing-field retry ----
            # If we got records but some fields are still null, do a
            # targeted follow-up extracting only the missing fields.
            if records:
                effective_schema = schema_obj if isinstance(schema_obj, dict) else {}
                for rec_idx, rec in enumerate(records):
                    missing = _identify_missing_fields(rec, effective_schema)
                    if not missing:
                        continue
                    logger.info(
                        "Tier 1 missing-field retry",
                        extra={"record_idx": rec_idx, "missing_fields": missing},
                    )
                    _extraction_tasks[task_id]["step"] = f"tier1_missing_field_retry_{rec_idx}"
                    retry_schema = _build_records_response_schema(
                        effective_schema, max_records=1, field_names=missing,
                    )
                    retry_prompt = _build_extraction_prompt(
                        schema_document_id=payload.schema_document_id,
                        file_id=payload.file_id,
                        schema_obj=effective_schema,
                        markdown=markdown,
                        instructions=(
                            f"{instructions}\n"
                            "Some fields were not extracted in the first pass. "
                            f"Extract ONLY these fields: {', '.join(missing)}. "
                            "Return exactly one record with only these fields populated."
                        ),
                        compact_mode=False,
                        max_records=1,
                    )
                    async with SessionLocal() as retry_session:
                        retry_run = await create_run(
                            session=retry_session,
                            principal=principal,
                            agent_id=agent_uuid,
                            payload={
                                "prompt": retry_prompt,
                                "response_schema": retry_schema,
                            },
                            scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                            purpose=f"structured-extraction-missing-fields-{rec_idx}",
                            agent_tier="complex",
                        )
                    if retry_run.status == "succeeded":
                        retry_output = retry_run.output or {}
                        if not isinstance(retry_output, dict):
                            retry_output = {"result": str(retry_output)}
                        retry_recs = _extract_records(retry_output)
                        if retry_recs:
                            for field_name in missing:
                                val = retry_recs[0].get(field_name)
                                if val is not None and val != "" and val != []:
                                    rec[field_name] = val
                            logger.info(
                                "Tier 1 missing-field retry merged",
                                extra={
                                    "record_idx": rec_idx,
                                    "filled_fields": [
                                        f for f in missing
                                        if rec.get(f) is not None and rec.get(f) != "" and rec.get(f) != []
                                    ],
                                },
                            )

            if not records:
                _extraction_tasks[task_id]["step"] = "tier1_retry"
                retry_prompt = _build_extraction_prompt(
                    schema_document_id=payload.schema_document_id,
                    file_id=payload.file_id,
                    schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
                    markdown=markdown,
                    instructions=(
                        f"{instructions} "
                        "Previous output was invalid or non-parseable. "
                        "Retry with compact output."
                    ),
                    compact_mode=True,
                    max_records=max_records,
                )
                async with SessionLocal() as retry_session:
                    retry_run = await create_run(
                        session=retry_session,
                        principal=principal,
                        agent_id=agent_uuid,
                        payload={
                            "prompt": retry_prompt,
                            "response_schema": response_schema,
                        },
                        scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                        purpose="structured-extraction-retry",
                        agent_tier="complex",
                    )
                logger.info(
                    "Extraction retry run completed",
                    extra={
                        "retry_run_id": str(retry_run.id),
                        "retry_status": retry_run.status,
                        "retry_output_preview": str(retry_run.output)[:500] if retry_run.output else "None",
                    },
                )
                if retry_run.status == "succeeded":
                    retry_output = retry_run.output or {}
                    if not isinstance(retry_output, dict):
                        retry_output = {"result": str(retry_output)}
                    retry_records = _extract_records(retry_output)
                    logger.info(
                        "Extraction retry records parsed",
                        extra={"retry_run_id": str(retry_run.id), "record_count": len(retry_records)},
                    )
                    if retry_records:
                        run = retry_run
                        output = retry_output
                        records = retry_records

        if not records:
            run_id = str(run.id) if run is not None else "unknown"
            output_preview = str(output)[:300] if output else "None"
            logger.error(
                "Extraction produced no records after all tiers + retry",
                extra={
                    "run_id": run_id,
                    "output_preview": output_preview,
                    "output_type": type(output).__name__,
                    "extraction_tier": extraction_tier,
                },
            )
            raise RuntimeError(
                f"Agent did not return extractable records: run_id={run_id}"
            )

        run_id = str(run.id) if run is not None else "unknown"
        _extraction_tasks[task_id]["step"] = "post_processing"

        enriched_records = _validate_and_enrich_records(
            records=records,
            schema=schema_obj if isinstance(schema_obj, dict) else {},
            file_id=payload.file_id,
            agent_id=str(agent_uuid),
        )

        # Preserve appOnly fields from pre-existing records for this file
        app_only_fields = _get_app_only_fields(
            schema_obj if isinstance(schema_obj, dict) else {}
        )
        if app_only_fields and payload.store_results:
            try:
                existing = await client.request(
                    "POST",
                    f"/data/{payload.schema_document_id}/query",
                    json={
                        "filters": {"_sourceFileId": payload.file_id},
                        "limit": 100,
                    },
                )
                existing_records = existing.get("records", [])
                if existing_records:
                    saved_values: Dict[int, Dict[str, Any]] = {}
                    for idx, rec in enumerate(existing_records):
                        vals = {
                            f: rec[f]
                            for f in app_only_fields
                            if f in rec and rec[f] is not None
                        }
                        if vals:
                            saved_values[idx] = vals
                    for idx, new_rec in enumerate(enriched_records):
                        if idx in saved_values:
                            for field_name, value in saved_values[idx].items():
                                if field_name not in new_rec or new_rec[field_name] is None:
                                    new_rec[field_name] = value
            except Exception:
                logger.debug(
                    "Could not fetch existing records for appOnly merge (non-fatal)",
                    extra={"file_id": payload.file_id},
                )

        # Apply record_defaults from trigger configuration (e.g. campaignId, stage).
        # These are app-supplied values that should be set on every record produced
        # by this extraction, regardless of what the LLM returns.
        if payload.record_defaults:
            for rec in enriched_records:
                for field_name, value in payload.record_defaults.items():
                    if field_name not in rec or rec[field_name] is None:
                        rec[field_name] = value

        # -- Phase 1: store records immediately so the frontend can show them --
        _extraction_tasks[task_id]["step"] = "storing_records"

        insert_result: Dict[str, Any] = {"stored": False, "count": 0}
        if payload.store_results:
            try:
                result = await client.request(
                    "POST",
                    f"/data/{payload.schema_document_id}/records",
                    json={"records": enriched_records, "validate": True},
                )
                insert_result = {
                    "stored": True,
                    "count": result.get("count", len(enriched_records)),
                    "recordIds": result.get("recordIds", []),
                }
            except Exception as exc:
                logger.error(
                    "Failed to store extracted records",
                    extra={
                        "task_id": task_id,
                        "file_id": payload.file_id,
                        "error": str(exc),
                    },
                )
                raise RuntimeError(f"Failed to store extracted records: {exc}") from exc

        try:
            await client.request(
                "PATCH",
                f"/files/{payload.file_id}",
                json={
                    "metadata": {
                        "extraction": {
                            "schemaDocumentId": payload.schema_document_id,
                            "schemaName": schema_doc.get("name"),
                            "status": "extracted",
                            "runId": run_id,
                            "agentId": str(agent_uuid),
                            "recordCount": len(enriched_records),
                            "records": enriched_records,
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                },
            )
        except Exception:
            logger.warning(
                "Failed to persist extracted data to file metadata (extracted phase)",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "run_id": run_id,
                },
            )

        # -- Phase 2: compute provenance via text-search and patch records --
        _extraction_tasks[task_id]["step"] = "provenance"

        _populate_provenance_from_markdown(
            records=enriched_records,
            markdown=original_markdown,
            schema=schema_obj if isinstance(schema_obj, dict) else {},
        )

        if payload.store_results:
            try:
                await client.request(
                    "PATCH",
                    f"/files/{payload.file_id}",
                    json={
                        "metadata": {
                            "extraction": {
                                "records": enriched_records,
                                "updatedAt": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    },
                )
            except Exception:
                logger.warning(
                    "Failed to patch provenance into file metadata (non-fatal)",
                    extra={
                        "file_id": payload.file_id,
                        "schema_document_id": payload.schema_document_id,
                    },
                )

        # -- Phase 3: graph and field indexing --
        _extraction_tasks[task_id]["step"] = "graph_indexing"

        graph_result: Dict[str, Any] = {"entity_count": 0}
        try:
            graph_result = await client.request(
                "POST",
                "/data/graph/from-extraction",
                params={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                },
            )
            logger.info(
                "Graph entities created from extraction",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "entity_count": graph_result.get("entity_count", 0),
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to create graph entities from extraction (non-fatal)",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "error": str(exc),
                },
            )

        _extraction_tasks[task_id]["step"] = "field_indexing"

        index_result: Dict[str, Any] = {"indexed_count": 0}
        try:
            index_result = await client.request(
                "POST",
                "/data/index-from-extraction",
                params={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                },
            )
            logger.info(
                "Field indexing complete from extraction",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "indexed_count": index_result.get("indexed_count", 0),
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to index extracted fields (non-fatal)",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "error": str(exc),
                },
            )

        # -- Phase 4: finalize --
        _extraction_tasks[task_id]["step"] = "finalizing"

        try:
            await client.request(
                "PATCH",
                f"/files/{payload.file_id}",
                json={
                    "metadata": {
                        "extraction": {
                            "status": "completed",
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                },
            )
        except Exception:
            logger.warning(
                "Failed to persist final completed status to file metadata",
                extra={
                    "file_id": payload.file_id,
                    "schema_document_id": payload.schema_document_id,
                    "run_id": run_id,
                },
            )

        final_result = {
            "success": True,
            "runId": run_id,
            "fileId": payload.file_id,
            "schemaDocumentId": payload.schema_document_id,
            "agentId": str(agent_uuid),
            "recordCount": len(enriched_records),
            "records": enriched_records,
            "storage": insert_result,
            "graph": graph_result,
            "indexing": index_result,
        }

        _extraction_tasks[task_id]["status"] = "completed"
        _extraction_tasks[task_id]["step"] = "done"
        _extraction_tasks[task_id]["result"] = final_result

        logger.info(
            "Background extraction completed",
            extra={
                "task_id": task_id,
                "file_id": payload.file_id,
                "record_count": len(enriched_records),
            },
        )

    except Exception as exc:
        logger.error(
            "Background extraction failed",
            extra={
                "task_id": task_id,
                "file_id": payload.file_id,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
            exc_info=True,
        )
        _extraction_tasks[task_id]["status"] = "failed"
        _extraction_tasks[task_id]["error"] = str(exc)

        try:
            await client.request(
                "PATCH",
                f"/files/{payload.file_id}",
                json={
                    "metadata": {
                        "extraction": {
                            "schemaDocumentId": payload.schema_document_id,
                            "schemaName": schema_doc.get("name"),
                            "status": "failed",
                            "error": str(exc),
                            "updatedAt": datetime.now(timezone.utc).isoformat(),
                        }
                    }
                },
            )
        except Exception:
            pass


@router.post("")
async def extract_document(
    payload: ExtractRequest,
    authorization: Optional[str] = Header(default=None),
):
    """
    Kick off structured extraction as a background task.

    Returns immediately with a ``taskId`` that can be polled via
    ``GET /extract/status/{taskId}``.  The file's ``metadata.extraction.status``
    is also updated throughout (running -> completed/failed) so the UI can
    poll via normal document metadata refresh.
    """
    try:
        uuid.UUID(payload.file_id)
        uuid.UUID(payload.schema_document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    principal = await _resolve_principal(payload, authorization)
    if not principal.token:
        raise HTTPException(status_code=401, detail="Missing principal token for downstream data-api access")
    try:
        data_api_token = await get_service_token(
            user_token=principal.token,
            user_id=principal.sub,
            target_audience="data-api",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to get data-api token: {exc}") from exc

    client = BusiboxClient(data_api_token)

    try:
        markdown_resp = await client.request("GET", f"/files/{payload.file_id}/markdown")
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Unable to fetch markdown for file: {exc}") from exc

    markdown = (
        markdown_resp.get("markdown")
        or markdown_resp.get("content")
        or (markdown_resp.get("data") or {}).get("markdown")
        or ""
    )
    if not markdown:
        raise HTTPException(status_code=404, detail="Document has no markdown content to extract from")

    try:
        schema_doc = await client.request(
            "GET",
            f"/data/{payload.schema_document_id}",
            params={"includeRecords": "false"},
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Unable to fetch schema document: {exc}") from exc

    schema_obj = schema_doc.get("schema") or {}
    schema_meta = schema_doc.get("metadata") or {}
    resolved_agent_id = payload.agent_id or schema_meta.get("extractionAgentId")
    if not resolved_agent_id:
        resolved_agent_id = DEFAULT_EXTRACTION_AGENT_ID
    elif (
        not payload.agent_id
        and str(resolved_agent_id) == SCHEMA_BUILDER_AGENT_ID
    ):
        resolved_agent_id = DEFAULT_EXTRACTION_AGENT_ID

    try:
        agent_uuid = uuid.UUID(str(resolved_agent_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent_id: {resolved_agent_id}") from exc

    instructions = payload.prompt_override or (
        "Extract data from this document into records matching the schema."
    )
    markdown_tokens = _estimate_markdown_tokens(markdown)
    extraction_tier = _select_extraction_tier(markdown_tokens)

    schema_field_count = len(schema_obj.get("fields", {})) if isinstance(schema_obj, dict) else 0
    logger.info(
        "Extraction request accepted (background)",
        extra={
            "file_id": payload.file_id,
            "schema_document_id": payload.schema_document_id,
            "resolved_agent_id": str(agent_uuid),
            "markdown_tokens": markdown_tokens,
            "extraction_tier": extraction_tier,
            "schema_field_count": schema_field_count,
            "schema_keys": list(schema_obj.keys()) if isinstance(schema_obj, dict) else str(type(schema_obj).__name__),
            "schema_preview": str(schema_obj)[:400],
        },
    )
    if schema_field_count == 0:
        logger.warning(
            "Schema has no fields — extraction will likely produce empty records",
            extra={"schema_document_id": payload.schema_document_id, "schema_obj": str(schema_obj)[:500]},
        )

    # Mark extraction as running in file metadata immediately.
    task_id = str(uuid.uuid4())
    try:
        await client.request(
            "PATCH",
            f"/files/{payload.file_id}",
            json={
                "metadata": {
                    "extraction": {
                        "schemaDocumentId": payload.schema_document_id,
                        "schemaName": schema_doc.get("name"),
                        "status": "running",
                        "taskId": task_id,
                        "appliedAt": datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
        )
    except Exception:
        logger.warning(
            "Failed to persist applied schema metadata",
            extra={
                "file_id": payload.file_id,
                "schema_document_id": payload.schema_document_id,
            },
        )

    response_schema = _build_records_response_schema(schema_obj if isinstance(schema_obj, dict) else {})

    _extraction_tasks[task_id] = {
        "status": "accepted",
        "step": "queued",
        "fileId": payload.file_id,
        "schemaDocumentId": payload.schema_document_id,
        "startedAt": datetime.now(timezone.utc).isoformat(),
        "result": None,
        "error": None,
    }

    asyncio.create_task(
        _run_extraction_pipeline(
            task_id=task_id,
            payload=payload,
            principal=principal,
            client=client,
            markdown=markdown,
            schema_doc=schema_doc,
            schema_obj=schema_obj,
            agent_uuid=agent_uuid,
            instructions=instructions,
            extraction_tier=extraction_tier,
            response_schema=response_schema,
        )
    )

    return {
        "taskId": task_id,
        "status": "accepted",
        "fileId": payload.file_id,
        "schemaDocumentId": payload.schema_document_id,
        "message": "Extraction started in background. Poll GET /extract/status/{taskId} or watch file metadata for completion.",
    }


@router.get("/status/{task_id}")
async def extraction_status(task_id: str):
    """
    Poll extraction task status.

    Returns the current state of a background extraction task including
    which step it's on.  When completed, includes the full extraction result.
    """
    task = _extraction_tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Extraction task not found")

    response: Dict[str, Any] = {
        "taskId": task_id,
        "status": task["status"],
        "step": task.get("step"),
        "fileId": task.get("fileId"),
        "schemaDocumentId": task.get("schemaDocumentId"),
        "startedAt": task.get("startedAt"),
    }

    if task["status"] == "completed" and task.get("result"):
        response["result"] = task["result"]
    elif task["status"] == "failed":
        response["error"] = task.get("error")

    return response
