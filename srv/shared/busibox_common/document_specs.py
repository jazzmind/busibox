"""
Typed specifications for generated documents (shared by the agent and the
data-api).

These models are the contract between the agent (which fills them from a
conversation and exposes them as tool schemas) and the data-api's document
engine (``srv/data/src/services/document_engine``, which renders and
validates them). Keeping one definition here means the tool the model sees
and the API that renders it can never drift apart. They are
deliberately declarative: no code, no macros, no external references. Every
field an LLM might get wrong is validated here so a bad spec fails with a
message the model can act on rather than producing a broken file.

Column references
-----------------
Wherever a field names a *column* (formulas, totals, validations, charts)
it accepts either the column's header text (case-insensitive, preferred —
``"Unit Price"``) or its letter (``"C"``). Header matches win.

Formulas
--------
Formulas are ordinary Excel formulas. In ``column_formulas`` the placeholder
``{row}`` expands to each data row's number, so ``"=B{row}*C{row}"`` becomes
``=B2*C2``, ``=B3*C3`` … Functions that reach outside the workbook
(``WEBSERVICE``, ``RTD``, ``DDE`` …) are rejected.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared limits
# ---------------------------------------------------------------------------

MAX_SHEETS = 12
MAX_COLUMNS = 60
MAX_ROWS = 20_000
MAX_SECTIONS = 60
MAX_MARKDOWN_CHARS = 400_000
MAX_IMAGES = 40

_FILENAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
MAX_FILENAME_STEM = 120

# Names that say nothing about the content. A generated file must be findable
# in the Documents library a month later, so these are rejected in favour of
# the document's title (or the model is told to supply a real name).
GENERIC_FILE_STEMS = frozenset({
    "document", "doc", "docx", "file", "report", "spreadsheet", "workbook", "sheet",
    "book", "data", "output", "untitled", "new", "presentation", "deck", "slides",
    "slide", "memo", "export", "result", "results", "summary", "table", "notes",
    "analysis", "final", "draft", "test", "example", "sample", "generated",
})
_CELL_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}$")
_RANGE_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]{0,6}:[A-Z]{1,3}[1-9][0-9]{0,6}$")
_SHEET_NAME_BAD = re.compile(r"[\[\]:*?/\\]")

# Functions that pull data from outside the workbook or execute code. Excel
# will either block them or the user will get a security prompt; neither is
# a "working spreadsheet". Matched case-insensitively as ``NAME(``.
FORMULA_DENYLIST = (
    "WEBSERVICE",
    "FILTERXML",
    "RTD",
    "DDE",
    "CALL",
    "REGISTER",
    "REGISTER.ID",
    "EVALUATE",
    "HYPERLINK",  # external URLs in a generated file are a phishing vector
    "INDIRECT",  # not unsafe, but it hides references from validation
)
_DENY_RE = re.compile(
    r"(?<![A-Z0-9_.])(" + "|".join(re.escape(f) for f in FORMULA_DENYLIST) + r")\s*\(",
    re.IGNORECASE,
)
_EXTERNAL_REF_RE = re.compile(r"\[\d+\]|'[^']*\.xls[xm]?'!|\.xls[xm]?\]", re.IGNORECASE)


def _clean_stem(name: Optional[str], ext: str) -> str:
    """A filesystem-safe stem: unsafe characters become spaces, whitespace collapses."""
    stem = (name or "").strip()
    if ext and stem.lower().endswith(ext):
        stem = stem[: -len(ext)]
    stem = _FILENAME_RE.sub(" ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" .-")
    return stem[:MAX_FILENAME_STEM].rstrip(" .-")


def is_generic_stem(stem: str) -> bool:
    """True for names like 'document', 'report 2', 'Book1', 'untitled-final' or anything under 3 letters."""
    words = [w for w in re.split(r"[\s_\-.]+", (stem or "").lower()) if w]
    letters = [re.sub(r"[^a-z]", "", w) for w in words]
    letters = [w for w in letters if w]
    if not letters or sum(len(w) for w in letters) < 3:
        return True
    return all(w in GENERIC_FILE_STEMS or w in ("the", "a", "an", "of", "for", "my", "our", "v", "ver", "version", "copy") for w in letters)


def safe_filename(name: str, ext: str) -> str:
    """Normalise a user/LLM supplied filename to ``stem.ext`` with a safe stem."""
    stem = _clean_stem(name, ext)
    return f"{stem or 'document'}{ext}"


def descriptive_filename(
    *,
    subject: Optional[str],
    kind: Optional[str],
    ext: str,
    explicit: Optional[str] = None,
    today: Optional[dt.date] = None,
) -> str:
    """Build ``Subject - Kind - YYYY-MM-DD.ext``.

    ``explicit`` (a filename the model or caller chose) wins when it is
    descriptive; otherwise the ``subject`` (usually the document title) is
    used. The kind is appended unless the name already says it, the date is
    appended unless the name already carries one, so the function is
    idempotent — the data-api re-validates a spec the agent already filled.

    Raises ``ValueError`` when neither name says what the file is about.
    """
    stem = _clean_stem(explicit, ext) if explicit else ""
    if not stem or is_generic_stem(stem):
        stem = _clean_stem(subject, ext)
    if not stem or is_generic_stem(stem):
        offered = explicit or subject or ""
        raise ValueError(
            f"'{offered}' is not a descriptive name — give the file a title that says what it is about "
            "(e.g. 'Q3 Crew Hours by Week', 'Harbor Dredging Bid Comparison')"
        )
    kind_clean = _clean_stem(kind, "") if kind else ""
    if kind_clean and kind_clean.lower() not in stem.lower():
        stem = f"{stem} - {kind_clean}"
    if not _DATE_RE.search(stem):
        stem = f"{stem} - {(today or dt.date.today()).isoformat()}"
    return f"{stem[:MAX_FILENAME_STEM].rstrip(' .-')}{ext}"


def check_formula(formula: str) -> Optional[str]:
    """Return a reason the formula is not allowed, or ``None`` if it is fine."""
    if not isinstance(formula, str) or not formula.startswith("="):
        return "formulas must be strings starting with '='"
    if len(formula) > 2000:
        return "formula is too long (max 2000 characters)"
    m = _DENY_RE.search(formula)
    if m:
        return f"function {m.group(1).upper()}() is not allowed in generated workbooks"
    if _EXTERNAL_REF_RE.search(formula):
        return "references to other workbooks are not allowed"
    return None


# ---------------------------------------------------------------------------
# Workbook (Excel)
# ---------------------------------------------------------------------------

ColumnType = Literal["text", "number", "integer", "currency", "percent", "date", "bool"]

DEFAULT_FORMATS: Dict[str, Optional[str]] = {
    "text": None,
    "number": "#,##0.00",
    "integer": "#,##0",
    "currency": '"$"#,##0.00',
    "percent": "0.0%",
    "date": "yyyy-mm-dd",
    "bool": None,
}


class ColumnSpec(BaseModel):
    header: str = Field(min_length=1, max_length=120)
    type: ColumnType = Field(default="text", description="Drives the cell number format and value coercion")
    width: Optional[float] = Field(default=None, ge=4, le=120, description="Column width in characters")
    format: Optional[str] = Field(
        default=None, max_length=60, description="Explicit Excel number format; overrides the type default"
    )


class ColumnFormula(BaseModel):
    """A formula applied to every data row of one column."""

    column: str = Field(description="Target column: header text or letter")
    formula: str = Field(description="Excel formula; use {row} for the current row number, e.g. '=B{row}*C{row}'")

    @field_validator("formula")
    @classmethod
    def _formula_ok(cls, v: str) -> str:
        v = v.strip()
        if "{row}" not in v:
            raise ValueError("a column formula must reference {row} (e.g. '=B{row}*C{row}')")
        reason = check_formula(v.replace("{row}", "1"))
        if reason:
            raise ValueError(reason)
        return v


class TotalSpec(BaseModel):
    """A summary formula placed in the row directly under the data."""

    column: str = Field(description="Column to total: header text or letter")
    function: Literal["SUM", "AVERAGE", "MIN", "MAX", "COUNT", "COUNTA"] = "SUM"


class CellSpec(BaseModel):
    """A free-standing cell outside the table: a label, note, or summary formula."""

    cell: str = Field(description="A1-style address, e.g. 'H2'")
    value: Any = Field(description="Text, number, bool, ISO date string, or a formula starting with '='")
    bold: bool = False
    format: Optional[str] = Field(default=None, max_length=60)

    @field_validator("cell")
    @classmethod
    def _cell_ok(cls, v: str) -> str:
        v = v.strip().upper()
        if not _CELL_RE.match(v):
            raise ValueError(f"'{v}' is not a cell address like 'H2'")
        return v

    @field_validator("value")
    @classmethod
    def _value_ok(cls, v: Any) -> Any:
        if isinstance(v, str) and v.startswith("="):
            reason = check_formula(v)
            if reason:
                raise ValueError(reason)
        return v


class ValidationRule(BaseModel):
    """Data validation (drop-down list or numeric/date bounds) on a column's data cells."""

    column: str
    type: Literal["list", "whole", "decimal", "date"]
    values: Optional[List[str]] = Field(default=None, max_length=50, description="Allowed values for type='list'")
    min: Optional[float] = None
    max: Optional[float] = None

    @model_validator(mode="after")
    def _consistent(self) -> "ValidationRule":
        if self.type == "list" and not self.values:
            raise ValueError("a 'list' validation needs 'values'")
        if self.type != "list" and self.min is None and self.max is None:
            raise ValueError(f"a '{self.type}' validation needs 'min' and/or 'max'")
        return self


