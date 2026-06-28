"""Unit tests for purpose-to-model display name resolution."""

from app.api.llm import (
    CONFIGURABLE_PURPOSES,
    _build_purpose_map,
    _looks_like_encrypted_blob,
    _merge_model_entries,
    _resolve_purpose_target_model_name,
    _reverse_lookup_model_name,
)


def test_looks_like_encrypted_blob_detects_ciphertext():
    blob = "IATebipEoM8yptBH-S360VqPFEpIa-UUcq0ZLemFu4nTw_eYGQy4PMMcc17ss4Re0Su8wK9y4IJ3Q38fC1-z8mmUCi_dMs7-ut23"
    assert _looks_like_encrypted_blob(blob) is True
    assert _looks_like_encrypted_blob("qwen3.6-35b-a3b-fp8") is False
    assert _looks_like_encrypted_blob("openai/gpt-4.1") is False


def test_reverse_lookup_prefers_concrete_model_name():
    entries = [
        {
            "model_name": "fast",
            "litellm_params": {"model": "openai/qwen3.5-0.8b"},
        },
        {
            "model_name": "qwen3.5-0.8b-vllm",
            "litellm_params": {"model": "openai/qwen3.5-0.8b"},
        },
    ]
    purpose_names = set(CONFIGURABLE_PURPOSES)
    assert (
        _reverse_lookup_model_name("qwen3.5-0.8b", entries, purpose_names, "fast")
        == "qwen3.5-0.8b-vllm"
    )


def test_resolve_uses_backing_model_name_from_model_info():
    entries = [
        {
            "model_name": "agent",
            "litellm_params": {"model": "ENCRYPTED_BLOB_THAT_SHOULD_NOT_DISPLAY"},
            "model_info": {"backing_model_name": "qwen3.6-35b-a3b-vllm-fp8"},
        },
    ]
    purpose_names = set(CONFIGURABLE_PURPOSES)
    assert (
        _resolve_purpose_target_model_name("agent", entries[0], entries, purpose_names)
        == "qwen3.6-35b-a3b-vllm-fp8"
    )


def test_build_purpose_map_returns_friendly_names():
    entries = [
        {"model_name": "fast", "litellm_params": {"model": "openai/qwen3.5-0.8b"}},
        {"model_name": "qwen3.5-0.8b-vllm", "litellm_params": {"model": "openai/qwen3.5-0.8b"}},
        {"model_name": "default", "litellm_params": {"model": "openai/qwen3.6-35b-a3b-fp8"}},
    ]
    purpose_map = _build_purpose_map(entries)
    assert purpose_map["fast"] == "qwen3.5-0.8b-vllm"
    assert purpose_map["default"] == "qwen3.6-35b-a3b-fp8"


def test_merge_keeps_readable_model_when_db_has_ciphertext():
    config_entries = [
        {"model_name": "fast", "litellm_params": {"model": "openai/qwen3.5-0.8b"}},
    ]
    db_entries = [
        {
            "model_name": "fast",
            "litellm_params": {
                "model": "IATebipEoM8yptBH-S360VqPFEpIa-UUcq0ZLemFu4nTw_eYGQy4PMMcc17ss4Re0Su8wK9y4IJ3Q38fC1-z8mmUCi_dMs7-ut23",
            },
            "model_info": {"db_model": True},
        },
    ]
    merged = _merge_model_entries(db_entries, config_entries)
    fast = next(e for e in merged if e["model_name"] == "fast")
    assert fast["litellm_params"]["model"] == "openai/qwen3.5-0.8b"
