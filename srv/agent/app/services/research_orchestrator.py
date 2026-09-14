"""
Deep-research orchestrator: a lead agent and parallel workers.

What a consented deep-research turn used to be: one call to Tavily's
``/research`` endpoint, whatever came back rendered by the chat model. What
it is now — the orchestrator–worker pattern that the frontier research
products converge on, sized for a self-hosted box:

    question
       │
       ├─ decompose (structured output) ──▶ 2–N sub-questions, each with an angle
       │
       ├─ fan out, in parallel ────────────────────────────────────────────┐
       │     worker "tavily"   : deep_research(question)  — breadth        │
       │     worker 1..N       : an isolated model loop on research_worker │
       │                          with web_search / web_extract / web_map  │
       │                          — depth on one sub-question each         │
       │◀───────────────────────────────────────────────────────────────────┘
       │
       └─ lead writes the report on `chat`, tools = [render_chart]
          under the research directive (sections, tables, charts, citations)

Why workers get their *own* AgentContext: the point is context isolation.
Each worker starts with an empty ``tool_calls``, no conversation history and
a single sub-question, so it spends its whole window on that question rather
than on the user's history and the other workers' output. The lead is the
only thing that sees everything, and it sees it as compact findings, not raw
tool output.

Cost: this is several model loops per research turn instead of one API call.
That is the trade the user makes when they say "yes" to the research offer,
and it is why ``research_worker`` is its own purpose alias — start it local,
promote it to a cloud model in the admin UI only if worker quality is the
bottleneck. The lead stays on ``chat``.

Failure isolation: one worker timing out or erroring yields a finding marked
``ok=False`` with the error text; the others' findings still reach the lead.
A research pass degrades, it does not sink.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

from app.agents.base_agent import (
    AgentConfig,
    AgentContext,
    BaseStreamingAgent,
    ExecutionMode,
    ToolCallRecord,
    ToolStrategy,
)
from app.config.settings import get_settings
from app.schemas.streaming import StreamEvent, content, progress, thought
from app.services import model_capabilities

logger = logging.getLogger(__name__)

StreamCallback = Callable[[StreamEvent], Awaitable[None]]

WORKER_TOOLS = ["web_search", "web_extract", "web_map"]
LEAD_TOOLS = ["render_chart"]

MIN_SUB_QUESTIONS = 2


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class SubQuestion(BaseModel):
    question: str = Field(description="A self-contained question a worker can research on its own")
    angle: str = Field(default="", description="What makes this angle distinct from the others")
    prefer_recent: bool = Field(default=False, description="True if the answer is time-sensitive (news, prices)")
    domains: List[str] = Field(default_factory=list, description="Sites worth mapping first, if any")


class Decomposition(BaseModel):
    sub_questions: List[SubQuestion] = Field(default_factory=list)


DECOMPOSITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "angle": {"type": "string"},
                    "prefer_recent": {"type": "boolean"},
                    "domains": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question"],
            },
        }
    },
    "required": ["sub_questions"],
}


@dataclass
class WorkerFinding:
    worker_id: str
    sub_question: str
    findings: str
    sources: List[Dict[str, str]] = field(default_factory=list)
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    elapsed_ms: int = 0
    ok: bool = True
    error: Optional[str] = None


class ResearchBundle(BaseModel):
    """What the lead is given. Has ``report`` so the existing synthesis path
    (``_has_research_report`` / ``_render_tool_result``) treats it as research."""

    question: str
    report: str
    # grounding.assess_grounding keys deep_research on `success`; without it
    # a bundle full of findings would be tiered as "nothing retrieved".
    success: bool = True
    sources: List[Dict[str, str]] = Field(default_factory=list)
    worker_count: int = 0
    failed_workers: int = 0
    elapsed_ms: int = 0


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


DECOMPOSE_SYSTEM = """You plan research. Given one question, split it into the
smallest set of distinct sub-questions that together answer it — usually 2 to 5.

Each sub-question must be answerable on its own by someone who has not seen
the others. Make the angles genuinely different: history vs. current state,
supply vs. demand, one region vs. another, the official position vs. critics,
the numbers vs. the reasons. Do not split a question that is already narrow.

