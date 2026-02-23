#!/usr/bin/env bash
#
# Format host-agent responses for terminal output.
# - Parses SSE lines (data: {...})
# - Parses plain JSON responses
# - Decodes escaped ANSI/unicode via JSON parsing
#
set -euo pipefail

RAW_INPUT="$(cat)"

HOST_AGENT_RAW="$RAW_INPUT" python3 - <<'PY'
import json
import os
import sys


def emit(message):
    if message is None:
        return
    text = str(message).rstrip("\r\n")
    if text:
        print(text)


raw = os.environ.get("HOST_AGENT_RAW", "")
if not raw.strip():
    # Treat empty input as failure so curl pipeline errors surface.
    sys.exit(1)

lines = [line.strip() for line in raw.splitlines() if line.strip()]
handled = False

# First pass: SSE ("data: {...}")
for line in lines:
    if not line.startswith("data:"):
        continue
    payload = line[5:].strip()
    if not payload:
        continue
    try:
        obj = json.loads(payload)
    except Exception:
        emit(payload)
        handled = True
        continue

    handled = True
    message = obj.get("message")
    if message:
        emit(message)

# Second pass: plain JSON responses (mlx/stop style)
for line in lines:
    if line.startswith("data:"):
        continue
    try:
        obj = json.loads(line)
    except Exception:
        if not handled:
            emit(line)
        continue

    handled = True
    if isinstance(obj, dict):
        message = obj.get("message")
        if message:
            emit(message)
            continue
        primary = obj.get("primary", {}).get("message") if isinstance(obj.get("primary"), dict) else None
        fast = obj.get("fast", {}).get("message") if isinstance(obj.get("fast"), dict) else None
        if primary:
            emit(primary)
        if fast:
            emit(fast)
        if not (message or primary or fast):
            emit(obj)
    else:
        emit(obj)

if not handled:
    emit(raw.strip())
PY
