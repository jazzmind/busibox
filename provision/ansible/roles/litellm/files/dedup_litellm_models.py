#!/usr/bin/env python3
"""
Deduplicate LiteLLM model entries that accumulate when store_model_in_db=true
and config.yaml is re-applied on every deploy.

Classification
--------------
For each model_name that has > 1 DB row, entries are split into:

  config_entries  — backend model matches what config.yaml currently defines
  db_entries      — backend model differs (user set this via the admin UI)

Decision (per model_name)
-------------------------
1. If db_entries exist:
     a. Validate them by calling /chat/completions directly against the backend
        model string (not the purpose name, so the router is bypassed).
     b. If VALID  → keep DB entry, delete config_entries.
        If INVALID → delete DB entry, keep config_entry (fall back to config.yaml).
   Multiple db_entries: keep newest valid one.
   Multiple config_entries: keep newest one.

2. If all entries are config duplicates (same backend, just re-added):
     Keep newest, delete the rest.

Usage
-----
    python3 dedup_litellm_models.py \\
        --host localhost --port 4000 \\
        --key <litellm_master_key> \\
        [--config-yaml /etc/litellm/config.yaml] \\
        [--dry-run]
"""
import argparse
import json
import sys
import urllib.request
import urllib.error
from collections import defaultdict

try:
    import yaml as _yaml

    def _load_yaml(path):
        with open(path) as f:
            return _yaml.safe_load(f)
except ImportError:
    import re as _re

    def _load_yaml(path):
        """Minimal fallback YAML parser for the model_list block only."""
        with open(path) as f:
            content = f.read()
        model_list = []
        for block in _re.split(r'(?m)^\s{2}-\s+model_name:', content)[1:]:
            lines = block.splitlines()
            name = lines[0].strip()
            backend = ""
            for line in lines:
                m = _re.match(r'\s+model:\s+(\S+)', line)
                if m:
                    backend = m.group(1)
                    break
            if name:
                model_list.append({"model_name": name,
                                   "litellm_params": {"model": backend}})
        return {"model_list": model_list}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(host, port, key, method, path, body=None, timeout=15):
    url = f"http://{host}:{port}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} {method} {path}: {detail}") from exc


def _delete_model(host, port, key, model_id, dry_run, label=""):
    tag = f"id={model_id[:8]}...{(' ' + label) if label else ''}"
    if dry_run:
        print(f"[dedup]     DRY-RUN would delete {tag}")
        return True
    try:
        _request(host, port, key, "POST", "/model/delete", {"id": model_id})
        print(f"[dedup]     Deleted {tag}")
        return True
    except RuntimeError as exc:
        print(f"[dedup]     WARNING: could not delete {tag}: {exc}", file=sys.stderr)
        return False


def _test_backend(host, port, key, backend_model):
    """
    Call the backend model DIRECTLY (bypasses the purpose-name router so we
    validate the specific backend, not whatever the router picks).
    Returns True if the model responds with at least one choice.
    """
    try:
        resp = _request(host, port, key, "POST", "/chat/completions", {
            "model": backend_model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }, timeout=25)
        return bool(resp.get("choices"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def load_config_mapping(config_yaml_path):
    """Return {model_name: backend_model} from config.yaml's model_list."""
    try:
        cfg = _load_yaml(config_yaml_path)
        return {
            m["model_name"]: (m.get("litellm_params") or {}).get("model", "")
            for m in cfg.get("model_list", [])
            if "model_name" in m
        }
    except Exception as exc:
        print(f"[dedup] WARNING: could not parse {config_yaml_path}: {exc}",
              file=sys.stderr)
        return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--key", required=True, help="LiteLLM master key")
    parser.add_argument("--config-yaml", default="/etc/litellm/config.yaml",
                        help="Path to the deployed config.yaml on this host")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report what would be done without making changes")
    args = parser.parse_args()

    config_map = load_config_mapping(args.config_yaml)
    print(f"[dedup] Config.yaml defines {len(config_map)} model(s): "
          f"{list(config_map.keys())}")

    try:
        resp = _request(args.host, args.port, args.key, "GET", "/model/info")
    except RuntimeError as exc:
        print(f"[dedup] Failed to list models: {exc}", file=sys.stderr)
        sys.exit(1)

    models = resp.get("data", [])
    if not models:
        print("[dedup] No models returned — nothing to do")
        return

    by_name = defaultdict(list)
    for m in models:
        name = m.get("model_name", "")
        info = m.get("model_info") or {}
        model_id = info.get("id") or m.get("model_id") or m.get("id")
        backend = (m.get("litellm_params") or {}).get("model", "")
        created_at = str(info.get("created_at") or "")
        if name and model_id:
            by_name[name].append({
                "id": model_id,
                "backend": backend,
                "created_at": created_at,
            })

    deleted = 0

    for name, entries in sorted(by_name.items()):
        if len(entries) <= 1:
            continue

        config_backend = config_map.get(name, "")
        config_entries = [e for e in entries if e["backend"] == config_backend]
        db_entries     = [e for e in entries if e["backend"] != config_backend]

        # Sort each group newest-last so [-1] is the most recent
        config_entries.sort(key=lambda e: e["created_at"])
        db_entries.sort(key=lambda e: e["created_at"])

        print(f"[dedup] '{name}': {len(entries)} entries — "
              f"{len(config_entries)} config.yaml (backend={config_backend!r}), "
              f"{len(db_entries)} DB/admin-UI")

        to_delete = []

        if db_entries:
            # Validate the newest DB (admin UI) entry against its backend directly
            best_db = db_entries[-1]
            print(f"[dedup]   Testing DB entry backend={best_db['backend']!r} …")

            if args.dry_run:
                print("[dedup]   DRY-RUN: skipping live test")
                continue

            valid = _test_backend(args.host, args.port, args.key, best_db["backend"])
            if valid:
                print(f"[dedup]   DB entry is VALID → keeping it, removing config entries")
                to_delete += config_entries          # all config.yaml copies
                to_delete += db_entries[:-1]         # older DB entries if any
            else:
                print(f"[dedup]   DB entry is INVALID → removing it, keeping config entry")
                to_delete += db_entries              # all DB entries
                to_delete += config_entries[:-1]     # older config copies if any
        else:
            # Pure config duplicates (same backend repeated) — keep newest
            print(f"[dedup]   All config duplicates — keeping newest, removing older")
            to_delete += config_entries[:-1]

        for entry in to_delete:
            if _delete_model(args.host, args.port, args.key, entry["id"],
                             args.dry_run, label=f"backend={entry['backend']!r}"):
                deleted += 1

    if deleted:
        print(f"[dedup] Done — removed {deleted} duplicate model entrie(s)")
    else:
        print("[dedup] No duplicates removed")


if __name__ == "__main__":
    main()
