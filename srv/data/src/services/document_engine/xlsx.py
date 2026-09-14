"""
Excel builder and validator.

``build_workbook(spec)`` turns a :class:`WorkbookSpec` into ``.xlsx`` bytes
in three steps:

1. **Build** with openpyxl — typed cells, formulas, totals, validations,
   conditional formats, defined names and native charts.
2. **Recalculate** with LibreOffice (``soffice --convert-to xlsx``), which
   evaluates every formula exactly as a spreadsheet application would.
3. **Verify** the recalculated copy — scan every cell for ``#REF!``,
   ``#NAME?``, ``#DIV/0!`` … , confirm the structure survived the round
   trip, and run the spec's numeric assertions.

The recalculated copy is what gets delivered when it preserved everything
we built (so previews, Quick Look and Busibox's own indexer see real
numbers, not blanks). If LibreOffice dropped something — a chart, a
validation — the original is delivered instead and Excel computes the
formulas on open.
"""

from __future__ import annotations

import datetime as dt
import io
import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, PieChart, Reference
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet

from services.document_engine.office import OfficeToolError, soffice_convert, tool_availability
from services.document_engine.specs import (
    DEFAULT_FORMATS,
    ColumnSpec,
    SheetSpec,
    ValidationIssue,
    ValidationReport,
    WorkbookSpec,
    check_formula,
)

logger = logging.getLogger(__name__)

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXCEL_ERRORS = ("#REF!", "#NAME?", "#DIV/0!", "#VALUE!", "#N/A", "#NUM!", "#NULL!")
_ERROR_RE = re.compile("|".join(re.escape(e) for e in EXCEL_ERRORS))
_LETTER_RE = re.compile(r"^[A-Z]{1,3}$")
_NUMBER_JUNK_RE = re.compile(r"[,$€£%\s]")

HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
HEADER_FONT = Font(bold=True)
TOTAL_FONT = Font(bold=True)
_THIN = Side(style="thin", color="A6A6A6")
HEADER_BORDER = Border(bottom=_THIN)
TOTAL_BORDER = Border(top=_THIN)

MAX_ISSUES = 50


@dataclass
class BuildOutput:
    data: bytes
    report: ValidationReport
    summary: str
    thumbnail: Optional[bytes] = None
    delivered: str = "recalculated"  # or "original"
    sheet_summaries: List[str] = field(default_factory=list)


class SpecError(ValueError):
    """The spec is well-formed but refers to something that does not exist."""


# ---------------------------------------------------------------------------
# Column resolution and value coercion
# ---------------------------------------------------------------------------


class _Columns:
    """Resolve header text or column letters to (index, letter, ColumnSpec)."""

    def __init__(self, sheet: SheetSpec):
        self.specs = sheet.columns
        self._by_header = {c.header.strip().lower(): i + 1 for i, c in enumerate(sheet.columns)}

    def index(self, ref: str, what: str) -> int:
        key = (ref or "").strip()
        if key.lower() in self._by_header:
            return self._by_header[key.lower()]
        if _LETTER_RE.match(key.upper()):
            idx = 0
            for ch in key.upper():
                idx = idx * 26 + (ord(ch) - 64)
            if 1 <= idx <= len(self.specs):
                return idx
        raise SpecError(f"{what}: column '{ref}' is not one of the sheet's columns")

    def letter(self, ref: str, what: str) -> str:
        return get_column_letter(self.index(ref, what))

    def spec(self, ref: str, what: str) -> ColumnSpec:
        return self.specs[self.index(ref, what) - 1]


def _parse_date(value: Any) -> Optional[Any]:
    if isinstance(value, (dt.date, dt.datetime)):
        return value
    if not isinstance(value, str):
        return None
    s = value.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            parsed = dt.datetime.strptime(s, fmt)
            return parsed if "%H" in fmt else parsed.date()
        except ValueError:
            continue
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        return None