class ConditionalRule(BaseModel):
    """Highlight data cells in a column when a condition holds."""

    column: str
    rule: Literal["greater_than", "less_than", "between", "equal", "not_equal", "contains"]
    value: Any = None
    value2: Optional[float] = Field(default=None, description="Upper bound for 'between'")
    fill: str = Field(default="FFC7CE", pattern=r"^[0-9A-Fa-f]{6}$", description="Hex fill colour")
    font_color: str = Field(default="9C0006", pattern=r"^[0-9A-Fa-f]{6}$")

    @model_validator(mode="after")
    def _consistent(self) -> "ConditionalRule":
        if self.rule == "between" and (self.value is None or self.value2 is None):
            raise ValueError("'between' needs 'value' and 'value2'")
        if self.rule != "between" and self.value is None:
            raise ValueError(f"'{self.rule}' needs 'value'")
        return self


class NamedRange(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_.]{0,60}$")
    column: str = Field(description="The column whose data cells the name refers to")


class ChartSpec(BaseModel):
    """A native Excel chart drawn from the sheet's table."""

    type: Literal["bar", "column", "line", "pie"] = "column"
    title: str = Field(default="", max_length=120)
    categories: str = Field(description="Column with the category labels (header text or letter)")
    values: List[str] = Field(min_length=1, max_length=8, description="Column(s) with the numbers")
    anchor: Optional[str] = Field(default=None, description="Top-left cell for the chart; default is right of the table")
    y_title: Optional[str] = Field(default=None, max_length=60)
    x_title: Optional[str] = Field(default=None, max_length=60)

    @field_validator("anchor")
    @classmethod
    def _anchor_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if not _CELL_RE.match(v):
            raise ValueError(f"'{v}' is not a cell address")
        return v


