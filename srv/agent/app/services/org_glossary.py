"""
Organizational glossary — company terminology injected into chat prompts.

Loads config/org_glossary.yaml once and renders a compact prompt section
so the assistant resolves internal acronyms (PREC, C-SAFE, ...) the way
an employee would, instead of guessing general-knowledge meanings.

Deliberately simple: a small static map, injected in full (a 20-term
glossary is ~300 tokens and sits in the cacheable prompt prefix). The
durable evolution is admin-curated org memory in the Config API; this
file is the immediate, zero-dependency version.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "org_glossary.yaml"

_terms: Optional[Dict[str, str]] = None


def _load() -> Dict[str, str]:
    global _terms
    if _terms is not None:
        return _terms
    try:
        if _DEFAULT_PATH.exists():
            with open(_DEFAULT_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            _terms = {
                str(k): str(v)
                for k, v in (raw.get("terms") or {}).items()
                if k and v
            }
            logger.info("org_glossary: loaded %d terms", len(_terms))
        else:
            _terms = {}
            logger.info("org_glossary: no glossary file at %s", _DEFAULT_PATH)
    except Exception as e:  # noqa: BLE001 — glossary failure must never break chat
        logger.warning("org_glossary: failed to load: %s", e)
        _terms = {}
    return _terms


def glossary_prompt_section() -> str:
    """Render the glossary as a prompt section, or '' when empty.

    Placed early in prompts so it benefits from prefix caching.
    """
    terms = _load()
    if not terms:
        return ""
    lines = ["## Company terminology (authoritative — prefer these meanings)"]
    for term, meaning in terms.items():
        lines.append(f"- {term}: {meaning}")
    return "\n".join(lines)


def reload_glossary() -> int:
    """Re-read the YAML (for a future admin reload endpoint)."""
    global _terms
    _terms = None
    return len(_load())
