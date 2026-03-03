#!/usr/bin/env bash
#
# Generate model_config.yml for LiteLLM/vLLM routing (non-interactive)
#
# EXECUTION CONTEXT:
#   - Proxmox host (recommended, as root)
#   - Admin workstation with SSH access to Proxmox-host-mounted repo
#
# PURPOSE:
#   Build provision/ansible/group_vars/all/model_config.yml from:
#   - model_registry.yml (purpose -> model mapping)
#   - local GPU inventory (nvidia-smi)
#   - existing model_config.yml (preserve technical metadata)
#
# INPUTS (optional env vars):
#   LLM_BACKEND           Default: auto-detect (vllm if nvidia-smi exists, else mlx)
#   LLM_TIER/MODEL_TIER   Currently informational only (kept for future tier filtering)
#   GPU_COUNT             Override detected GPU count
#   NETWORK_BASE_OCTETS   For informational output (e.g. 10.96.200)
#
# OUTPUT:
#   provision/ansible/group_vars/all/model_config.yml
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

REGISTRY_FILE="${REPO_ROOT}/provision/ansible/group_vars/all/model_registry.yml"
MODEL_CONFIG_FILE="${REPO_ROOT}/provision/ansible/group_vars/all/model_config.yml"

if [[ ! -f "${REGISTRY_FILE}" ]]; then
  echo "[ERROR] model_registry.yml not found: ${REGISTRY_FILE}" >&2
  exit 1
fi

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "/opt/model-downloader/bin/python3" ]]; then
    PYTHON_BIN="/opt/model-downloader/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    echo "[ERROR] python3 not found" >&2
    exit 1
  fi
fi

