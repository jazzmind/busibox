"""
Structured extraction API.

Runs an agent against a document + schema and stores extracted records
into the target data document with provenance metadata.
"""

import json
import logging
import math
import asyncio
import re
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import validate_bearer
from app.auth.tokens import get_service_token
from app.clients.busibox import BusiboxClient
from app.db.session import SessionLocal
from app.db.session import get_session
from app.schemas.auth import Principal
from app.services.run_service import create_run

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/extract", tags=["extract"])

# Long-document field-batched extraction controls
LONG_DOC_TOKEN_THRESHOLD = 12000
MIN_FIELDS_FOR_BATCH_MODE = 8
FIELD_BATCH_SIZE = 6
FIELD_SEARCH_TOP_K = 6
MAX_PARALLEL_BATCH_RUNS = 3

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
    if isinstance(output.get("records"), list):
        return [r for r in output["records"] if isinstance(r, dict)]

    data_obj = output.get("data")
    if isinstance(data_obj, dict) and isinstance(data_obj.get("records"), list):
        return [r for r in data_obj["records"] if isinstance(r, dict)]
    if isinstance(data_obj, list):
        return [r for r in data_obj if isinstance(r, dict)]

    result_obj = output.get("result")
    if isinstance(result_obj, dict):
        if isinstance(result_obj.get("records"), list):
            return [r for r in result_obj["records"] if isinstance(r, dict)]
        return [result_obj]

    if isinstance(result_obj, str):
        parsed = _parse_json_text(result_obj)
        if parsed and isinstance(parsed.get("records"), list):
            return [r for r in parsed["records"] if isinstance(r, dict)]
        if parsed and isinstance(parsed.get("record"), dict):
            return [parsed["record"]]
        if parsed and parsed:
            return [parsed]

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
            item_type = str(items_def.get("type", "string"))
            if item_type == "integer":
                return {"type": "array", "maxItems": 25, "items": {"type": "integer"}}
            if item_type == "number":
                return {"type": "array", "maxItems": 25, "items": {"type": "number"}}
            if item_type == "boolean":
                return {"type": "array", "maxItems": 25, "items": {"type": "boolean"}}
            if item_type == "object":
                return {
                    "type": "array",
                    "maxItems": 10,
                    "items": {"type": "object", "additionalProperties": True},
                }
            return {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "maxLength": 200},
            }
        return {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 200}}
    # default/string
    return {"type": "string", "maxLength": 500}


def _build_records_response_schema(schema_obj: Dict[str, Any], max_records: int = 5) -> Dict[str, Any]:
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    record_properties: Dict[str, Any] = {}
    required: List[str] = []

    if isinstance(fields, dict):
        for field_name, field_def in fields.items():
            if not isinstance(field_name, str) or not isinstance(field_def, dict):
                continue
            record_properties[field_name] = _map_field_type_to_json_schema(field_def)
            if bool(field_def.get("required")):
                required.append(field_name)

    # Provenance fields we enrich server-side, but allow model to provide too.
    record_properties["_provenance"] = {"type": "object", "additionalProperties": True}

    record_schema: Dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
        "properties": record_properties,
    }
    if required:
        record_schema["required"] = required

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


