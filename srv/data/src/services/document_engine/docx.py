"""
Word builder and validator.

``build_document(spec, images)`` turns a :class:`DocumentSpec` into ``.docx``
bytes:

1. **Assemble** one Markdown file: title metadata, an optional contents
   list, each section (heading + body with in-body headings shifted under
   it + appended figures), and a numbered Sources section. Image links that
   point at Busibox media (``/portal/api/media/{id}``) are rewritten to
   files the caller fetched; any other image becomes its alt text so pandoc
   never reaches the network.
2. **Render** with pandoc against the neutral ``reference.docx`` template.
3. **Post-process** with python-docx: table widths (pandoc 2.9 leaves them
   at zero, which LibreOffice and Google Docs render as overflow), header
   row repeat, core properties.
4. **Verify**: every section heading is present, tables and figures made it
   in, then LibreOffice renders a PDF for the page count and a first-page
   thumbnail.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches

from services.document_engine.office import (
    OfficeToolError,
    pandoc_markdown_to_docx,
    pdf_first_page_png,
    pdf_page_count,
    soffice_convert,
    tool_availability,
)
from services.document_engine.specs import DocumentSpec, SectionSpec, ValidationIssue, ValidationReport

logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
REFERENCE_DOCX = Path(__file__).resolve().parent / "templates" / "reference.docx"

# pandoc's own Markdown reader (2.9-compatible) trimmed to what LLM output
# uses and stripped of the surprises: no TeX math ($ is currency here), no
# raw TeX, no fancy lists ("A. Smith" is not a list).
PANDOC_FROM = (
    "markdown_strict"
    "+pipe_tables+table_captions"
    "+backtick_code_blocks+fenced_code_blocks+fenced_code_attributes"
    "+header_attributes+link_attributes+implicit_figures"
    "+strikeout+task_lists+autolink_bare_uris+intraword_underscores"
    "+shortcut_reference_links+startnum+footnotes+smart"
    "+lists_without_preceding_blankline+escaped_line_breaks+all_symbols_escapable"
    "+raw_attribute"
)

TEXT_WIDTH_IN = 6.5

_MEDIA_ID_RE = re.compile(r"/api/media/([0-9a-fA-F-]{36})|/files/([0-9a-fA-F-]{36})/download")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_RAW_ATTR_RE = re.compile(r"\{=[^}]*\}")


@dataclass
class DocBuildOutput:
    data: bytes
    report: ValidationReport
    summary: str
    thumbnail: Optional[bytes] = None
    markdown: str = ""
    stats: Dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Markdown assembly
# ---------------------------------------------------------------------------


def collect_image_ids(spec: DocumentSpec) -> List[str]:
    """Every Busibox file id the document needs: explicit figures plus inline
    ``/api/media/{id}`` links in the Markdown. The caller fetches these."""
    ids: List[str] = []
    seen: Set[str] = set()

    def add(file_id: str):
        if file_id and file_id not in seen:
            seen.add(file_id)
            ids.append(file_id)

    for section in spec.sections:
        for img in section.images:
            add(img.file_id)
        for m in _IMAGE_RE.finditer(section.markdown):
            mid = _MEDIA_ID_RE.search(m.group(2))
            if mid:
                add(mid.group(1) or mid.group(2))
    return ids


def _md_escape(text: str) -> str:
    return re.sub(r"([\\`*_\[\]{}<>#])", r"\\\1", text or "")


def _nest_headings(markdown: str, under_level: int) -> str:
    """Re-level the body's headings so its top-most heading sits one level
    under the section heading (``under_level``), preserving relative depth
    and leaving fenced code blocks alone."""
    lines = markdown.splitlines()
    levels: List[Optional[int]] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            levels.append(None)
            continue
        m = _HEADING_RE.match(line) if not in_fence else None
        levels.append(len(m.group(1)) if m else None)
    present = [lv for lv in levels if lv is not None]
    if not present:
        return markdown
    shift = (under_level + 1) - min(present)
    if shift == 0:
        return markdown
    out: List[str] = []
    for line, lv in zip(lines, levels):
        if lv is None:
            out.append(line)
        else:
            m = _HEADING_RE.match(line)
            out.append(f"{'#' * max(1, min(6, lv + shift))} {m.group(2)}")
    return "\n".join(out)


def _strip_raw_attributes(markdown: str) -> str:
    """Neutralise ```{=openxml} fences so body text can never inject raw XML."""
    out = []
    for line in markdown.splitlines():
        if _FENCE_RE.match(line) and "{=" in line:
            line = _RAW_ATTR_RE.sub("", line)
        out.append(line)
    return "\n".join(out)


def _rewrite_images(markdown: str, images: Dict[str, bytes], img_dir: Path, issues: List[ValidationIssue], where: str) -> Tuple[str, int]:
    """Point Busibox media links at local files; drop everything else to alt text."""
    placed = 0

    def repl(m: re.Match) -> str:
        nonlocal placed
        alt, target = m.group(1), m.group(2)
        mid = _MEDIA_ID_RE.search(target)
        file_id = (mid.group(1) or mid.group(2)) if mid else None
        if file_id and file_id in images:
            path = _place_image(images[file_id], file_id, img_dir)
            if path is not None:
                placed += 1
                return f"![{_md_escape(alt) or 'Figure'}]({path.name})"
        issues.append(ValidationIssue(
            severity="warning", location=where,
            message=f"image '{alt or target[:60]}' could not be embedded" + ("" if file_id else " (only Busibox media links are embedded)"),
        ))
        return f"*[{_md_escape(alt) or 'image'}]*"

    return _IMAGE_RE.sub(repl, markdown), placed


def _place_image(data: bytes, file_id: str, img_dir: Path) -> Optional[Path]:
    ext = ".png"
    if data[:3] == b"\xff\xd8\xff":
        ext = ".jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return None  # pandoc 2.9's docx writer cannot embed WebP
    elif data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    path = img_dir / f"{file_id}{ext}"
    if not path.exists():
        path.write_bytes(data)
    return path


def _section_anchor(i: int) -> str:
    return f"sec-{i + 1}"


def assemble_markdown(spec: DocumentSpec, images: Dict[str, bytes], img_dir: Path, issues: List[ValidationIssue]) -> Tuple[str, Dict[str, int]]:
    parts: List[str] = []
    n_figures = 0
    n_tables = 0

    if spec.toc:
        titled = [(i, s) for i, s in enumerate(spec.sections) if s.heading and s.level <= 2]
        if len(titled) >= 2:
            parts.append("**Contents**\n")
            for i, s in titled:
                indent = "    " if s.level == 2 else ""
                parts.append(f"{indent}- [{_md_escape(s.heading)}](#{_section_anchor(i)})")
            parts.append("")

    for i, section in enumerate(spec.sections):
        where = f"section {i + 1}" + (f" ({section.heading})" if section.heading else "")
        if section.page_break_before and i > 0:
            parts.append('```{=openxml}\n<w:p><w:r><w:br w:type="page"/></w:r></w:p>\n```\n')
        body = _strip_raw_attributes(section.markdown or "")
        if section.heading:
            parts.append(f"{'#' * section.level} {_md_escape(section.heading)} {{#{_section_anchor(i)}}}\n")
            body = _nest_headings(body, section.level)
        body, placed = _rewrite_images(body, images, img_dir, issues, where)
        n_figures += placed
        n_tables += sum(1 for line in body.splitlines() if _TABLE_SEP_RE.match(line))
        parts.append(body.strip() + "\n")
        for img in section.images:
            data = images.get(img.file_id)
            path = _place_image(data, img.file_id, img_dir) if data else None
            if path is None:
                issues.append(ValidationIssue(severity="warning", location=where, message=f"figure {img.file_id} could not be embedded"))
                continue
            n_figures += 1
            caption = _md_escape(img.caption or "")
            parts.append(f"![{caption or 'Figure'}]({path.name}){{width={min(float(img.width_in), TEXT_WIDTH_IN):g}in}}\n")

    if spec.sources:
        parts.append("# Sources {#sources}\n")
        for n, src in enumerate(spec.sources, start=1):
            title = _md_escape(src.title)
            if src.url and re.match(r"^https?://", src.url):
                parts.append(f"{n}. {title} — <{src.url}>")
            else:
                parts.append(f"{n}. {title}")
        parts.append("")

    return "\n".join(parts), {"figures": n_figures, "tables": n_tables}


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def _fix_tables(doc: Document) -> int:
    """Give every table a real width. pandoc 2.9 emits ``tblW w=0`` and an
    empty grid for pipe tables; Word autofits, LibreOffice and Google Docs
    overflow the page. Columns are sized by their longest cell text."""
    fixed = 0
    for table in doc.tables:
        n_cols = max((len(r.cells) for r in table.rows), default=0)
        if n_cols == 0:
            continue
        weights = []
        for c in range(n_cols):
            longest = 3
            for row in table.rows:
                if c < len(row.cells):
                    longest = max(longest, min(40, len(row.cells[c].text.strip())))
            weights.append(longest)
        total = float(sum(weights)) or 1.0
        widths = [Inches(TEXT_WIDTH_IN * w / total) for w in weights]

        tbl = table._tbl
        tblpr = tbl.tblPr
        tblw = tblpr.find(qn("w:tblW"))
        if tblw is None:
            tblw = parse_xml(f'<w:tblW {nsdecls("w")}/>')
            tblpr.append(tblw)
        tblw.set(qn("w:type"), "pct")
        tblw.set(qn("w:w"), "5000")
        layout = tblpr.find(qn("w:tblLayout"))
        if layout is None:
            tblpr.append(parse_xml(f'<w:tblLayout {nsdecls("w")} w:type="autofit"/>'))
        grid = tbl.find(qn("w:tblGrid"))
        if grid is None:
            grid = parse_xml(f'<w:tblGrid {nsdecls("w")}/>')
            tbl.insert(1, grid)
        for child in list(grid):
            grid.remove(child)
        for w in widths:
            grid.append(parse_xml(f'<w:gridCol {nsdecls("w")} w:w="{int(w.twips)}"/>'))
        for row in table.rows:
            for c, cell in enumerate(row.cells):
                if c < len(widths):
                    cell.width = widths[c]
        # Repeat the header row on every page.
        if table.rows:
            trpr = table.rows[0]._tr.get_or_add_trPr()
            if trpr.find(qn("w:tblHeader")) is None:
                trpr.append(parse_xml(f'<w:tblHeader {nsdecls("w")}/>'))
        fixed += 1
    return fixed


def _post_process(path: Path, spec: DocumentSpec) -> Dict[str, int]:
    doc = Document(str(path))
    n_tables = _fix_tables(doc)
    props = doc.core_properties
    props.title = spec.title
    props.subject = spec.subtitle or ""
    props.author = spec.author or "Busibox"
    props.last_modified_by = "Busibox"
    props.created = dt.datetime.now()
    props.modified = props.created
    props.comments = "Generated by Busibox AI Chat"
    doc.save(str(path))
    return {"tables": n_tables}


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')).strip().lower()


def _verify_structure(path: Path, spec: DocumentSpec, expected: Dict[str, int], issues: List[ValidationIssue], checks: List[str]) -> Dict[str, int]:
    doc = Document(str(path))
    headings = [_norm(p.text) for p in doc.paragraphs if p.style is not None and p.style.name.lower().startswith("heading")]
    missing = [s.heading for s in spec.sections if s.heading and _norm(s.heading) not in headings]
    for h in missing:
        issues.append(ValidationIssue(severity="error", location="document", message=f"section heading '{h}' is missing from the rendered document"))
    checks.append(f"checked {sum(1 for s in spec.sections if s.heading)} section heading(s)" + (f" — {len(missing)} missing" if missing else " — all present"))

    n_tables = len(doc.tables)
    empty_tables = 0
    for t in doc.tables:
        if not t.rows or all(not c.text.strip() for c in t.rows[0].cells):
            empty_tables += 1
    if expected.get("tables", 0) > n_tables:
        issues.append(ValidationIssue(severity="warning", location="document", message=f"{expected['tables'] - n_tables} Markdown table(s) did not render as tables — check the pipe-table syntax"))
    if empty_tables:
        issues.append(ValidationIssue(severity="warning", location="document", message=f"{empty_tables} table(s) have an empty header row"))
    checks.append(f"found {n_tables} table(s)")

    n_images = len(doc.inline_shapes)
    if expected.get("figures", 0) > n_images:
        issues.append(ValidationIssue(severity="error", location="document", message=f"{expected['figures'] - n_images} figure(s) did not embed"))
    checks.append(f"found {n_images} figure(s)")

    words = sum(len(p.text.split()) for p in doc.paragraphs)
    if words < 20:
        issues.append(ValidationIssue(severity="warning", location="document", message="the document has almost no body text"))
    return {"headings": len(headings), "tables": n_tables, "figures": n_images, "words": words}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_document(spec: DocumentSpec, images: Optional[Dict[str, bytes]] = None, timeout: int = 90, thumbnail: bool = True) -> DocBuildOutput:
    """Assemble, render, post-process and verify a Word document.

    ``images`` maps Busibox file ids (see :func:`collect_image_ids`) to PNG
    or JPEG bytes. Raises :class:`OfficeToolError` only when pandoc itself
    is unavailable or fails; LibreOffice problems degrade to warnings.
    """
    images = images or {}
    issues: List[ValidationIssue] = []
    checks: List[str] = []
    tools = tool_availability()
    if not tools.pandoc:
        raise OfficeToolError("pandoc is not installed on this server")

    with tempfile.TemporaryDirectory(prefix="docgen-docx-") as tmp:
        tmpdir = Path(tmp)
        img_dir = tmpdir / "img"
        img_dir.mkdir()
        markdown, expected = assemble_markdown(spec, images, img_dir, issues)
        md_path = tmpdir / "doc.md"
        md_path.write_text(markdown, encoding="utf-8")
        # Images are referenced by bare filename; pandoc resolves them from the resource path.
        for p in img_dir.iterdir():
            shutil.copy(p, tmpdir / p.name)
        checks.append(f"assembled {len(spec.sections)} section(s), {expected['figures']} figure(s), {expected['tables']} table(s)")

        out_path = tmpdir / "doc.docx"
        metadata = {
            "title": spec.title,
            "subtitle": spec.subtitle or "",
            "author": spec.author or "",
            "date": spec.date or dt.date.today().strftime("%B %d, %Y"),
        }
        pandoc_markdown_to_docx(md_path, out_path, reference_doc=REFERENCE_DOCX if REFERENCE_DOCX.exists() else None,
                                resource_path=tmpdir, timeout=timeout, from_format=PANDOC_FROM, metadata=metadata)
        checks.append("rendered with pandoc" + (" using the neutral template" if REFERENCE_DOCX.exists() else " (template missing — default styles)"))
        if not REFERENCE_DOCX.exists():
            issues.append(ValidationIssue(severity="warning", location="document", message="reference.docx template not found; default pandoc styling used"))

        post = _post_process(out_path, spec)
        checks.append(f"post-processed {post['tables']} table(s) for width and repeating headers")

        stats = _verify_structure(out_path, spec, expected, issues, checks)

        pages: Optional[int] = None
        thumb: Optional[bytes] = None
        if tools.soffice:
            try:
                pdf = soffice_convert(out_path, "pdf", timeout=timeout)
                pages = pdf_page_count(pdf)
                checks.append(f"rendered to PDF with LibreOffice — {pages if pages is not None else '?'} page(s)")
                if thumbnail:
                    thumb = pdf_first_page_png(pdf)
                    if thumb is None:
                        issues.append(ValidationIssue(severity="warning", location="document", message="first-page thumbnail could not be rendered"))
                if pages == 0:
                    issues.append(ValidationIssue(severity="error", location="document", message="the rendered PDF has no pages"))
            except OfficeToolError as exc:
                issues.append(ValidationIssue(severity="warning", location="document", message=f"PDF render skipped: {exc}"))
        else:
            issues.append(ValidationIssue(severity="warning", location="document", message="LibreOffice is not installed; page count and thumbnail unavailable"))

        data = out_path.read_bytes()

    ok = not any(i.severity == "error" for i in issues)
    report = ValidationReport(ok=ok, checks=checks, issues=issues, pages=pages, recalculated=False)
    stats["pages"] = pages or 0
    bits = [f"{len(spec.sections)} section(s)", f"{stats['tables']} table(s)", f"{stats['figures']} figure(s)", f"~{stats['words']} words"]
    if pages:
        bits.append(f"{pages} page(s)")
    summary = f"{spec.filename}: " + ", ".join(bits) + ("." if ok else f". {sum(1 for i in issues if i.severity == 'error')} error(s).")
    return DocBuildOutput(data=data, report=report, summary=summary, thumbnail=thumb, markdown=markdown, stats=stats)
