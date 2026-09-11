"""
Tiered grounding policy for answer synthesis.

After the tools have run we know what evidence exists. Instead of one generic
"answer from the context" instruction, the synthesis prompt gets a tier-
specific rule block chosen from the evidence:

    attachment  the user uploaded a file — answer from it first
    documents   strong company-document hits — answer only from them, cite
    web         no strong documents but web results — answer from the web,
                label it as not from company documents
    estimate    nothing retrieved and the user wants a figure/date — give a
                best estimate with stated assumptions instead of refusing
    knowledge   nothing retrieved, general question — answer from knowledge,
                say the documents do not cover it

Universal rules (absence, recency, conflicts, honesty about tool failures)
are appended to every tier. The assessment is also emitted as a thought so
it is persisted in messages.routing_decision for evaluation.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

TIER_ATTACHMENT = "attachment"
TIER_DOCUMENTS = "documents"
TIER_WEB = "web"
TIER_ESTIMATE = "estimate"
TIER_KNOWLEDGE = "knowledge"

# Questions whose natural answer is a number or a date. When nothing was
# retrieved these get an estimate with assumptions rather than a refusal.
_FIGURE_RE = re.compile(
    r"\b(how (?:many|much|long|often|far|old)|when|what (?:date|day|year|time|percent|rate|cost|price|number)"
    r"|number of|count of|total|average|per[- ]diem|cost|price|rate|budget|deadline|due date"
    r"|percent|%|days?|hours?|weeks?|months?|years?)\b",
    re.IGNORECASE,
)

_DATE_KEYS = ("document_date", "modified_at", "updated_at", "last_modified", "created_at", "date")


@dataclass
class GroundingAssessment:
    tier: str
    doc_hits: int = 0
    doc_max_score: float = 0.0
    doc_files: List[str] = field(default_factory=list)
    web_hits: int = 0
    research_report: bool = False
    attachment_present: bool = False
    newest_doc_date: Optional[str] = None
    oldest_doc_date: Optional[str] = None
    tool_errors: List[str] = field(default_factory=list)
    wants_figure: bool = False
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def _item_date(item: Any) -> Optional[date]:
    for key in _DATE_KEYS:
        parsed = _parse_date(_get(item, key))
        if parsed:
            return parsed
    meta = _get(item, "metadata")
    if isinstance(meta, dict):
        for key in _DATE_KEYS:
            parsed = _parse_date(meta.get(key))
            if parsed:
                return parsed
    return None


def assess_grounding(
    query: str,
    tool_results: Dict[str, Any],
    resolved_attachments: Optional[List[Dict[str, Any]]] = None,
    *,
    strong_doc_score: float = 0.65,
) -> GroundingAssessment:
    """Classify the evidence gathered for this turn into a grounding tier."""
    assessment = GroundingAssessment(tier=TIER_KNOWLEDGE)
    assessment.wants_figure = bool(_FIGURE_RE.search(query or ""))
    assessment.attachment_present = any(
        (a.get("content") or "").strip() or a.get("source_kind") in {"image", "no_text"}
        for a in (resolved_attachments or [])
        if isinstance(a, dict)
    )

    dates: List[date] = []
    for tool_name, result in (tool_results or {}).items():
        if result is None or tool_name == "llm_response":
            continue
        error = _get(result, "error")
        if error and not _get(result, "found", True) and not _get(result, "success", True):
            assessment.tool_errors.append(f"{tool_name}: {str(error)[:120]}")
        elif error and tool_name in {"web_search", "document_search"} and not _get(result, "results"):
            assessment.tool_errors.append(f"{tool_name}: {str(error)[:120]}")

        if tool_name == "document_search":
            items = _get(result, "results") or []
            assessment.doc_hits = len(items)
            for item in items:
                score = float(_get(item, "score", 0.0) or 0.0)
                assessment.doc_max_score = max(assessment.doc_max_score, score)
                fname = _get(item, "filename")
                if fname and fname not in assessment.doc_files:
                    assessment.doc_files.append(str(fname))
                parsed = _item_date(item)
                if parsed:
                    dates.append(parsed)
        elif tool_name in {"web_search", "web_extract"}:
            items = _get(result, "results") or _get(result, "pages") or []
            assessment.web_hits += len(items)
        elif tool_name == "deep_research":
            if _get(result, "success"):
                assessment.research_report = True
                assessment.web_hits += len(_get(result, "sources") or []) or 1

    if dates:
        assessment.newest_doc_date = max(dates).isoformat()
        assessment.oldest_doc_date = min(dates).isoformat()

    if assessment.attachment_present:
        assessment.tier = TIER_ATTACHMENT
        assessment.reasons.append("user attachment resolved")
    elif assessment.doc_hits and assessment.doc_max_score >= strong_doc_score:
        assessment.tier = TIER_DOCUMENTS
        assessment.reasons.append(f"document hit score {assessment.doc_max_score:.2f} >= {strong_doc_score:.2f}")
    elif assessment.web_hits or assessment.research_report:
        assessment.tier = TIER_WEB
        assessment.reasons.append("web results only" if not assessment.doc_hits else "documents weak, web results present")
    elif assessment.wants_figure:
        assessment.tier = TIER_ESTIMATE
        assessment.reasons.append("nothing retrieved; question asks for a figure or date")
    else:
        assessment.tier = TIER_KNOWLEDGE
        assessment.reasons.append("nothing retrieved")
    if assessment.doc_hits and assessment.tier != TIER_DOCUMENTS:
        assessment.reasons.append(f"weak document hits (max score {assessment.doc_max_score:.2f})")
    return assessment


_TIER_RULES: Dict[str, str] = {
    TIER_ATTACHMENT: (
        "Answer from the attached document(s) first. Use company documents or web "
        "results only to add context, and say which is which. If the attachment has "
        "no extractable text, say so and do not describe contents you cannot see."
    ),
    TIER_DOCUMENTS: (
        "Answer ONLY from the company documents in the tool results and cite every "
        "factual claim with the required [Source: filename, p.N](doc:...) format. Do "
        "not add facts from memory. If the documents answer only part of the "
        "question, answer that part and say plainly what they do not cover."
    ),
    TIER_WEB: (
        "No sufficiently relevant company document was found; the evidence below "
        "comes from the web. Say so in the first sentence (e.g. 'I didn't find this "
        "in the company documents; from public sources: ...'), answer from the web "
        "results, and cite their URLs. Do not present web information as company policy."
    ),
    TIER_ESTIMATE: (
        "Nothing relevant was retrieved and the question asks for a number or date. "
        "Do NOT refuse. In the first sentence say the documents do not contain it, "
        "then give your best estimate, label it clearly as an estimate, and state the "
        "assumptions it rests on (one or two lines). Suggest where the exact figure "
        "would be found."
    ),
    TIER_KNOWLEDGE: (
        "Nothing relevant was retrieved. In the first sentence say the company "
        "documents do not cover this, then answer from general knowledge and mark "
        "it as such. Do not invent company-specific details."
    ),
}


def grounding_prompt_section(assessment: GroundingAssessment, today: Optional[date] = None,
                             stale_after_months: int = 12) -> str:
    """Render the tier rules plus the universal rules as a prompt section."""
    today = today or date.today()
    lines = ["## Grounding Policy (tier: %s)" % assessment.tier, _TIER_RULES[assessment.tier], ""]
    lines.append("Always:")
    lines.append(
        "- Absence rule: if the documents do not mention something the user asked about, "
        "say so explicitly rather than implying it is covered."
    )
    if assessment.newest_doc_date:
        newest = _parse_date(assessment.newest_doc_date)
        age_months = None
        if newest:
            age_months = (today.year - newest.year) * 12 + (today.month - newest.month)
        stale = age_months is not None and age_months > stale_after_months
        lines.append(
            f"- Recency rule: the newest matching document is dated {assessment.newest_doc_date}"
            + (f" (about {age_months} months old)" if age_months is not None else "")
            + ". "
            + ("That is older than %d months — say the information may be outdated and give the document date."
               % stale_after_months if stale else "Mention the document date when it matters to the answer.")
        )
    else:
        lines.append(
            f"- Recency rule: today is {today.isoformat()}. Treat a document's own date as the truth about "
            f"'current' figures; if a document is more than {stale_after_months} months old, say it may be outdated."
        )
    lines.append(
        "- Conflict rule: if sources disagree, prefer the most recent one, give its figure, and note "
        "the other value and its source in one clause."
    )
    if assessment.tool_errors:
        lines.append(
            "- A search step failed (" + "; ".join(assessment.tool_errors[:3]) + "). Say that this "
            "source was unavailable; never imply it was searched successfully."
        )
    lines.append(
        "- Never claim you searched something you did not, and never pad a thin answer with "
        "generic advice the user did not ask for."
    )
    return "\n".join(lines)


STATIC_GROUNDING_RULES = (
    "## Grounding Policy\n"
    "- Prefer company documents; cite them with the required [Source: filename, p.N](doc:...) format.\n"
    "- If you had to use the web instead, say so in the first sentence and cite URLs.\n"
    "- If nothing relevant was found, say so first, then answer from general knowledge and mark it "
    "as such; for a number or date, give a clearly labelled estimate with its assumptions rather than refusing.\n"
    "- Absence rule: say explicitly when the documents do not cover part of the question.\n"
    "- Recency rule: give document dates when they matter; flag documents older than a year as possibly outdated.\n"
    "- Conflict rule: prefer the most recent source and note the disagreement in one clause.\n"
    "- Never claim a search succeeded when the tool reported an error."
)