def _estimate_max_tokens_for_response_schema(response_schema: Dict[str, Any]) -> int:
    """
    Estimate max_tokens budget from structured response schema complexity.
    Goal: avoid truncation while keeping costs bounded.
    """
    try:
        schema = response_schema.get("schema", {}) if isinstance(response_schema, dict) else {}
        records_def = ((schema.get("properties") or {}).get("records") or {})
        record_max_items = int(records_def.get("maxItems", 1) or 1)
        record_items = records_def.get("items", {}) if isinstance(records_def, dict) else {}
        record_props = record_items.get("properties", {}) if isinstance(record_items, dict) else {}

        # Estimate chars for one record from field definitions
        per_record_chars = 64  # JSON overhead per object
        for _, prop in record_props.items():
            if not isinstance(prop, dict):
                continue
            ptype = prop.get("type")
            if ptype == "string":
                per_record_chars += min(int(prop.get("maxLength", 120) or 120), 300) + 8
            elif ptype in ("integer", "number", "boolean"):
                per_record_chars += 16
            elif ptype == "array":
                items = prop.get("items", {}) if isinstance(prop.get("items"), dict) else {}
                item_type = items.get("type", "string")
                max_items = min(int(prop.get("maxItems", 10) or 10), 30)
                if item_type == "string":
                    max_len = min(int(items.get("maxLength", 60) or 60), 120)
                    per_record_chars += max_items * (max_len + 4)
                elif item_type == "object":
                    per_record_chars += max_items * 120
                else:
                    per_record_chars += max_items * 12
            else:
                per_record_chars += 40

        total_chars = 64 + (record_max_items * per_record_chars)
        # ~3.5 chars/token + safety buffer for formatting/completions
        estimated_tokens = int(math.ceil(total_chars / 3.5) + 300)
        # Keep within practical boundaries
        return max(1200, min(12000, estimated_tokens))
    except Exception:
        return 2400


def _estimate_markdown_tokens(markdown: str) -> int:
    # Rough token estimate: 1 token ~= 4 characters for mixed prose/JSON.
    return max(1, int(math.ceil(len(markdown) / 4)))


def _should_use_field_batch_mode(markdown_tokens: int, schema_obj: Dict[str, Any]) -> bool:
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    field_count = len(fields) if isinstance(fields, dict) else 0
    return markdown_tokens >= LONG_DOC_TOKEN_THRESHOLD and field_count >= MIN_FIELDS_FOR_BATCH_MODE


def _partition_fields(schema_obj: Dict[str, Any], batch_size: int) -> List[List[str]]:
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}
    field_names = list(fields.keys()) if isinstance(fields, dict) else []
    return [field_names[i : i + batch_size] for i in range(0, len(field_names), batch_size)]


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


def _merge_partial_records(
    partial_records: List[Dict[str, Any]],
    schema_obj: Dict[str, Any],
) -> List[Dict[str, Any]]:
    if not partial_records:
        return []

    merged: Dict[str, Any] = {}
    fields = schema_obj.get("fields", {}) if isinstance(schema_obj, dict) else {}

    for record in partial_records:
        for key, value in record.items():
            if key == "_provenance":
                merged["_provenance"] = _merge_provenance(merged.get("_provenance"), value)
                continue
            if value is None:
                continue
            field_def = fields.get(key, {}) if isinstance(fields, dict) else {}
            field_type = field_def.get("type")
            if field_type == "array" and isinstance(value, list):
                existing = merged.get(key, [])
                if not isinstance(existing, list):
                    existing = []
                for item in value:
                    if item not in existing:
                        existing.append(item)
                max_items = int(field_def.get("maxItems", 20) or 20)
                merged[key] = existing[:max_items]
            elif key not in merged:
                merged[key] = value

    return [merged] if merged else []


