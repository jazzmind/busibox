"""
Deterministic guards around the chat routing decision.

The fast-ack classifier is a small model. These rules catch the failure
modes seen in production review and override it without another model call:

- clarify loop      two clarifying questions in a row → search with the best
                    interpretation instead of asking again
- affirmation       "yes" / "ok" after an offer ("would you like me to…?") →
                    the offer becomes the query; "no" → short direct reply
- factual guard     a company-fact question (policy, rates, holidays, glossary
                    terms) settled without retrieval — answered from the model's
                    priors, or clarified instead of searched → search
- research offer    "yes" after the deep-research offer ("takes a few minutes,
                    run it?") → run deep research on the original question;
                    "no" → short close
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
# Explicit asks for a report / deep dive. Backs up the semantic router's
# deep_research route when the router is off or the query is phrased in a
# way its utterances do not cover.
_RESEARCH_RE = re.compile(
    r"\b(?:deep[- ]research|deep[- ]dive|research report|research (?:this|it|the topic) (?:thoroughly|in depth|in-depth)|"
    r"(?:comprehensive|in-depth|detailed|thorough|full) (?:report|analysis|overview|study|review|comparison)|"
    r"(?:write|prepare|compile|put together|draft|create|produce)(?: me)? a (?:\w+ )?(?:report|white ?paper|market analysis|competitive analysis|literature review)|"
    r"market (?:analysis|research|study)|competitive analysis|due diligence|white ?paper|literature review|"
    r"cite your sources|with sources)\b",
    re.IGNORECASE,
)

_GREETING_RE = re.compile(r"^\s*(?:hi|hello|hey|thanks|thank you|good (?:morning|afternoon|evening))\b", re.IGNORECASE)

# The closing question of the deep-research offer. Kept here so the guard can
# recognise the offer from message text alone when the routing annotation
# (``pending_research`` on the history entry) is missing.
DEEP_RESEARCH_OFFER_QUESTION = "Would you like me to run it?"


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


def _previous_user_message(history: Sequence[Dict[str, Any]], before: Dict[str, Any]) -> str:
    """The user message that preceded *before* in the history."""
    items = list(history or [])
    for i, msg in enumerate(items):
        if msg is before:
            for prior in reversed(items[:i]):
                if prior.get("role") == "user" and str(prior.get("content", "")).strip():
                    return str(prior["content"]).strip()
            break
    return ""


def pending_research_query(history: Sequence[Dict[str, Any]]) -> Optional[str]:
    """The question a deep-research offer is waiting on, if the last assistant turn was one.

    Prefers the ``pending_research`` annotation api/chat.py copies from the
    persisted routing decision; falls back to recognising the offer text and
    taking the user's previous message as the question.
    """
    last = _last_assistant(history)
    if not last:
        return None
    pending = str(last.get("pending_research") or "").strip()
    if pending:
        return pending
    if DEEP_RESEARCH_OFFER_QUESTION.lower() in str(last.get("content", "")).lower():
        return _previous_user_message(history, last) or None
    return None


def deep_research_offer_guard(query: str, history: Sequence[Dict[str, Any]]) -> GuardOutcome:
    """Resolve the user's answer to the deep-research offer.

    "yes" restores the original question as the query so the planner runs
    deep research on it (not on the word "yes"); "no" closes politely. Any
    other reply — including a rephrased question — falls through to normal
    routing, which may offer again for the new question.
    """
    text = (query or "").strip()
    if not text or len(text) > 40:
        return GuardOutcome()
    original = pending_research_query(history)
    if not original:
        return GuardOutcome()
    if _AFFIRMATIVE_RE.match(text):
        return GuardOutcome(
            triggered=True,
            name="deep_research_offer",
            reason="user accepted the deep-research offer",
            query=original,
            action_type="research",
            needs_tools=True,
        )
    if _NEGATIVE_RE.match(text):
        return GuardOutcome(
            triggered=True,
            name="deep_research_offer",
            reason="user declined the deep-research offer",
            direct_reply=(
                "Okay — I won't run the deep research. If you'd like a quick answer "
                "instead, ask the question again and I'll do a standard search."
            ),
            action_type="direct",
            needs_tools=False,
        )
    return GuardOutcome()


def factual_guard(query: str, action_type: str, needs_tools: bool, routing_source: str,
                  glossary_terms: Optional[List[str]] = None) -> GuardOutcome:
    """Never let the fast path settle a company-fact question without retrieval.

    Applies to both ``direct`` (answered from the model's priors) and
    ``clarify`` (asked a question instead of looking): both are decisions not
    to retrieve, and both are wrong for a question the documents can answer.
    """
    if needs_tools or action_type not in {"direct", "clarify"} or routing_source == "attachment_rule":
        return GuardOutcome()
    text = (query or "").strip()
    if len(text.split()) < 3 or _GREETING_RE.match(text):
        return GuardOutcome()
    hit = _FACTUAL_RE.search(text)
    terms = list(glossary_terms or [])
    if not hit and not terms:
        return GuardOutcome()
    reason = f"matched '{hit.group(0)}'" if hit else f"mentions glossary term {terms[0]}"
    settled = "answered from memory" if action_type == "direct" else "clarified instead of searched"
    return GuardOutcome(
        triggered=True,
        name="factual",
        reason=f"company-fact question {settled} ({reason})",
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


# Explicit asks for a file the user can open in Excel or Word. These turns
# need the create_spreadsheet / create_document tools with their full typed
# schemas, which only the loop-first path provides.
_DOCUMENT_RE = re.compile(
    r"\b(?:spreadsheet|excel(?: file| sheet| workbook)?|xlsx|workbook|"
    r"word (?:doc|document|file)|docx|"
    r"powerpoint|pptx|slide ?deck|slides|(?:a |the )?deck\b|presentation|"
    r"(?:export|save|download|turn|put|write|convert|give)(?: \w+){0,5} (?:as|to|into|in) (?:an? )?(?:excel|spreadsheet|word|docx|xlsx|powerpoint|pptx|slides|deck|presentation)(?: (?:file|document|doc|workbook|deck))?|"
    r"(?:downloadable|editable|printable) (?:file|document|report|version|deck))\b",
    re.IGNORECASE,
)
# Mentions that are *about* an existing file rather than asking for one.
_DOCUMENT_QUESTION_RE = re.compile(
    r"^\s*(?:what|where|who|when|why|how|which|does|do|is|are|can you (?:find|open|read|summari[sz]e|explain))\b",
    re.IGNORECASE,
)


def document_intent_guard(query: str) -> GuardOutcome:
    """Detect a request to produce a spreadsheet or Word document."""
    text = (query or "").strip()
    if len(text.split()) < 3:
        return GuardOutcome()
    hit = _DOCUMENT_RE.search(text)
    if not hit:
        return GuardOutcome()
    if _DOCUMENT_QUESTION_RE.match(text) and not re.search(r"\b(?:make|create|build|generate|export|produce|turn|put|save|write|give)\b", text, re.IGNORECASE):
        return GuardOutcome()  # asking about a file, not for one
    return GuardOutcome(
        triggered=True,
        name="document_intent",
        reason=f"file requested ('{hit.group(0)}')",
        action_type="analysis",
        needs_tools=True,
    )


def research_intent_guard(query: str) -> GuardOutcome:
    """Detect an explicit request for deep, multi-source research."""
    text = (query or "").strip()
    if len(text.split()) < 4:
        return GuardOutcome()
    hit = _RESEARCH_RE.search(text)
    if not hit:
        return GuardOutcome()
    return GuardOutcome(
        triggered=True,
        name="research_intent",
        reason=f"explicit research request ('{hit.group(0)}')",
        action_type="research",
        needs_tools=True,
    )