def _parse_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = _NUMBER_JUNK_RE.sub("", value.strip())
        if s.startswith("(") and s.endswith(")"):
            s = "-" + s[1:-1]
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _coerce(value: Any, col: ColumnSpec, where: str, issues: List[ValidationIssue]) -> Tuple[Any, bool]:
    """Return (cell value, is_literal_text). Never raises; logs a warning issue on mismatch."""
    if value is None or value == "":
        return None, False
    kind = col.type
    if kind == "text":
        return (value if isinstance(value, str) else str(value)), True
    if kind == "bool":
        if isinstance(value, bool):
            return value, False
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "y", "1"):
            return True, False
        if isinstance(value, str) and value.strip().lower() in ("false", "no", "n", "0"):
            return False, False
        return str(value), True
    if kind == "date":
        parsed = _parse_date(value)
        if parsed is None:
            issues.append(ValidationIssue(severity="warning", location=where, message=f"'{value}' is not a date; stored as text"))
            return str(value), True
        return parsed, False
    # number / integer / currency / percent
    if isinstance(value, str) and value.startswith("="):
        reason = check_formula(value)
        if reason:
            raise SpecError(f"{where}: {reason}")
        return value, False
    num = _parse_number(value)
    if num is None:
        issues.append(ValidationIssue(severity="warning", location=where, message=f"'{value}' is not a number; stored as text (totals will skip it)"))
        return str(value), True
    if kind == "integer" and float(num).is_integer():
        return int(num), False
    return num, False


