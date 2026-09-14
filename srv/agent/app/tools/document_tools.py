"""
create_spreadsheet / create_document — produce real Excel and Word files.

Both tools are thin clients: the model describes the file as a typed spec
(``WorkbookSpec`` / ``DocumentSpec`` from ``busibox_common.document_specs``)
and the data-api's document engine builds it, verifies it the way a user
would (LibreOffice recalculation for Excel, a PDF render for Word) and
stores it in the user's library. The tool returns a download link the model
pastes into its answer, plus the engine's validation report so the model
can fix a bad spec and try again instead of shipping a broken file.

Why the spec is typed rather than free-form: the model never runs code and
never writes a byte of the file. It can only ask for things the engine
knows how to build and check, which is what makes "the spreadsheet works"
a property of the system rather than a hope.

Calls carry the user's own data-api token (``_data_api_token``) — the file
is created as the user, in the user's library, with no service credential.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Union

import httpx
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import RunContext

from app.config.settings import get_settings
from app.tools.image_tool import _data_api_token
from busibox_common.document_specs import DocumentSpec, WorkbookSpec

logger = logging.getLogger(__name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MAX_ISSUES_RELAYED = 12


class DocumentFileOutput(BaseModel):
    """Result of creating a spreadsheet or document."""

    success: bool = Field(description="True when the file was built, verified clean and stored")
    file_id: Optional[str] = Field(default=None, description="File ID in the user's library")
    filename: Optional[str] = None
    download_url: Optional[str] = Field(default=None, description="Portal-relative URL that downloads the file")
    thumbnail_url: Optional[str] = Field(default=None, description="Portal-relative URL of a first-page preview (Word only)")
    markdown: str = Field(default="", description="Ready-to-paste markdown: the download link (and preview image for Word)")
    summary: str = Field(default="", description="What the file contains, from the engine")
    pages: Optional[int] = None
    issues: List[str] = Field(default_factory=list, description="Validation problems, most important first")
    error: Optional[str] = Field(default=None, description="Why the file could not be produced, if it could not")


def _format_pydantic_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:MAX_ISSUES_RELAYED]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "Invalid spec — " + "; ".join(parts)


def _conversation_id(ctx: RunContext[Any]) -> Optional[str]:
    deps = getattr(ctx, "deps", None)
    meta = getattr(deps, "metadata", None) or {}
    cid = meta.get("conversation_id") or meta.get("conversationId")
    return str(cid) if cid else None


def _link_markdown(kind: str, filename: str, download_url: str, thumbnail_url: Optional[str]) -> str:
    label = "Download the spreadsheet" if kind == "xlsx" else "Download the document"
    lines = [f"[{label}: {filename}]({download_url})"]
    if thumbnail_url:
        lines.append(f"![First page of {filename}]({thumbnail_url})")
    return "\n\n".join(lines)


def _relay_issues(validation: Dict[str, Any]) -> List[str]:
    issues = validation.get("issues") or []
    ordered = sorted(issues, key=lambda i: 0 if i.get("severity") == "error" else 1)
    out = [f"{i.get('severity', 'issue')} at {i.get('location', '?')}: {i.get('message', '')}" for i in ordered[:MAX_ISSUES_RELAYED]]
    if len(ordered) > MAX_ISSUES_RELAYED:
        out.append(f"… and {len(ordered) - MAX_ISSUES_RELAYED} more")
    return out


async def _post_generate(ctx: RunContext[Any], kind: str, body: Dict[str, Any]) -> DocumentFileOutput:
    token = _data_api_token(getattr(ctx, "deps", None))
    if not token:
        return DocumentFileOutput(success=False, error="No authenticated token available to store the file.")
    settings = get_settings()
    base_url = str(settings.data_api_url).rstrip("/")
    timeout = float(getattr(settings, "document_generation_timeout_seconds", 180))
    cid = _conversation_id(ctx)
    if cid:
        body["conversation_id"] = cid

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url}/files/generate/{kind}",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
    except httpx.TimeoutException:
        return DocumentFileOutput(success=False, error=f"The document engine did not finish within {int(timeout)}s. Try a smaller file.")
    except httpx.HTTPError as exc:
        logger.warning("document_tools: %s request failed: %s", kind, exc)
        return DocumentFileOutput(success=False, error=f"Could not reach the document engine: {exc}")

    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    if resp.status_code == 400:
        hint = payload.get("hint")
        return DocumentFileOutput(success=False, error=(payload.get("error") or "The spec was rejected.") + (f" ({hint})" if hint else ""))
    if resp.status_code == 503:
        return DocumentFileOutput(success=False, error=payload.get("error") or "Document generation is not available on this server right now.")
    if resp.status_code != 200:
        logger.warning("document_tools: %s returned %s: %s", kind, resp.status_code, (resp.text or "")[:300])
        return DocumentFileOutput(success=False, error=payload.get("error") or f"The document engine returned HTTP {resp.status_code}.")

    validation = payload.get("validation") or {}
    issues = _relay_issues(validation)
    filename = payload.get("filename") or ("file.xlsx" if kind == "xlsx" else "file.docx")
    download_url = payload.get("download_url")
    thumbnail_url = payload.get("thumbnail_url")
    ok = bool(payload.get("success")) and bool(download_url)
    markdown = _link_markdown(kind, filename, download_url, thumbnail_url) if download_url else ""
    logger.info("document_tools: %s stored file_id=%s ok=%s issues=%d", kind, payload.get("file_id"), ok, len(issues))
    return DocumentFileOutput(
        success=ok,
        file_id=payload.get("file_id"),
        filename=filename,
        download_url=download_url,
        thumbnail_url=thumbnail_url,
        markdown=markdown,
        summary=payload.get("summary") or "",
        pages=validation.get("pages"),
        issues=issues,
        error=None if ok else (payload.get("error") or "The file was stored but failed verification; fix the issues and call again."),
    )


def _coerce(model: type, spec: Any) -> Union[BaseModel, DocumentFileOutput]:
    """Accept a model instance or the planner's raw dict; return the model or an error output."""
    if isinstance(spec, model):
        return spec
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError:
            return DocumentFileOutput(success=False, error="spec must be an object, not a string")
    try:
        return model.model_validate(spec)
    except ValidationError as exc:
        return DocumentFileOutput(success=False, error=_format_pydantic_error(exc))


