"""
Deterministic guards around the chat routing decision.

The fast-ack classifier is a small model. These rules catch the failure
modes seen in production review and override it without another model call:

- clarify loop      two clarifying questions in a row → search with the best
                    interpretation instead of asking again
- affirmation       "yes" / "ok" after an offer ("would you like me to…?") →
                    the offer becomes the query; "no" → short direct reply
- factual guard     a direct (no-tools) answer to a question about company
                    facts (policy, rates, holidays, glossary terms) → search
- tool-step budget  cap the number of planned steps per turn

All functions are pure; the agent decides how to act on the result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:yes|yes please|yeah|yep|yup|sure|ok|okay|okay please|please do|please|go ahead|"
    r"do it|sounds good|that works|correct|right|absolutely|of course|y)\s*[.!]*\s*$",
    re.IGNORECASE,
)
_NEGATIVE_RE = re.compile(
    r"^\s*(?:no|nope|nah|no thanks|no thank you|not now|not right now|no need|n)\s*[.!]*\s*$",
    re.IGNORECASE,
)

# Questions whose answer is a company fact. The classifier sometimes marks
# these "direct" and the 0.8B model then answers from its own priors.
_FACTUAL_RE = re.compile(
    r"\b(?:how (?:many|much|long)|when (?:is|are|do|does|did|will|was)|"
    r"what(?:'s| is| are| was| were) (?:our|the company'?s?|my|jay cashman'?s?|cashman'?s?)|"
    r"who (?:is|are|was) (?:our|my|the company'?s?)|"
    r"polic(?:y|ies)|procedure|per[- ]diem|holiday|holidays|vacation|pto|sick (?:leave|time|days)|"
    r"benefit|benefits|deadline|reimburse|expense|payroll|handbook|safety|training|"
    r"overtime|insurance|401\s?k|bonus|rate|rates|dress code|onboarding|timesheet|"
    r"mileage)\b",
    re.IGNORECASE,
)
_GREETING_RE = re.compile(r"^\s*(?:hi|hello|hey|thanks|thank you|good (?:morning|afternoon|evening))\b", re.IGNORECASE)


@dataclass
class GuardOutcome:
    """What a guard decided. `override` fields are applied by the agent."""

    triggered: bool = False
    name: str = ""
    reason: str = ""
    query: Optional[str] = None            # rewritten query, if any
    action_type: Optional[str] = None
    needs_tools: Optional[bool] = None
    direct_reply: Optional[str] = None     # answer now, no tools, no classifier


def _last_assistant(history: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for msg in reversed(list(history or [])):
        if msg.get("role") == "assistant" and str(msg.get("content", "")).strip():
            return msg
    return None


def _last_sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    parts = re.split(r"(?<=[.!?])\s+", text)
    return parts[-1].strip() if parts else text.strip()


def _ends_with_question(text: str) -> bool:
    return str(text or "").rstrip().endswith("?")


def previous_turn_was_clarify(history: Sequence[Dict[str, Any]]) -> bool:
    """True when the assistant's last message was a clarifying question.

    Uses the persisted routing action when api/chat.py annotated it, and a
    conservative heuristic (short message ending in '?') otherwise.
    """
    last = _last_assistant(history)
    if not last:
        return False
    if last.get("action_type") == "clarify":
        return True
    content = str(last.get("content", ""))
    return _ends_with_question(content) and len(content) <= 300


def clarify_loop_guard(action_type: str, history: Sequence[Dict[str, Any]]) -> GuardOutcome:
    """Refuse a second clarifying question in a row."""
    if action_type != "clarify" or not previous_turn_was_clarify(history):
        return GuardOutcome()
    return GuardOutcome(
        triggered=True,
        name="clarify_loop",
        reason="previous assistant turn already asked a clarifying question",
        action_type="search",
        needs_tools=True,
    )


def affirmation_guard(query: str, history: Sequence[Dict[str, Any]],
                      ends_with_yes_no_question) -> GuardOutcome:
    """Turn 'yes' / 'no' after an offer into the offer itself or a short close."""
    text = (query or "").strip()
    if not text or len(text) > 40:
        return GuardOutcome()
    last = _last_assistant(history)
    if not last:
        return GuardOutcome()
    offer_text = str(last.get("content", ""))
    if not ends_with_yes_no_question(offer_text):
        return GuardOutcome()
    offer = _last_sentence(offer_text)
    if _AFFIRMATIVE_RE.match(text):
        return GuardOutcome(
            triggered=True,
            name="affirmation",
            reason="user accepted the assistant's offer",
            query=f"Yes. Please go ahead with what you offered: {offer}",
            action_type="search",
            needs_tools=True,
        )
    if _NEGATIVE_RE.match(text):
        return GuardOutcome(
            triggered=True,
            name="affirmation",
            reason="user declined the assistant's offer",
            direct_reply="Okay — I'll leave it there. Let me know if you'd like anything else.",
            action_type="direct",
            needs_tools=False,
        )
    return GuardOutcome()


def factual_guard(query: str, action_type: str, needs_tools: bool, routing_source: str,
                  glossary_terms: Optional[List[str]] = None) -> GuardOutcome:
    """Never let the fast path answer a company-fact question from memory."""
    if needs_tools or action_type != "direct" or routing_source == "attachment_rule":
        return GuardOutcome()
    text = (query or "").strip()
    if len(text.split()) < 3 or _GREETING_RE.match(text):
        return GuardOutcome()
    hit = _FACTUAL_RE.search(text)
    terms = list(glossary_terms or [])
    if not hit and not terms:
        return GuardOutcome()
    reason = f"matched '{hit.group(0)}'" if hit else f"mentions glossary term {terms[0]}"
    return GuardOutcome(
        triggered=True,
        name="factual",
        reason=f"direct answer to a company-fact question ({reason})",
        action_type="search",
        needs_tools=True,
    )


def cap_plan_steps(steps: List[Any], max_steps: int, protected: Sequence[str] = ("deep_research",)) -> List[Any]:
    """Keep at most `max_steps` steps, never dropping protected tools."""
    if max_steps <= 0 or len(steps) <= max_steps:
        return list(steps)
    kept: List[Any] = [s for s in steps if getattr(s, "tool", None) in protected]
    for step in steps:
        if len(kept) >= max_steps:
            break
        if not any(s is step for s in kept):
            kept.append(step)
    # Preserve original order.
    order = {id(s): i for i, s in enumerate(steps)}
    kept.sort(key=lambda s: order[id(s)])
    return kept
