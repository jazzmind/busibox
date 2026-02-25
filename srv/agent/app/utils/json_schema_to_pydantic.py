"""
Convert a JSON Schema dict to a dynamic Pydantic BaseModel at runtime.

Used to bridge runtime ``response_schema`` dicts (from extraction and
schema-builder APIs) into PydanticAI's ``output_type`` / ``NativeOutput``
mechanism, giving us both ``response_format`` enforcement *and* automatic
response validation with retry.

Handles the JSON Schema subset used by our extraction and schema-builder
schemas: basic types, enums, arrays, nested objects, and
``additionalProperties`` (both ``true`` and ``{schema}`` forms).
"""

from __future__ import annotations

import logging
from enum import Enum as _Enum
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, ConfigDict, create_model

logger = logging.getLogger(__name__)

_SCALAR_MAP: Dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
}

_counter = 0


def _unique_name(base: str) -> str:
    global _counter
    _counter += 1
    return f"{base}_{_counter}"


def json_schema_to_pydantic(
    response_schema: Dict[str, Any],
) -> Type[BaseModel]:
    """
    Convert a ``response_schema`` envelope (with ``name``, ``strict``,
    ``schema`` keys) into a Pydantic model.

    If the dict already *is* a raw JSON Schema (has ``type`` / ``properties``
    at the top level), it is used directly.
    """
    schema_name = response_schema.get("name", "DynamicOutput")
    inner = response_schema.get("schema", response_schema)
    return _build_model(inner, _sanitise_class_name(schema_name))


def _sanitise_class_name(raw: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw).strip("_") or "Model"


def _build_model(
    schema: Dict[str, Any],
    name: str,
) -> Type[BaseModel]:
    """Recursively build a Pydantic model from a JSON Schema ``object`` node."""
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    additional = schema.get("additionalProperties", True)

    field_defs: Dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        python_type = _resolve_type(prop_schema, f"{name}_{prop_name}")
        if prop_name in required:
            field_defs[prop_name] = (python_type, ...)
        else:
            field_defs[prop_name] = (Optional[python_type], None)

    config: Dict[str, Any] = {}
    if additional is False:
        config["extra"] = "forbid"
    elif additional is True:
        config["extra"] = "allow"
    elif isinstance(additional, dict):
        config["extra"] = "allow"

    model = create_model(
        _unique_name(name),
        __config__=type("Config", (), config) if config else None,
        **field_defs,
    )
    return model


def _resolve_type(prop_schema: Dict[str, Any], context_name: str) -> type:
    """Map a JSON Schema property definition to a Python type annotation."""
    prop_type = prop_schema.get("type", "string")

    if "enum" in prop_schema:
        values = prop_schema["enum"]
        if values and all(isinstance(v, str) for v in values):
            enum_cls = _Enum(_unique_name(f"{context_name}_Enum"), {v: v for v in values})
            return enum_cls
        return str

    if prop_type == "array":
        items_schema = prop_schema.get("items", {"type": "string"})
        item_type = _resolve_type(items_schema, f"{context_name}_item")
        return List[item_type]  # type: ignore[valid-type]

    if prop_type == "object":
        return _resolve_object_type(prop_schema, context_name)

    return _SCALAR_MAP.get(str(prop_type), str)


def _resolve_object_type(prop_schema: Dict[str, Any], context_name: str) -> type:
    """
    Resolve an ``object``-typed schema node.

    Three patterns:

    1. Has ``properties`` → build a nested Pydantic model.
    2. Has ``additionalProperties`` with a sub-schema (but no ``properties``)
       → ``Dict[str, ValueType]``.
    3. Neither → ``Dict[str, Any]``.
    """
    properties = prop_schema.get("properties")
    additional = prop_schema.get("additionalProperties")

    if properties:
        return _build_model(prop_schema, context_name)

    if isinstance(additional, dict) and additional:
        value_type = _resolve_type(additional, f"{context_name}_val")
        return Dict[str, value_type]  # type: ignore[valid-type]

    return Dict[str, Any]