def _set(ws: Worksheet, coord: str, value: Any, literal_text: bool = False, number_format: Optional[str] = None, font: Optional[Font] = None):
    cell = ws[coord]
    cell.value = value
    if literal_text and isinstance(value, str) and value.startswith("="):
        cell.data_type = "s"
    if number_format:
        cell.number_format = number_format
    if font:
        cell.font = font
    return cell


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _build_sheet(wb: Workbook, ws: Worksheet, sheet: SheetSpec, issues: List[ValidationIssue]) -> Dict[str, Any]:
    cols = _Columns(sheet)
    n_cols = len(sheet.columns)
    header_row = sheet.header_row
    first = header_row + 1
    last = header_row + len(sheet.rows)  # last data row (== header_row when no rows)
    fmt_for = [c.format or DEFAULT_FORMATS.get(c.type) for c in sheet.columns]
    pct_suspects = 0

    # Header
    for i, col in enumerate(sheet.columns, start=1):
        cell = ws.cell(row=header_row, column=i, value=col.header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.border = HEADER_BORDER
        cell.alignment = Alignment(vertical="center", wrap_text=True)

    # Data
    for r_off, row in enumerate(sheet.rows):
        r = first + r_off
        for i, col in enumerate(sheet.columns, start=1):
            raw = row[i - 1] if i - 1 < len(row) else None
            where = f"{sheet.name}!{get_column_letter(i)}{r}"
            value, literal = _coerce(raw, col, where, issues)
            if value is None:
                continue
            cell = ws.cell(row=r, column=i)
            cell.value = value
            if literal and isinstance(value, str) and value.startswith("="):
                cell.data_type = "s"
            if fmt_for[i - 1] and not literal:
                cell.number_format = fmt_for[i - 1]
            if col.type == "percent" and isinstance(value, (int, float)) and value > 1.5:
                pct_suspects += 1
    if pct_suspects:
        issues.append(ValidationIssue(
            severity="warning", location=sheet.name,
            message=f"{pct_suspects} percent cell(s) are larger than 1.5 — percent columns expect fractions (0.125 for 12.5%)",
        ))

    # Column formulas (one per data row)
    for cf in sheet.column_formulas:
        idx = cols.index(cf.column, f"{sheet.name} column_formulas")
        letter = get_column_letter(idx)
        for r in range(first, last + 1):
            cell = ws[f"{letter}{r}"]
            cell.value = cf.formula.replace("{row}", str(r))
            if fmt_for[idx - 1]:
                cell.number_format = fmt_for[idx - 1]

    # Totals row
    totals_row = None
    if sheet.totals and last >= first:
        totals_row = last + 1
        total_cols = {cols.index(t.column, f"{sheet.name} totals") for t in sheet.totals}
        label_col = 1 if 1 not in total_cols else None
        if label_col:
            c = ws.cell(row=totals_row, column=label_col, value=sheet.totals_label)
            c.font = TOTAL_FONT
            c.border = TOTAL_BORDER
        for t in sheet.totals:
            idx = cols.index(t.column, f"{sheet.name} totals")
            letter = get_column_letter(idx)
            c = ws[f"{letter}{totals_row}"]
            c.value = f"={t.function}({letter}{first}:{letter}{last})"
            c.font = TOTAL_FONT
            c.border = TOTAL_BORDER
            if fmt_for[idx - 1]:
                c.number_format = fmt_for[idx - 1]

    # Free cells
    for cs in sheet.cells:
        value: Any = cs.value
        literal = False
        if isinstance(value, str) and not value.startswith("="):
            parsed = _parse_date(value) if re.match(r"^\d{4}-\d{2}-\d{2}", value) else None
            value = parsed if parsed is not None else value
            literal = isinstance(value, str)
        elif isinstance(value, str):
            pass  # formula, already checked by the spec
        cell = _set(ws, cs.cell, value, literal_text=literal, number_format=cs.format, font=Font(bold=True) if cs.bold else None)
        if isinstance(value, (dt.date, dt.datetime)) and not cs.format:
            cell.number_format = DEFAULT_FORMATS["date"]

    # Layout
    if sheet.freeze_header:
        ws.freeze_panes = f"A{header_row + 1}"
    if sheet.autofilter and last >= first:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(n_cols)}{last}"
    total_cols = {cols.index(t.column, f"{sheet.name} totals") for t in sheet.totals}
    for i, col in enumerate(sheet.columns, start=1):
        if col.width:
            width = col.width
        else:
            longest = len(col.header)
            if col.type in ("number", "integer", "currency", "percent"):
                # Size for the *formatted* number, and for the total if there is one:
                # "$13,046.00" is a lot wider than "4800".
                magnitude = 0.0
                for row in sheet.rows:
                    v = row[i - 1] if i - 1 < len(row) else None
                    n = _parse_number(v)
                    if n is not None:
                        magnitude = magnitude + abs(n) if i in total_cols else max(magnitude, abs(n))
                formatted = f"{magnitude:,.2f}" + ("$" if col.type == "currency" else "")
                # Formula columns have no row values, so keep a floor wide enough for a six-figure total.
                longest = max(longest, len(formatted) + 1, 14 if col.type == "currency" else 12)
            else:
                for row in sheet.rows[:500]:
                    v = row[i - 1] if i - 1 < len(row) else None
                    if v is not None:
                        longest = max(longest, len(str(v)))
            width = min(60, max(10, longest + 2))
        ws.column_dimensions[get_column_letter(i)].width = width

    # Validations (data rows plus headroom for the user to add rows)
    val_last = max(last, first) + 200
    for v in sheet.validations:
        letter = cols.letter(v.column, f"{sheet.name} validations")
        rng = f"{letter}{first}:{letter}{val_last}"
        if v.type == "list":
            joined = ",".join(s.replace('"', "'") for s in (v.values or []))
            if len(joined) > 250:
                raise SpecError(f"{sheet.name} validations: list values for '{v.column}' exceed Excel's 255-character limit; put the options in a column and use a named range instead")
            dv = DataValidation(type="list", formula1=f'"{joined}"', allow_blank=True)
        else:
            op = "between" if v.min is not None and v.max is not None else ("greaterThanOrEqual" if v.min is not None else "lessThanOrEqual")
            f1 = str(v.min if v.min is not None else v.max)
            f2 = str(v.max) if op == "between" else None
            dv = DataValidation(type=v.type, operator=op, formula1=f1, formula2=f2, allow_blank=True)
        dv.error = "Value not allowed"
        dv.errorTitle = "Invalid entry"
        ws.add_data_validation(dv)
        dv.add(rng)

    # Conditional formatting (data rows only)
    if last >= first:
        for rule in sheet.conditional:
            letter = cols.letter(rule.column, f"{sheet.name} conditional")
            rng = f"{letter}{first}:{letter}{last}"
            fill = PatternFill("solid", fgColor=rule.fill)
            font = Font(color=rule.font_color)
            if rule.rule == "contains":
                needle = str(rule.value).replace('"', '""')
                ws.conditional_formatting.add(rng, FormulaRule(formula=[f'ISNUMBER(SEARCH("{needle}",{letter}{first}))'], fill=fill, font=font))
            else:
                ops = {"greater_than": "greaterThan", "less_than": "lessThan", "equal": "equal", "not_equal": "notEqual", "between": "between"}
                vals = [str(rule.value)] if rule.rule != "between" else [str(rule.value), str(rule.value2)]
                if isinstance(rule.value, str) and rule.rule in ("equal", "not_equal"):
                    vals = [f'"{rule.value}"']
                ws.conditional_formatting.add(rng, CellIsRule(operator=ops[rule.rule], formula=vals, fill=fill, font=font))

    # Defined names
    for nr in sheet.named_ranges:
        letter = cols.letter(nr.column, f"{sheet.name} named_ranges")
        ref = f"'{sheet.name}'!${letter}${first}:${letter}${max(last, first)}"
        wb.defined_names[nr.name] = DefinedName(nr.name, attr_text=ref)

    # Chart
    chart_kind = None
    if sheet.chart and last >= first:
        ch = sheet.chart
        cat_idx = cols.index(ch.categories, f"{sheet.name} chart.categories")
        val_idx = [cols.index(v, f"{sheet.name} chart.values") for v in ch.values]
        if ch.type == "pie":
            chart = PieChart()
        elif ch.type == "line":
            chart = LineChart()
        else:
            chart = BarChart()
            chart.type = "bar" if ch.type == "bar" else "col"
        chart.title = ch.title or None
        if ch.type != "pie":
            if ch.y_title:
                chart.y_axis.title = ch.y_title
            if ch.x_title:
                chart.x_axis.title = ch.x_title
        for vi in val_idx:
            data = Reference(ws, min_col=vi, min_row=header_row, max_row=last)
            chart.add_data(data, titles_from_data=True)
        chart.set_categories(Reference(ws, min_col=cat_idx, min_row=first, max_row=last))
        chart.width, chart.height = 18, 9
        anchor = ch.anchor or f"{get_column_letter(n_cols + 2)}{header_row}"
        ws.add_chart(chart, anchor)
        chart_kind = ch.type

    return {
        "rows": len(sheet.rows),
        "cols": n_cols,
        "totals_row": totals_row,
        "chart": chart_kind,
        "validations": len(sheet.validations),
        "conditional": len(sheet.conditional),
    }