def _build_extraction_prompt(
    *,
    schema_document_id: str,
    file_id: str,
    schema_obj: Dict[str, Any],
    markdown: str,
    instructions: str,
    compact_mode: bool,
) -> str:
    mode_instructions = (
        "Return ONLY valid JSON with shape {\"records\":[...]} and no markdown/prose. "
        "If a field is not present, omit it or set null. "
        "Do not invent data. "
        "Do not produce exhaustive lists; include only the most salient values. "
        "For array fields, include at most 25 items (compact mode) "
        "to avoid overly large responses."
        if compact_mode
        else (
            "Return strict JSON with shape {\"records\":[...]} and no markdown/prose. "
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
        if value is None:
            return None

        field_type = field_def.get("type", "string")

        if field_type == "string":
            if isinstance(value, (str, int, float, bool)):
                return str(value)
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
            if isinstance(value, list):
                return value
            raise ValueError(f"Field '{field_name}' must be an array")

        if field_type == "object":
            if isinstance(value, dict):
                return value
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    # If model returned plain text for an object field, preserve it
                    # in a stable envelope instead of failing extraction.
                    return {"value": value}
            if isinstance(value, (int, float, bool)):
                return {"value": value}
            raise ValueError(f"Field '{field_name}' must be an object")

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

    for record in records:
        row = dict(record)
        for field_name, field_def in fields.items():
            if not isinstance(field_def, dict):
                continue
            required = bool(field_def.get("required", False))
            current_value = row.get(field_name)

            if required and current_value is None:
                raise ValueError(f"Missing required field: {field_name}")

            if current_value is not None:
                row[field_name] = _coerce_to_field_type(field_name, current_value, field_def)

                # Numeric range checks (keep parity with data-api validation)
                field_type = field_def.get("type", "string")
                if field_type in ("integer", "number"):
                    min_val = field_def.get("min")
                    max_val = field_def.get("max")
                    value = row[field_name]
                    if min_val is not None and value < min_val:
                        raise ValueError(f"Field '{field_name}' must be >= {min_val}")
                    if max_val is not None and value > max_val:
                        raise ValueError(f"Field '{field_name}' must be <= {max_val}")

        if "_provenance" not in row or not isinstance(row.get("_provenance"), dict):
            row["_provenance"] = {}
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


@router.post("")
async def extract_document(
    payload: ExtractRequest,
    session: AsyncSession = Depends(get_session),
    authorization: Optional[str] = Header(default=None),
):
    """
    Structured extraction endpoint used by UI and library triggers.
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
        # Schema-builder is for schema authoring/chat, not record extraction execution.
        # Keep explicit payload.agent_id overrides intact.
        resolved_agent_id = DEFAULT_EXTRACTION_AGENT_ID

    try:
        agent_uuid = uuid.UUID(str(resolved_agent_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid agent_id: {resolved_agent_id}") from exc

    logger.info(
        "Resolved extraction agent",
        extra={
            "file_id": payload.file_id,
            "schema_document_id": payload.schema_document_id,
            "resolved_agent_id": str(agent_uuid),
            "explicit_agent_override": payload.agent_id is not None,
        },
    )

    instructions = payload.prompt_override or (
        "Extract data from this document into records matching the schema. "
        "Include _provenance per extracted field with source text and char offsets when available."
    )
    markdown_tokens = _estimate_markdown_tokens(markdown)
    batch_mode = _should_use_field_batch_mode(markdown_tokens, schema_obj if isinstance(schema_obj, dict) else {})
    logger.info(
        "Extraction mode selection",
        extra={
            "file_id": payload.file_id,
            "schema_document_id": payload.schema_document_id,
            "markdown_tokens": markdown_tokens,
            "batch_mode": batch_mode,
        },
    )

    # Persist applied schema metadata immediately (without touching markdown/content).
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
                        "appliedAt": datetime.now(timezone.utc).isoformat(),
                    }
                }
            },
        )
    except Exception:
        # Non-fatal for extraction flow
        logger.warning(
            "Failed to persist applied schema metadata",
            extra={
                "file_id": payload.file_id,
                "schema_document_id": payload.schema_document_id,
            },
        )

    response_schema = _build_records_response_schema(schema_obj if isinstance(schema_obj, dict) else {})
    estimated_max_tokens = _estimate_max_tokens_for_response_schema(response_schema)
    run = None
    output: Dict[str, Any] = {}
    records: List[Dict[str, Any]] = []

    if batch_mode:
        # Acquire search token/client for field-centric retrieval over existing embeddings.
        try:
            search_api_token = await get_service_token(
                user_token=principal.token,
                user_id=principal.sub,
                target_audience="search-api",
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Failed to get search-api token: {exc}") from exc

        search_client = BusiboxClient(search_api_token)
        fields = (schema_obj.get("fields") or {}) if isinstance(schema_obj, dict) else {}
        schema_name = schema_doc.get("name")

        # Retrieve relevant chunks for each field using embedding search.
        field_context_tasks = []
        field_names = list(fields.keys()) if isinstance(fields, dict) else []
        for field_name in field_names:
            field_def = fields.get(field_name)
            if isinstance(field_def, dict):
                field_context_tasks.append(
                    _search_context_for_field(
                        search_client=search_client,
                        file_id=payload.file_id,
                        field_name=field_name,
                        field_def=field_def,
                        schema_name=schema_name,
                    )
                )
            else:
                field_context_tasks.append(asyncio.sleep(0, result=""))
        field_context_values = await asyncio.gather(*field_context_tasks)
        field_context_map = {name: field_context_values[idx] for idx, name in enumerate(field_names)}

        field_batches = _partition_fields(schema_obj if isinstance(schema_obj, dict) else {}, FIELD_BATCH_SIZE)
        semaphore = asyncio.Semaphore(MAX_PARALLEL_BATCH_RUNS)

        async def _run_field_batch(batch_index: int, batch_fields: List[str]) -> Dict[str, Any]:
            subset_fields = {f: fields[f] for f in batch_fields if f in fields}
            subset_schema = {
                "schemaName": schema_obj.get("schemaName"),
                "displayName": schema_obj.get("displayName"),
                "itemLabel": schema_obj.get("itemLabel"),
                "fields": subset_fields,
            }
            subset_response_schema = _build_records_response_schema(subset_schema, max_records=1)
            subset_max_tokens = _estimate_max_tokens_for_response_schema(subset_response_schema)

            context_blocks: List[str] = []
            for field_name in batch_fields:
                context_text = field_context_map.get(field_name, "")
                if context_text:
                    context_blocks.append(f"Field: {field_name}\nRelevant chunks:\n{context_text}")

            # Fallback to short markdown slice if search context is empty.
            retrieved_context = "\n\n".join(context_blocks).strip()
            if not retrieved_context:
                retrieved_context = markdown[:12000]

            batch_prompt = (
                f"{instructions}\n\n"
                "You are running FIELD-BATCHED extraction for long documents.\n"
                "Extract ONLY the fields listed in this batch schema.\n"
                "Return ONLY valid JSON matching the response schema (no prose).\n\n"
                f"Batch index: {batch_index}\n"
                f"Source file ID: {payload.file_id}\n"
                f"Schema document ID: {payload.schema_document_id}\n\n"
                f"Batch schema:\n```json\n{json.dumps(subset_schema, indent=2)}\n```\n\n"
                f"Retrieved evidence chunks:\n{retrieved_context}\n"
            )

            async with semaphore:
                async with SessionLocal() as batch_session:
                    batch_run = await create_run(
                        session=batch_session,
                        principal=principal,
                        agent_id=agent_uuid,
                        payload={
                            "prompt": batch_prompt,
                            "response_schema": subset_response_schema,
                            "max_tokens": subset_max_tokens,
                        },
                        scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                        purpose=f"structured-extraction-batch-{batch_index}",
                        agent_tier="complex",
                    )

                batch_output = batch_run.output or {}
                if not isinstance(batch_output, dict):
                    batch_output = {"result": str(batch_output)}
                batch_records = _extract_records(batch_output) if batch_run.status == "succeeded" else []
                return {
                    "run": batch_run,
                    "output": batch_output,
                    "records": batch_records,
                }

        batch_results = await asyncio.gather(
            *[_run_field_batch(i + 1, batch_fields) for i, batch_fields in enumerate(field_batches)]
        )
        successful_runs = [r["run"] for r in batch_results if r.get("run") and r["run"].status == "succeeded"]
        partial_records = []
        for result_item in batch_results:
            partial_records.extend(result_item.get("records", []))

        records = _merge_partial_records(partial_records, schema_obj if isinstance(schema_obj, dict) else {})
        if successful_runs:
            run = successful_runs[0]
            output = {"result": "field-batched", "batchRunIds": [str(r.id) for r in successful_runs]}
        elif batch_results:
            run = batch_results[0].get("run")
            output = batch_results[0].get("output", {})

    if not records:
        prompt = _build_extraction_prompt(
            schema_document_id=payload.schema_document_id,
            file_id=payload.file_id,
            schema_obj=schema_obj if isinstance(schema_obj, dict) else {},
            markdown=markdown,
            instructions=instructions,
            compact_mode=False,
        )
        run = await create_run(
            session=session,
            principal=principal,
            agent_id=agent_uuid,
            payload={
                "prompt": prompt,
                "response_schema": response_schema,
                "max_tokens": estimated_max_tokens,
            },
            scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
            purpose="structured-extraction",
            agent_tier="complex",
        )

        if run.status != "succeeded":
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Extraction run failed",
                    "run_id": str(run.id),
                    "status": run.status,
                    "output": run.output,
                },
            )

        output = run.output or {}
        if not isinstance(output, dict):
            output = {"result": str(output)}
        records = _extract_records(output)

        # Retry once with stricter compact instructions if first output is verbose/truncated.
        if not records:
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
            )
            retry_run = await create_run(
                session=session,
                principal=principal,
                agent_id=agent_uuid,
                payload={
                    "prompt": retry_prompt,
                    "response_schema": response_schema,
                    "max_tokens": estimated_max_tokens,
                },
                scopes=["agent.execute", "data.read", "data.write", "search.read", "graph.read", "graph.write"],
                purpose="structured-extraction-retry",
                agent_tier="complex",
            )
            if retry_run.status == "succeeded":
                retry_output = retry_run.output or {}
                if not isinstance(retry_output, dict):
                    retry_output = {"result": str(retry_output)}
                retry_records = _extract_records(retry_output)
                if retry_records:
                    run = retry_run
                    output = retry_output
                    records = retry_records

    if not records:
        run_id = str(run.id) if run is not None else "unknown"
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Agent did not return extractable records",
                "run_id": run_id,
                "output": output,
            },
        )

    run_id = str(run.id) if run is not None else "unknown"

    try:
        _populate_provenance_from_markdown(
            records=records,
            markdown=markdown,
            schema=schema_obj if isinstance(schema_obj, dict) else {},
        )
        enriched_records = _validate_and_enrich_records(
            records=records,
            schema=schema_obj if isinstance(schema_obj, dict) else {},
            file_id=payload.file_id,
            agent_id=str(agent_uuid),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Schema validation failed: {exc}") from exc

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
            response = getattr(exc, "response", None)
            if response is not None:
                detail_text: Any = None
                try:
                    detail_payload = response.json()
                    detail_text = detail_payload.get("detail", detail_payload)
                except Exception:
                    detail_text = response.text or str(exc)

                status_code = 422 if response.status_code in (400, 422) else 502
                raise HTTPException(
                    status_code=status_code,
                    detail=f"Failed to store extracted records: {detail_text}",
                ) from exc

            raise HTTPException(status_code=502, detail=f"Failed to store extracted records: {exc}") from exc

    # Persist extraction results summary + records in source doc metadata.
    # This augments data_files.metadata only and does not modify markdown content.
    try:
        await client.request(
            "PATCH",
            f"/files/{payload.file_id}",
            json={
                "metadata": {
                    "extraction": {
                        "schemaDocumentId": payload.schema_document_id,
                        "schemaName": schema_doc.get("name"),
                        "status": "completed",
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
            "Failed to persist extracted data to file metadata",
            extra={
                "file_id": payload.file_id,
                "schema_document_id": payload.schema_document_id,
                "run_id": run_id,
            },
        )

    return {
        "success": True,
        "runId": run_id,
        "fileId": payload.file_id,
        "schemaDocumentId": payload.schema_document_id,
        "agentId": str(agent_uuid),
        "recordCount": len(enriched_records),
        "records": enriched_records,
        "storage": insert_result,
    }