Mark prefer_recent when the answer changes month to month. List domains only
when a specific site clearly owns the answer (a regulator, a company, a
standards body)."""

WORKER_INSTRUCTIONS = """You are one research worker among several. You own
exactly one sub-question; other workers own the rest, and a lead will combine
everything. Do not answer the wider question — answer yours, thoroughly.

Work the tools in a loop: search from a few angles, extract the two to five
strongest pages in full, map a site only when the answer clearly lives there.
Stop when new searches return sources you have already seen.

Return your findings as compact markdown:
- Lead with the direct answer to your sub-question.
- Then the supporting facts, each with the URL it came from in parentheses.
- Then a short "Uncertain or conflicting" section if anything is.
- Finish with a "Sources" list: one line per URL you actually used.

Numbers matter: when you find figures over time or across categories, write
them out as a small table so the lead can chart them. Do not pad. Do not
speculate beyond what the pages say."""

LEAD_INSTRUCTIONS = """You are the lead on a research task. Several workers
have each investigated one angle in isolation and reported back; a breadth
report from an automated research service is included too. You have not seen
the web yourself — everything you know is in the findings below.

Write the report. Reconcile the workers: where they agree, say so once; where
they conflict, say which is better supported and why; where all of them came
up empty, say that plainly rather than filling the gap.

You have one tool, `render_chart`. Whenever the findings contain a numeric
series — figures over time, quantities across categories, shares of a whole —
call it with the real numbers and place the markdown image it returns where
the chart belongs. Keep the table as well.