async def create_spreadsheet(ctx: RunContext[Any], spec: WorkbookSpec) -> DocumentFileOutput:
    """Create a working Excel workbook (.xlsx) from data you already have and return a download link.

    Use this when the user asks for a spreadsheet, an Excel file, a workbook,
    a table they can sort/filter/edit, or to "export" numbers. Do not use it
    for a quick in-chat table — a markdown table is faster for that.

    How to fill the spec:
    - One sheet per logical table. Give every column a header and a type
      (text, number, integer, currency, percent, date, bool) — types drive
      the number formats. Percent values are fractions (0.125 = 12.5%).
    - Put the raw values in `rows`, in column order. Leave a formula
      column's cells empty and describe it once in `column_formulas` with
      `{row}` for the row number (e.g. "=C{row}*D{row}"); the engine fills
      every row. Use `totals` for SUM/AVERAGE rows rather than typing the
      formula.
    - Formulas are checked by actually recalculating the workbook. Add
      `assertions` for numbers you know (e.g. the total equals the sum of a
      range) so mistakes are caught before the user opens the file.
    - Optional: `chart` (column/bar/line/pie from the table), `validations`
      (drop-downs, numeric bounds), `conditional` highlights, `named_ranges`.
    - Reference columns by header text. Functions that reach outside the
      workbook (WEBSERVICE, HYPERLINK, INDIRECT …) are rejected.

    The result carries `markdown` — paste it into your answer so the user
    gets the link — and `summary`, which you should relay in a sentence.
    If `success` is false, read `issues`/`error`, fix the spec, and call
    again (at most twice). Never tell the user a file exists when it does not.
    """
    coerced = _coerce(WorkbookSpec, spec)
    if isinstance(coerced, DocumentFileOutput):
        return coerced
    return await _post_generate(ctx, "xlsx", {"spec": coerced.model_dump(mode="json")})


async def create_document(ctx: RunContext[Any], spec: DocumentSpec) -> DocumentFileOutput:
    """Create a formatted Word document (.docx) and return a download link and first-page preview.

    Use this when the user asks for a Word document, a .docx, a report or
    memo "as a file", or to export an answer they can edit, print or send.
    Do not use it for an ordinary chat answer.

    How to fill the spec:
    - `title` (and optional `subtitle`, `author`, `date`) make the title
      block. `toc=true` adds a contents list when there are 2+ sections.
    - `sections`: each has an optional `heading` (with `level` 1–3) and a
      `markdown` body. Use real Markdown: paragraphs, **bold**, bullet and
      numbered lists, pipe tables (`| a | b |` with a `|---|---|` row) and
      links. Headings inside the body are nested under the section heading
      automatically.
    - Charts: images you produced with `render_chart` embed when you include
      their markdown image line in a section body, or list their `file_id`
      in the section's `images` with a caption. Other image URLs are not
      embedded.
    - `sources`: title + URL pairs, rendered as a numbered Sources section.

    The document is rendered and checked (every heading present, tables and
    figures embedded, page count). The result carries `markdown` — the
    download link and a preview image — paste it into your answer. If
    `success` is false, read `issues`/`error`, fix the spec and call again
    (at most twice).
    """
    coerced = _coerce(DocumentSpec, spec)
    if isinstance(coerced, DocumentFileOutput):
        return coerced
    return await _post_generate(ctx, "docx", {"spec": coerced.model_dump(mode="json"), "thumbnail": True})


async def export_document(
    ctx_or_deps: Any,
    spec: DocumentSpec,
    *,
    thumbnail: bool = True,
) -> DocumentFileOutput:
    """Programmatic entry point (no model in the loop): build a document from
    a ready ``DocumentSpec``. Used by the research orchestrator to export the
    finished report. ``ctx_or_deps`` may be a RunContext or the deps object."""
    ctx = ctx_or_deps if hasattr(ctx_or_deps, "deps") else _DepsCtx(ctx_or_deps)
    return await _post_generate(ctx, "docx", {"spec": spec.model_dump(mode="json"), "thumbnail": thumbnail})


class _DepsCtx:
    """Minimal stand-in for RunContext when only deps are at hand."""

    def __init__(self, deps: Any):
        self.deps = deps