def build_openpyxl(spec: WorkbookSpec, issues: List[ValidationIssue]) -> Tuple[bytes, List[Dict[str, Any]]]:
    """Build the workbook with openpyxl. Returns (bytes, per-sheet info)."""
    wb = Workbook()
    wb.remove(wb.active)
    if spec.title:
        wb.properties.title = spec.title
    wb.properties.creator = "Busibox"
    infos = []
    for sheet in spec.sheets:
        ws = wb.create_sheet(title=sheet.name)
        infos.append(_build_sheet(wb, ws, sheet, issues))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), infos


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


def _formula_cells(path: Path) -> Dict[str, set]:
    wb = load_workbook(path, data_only=False)
    out: Dict[str, set] = {}
    for ws in wb.worksheets:
        coords = set()
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                text = getattr(v, "text", v)
                if isinstance(text, str) and text.startswith("=") and cell.data_type == "f":
                    coords.add(cell.coordinate)
        out[ws.title] = coords
    wb.close()
    return out


def _zip_part_count(path: Path, prefix: str) -> int:
    with zipfile.ZipFile(path) as zf:
        return sum(1 for n in zf.namelist() if n.startswith(prefix))


def _scan_errors(path: Path, issues: List[ValidationIssue]) -> int:
    wb = load_workbook(path, data_only=True)
    count = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and _ERROR_RE.search(cell.value):
                    count += 1
                    if len(issues) < MAX_ISSUES:
                        issues.append(ValidationIssue(severity="error", location=f"{ws.title}!{cell.coordinate}", message=f"formula result is {cell.value}"))
    wb.close()
    return count