LLM_BACKEND="${LLM_BACKEND:-}"
if [[ -z "${LLM_BACKEND}" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    LLM_BACKEND="vllm"
  else
    LLM_BACKEND="mlx"
  fi
fi

if [[ "${LLM_BACKEND}" != "vllm" ]]; then
  echo "[INFO] LLM_BACKEND=${LLM_BACKEND}; skipping model_config generation (only needed for vllm)." >&2
  exit 0
fi

GPU_COUNT_ENV="${GPU_COUNT:-}"
if [[ -z "${GPU_COUNT_ENV}" ]]; then
  if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_COUNT_ENV="$(nvidia-smi --query-gpu=index --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ')"
  else
    GPU_COUNT_ENV="0"
  fi
fi

if [[ -z "${GPU_COUNT_ENV}" || "${GPU_COUNT_ENV}" -lt 1 ]]; then
  echo "[WARN] No GPUs detected; writing empty vLLM assignments." >&2
fi

echo "[INFO] Generating model_config.yml" >&2
echo "[INFO] Backend: ${LLM_BACKEND}" >&2
echo "[INFO] GPU count: ${GPU_COUNT_ENV}" >&2
echo "[INFO] Tier: ${LLM_TIER:-${MODEL_TIER:-unset}}" >&2
echo "[INFO] Network base: ${NETWORK_BASE_OCTETS:-unset}" >&2

export REPO_ROOT REGISTRY_FILE MODEL_CONFIG_FILE GPU_COUNT_ENV

"${PYTHON_BIN}" <<'PYEOF'
import os
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    print(f"[ERROR] PyYAML unavailable: {exc}", file=sys.stderr)
    sys.exit(1)

repo_root = Path(os.environ["REPO_ROOT"])
registry_file = Path(os.environ["REGISTRY_FILE"])
model_config_file = Path(os.environ["MODEL_CONFIG_FILE"])
gpu_count = int(os.environ.get("GPU_COUNT_ENV", "0") or "0")

with registry_file.open("r", encoding="utf-8") as f:
    registry = yaml.safe_load(f) or {}

existing: Dict[str, Any] = {}
if model_config_file.exists():
    with model_config_file.open("r", encoding="utf-8") as f:
        existing = yaml.safe_load(f) or {}

available_models: Dict[str, Any] = registry.get("available_models", {}) or {}
model_purposes: Dict[str, str] = registry.get("model_purposes", {}) or {}
existing_models: Dict[str, Any] = (existing.get("models", {}) or {})

def estimate_params_billions(model_key: str, model_name: str) -> float:
    joined = f"{model_key} {model_name}".lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*b", joined)
    if m:
        return float(m.group(1))
    # Fallbacks for known tiny models
    if "0.6b" in joined:
        return 0.6
    if "phi-4" in joined:
        return 4.0
    return 12.0

def classify_size(model_key: str, model_name: str) -> str:
    return "small" if estimate_params_billions(model_key, model_name) < 10 else "large"

def _normalize_model_key(key: str) -> str:
    return key.replace(".", "-").replace("_", "-").lower()

def get_explicit_gpu_assignments() -> Dict[str, str]:
    """Read GPU_ASSIGN_<MODEL_KEY>=<GPU> from environment variables."""
    assignments: Dict[str, str] = {}
    for key, val in os.environ.items():
        if key.startswith("GPU_ASSIGN_"):
            model_key = key[len("GPU_ASSIGN_"):].lower().replace("_", "-")
            assignments[model_key] = val.strip("'\"")
    return assignments

def get_explicit_tp_overrides() -> Dict[str, int]:
    """Read GPU_TP_<MODEL_KEY>=<tp_count> from environment variables."""
    overrides: Dict[str, int] = {}
    for key, val in os.environ.items():
        if key.startswith("GPU_TP_"):
            model_key = key[len("GPU_TP_"):].lower().replace("_", "-")
            try:
                overrides[model_key] = int(val.strip("'\""))
            except ValueError:
                pass
    return overrides

def find_explicit_gpu(model_key: str, explicit: Dict[str, str]) -> str | None:
    """Match a model key against explicit GPU assignments, handling naming variants."""
    if model_key in explicit:
        return explicit[model_key]
    normalized = _normalize_model_key(model_key)
    for k, v in explicit.items():
        if _normalize_model_key(k) == normalized:
            return v
    return None

def find_explicit_tp(model_key: str, tp_overrides: Dict[str, int]) -> int | None:
    """Match a model key against explicit TP overrides."""
    if model_key in tp_overrides:
        return tp_overrides[model_key]
    normalized = _normalize_model_key(model_key)
    for k, v in tp_overrides.items():
        if _normalize_model_key(k) == normalized:
            return v
    return None

def assign_models(vllm_models: List[Tuple[str, Dict[str, Any]]], gpus: int) -> Dict[str, Dict[str, Any]]:
    assigned: Dict[str, Dict[str, Any]] = {}
    port = 8000
    explicit = get_explicit_gpu_assignments()
    tp_overrides = get_explicit_tp_overrides()

    # First pass: assign models with explicit GPU assignments
    explicitly_assigned = set()
    for model_key, entry in vllm_models:
        gpu_val = find_explicit_gpu(model_key, explicit)
        if gpu_val is not None:
            tp_override = find_explicit_tp(model_key, tp_overrides)
            if tp_override is not None:
                tp = tp_override
            elif "," in gpu_val:
                tp = len(gpu_val.split(","))
            else:
                tp = 1
            assigned[model_key] = {"gpu": gpu_val, "port": port, "tensor_parallel": tp}
            port += 1
            explicitly_assigned.add(model_key)

    # Second pass: auto-assign remaining models
    remaining = [(k, e) for k, e in vllm_models if k not in explicitly_assigned]
    small = []
    large = []
    for model_key, entry in remaining:
        model_name = entry.get("model_name", "")
        (small if classify_size(model_key, model_name) == "small" else large).append((model_key, entry))

    # Small models: prefer GPU 0, then 1
    small_index = 0
    for model_key, entry in small:
        if gpus <= 0:
            break
        if small_index == 0:
            gpu = "0"
        elif gpus >= 2:
            gpu = "1"
        else:
            gpu = "0"
        assigned[model_key] = {"gpu": gpu, "port": port, "tensor_parallel": 1}
        port += 1
        small_index += 1

    # Large models: prefer 2,3 with TP=2 when available
    for model_key, entry in large:
        if gpus >= 4:
            assigned[model_key] = {"gpu": "2,3", "port": port, "tensor_parallel": 2}
        elif gpus >= 3:
            assigned[model_key] = {"gpu": "2", "port": port, "tensor_parallel": 1}
        elif gpus >= 2:
            assigned[model_key] = {"gpu": "1", "port": port, "tensor_parallel": 1}
        elif gpus == 1:
            assigned[model_key] = {"gpu": "0", "port": port, "tensor_parallel": 1}
        else:
            continue
        port += 1
    return assigned

# Build unique list of vLLM model keys referenced by production purposes
vllm_model_keys = []
for _purpose, model_key in model_purposes.items():
    if model_key in vllm_model_keys:
        continue
    model_entry = available_models.get(model_key, {})
    if (model_entry.get("provider", "") or "").lower() == "vllm":
        vllm_model_keys.append(model_key)

vllm_entries = [(k, available_models.get(k, {})) for k in vllm_model_keys]
routing = assign_models(vllm_entries, gpu_count)

output_models: Dict[str, Any] = {}

# Start with existing config and then overlay authoritative routing/provider data
for model_name, cfg in existing_models.items():
    output_models[model_name] = dict(cfg or {})

for model_key in vllm_model_keys:
    entry = available_models.get(model_key, {}) or {}
    model_name = entry.get("model_name", "")
    if not model_name:
        continue
    merged = dict(output_models.get(model_name, {}))
    merged["provider"] = "vllm"
    merged["model_key"] = model_key

    # Preserve technical/tuning hints from registry when present
    for key in (
        "gpu_memory_utilization",
        "max_model_len",
        "max_num_seqs",
        "cpu_offload_gb",
        "hf_overrides",
        "tool_calling",
        "tool_call_parser",
        "tool_chat_template",
    ):
        if key in entry and entry[key] is not None:
            merged[key] = entry[key]

    assigned = routing.get(model_key)
    if assigned:
        merged["assigned"] = True
        merged["gpu"] = assigned["gpu"]
        merged["port"] = int(assigned["port"])
        merged["tensor_parallel"] = int(assigned["tensor_parallel"])
    else:
        merged["assigned"] = False
        merged.pop("gpu", None)
        merged.pop("port", None)
        merged.pop("tensor_parallel", None)

    output_models[model_name] = merged

result = {"models": output_models}
model_config_file.parent.mkdir(parents=True, exist_ok=True)
with model_config_file.open("w", encoding="utf-8") as f:
    yaml.safe_dump(result, f, sort_keys=False)

assigned_count = sum(
    1 for key in vllm_model_keys
    if (available_models.get(key, {}) or {}).get("model_name", "") in output_models
    and output_models[(available_models.get(key, {}) or {}).get("model_name", "")].get("assigned") is True
)
print(f"[INFO] Wrote model_config.yml with {assigned_count} assigned vLLM model(s)", file=sys.stderr)
print(f"[INFO] Output: {model_config_file}", file=sys.stderr)
PYEOF

echo "[SUCCESS] model_config.yml generation complete" >&2