Every substantive claim carries the URL it rests on. Do not cite a source no
worker reported."""


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------


def resolve_worker_purpose() -> str:
    """The purpose alias workers run on, or the fallback if LiteLLM does not
    know it yet (the alias is created on the next LiteLLM deploy)."""
    settings = get_settings()
    wanted = settings.research_worker_purpose
    try:
        if model_capabilities.get(wanted) is not None:
            return wanted
    except Exception as exc:  # noqa: BLE001 — capability lookup must not break research
        logger.debug("worker purpose lookup failed: %s", exc)
    logger.warning(
        "research_worker purpose %r is not a known LiteLLM alias; workers will use %r",
        wanted, settings.research_worker_fallback_purpose,
    )
    return settings.research_worker_fallback_purpose


class ResearchWorkerAgent(BaseStreamingAgent):
    """An isolated search→extract→map loop on one sub-question."""

    def __init__(self, worker_id: str, purpose: str):
        super().__init__(AgentConfig(
            name=f"research-worker-{worker_id}",
            display_name=f"Research worker {worker_id}",
            instructions=WORKER_INSTRUCTIONS,
            tools=list(WORKER_TOOLS),
            model=purpose,
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
            max_iterations=8,
            max_tokens=6000,
            enable_history_compression=False,
        ))


class ResearchLeadAgent(BaseStreamingAgent):
    """Writes the report on `chat`, with render_chart as its only tool."""

    def __init__(self):
        super().__init__(AgentConfig(
            name="research-lead",
            display_name="Research lead",
            instructions=LEAD_INSTRUCTIONS,
            tools=list(LEAD_TOOLS),
            model="chat",
            execution_mode=ExecutionMode.RUN_ONCE,
            tool_strategy=ToolStrategy.LLM_DRIVEN,
            max_iterations=6,
            # Same ceiling as ChatAgent and for the same reason: `chat` is an
            # Anthropic model on Bedrock and the API requires max_tokens.
            max_tokens=32000,
            enable_history_compression=False,
        ))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _child_context(parent: AgentContext, *, loop_mode: str, budget_s: int) -> AgentContext:
    """A fresh context that shares identity with the parent and nothing else."""
    return AgentContext(
        principal=parent.principal,
        session=parent.session,
        deps=parent.deps,
        user_id=parent.user_id,
        agent_id=parent.agent_id,
        metadata=dict(parent.metadata),
        insights_enabled=False,
        loop_mode=loop_mode,
        loop_deadline=time.monotonic() + budget_s,
        turn_started=time.monotonic(),
    )


def _sources_from_calls(calls: List[ToolCallRecord]) -> List[Dict[str, str]]:
    """URLs a worker actually fetched, deduped, from its search/extract results."""
    seen: Dict[str, Dict[str, str]] = {}

    def add(url: Any, title: Any = "") -> None:
        if url and str(url) not in seen:
            seen[str(url)] = {"title": str(title or ""), "url": str(url)}

    for call in calls:
        if not call.ok or call.result is None:
            continue
        r = call.result
        # web_search → .results[WebSearchResult]; web_extract → .pages[ExtractedPage]
        for attr in ("results", "pages"):
            items = getattr(r, attr, None)
            if isinstance(items, list):
                for it in items:
                    if isinstance(it, dict):
                        add(it.get("url"), it.get("title", ""))
                    else:
                        add(getattr(it, "url", None), getattr(it, "title", ""))
        # web_map → .base_url (the site that was mapped); single-URL tools → .url
        for attr in ("url", "base_url"):
            add(getattr(r, attr, None), getattr(r, "title", ""))
    return list(seen.values())


# ---------------------------------------------------------------------------
# Report → DocumentSpec
# ---------------------------------------------------------------------------

_H1_RE = re.compile(r"^#\s+(.+?)\s*#*\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_SOURCES_HEADING_RE = re.compile(r"^#{1,3}\s+(?:sources|references|citations|bibliography)\b", re.IGNORECASE)
_MAX_TITLE_WORDS = 14


def _title_from_question(question: str) -> str:
    words = re.sub(r"\s+", " ", question or "").strip().rstrip("?.!").split(" ")
    title = " ".join(words[:_MAX_TITLE_WORDS])
    if len(words) > _MAX_TITLE_WORDS:
        title += "…"
    return title[:1].upper() + title[1:] if title else "Research report"


def _split_report(text: str):
    """Split a Markdown report into (title, sections).

    A leading H1 becomes the document title. H2 headings become document
    sections (so the Word contents list reflects the report's structure);
    text before the first H2 becomes a heading-less lead section. Fenced code
    blocks are never split. Returns (title_or_None, [(heading_or_None, body)]).
    """
    lines = (text or "").splitlines()
    title = None
    sections: List[tuple] = []
    current_heading: Optional[str] = None
    buf: List[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            buf.append(line)
            continue
        if not in_fence:
            if title is None and not sections and not any(b.strip() for b in buf):
                m1 = _H1_RE.match(line)
                if m1:
                    title = m1.group(1).strip()
                    continue
            m2 = _H2_RE.match(line)
            if m2:
                if any(b.strip() for b in buf) or current_heading:
                    sections.append((current_heading, "\n".join(buf).strip()))
                current_heading = m2.group(1).strip()
                buf = []
                continue
        buf.append(line)
    if any(b.strip() for b in buf) or current_heading:
        sections.append((current_heading, "\n".join(buf).strip()))
    if len(sections) < 2:
        # Not enough structure to be worth splitting; keep the report whole.
        body = "\n".join(l for l in lines if not (title and _H1_RE.match(l) and _H1_RE.match(l).group(1).strip() == title)).strip()
        sections = [(None, body)]
    return title, sections


def build_report_spec(question: str, text: str, bundle: ResearchBundle):
    """Turn the lead's Markdown report into a ``DocumentSpec`` for export.

    Charts the lead placed with ``render_chart`` are ordinary image links in
    the text; the data-api embeds them. Worker sources are appended as a
    Sources section unless the report already has one.
    """
    from busibox_common.document_specs import DocumentSpec, SectionSpec, SourceSpec, safe_filename

    title, parts = _split_report(text)
    title = (title or _title_from_question(question))[:300]
    sections = [
        SectionSpec(heading=(h[:200] if h else None), level=1, markdown=body)
        for h, body in parts
        if body or h
    ]
    has_sources_section = any(
        _SOURCES_HEADING_RE.match(line) for line in (text or "").splitlines()
    )
    sources = []
    if not has_sources_section:
        seen = set()
        for src in bundle.sources[:200]:
            url = (src.get("url") or "").strip()
            name = (src.get("title") or url or "").strip()
            if not name or url in seen:
                continue
            seen.add(url)
            sources.append(SourceSpec(title=name[:300], url=url[:2000] or None))
    return DocumentSpec(
        filename=safe_filename(title, ".docx"),
        title=title,
        subtitle="Deep research report",
        author="Busibox AI Chat",
        toc=True,
        sections=sections or [SectionSpec(markdown=text)],
        sources=sources,
    )


class ResearchOrchestrator:
    def __init__(self, parent_agent: BaseStreamingAgent):
        self.parent = parent_agent
        self.settings = get_settings()

    # -- decomposition ------------------------------------------------------

    async def decompose(self, question: str) -> List[SubQuestion]:
        settings = self.settings
        try:
            raw = await self.parent._call_structured_output(
                prompt=f"Question:\n{question}",
                system_prompt=DECOMPOSE_SYSTEM,
                response_schema=DECOMPOSITION_SCHEMA,
                max_tokens=1200,
            )
            data = json.loads(raw) if isinstance(raw, str) else raw
            subs = Decomposition.model_validate(data).sub_questions
        except Exception as exc:  # noqa: BLE001
            logger.warning("research decomposition failed (%s); using the question as-is", exc)
            subs = []
        subs = [s for s in subs if s.question.strip()]
        if len(subs) < MIN_SUB_QUESTIONS:
            # Too narrow to split, or the planner failed: one worker on the
            # whole question is still worth more than none.
            subs = [SubQuestion(question=question, angle="the question as asked")]
        return subs[: max(1, settings.research_max_workers)]

    # -- workers ------------------------------------------------------------

    async def _run_worker(
        self,
        worker_id: str,
        sub: SubQuestion,
        purpose: str,
        parent_ctx: AgentContext,
        stream: StreamCallback,
        cancel: asyncio.Event,
    ) -> WorkerFinding:
        t0 = time.monotonic()
        # "research_worker", not "research": the worker gets the loop and
        # Tavily-tool guidance but NOT the report-format directive — it
        # returns compact findings to the lead, and its own instructions say
        # so. Telling it to "go long" would contradict them.
        ctx = _child_context(
            parent_ctx, loop_mode="research_worker",
            budget_s=self.settings.research_worker_budget_seconds,
        )
        agent = ResearchWorkerAgent(worker_id, purpose)

        # Workers narrate progress but never stream answer text: their
        # findings are inputs to the lead, not the user's answer.
        async def worker_stream(ev: StreamEvent) -> None:
            if ev.type in ("content", "complete"):
                return
            await stream(ev.model_copy(update={"source": f"worker {worker_id}: {ev.source}"}))

        await stream(progress(
            source="research",
            message=f"Worker {worker_id}: {sub.question}",
            data={"phase": "worker_start", "worker": worker_id, "angle": sub.angle},
        ))

        hint = ""
        if sub.prefer_recent:
            hint += " This is time-sensitive: prefer topic=\"news\" with a time_range."
        if sub.domains:
            hint += f" Sites likely to own the answer: {', '.join(sub.domains)} — map them first."
        query = f"{sub.question}{hint}\n\nAngle to cover: {sub.angle or 'as asked'}."

        try:
            await asyncio.wait_for(
                agent._execute_llm_driven(query, worker_stream, cancel, ctx),
                # The loop refuses new tools past loop_deadline; this outer
                # bound only exists so a hung *model* call cannot pin the
                # gather forever. Generous on purpose.
                timeout=self.settings.research_worker_budget_seconds + 120,
            )
            text = str(ctx.tool_results.get("llm_response") or "").strip()
            ok = bool(text)
            err = None if ok else "worker produced no findings"
        except asyncio.TimeoutError:
            text, ok, err = "", False, f"worker exceeded {self.settings.research_worker_budget_seconds + 120}s"
        except Exception as exc:  # noqa: BLE001 — one worker must not sink the pass
            logger.warning("research worker %s failed: %s", worker_id, exc, exc_info=True)
            text, ok, err = "", False, str(exc)

        elapsed = round((time.monotonic() - t0) * 1000)
        finding = WorkerFinding(
            worker_id=worker_id, sub_question=sub.question, findings=text,
            sources=_sources_from_calls(ctx.tool_calls), tool_calls=list(ctx.tool_calls),
            elapsed_ms=elapsed, ok=ok, error=err,
        )
        await stream(progress(
            source="research",
            message=(
                f"Worker {worker_id} done: {len(finding.sources)} sources, "
                f"{len(ctx.tool_calls)} tool calls, {elapsed // 1000}s"
                if ok else f"Worker {worker_id} failed: {err}"
            ),
            data={"phase": "worker_done", "worker": worker_id, "ok": ok, "elapsed_ms": elapsed,
                  "sources": len(finding.sources), "tool_calls": len(ctx.tool_calls)},
        ))
        return finding

    async def _run_tavily_breadth(
        self, question: str, parent_ctx: AgentContext, stream: StreamCallback,
    ) -> WorkerFinding:
        """Tavily /research as the breadth worker. It is an external agent, not
        a model loop, so it runs as a plain tool call."""
        from app.tools.tavily_tools import deep_research

        t0 = time.monotonic()
        await stream(progress(
            source="research", message="Breadth worker: Tavily research pass",
            data={"phase": "worker_start", "worker": "tavily"},
        ))
        try:
            out = await deep_research(question=question)
            ok = bool(out.success and out.report.strip())
            report = out.report if ok else ""
            err = None if ok else (out.error or out.status)
            sources = [{"title": s.title, "url": s.url} for s in (out.sources or [])]
            parent_ctx.record_tool_call(
                "deep_research", {"question": question}, out,
                round((time.monotonic() - t0) * 1000), ok=ok, error=err, source="worker tavily",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tavily breadth worker failed: %s", exc)
            ok, report, err, sources = False, "", str(exc), []
        elapsed = round((time.monotonic() - t0) * 1000)
        await stream(progress(
            source="research",
            message=(f"Breadth worker done: {len(sources)} sources, {elapsed // 1000}s"
                     if ok else f"Breadth worker failed: {err}"),
            data={"phase": "worker_done", "worker": "tavily", "ok": ok, "elapsed_ms": elapsed},
        ))
        return WorkerFinding(
            worker_id="tavily", sub_question=question, findings=report, sources=sources,
            elapsed_ms=elapsed, ok=ok, error=err,
        )

    # -- bundle -------------------------------------------------------------

    @staticmethod
    def bundle(question: str, findings: List[WorkerFinding], elapsed_ms: int) -> ResearchBundle:
        parts: List[str] = [f"# Research findings\n\nQuestion: {question}\n"]
        all_sources: Dict[str, Dict[str, str]] = {}
        failed = 0
        for f in findings:
            label = "Breadth report (Tavily research)" if f.worker_id == "tavily" else f"Worker {f.worker_id}"
            parts.append(f"\n## {label}\n**Sub-question:** {f.sub_question}\n")
            if f.ok:
                parts.append(f.findings.strip())
            else:
                failed += 1
                parts.append(f"_This worker did not return findings ({f.error})._")
            # Only sources behind findings the lead can cite.
            if f.ok:
                for s in f.sources:
                    if s.get("url") and s["url"] not in all_sources:
                        all_sources[s["url"]] = s
        return ResearchBundle(
            question=question,
            report="\n".join(parts),
            sources=list(all_sources.values()),
            worker_count=len(findings),
            failed_workers=failed,
            elapsed_ms=elapsed_ms,
        )

    # -- lead ---------------------------------------------------------------

    def _render_bundle(self, bundle: ResearchBundle) -> str:
        """The findings as the lead's evidence, under the bundle's own budget.

        ``research_report_context_chars`` (60k) is sized for one Tavily
        report. The bundle is that report *plus* every worker's findings, so
        under that cap the trailing workers would be cut off with only an
        INFO log to show for it. Sections are kept whole: if the budget is
        hit, whole workers are dropped from the end and the lead is told.
        """
        budget = int(getattr(self.settings, "research_bundle_context_chars", 200000))
        report = bundle.report
        dropped_note = ""
        if len(report) > budget:
            # Cut at the last section boundary that fits.
            cut = report.rfind("\n## ", 0, budget)
            kept = report[: cut if cut > 0 else budget]
            dropped = report.count("\n## ") - kept.count("\n## ")
            dropped_note = (
                f"\n\n_[{dropped} worker section(s) omitted: findings exceeded the "
                f"{budget:,}-character budget. Say so in the report.]_"
            )
            logger.warning(
                "research bundle trimmed for the lead: %d -> %d chars, %d section(s) dropped",
                len(report), len(kept), dropped,
            )
            report = kept
        lines = [f"### research_findings (cited research report)\n{report}{dropped_note}"]
        if bundle.sources:
            lines.append("\nSources reported by workers:")
            lines.extend(f"- {s.get('title', '')} {s.get('url', '')}".strip() for s in bundle.sources[:80])
        return "\n".join(lines)

    async def _write_report(
        self, bundle: ResearchBundle, parent_ctx: AgentContext,
        stream: StreamCallback, cancel: asyncio.Event,
    ) -> str:
        ctx = _child_context(
            parent_ctx, loop_mode="research", budget_s=self.settings.research_lead_budget_seconds,
        )
        lead = ResearchLeadAgent()
        # Record the bundle so the lead's context is a faithful record of what
        # it was given (debug panel, tests). The loop's prompt builder does
        # not read tool_results, so the findings also go into the prompt
        # text — rendered by the same code any research report goes through,
        # which applies research_report_context_chars and lists sources.
        ctx.record_tool_call("research_findings", {"question": bundle.question}, bundle, 0, source="lead")
        findings_md = self._render_bundle(bundle)

        async def lead_stream(ev: StreamEvent) -> None:
            # The lead's text IS the answer: content streams to the user.
            await stream(ev)

        prompt = (
            f"Research question:\n{bundle.question}\n\n"
            f"{bundle.worker_count} workers reported"
            + (f" ({bundle.failed_workers} returned nothing)" if bundle.failed_workers else "")
            + ".\n\n"
            f"{findings_md}\n\n"
            "Write the full report now."
        )
        await lead._execute_llm_driven(prompt, lead_stream, cancel, ctx)
        text = str(ctx.tool_results.get("llm_response") or "").strip()
        # Surface the lead's chart calls on the parent so the debug panel and
        # any citation pass can see them.
        for call in ctx.tool_calls:
            parent_ctx.tool_calls.append(call)
        return text

    # -- export -------------------------------------------------------------

    async def _export_report(
        self, question: str, text: str, bundle: ResearchBundle,
        parent_ctx: AgentContext, stream: StreamCallback,
    ) -> str:
        """Export the finished report as a Word document and return the
        markdown to append under the answer ('' when export is off or failed).

        Non-fatal by design: the report is already on screen; a failed export
        costs the user a link, never the research.
        """
        if not getattr(self.settings, "research_export_docx", True):
            return ""
        deps = getattr(parent_ctx, "deps", None)
        if deps is None:
            logger.info("research export skipped: no deps on context")
            return ""
        try:
            from app.tools.document_tools import export_document
            spec = build_report_spec(question, text, bundle)
            await stream(thought(
                source="research", message="Exporting the report as a Word document…",
                data={"phase": "export", "sections": len(spec.sections)},
            ))
            t0 = time.monotonic()
            out = await export_document(deps, spec)
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            parent_ctx.record_tool_call(
                "create_document", {"filename": spec.filename, "sections": len(spec.sections)},
                out, elapsed_ms, source="lead",
            )
            if not out.success or not out.markdown:
                logger.warning("research export failed: %s | %s", out.error, "; ".join(out.issues[:3]))
                await stream(thought(
                    source="research",
                    message=f"Word export skipped: {out.error or 'verification failed'}",
                    data={"phase": "export", "ok": False, "issues": out.issues[:5]},
                ))
                return ""
            await stream(thought(
                source="research",
                message=f"Report exported: {out.summary}",
                data={"phase": "export", "ok": True, "file_id": out.file_id, "pages": out.pages, "ms": elapsed_ms},
            ))
            return out.markdown
        except Exception as exc:  # noqa: BLE001 — never lose the report over the export
            logger.warning("research export raised: %s", exc, exc_info=True)
            return ""

    # -- run ----------------------------------------------------------------

    async def run(
        self, question: str, parent_ctx: AgentContext,
        stream: StreamCallback, cancel: asyncio.Event,
    ) -> str:
        """Run the whole pass and return the report text (also stored on the
        parent as ``tool_results['llm_response']`` and ``tool_results['deep_research']``)."""
        t0 = time.monotonic()
        settings = self.settings

        await stream(thought(
            source="research", message="Breaking the question into angles for parallel workers…",
            data={"phase": "decompose"},
        ))
        subs = await self.decompose(question)
        purpose = resolve_worker_purpose()
        await stream(thought(
            source="research",
            message=f"{len(subs)} angle(s) plus a breadth pass; workers on `{purpose}`.",
            data={"phase": "fan_out", "workers": len(subs), "purpose": purpose,
                  "angles": [s.question for s in subs]},
        ))

        tasks = [self._run_tavily_breadth(question, parent_ctx, stream)]
        tasks += [
            self._run_worker(str(i + 1), sub, purpose, parent_ctx, stream, cancel)
            for i, sub in enumerate(subs)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        findings: List[WorkerFinding] = []
        for i, r in enumerate(results):
            if isinstance(r, WorkerFinding):
                findings.append(r)
            else:  # gather returned an exception object
                wid = "tavily" if i == 0 else str(i)
                logger.warning("research worker %s raised: %s", wid, r)
                findings.append(WorkerFinding(
                    worker_id=wid, sub_question=question if i == 0 else subs[i - 1].question,
                    findings="", ok=False, error=str(r),
                ))
        # Workers' tool calls become part of the turn's record, tagged with
        # the worker that made them.
        for f in findings:
            for c in f.tool_calls:
                c.source = f"worker {f.worker_id}"
                parent_ctx.tool_calls.append(c)

        fan_out_ms = round((time.monotonic() - t0) * 1000)
        bundle = self.bundle(question, findings, fan_out_ms)
        ok_count = sum(1 for f in findings if f.ok)
        await stream(thought(
            source="research",
            message=f"{ok_count}/{len(findings)} workers returned findings "
                    f"({len(bundle.sources)} distinct sources). Writing the report…",
            data={"phase": "synthesize", "ok": ok_count, "total": len(findings),
                  "sources": len(bundle.sources), "fan_out_ms": fan_out_ms},
        ))

        if ok_count == 0:
            text = (
                "I wasn't able to gather research on this: every worker came back empty "
                + (f"({findings[0].error})" if findings and findings[0].error else "")
                + ". Ask again to retry, or ask a narrower question for a standard search."
            )
            await stream(content(source="research", message=text, data={"phase": "deep_response"}))
        else:
            try:
                text = await self._write_report(bundle, parent_ctx, stream, cancel)
            except Exception as exc:  # noqa: BLE001 — findings must survive a lead failure
                logger.warning("research lead raised: %s", exc, exc_info=True)
                text = ""
            if not text:
                # Lead produced nothing (model error, cancelled): fall back to
                # handing the raw findings to the normal synthesis path.
                logger.warning("research lead returned no text; falling back to findings synthesis")
                # Synthesis renders from tool_calls. Leave it exactly one
                # research record — the bundle — rather than the raw Tavily
                # output plus every worker's search/extract results plus the
                # bundle that already contains all of it.
                parent_ctx.tool_calls = [
                    c for c in parent_ctx.tool_calls
                    if not c.source.startswith("worker") and c.source != "lead"
                ]
                parent_ctx.tool_results.pop("deep_research", None)
                parent_ctx.record_tool_call(
                    "deep_research", {"question": question}, bundle, fan_out_ms, source="lead",
                )
                return ""

        if ok_count and text and not cancel.is_set():
            export_md = await self._export_report(question, text, bundle, parent_ctx, stream)
            if export_md:
                # Stream the link as a final content chunk (the lead's text
                # already streamed) and persist it as part of the answer.
                tail = f"\n\n---\n\n{export_md}"
                await stream(content(source="research", message=tail, data={"streaming": True, "partial": True}))
                text = f"{text}{tail}"

        parent_ctx.tool_results["deep_research"] = bundle
        parent_ctx.tool_results["llm_response"] = text
        logger.info(
            "research orchestrator complete",
            extra={"workers": len(findings), "ok": ok_count, "sources": len(bundle.sources),
                   "report_chars": len(text), "total_ms": round((time.monotonic() - t0) * 1000)},
        )
        return text