def _cell_number(ws: Worksheet, coord: str) -> Optional[float]:
    v = ws[coord].value
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return _parse_number(v)


def _run_assertions(path: Path, spec: WorkbookSpec, issues: List[ValidationIssue]) -> int:
    wb = load_workbook(path, data_only=True)
    failed = 0
    try:
        for a in spec.assertions:
            sheet_name = a.sheet or spec.sheets[0].name
            if sheet_name not in wb.sheetnames:
                issues.append(ValidationIssue(severity="error", location=f"{sheet_name}!{a.cell}", message="assertion refers to a sheet that does not exist"))
                failed += 1
                continue
            ws = wb[sheet_name]
            actual = _cell_number(ws, a.cell)
            loc = f"{sheet_name}!{a.cell}"
            if actual is None:
                issues.append(ValidationIssue(severity="error", location=loc, message=f"assertion: cell is not numeric (value={ws[a.cell].value!r})"))
                failed += 1
                continue
            if a.equals is not None:
                if abs(actual - a.equals) > a.tolerance:
                    issues.append(ValidationIssue(severity="error", location=loc, message=f"assertion: expected {a.equals}, got {actual}"))
                    failed += 1
            elif a.equals_sum_of is not None:
                total = 0.0
                for row in ws[a.equals_sum_of]:
                    for cell in row:
                        n = _cell_number(ws, cell.coordinate)
                        if n is not None:
                            total += n
                if abs(actual - total) > a.tolerance:
                    issues.append(ValidationIssue(severity="error", location=loc, message=f"assertion: expected sum of {a.equals_sum_of} = {total}, got {actual}"))
                    failed += 1
            elif a.between is not None:
                lo, hi = a.between
                if not (lo - a.tolerance <= actual <= hi + a.tolerance):
                    issues.append(ValidationIssue(severity="error", location=loc, message=f"assertion: expected between {lo} and {hi}, got {actual}"))
                    failed += 1
    finally:
        wb.close()
    return failed