class SheetSpec(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    columns: List[ColumnSpec] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: List[List[Any]] = Field(default_factory=list, max_length=MAX_ROWS, description="Data rows, one list per row, values in column order")
    header_row: int = Field(default=1, ge=1, le=20, description="Row number of the header; rows above it are free for labels")
    column_formulas: List[ColumnFormula] = Field(default_factory=list, max_length=MAX_COLUMNS)
    totals: List[TotalSpec] = Field(default_factory=list, max_length=MAX_COLUMNS)
    totals_label: str = Field(default="Total", max_length=40)
    cells: List[CellSpec] = Field(default_factory=list, max_length=200)
    freeze_header: bool = True
    autofilter: bool = True
    validations: List[ValidationRule] = Field(default_factory=list, max_length=MAX_COLUMNS)
    conditional: List[ConditionalRule] = Field(default_factory=list, max_length=MAX_COLUMNS)
    named_ranges: List[NamedRange] = Field(default_factory=list, max_length=MAX_COLUMNS)
    chart: Optional[ChartSpec] = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        v = v.strip()
        if _SHEET_NAME_BAD.search(v):
            raise ValueError("sheet names cannot contain [ ] : * ? / \\")
        return v

    @model_validator(mode="after")
    def _shape_ok(self) -> "SheetSpec":
        width = len(self.columns)
        for i, row in enumerate(self.rows):
            if len(row) > width:
                raise ValueError(f"row {i + 1} has {len(row)} values but there are only {width} columns")
        headers = [c.header.strip().lower() for c in self.columns]
        if len(set(headers)) != len(headers):
            raise ValueError("column headers must be unique")
        return self


class AssertionSpec(BaseModel):
    """A numeric check run against the *recalculated* workbook."""

    sheet: Optional[str] = Field(default=None, description="Sheet name; default is the first sheet")
    cell: str
    equals: Optional[float] = None
    equals_sum_of: Optional[str] = Field(default=None, description="A range like 'E2:E10' whose sum the cell must equal")
    between: Optional[Tuple[float, float]] = None
    tolerance: float = Field(default=0.01, ge=0)

    @field_validator("cell")
    @classmethod
    def _cell_ok(cls, v: str) -> str:
        v = v.strip().upper()
        if not _CELL_RE.match(v):
            raise ValueError(f"'{v}' is not a cell address")
        return v

    @field_validator("equals_sum_of")
    @classmethod
    def _range_ok(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().upper()
        if not _RANGE_RE.match(v):
            raise ValueError(f"'{v}' is not a range like 'E2:E10'")
        return v

    @model_validator(mode="after")
    def _one_check(self) -> "AssertionSpec":
        if sum(x is not None for x in (self.equals, self.equals_sum_of, self.between)) != 1:
            raise ValueError("an assertion needs exactly one of equals, equals_sum_of, between")
        return self


class WorkbookSpec(BaseModel):
    title: str = Field(min_length=3, max_length=200, description="What the workbook is about, e.g. 'Q3 Crew Hours by Week'. Names the file and is stored as the workbook title.")
    kind: str = Field(default="Spreadsheet", max_length=40, description="Short noun for the file name, e.g. 'Budget', 'Bid Comparison', 'Tracker'")
    filename: Optional[str] = Field(default=None, max_length=160, description="Optional. Derived from title as 'Title - Kind - YYYY-MM-DD.xlsx' when omitted or generic.")
    sheets: List[SheetSpec] = Field(min_length=1, max_length=MAX_SHEETS)
    assertions: List[AssertionSpec] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _finish(self) -> "WorkbookSpec":
        names = [s.name.lower() for s in self.sheets]
        if len(set(names)) != len(names):
            raise ValueError("sheet names must be unique")
        self.filename = descriptive_filename(subject=self.title, kind=self.kind, ext=".xlsx", explicit=self.filename)
        return self


# ---------------------------------------------------------------------------
# Document (Word)
# ---------------------------------------------------------------------------


class ImageRef(BaseModel):
    """An image already stored in Busibox (e.g. produced by render_chart)."""

    file_id: str = Field(min_length=8, max_length=64)
    caption: Optional[str] = Field(default=None, max_length=300)
    width_in: float = Field(default=6.0, ge=1.0, le=7.0, description="Width in inches (page text width is 6.5)")


class SectionSpec(BaseModel):
    heading: Optional[str] = Field(default=None, max_length=200)
    level: int = Field(default=1, ge=1, le=3, description="Heading level for 'heading'")
    markdown: str = Field(default="", max_length=MAX_MARKDOWN_CHARS, description="Body in Markdown: paragraphs, lists, tables, images, links")
    images: List[ImageRef] = Field(default_factory=list, max_length=MAX_IMAGES, description="Images appended after the body")
    page_break_before: bool = False


class SourceSpec(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: Optional[str] = Field(default=None, max_length=2000)


class DocumentSpec(BaseModel):
    title: str = Field(min_length=3, max_length=300, description="What the document is about; names the file and heads the title page")
    kind: str = Field(default="Document", max_length=40, description="Short noun for the file name, e.g. 'Memo', 'Research Report', 'Briefing'")
    filename: Optional[str] = Field(default=None, max_length=160, description="Optional. Derived from title as 'Title - Kind - YYYY-MM-DD.docx' when omitted or generic.")
    subtitle: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=120)
    date: Optional[str] = Field(default=None, max_length=40, description="Printed under the title; default is today")
    toc: bool = Field(default=True, description="Insert a table of contents after the title")
    sections: List[SectionSpec] = Field(min_length=1, max_length=MAX_SECTIONS)
    sources: List[SourceSpec] = Field(default_factory=list, max_length=200, description="Rendered as a numbered 'Sources' section at the end")
    template: Literal["neutral"] = "neutral"

    @model_validator(mode="after")
    def _finish(self) -> "DocumentSpec":
        self.filename = descriptive_filename(subject=self.title, kind=self.kind, ext=".docx", explicit=self.filename)
        return self


# ---------------------------------------------------------------------------
# Presentation (PowerPoint)
# ---------------------------------------------------------------------------

MAX_SLIDES = 60
MAX_BULLETS = 12
MAX_TABLE_ROWS = 15
MAX_TABLE_COLS = 8
MAX_CHART_POINTS = 24

SlideLayout = Literal["title", "section", "bullets", "two_column", "image", "table", "chart"]


class SlideTable(BaseModel):
    headers: List[str] = Field(min_length=1, max_length=MAX_TABLE_COLS)
    rows: List[List[Any]] = Field(min_length=1, max_length=MAX_TABLE_ROWS, description="Cell values in header order; numbers are right-aligned")

    @model_validator(mode="after")
    def _shape(self) -> "SlideTable":
        for i, row in enumerate(self.rows):
            if len(row) > len(self.headers):
                raise ValueError(f"table row {i + 1} has {len(row)} cells but there are {len(self.headers)} headers")
        return self


class SlideChartSeries(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    values: List[float] = Field(min_length=1, max_length=MAX_CHART_POINTS)


class SlideChart(BaseModel):
    """A native, editable PowerPoint chart."""

    type: Literal["column", "bar", "line", "pie"] = "column"
    categories: List[str] = Field(min_length=1, max_length=MAX_CHART_POINTS)
    series: List[SlideChartSeries] = Field(min_length=1, max_length=6)
    number_format: Optional[str] = Field(default=None, max_length=40, description="Excel-style, e.g. '#,##0', '0.0%', '\"$\"#,##0'")
    source: Optional[str] = Field(default=None, max_length=200, description="Printed small under the chart")

    @model_validator(mode="after")
    def _shape(self) -> "SlideChart":
        n = len(self.categories)
        for srs in self.series:
            if len(srs.values) != n:
                raise ValueError(f"series '{srs.name}' has {len(srs.values)} values but there are {n} categories")
        if self.type == "pie" and len(self.series) != 1:
            raise ValueError("a pie chart takes exactly one series")
        return self


class SlideSpec(BaseModel):
    """One slide. Pick the layout, fill only the fields it uses:

    - ``title``: deck title slide (title/subtitle); usually auto-added, use for a closing slide
    - ``section``: a divider (title, optional subtitle)
    - ``bullets``: title + bullets, optionally with an ``image`` on the right
    - ``two_column``: title + ``bullets`` (left) and ``right_bullets`` (right), optional column headings
    - ``image``: title + one large ``image`` with a caption
    - ``table``: title + ``table``
    - ``chart``: title + ``chart``, optional ``bullets`` (takeaways) on the right
    Prefix a bullet with "- " to make it a sub-bullet.
    """

    layout: SlideLayout = "bullets"
    title: Optional[str] = Field(default=None, max_length=160, description="Slide title; required for every layout except 'title'")
    subtitle: Optional[str] = Field(default=None, max_length=200, description="'title' and 'section' layouts")
    bullets: List[str] = Field(default_factory=list, max_length=MAX_BULLETS, description="Short lines, ~12 words each; '- ' prefix for a sub-bullet")
    right_bullets: List[str] = Field(default_factory=list, max_length=MAX_BULLETS, description="'two_column' layout: the right column")
    left_heading: Optional[str] = Field(default=None, max_length=80)
    right_heading: Optional[str] = Field(default=None, max_length=80)
    image: Optional[ImageRef] = Field(default=None, description="A Busibox media file id (e.g. from render_chart)")
    table: Optional[SlideTable] = None
    chart: Optional[SlideChart] = None
    notes: Optional[str] = Field(default=None, max_length=4000, description="Speaker notes — the narration for this slide")

    @field_validator("bullets", "right_bullets")
    @classmethod
    def _bullets_ok(cls, v: List[str]) -> List[str]:
        cleaned = [b.strip() for b in v if b and b.strip()]
        for b in cleaned:
            if len(b) > 300:
                raise ValueError("a bullet must be under 300 characters — split it or move detail to notes")
        return cleaned

    @model_validator(mode="after")
    def _layout_ok(self) -> "SlideSpec":
        if self.layout != "title" and not (self.title or "").strip():
            raise ValueError(f"a '{self.layout}' slide needs a title")
        if self.layout == "table" and self.table is None:
            raise ValueError("a 'table' slide needs 'table'")
        if self.layout == "chart" and self.chart is None:
            raise ValueError("a 'chart' slide needs 'chart'")
        if self.layout == "image" and self.image is None:
            raise ValueError("an 'image' slide needs 'image'")
        if self.layout == "two_column" and not (self.bullets and self.right_bullets):
            raise ValueError("a 'two_column' slide needs 'bullets' and 'right_bullets'")
        if self.layout == "bullets" and not self.bullets and self.image is None:
            raise ValueError("a 'bullets' slide needs bullets")
        return self


class PresentationSpec(BaseModel):
    title: str = Field(min_length=3, max_length=200, description="What the deck is about; names the file and the title slide")
    kind: str = Field(default="Presentation", max_length=40, description="Short noun for the file name, e.g. 'Briefing', 'Bid Review', 'Kickoff'")
    filename: Optional[str] = Field(default=None, max_length=160, description="Optional. Derived from title as 'Title - Kind - YYYY-MM-DD.pptx' when omitted or generic.")
    subtitle: Optional[str] = Field(default=None, max_length=200)
    author: Optional[str] = Field(default=None, max_length=120)
    date: Optional[str] = Field(default=None, max_length=40, description="Printed on the title slide; default is today")
    slides: List[SlideSpec] = Field(min_length=1, max_length=MAX_SLIDES)
    title_slide: bool = Field(default=True, description="Add a title slide from title/subtitle/author/date unless the first slide already is one")
    sources: List[SourceSpec] = Field(default_factory=list, max_length=60, description="Rendered as closing 'Sources' slide(s)")
    template: Literal["neutral"] = "neutral"

    @model_validator(mode="after")
    def _finish(self) -> "PresentationSpec":
        self.filename = descriptive_filename(subject=self.title, kind=self.kind, ext=".pptx", explicit=self.filename)
        return self


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class ValidationIssue(BaseModel):
    severity: Literal["error", "warning"]
    location: str = Field(description="Where: 'Sheet!E12', 'section 3', 'document'")
    message: str


class ValidationReport(BaseModel):
    ok: bool
    checks: List[str] = Field(default_factory=list, description="What was verified, in order")
    issues: List[ValidationIssue] = Field(default_factory=list)
    formula_count: int = 0
    recalculated: bool = False
    pages: Optional[int] = None

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == "error"]


class GenerateResult(BaseModel):
    """What the API returns (and the agent tool relays) for a generation request."""

    success: bool
    filename: str
    mime_type: str
    size_bytes: int = 0
    file_id: Optional[str] = None
    download_url: Optional[str] = Field(default=None, description="Portal-relative URL that downloads the file")
    thumbnail_file_id: Optional[str] = None
    thumbnail_url: Optional[str] = Field(default=None, description="Portal-relative URL of a first-page PNG (Word only)")
    validation: ValidationReport
    summary: str = Field(default="", description="One-paragraph description of the file for the model to relay")
    error: Optional[str] = None