def _structure_preserved(original: Path, recalculated: Path, spec: WorkbookSpec, issues: List[ValidationIssue]) -> bool:
    """Did the LibreOffice round trip keep everything we built?"""
    ok = True
    try:
        orig_f, new_f = _formula_cells(original), _formula_cells(recalculated)
    except Exception as exc:  # noqa: BLE001
        issues.append(ValidationIssue(severity="warning", location="workbook", message=f"could not compare formulas after recalculation: {exc}"))
        return False
    if set(orig_f) != set(new_f):
        issues.append(ValidationIssue(severity="warning", location="workbook", message="sheet names changed during recalculation"))
        return False
    for name, coords in orig_f.items():
        missing = coords - new_f.get(name, set())
        if missing:
            issues.append(ValidationIssue(severity="warning", location=name, message=f"{len(missing)} formula(s) were replaced by values during recalculation"))
            ok = False
    n_charts = sum(1 for s in spec.sheets if s.chart and s.rows)
    if n_charts and _zip_part_count(recalculated, "xl/charts/chart") < n_charts:
        issues.append(ValidationIssue(severity="warning", location="workbook", message="chart was lost during recalculation"))
        ok = False
    wb_o = load_workbook(original)
    wb_n = load_workbook(recalculated)
    try:
        for s in spec.sheets:
            o, n = wb_o[s.name], wb_n[s.name]
            if len(o.data_validations.dataValidation) > len(n.data_validations.dataValidation):
                issues.append(ValidationIssue(severity="warning", location=s.name, message="data validation was lost during recalculation"))
                ok = False
            if len(list(o.conditional_formatting)) > len(list(n.conditional_formatting)):
                issues.append(ValidationIssue(severity="warning", location=s.name, message="conditional formatting was lost during recalculation"))
                ok = False
        if len(wb_o.defined_names) > len(wb_n.defined_names):
            issues.append(ValidationIssue(severity="warning", location="workbook", message="named ranges were lost during recalculation"))
            ok = False
    finally:
        wb_o.close()
        wb_n.close()
    return ok


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_workbook(spec: WorkbookSpec, recalc_timeout: int = 90) -> BuildOutput:
    """Build, recalculate and verify a workbook. Never raises for tool failures;
    the report says what could and could not be checked. Raises ``SpecError``
    when the spec refers to columns that do not exist."""
    issues: List[ValidationIssue] = []
    checks: List[str] = []

    original_bytes, infos = build_openpyxl(spec, issues)
    checks.append(f"built {len(spec.sheets)} sheet(s) with openpyxl")

    with tempfile.TemporaryDirectory(prefix="docgen-xlsx-") as tmp:
        tmpdir = Path(tmp)
        original = tmpdir / "book.xlsx"
        original.write_bytes(original_bytes)

        formula_count = sum(len(v) for v in _formula_cells(original).values())
        delivered_bytes = original_bytes
        delivered = "original"
        recalculated = False
        error_count = 0
        failed_assertions = 0

        tools = tool_availability()
        if not tools.soffice:
            issues.append(ValidationIssue(severity="warning", location="workbook", message="LibreOffice is not installed; formulas were not recalculated or checked (Excel will compute them on open)"))
        elif formula_count == 0 and not spec.assertions:
            checks.append("no formulas to recalculate")
        else:
            try:
                recalc_path = soffice_convert(original, "xlsx", timeout=recalc_timeout)
                recalculated = True
                checks.append(f"recalculated {formula_count} formula(s) with LibreOffice")
                error_count = _scan_errors(recalc_path, issues)
                checks.append("scanned every cell for Excel error values" + (f" — {error_count} found" if error_count else " — none"))
                if spec.assertions:
                    failed_assertions = _run_assertions(recalc_path, spec, issues)
                    checks.append(f"ran {len(spec.assertions)} assertion(s)" + (f" — {failed_assertions} failed" if failed_assertions else " — all passed"))
                if error_count == 0 and _structure_preserved(original, recalc_path, spec, issues):
                    delivered_bytes = recalc_path.read_bytes()
                    delivered = "recalculated"
                    checks.append("delivering the recalculated copy (cached values embedded)")
                elif error_count == 0:
                    checks.append("delivering the original (LibreOffice changed the structure); Excel computes formulas on open")
            except OfficeToolError as exc:
                issues.append(ValidationIssue(severity="warning", location="workbook", message=f"recalculation skipped: {exc}"))

    ok = error_count == 0 and failed_assertions == 0
    report = ValidationReport(ok=ok, checks=checks, issues=issues, formula_count=formula_count, recalculated=recalculated)

    sheet_summaries = []
    for s, info in zip(spec.sheets, infos):
        bits = [f"{info['rows']} rows × {info['cols']} cols"]
        if info["totals_row"]:
            bits.append("totals")
        if info["chart"]:
            bits.append(f"{info['chart']} chart")
        if info["validations"]:
            bits.append(f"{info['validations']} validation(s)")
        sheet_summaries.append(f"{s.name} ({', '.join(bits)})")
    summary = f"{spec.filename}: " + "; ".join(sheet_summaries) + f". {formula_count} formula(s)"
    summary += ", recalculated and error-free." if (recalculated and ok) else (", NOT verified." if not recalculated else f", {error_count} error(s), {failed_assertions} failed assertion(s).")

    return BuildOutput(data=delivered_bytes, report=report, summary=summary, delivered=delivered, sheet_summaries=sheet_summaries)
