"""
Document engine: spec validation, Excel build + recalculation + verification,
Word assembly + render + verification.

Run:  make test-docker SERVICE=data ARGS=tests/unit/test_document_engine.py

Tests that need LibreOffice or pandoc skip when the binary is absent so the
pure-Python parts still run anywhere; the data container has both.
"""

from __future__ import annotations

import datetime
import io
import zipfile

import pytest
from openpyxl import load_workbook
from pydantic import ValidationError

from services.document_engine import docx as docx_engine
from services.document_engine import xlsx as xlsx_engine
from services.document_engine.office import tool_availability
from services.document_engine.specs import (
    CellSpec,
    ColumnFormula,
    DocumentSpec,
    WorkbookSpec,
    check_formula,
    descriptive_filename,
    safe_filename,
)

TOOLS = tool_availability()
needs_soffice = pytest.mark.skipif(not TOOLS.soffice, reason="LibreOffice not installed")
needs_pandoc = pytest.mark.skipif(not TOOLS.pandoc, reason="pandoc not installed")

# A 1x1 white PNG.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\xbd\xfa\x00\x00\x00\x00IEND\xaeB`\x82"
)
CHART_ID = "11111111-2222-3333-4444-555555555555"


def _budget_spec(**overrides) -> WorkbookSpec:
    base = {
        "title": "Q3 Budget",
        "sheets": [{
            "name": "Budget",
            "header_row": 3,
            "cells": [{"cell": "A1", "value": "Q3 Department Budget", "bold": True},
                      {"cell": "I4", "value": "=SUM(E4:E6)", "format": '"$"#,##0.00'}],
            "columns": [
                {"header": "Item", "type": "text"},
                {"header": "Category", "type": "text"},
                {"header": "Qty", "type": "integer"},
                {"header": "Unit Price", "type": "currency"},
                {"header": "Total", "type": "currency"},
                {"header": "Share", "type": "percent"},
                {"header": "Ordered", "type": "date"},
            ],
            "rows": [
                ["Laptops", "Hardware", 4, 1200, None, None, "2026-07-01"],
                ["Monitors", "Hardware", 8, "$350.00", None, None, "07/03/2026"],
                ["Licenses", "Software", 12, 45.5, None, None, "2026-07-15"],
            ],
            "column_formulas": [{"column": "Total", "formula": "=C{row}*D{row}"},
                                {"column": "Share", "formula": "=E{row}/SUM($E$4:$E$6)"}],
            "totals": [{"column": "Qty"}, {"column": "Total"}, {"column": "Share"}],
            "validations": [{"column": "Category", "type": "list", "values": ["Hardware", "Software"]},
                            {"column": "Qty", "type": "whole", "min": 0}],
            "conditional": [{"column": "Total", "rule": "greater_than", "value": 3000}],
            "named_ranges": [{"name": "Totals", "column": "Total"}],
            "chart": {"type": "column", "title": "Spend", "categories": "Item", "values": ["Total"]},
        }],
        "assertions": [{"cell": "E7", "equals_sum_of": "E4:E6"}, {"cell": "F7", "equals": 1.0}],
    }
    base.update(overrides)
    return WorkbookSpec.model_validate(base)


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


def test_filenames_are_descriptive_dated_and_safe():
    today = datetime.date.today().isoformat()
    assert safe_filename("../etc/passwd", ".xlsx") == "etc passwd.xlsx"
    assert safe_filename("", ".docx") == "document.docx"
    spec = WorkbookSpec.model_validate({"title": "Q3 Budget", "sheets": [{"name": "S", "columns": [{"header": "A"}]}]})
    assert spec.filename == f"Q3 Budget - Spreadsheet - {today}.xlsx"
    spec = WorkbookSpec.model_validate({"title": "Q3 Budget", "kind": "Budget", "filename": "report.xlsx", "sheets": [{"name": "S", "columns": [{"header": "A"}]}]})
    assert spec.filename == f"Q3 Budget - {today}.xlsx"   # generic explicit name dropped; 'Budget' already in the title
    spec = DocumentSpec.model_validate({"title": "Harbor Dredging: Bid Comparison / 2026?", "kind": "Memo", "sections": [{"markdown": "x"}]})
    assert spec.filename == f"Harbor Dredging Bid Comparison 2026 - Memo - {today}.docx"
    explicit = DocumentSpec.model_validate({"title": "Harbor Dredging", "filename": "Harbor Dredging Memo 2026-09-01", "sections": [{"markdown": "x"}]})
    assert explicit.filename == "Harbor Dredging Memo 2026-09-01.docx"   # already descriptive and dated: kept
    for bad in ("Report", "document", "Book1", "ab"):
        with pytest.raises(ValidationError, match="descriptive name"):
            DocumentSpec.model_validate({"title": bad, "sections": [{"markdown": "x"}]})
    assert descriptive_filename(subject="Crew Hours", kind="Spreadsheet", ext=".xlsx", explicit=None, today=datetime.date(2026, 9, 15)) == "Crew Hours - Spreadsheet - 2026-09-15.xlsx"


@pytest.mark.parametrize("formula", ["=WEBSERVICE(\"http://x\")", "=hyperlink(A1)", "=RTD(1)", "=INDIRECT(A1)", "=[1]Sheet1!A1", "='C:\\evil.xlsx'!A1"])
def test_formulas_that_leave_the_workbook_are_rejected(formula):
    assert check_formula(formula) is not None


def test_ordinary_formulas_pass():
    assert check_formula("=SUM(A1:A9)/COUNT(A1:A9)") is None
    assert check_formula("=IF(B2>0,C2/B2,\"n/a\")") is None


def test_column_formula_must_use_row_placeholder():
    with pytest.raises(ValidationError):
        ColumnFormula(column="Total", formula="=C2*D2")
    ColumnFormula(column="Total", formula="=C{row}*D{row}")


def test_sheet_shape_is_validated():
    with pytest.raises(ValidationError, match="unique"):
        WorkbookSpec.model_validate({"title": "Shape check", "sheets": [{"name": "S", "columns": [{"header": "A"}, {"header": "a"}]}]})
    with pytest.raises(ValidationError, match="values but there are only"):
        WorkbookSpec.model_validate({"title": "Shape check", "sheets": [{"name": "S", "columns": [{"header": "A"}], "rows": [[1, 2]]}]})
    with pytest.raises(ValidationError, match="exactly one of"):
        WorkbookSpec.model_validate({"title": "Shape check", "sheets": [{"name": "S", "columns": [{"header": "A"}]}],
                                     "assertions": [{"cell": "A1"}]})


def test_unknown_column_reference_is_a_spec_error():
    spec = _budget_spec()
    spec.sheets[0].totals[0].column = "Nope"
    with pytest.raises(xlsx_engine.SpecError, match="Nope"):
        xlsx_engine.build_openpyxl(spec, [])


# ---------------------------------------------------------------------------
# Excel: build
# ---------------------------------------------------------------------------


def test_build_places_headers_data_formulas_and_totals():
    issues = []
    data, infos = xlsx_engine.build_openpyxl(_budget_spec(), issues)
    ws = load_workbook(io.BytesIO(data))["Budget"]
    assert [c.value for c in ws[3]][:7] == ["Item", "Category", "Qty", "Unit Price", "Total", "Share", "Ordered"]
    assert ws["A1"].value == "Q3 Department Budget" and ws["A1"].font.bold
    assert ws["D5"].value == 350.0          # "$350.00" parsed
    assert ws["E4"].value == "=C4*D4"        # {row} expanded per data row
    assert ws["F6"].value == "=E6/SUM($E$4:$E$6)"
    assert ws["C7"].value == "=SUM(C4:C6)" and ws["A7"].value == "Total"
    assert ws["D4"].number_format == '"$"#,##0.00' and ws["F4"].number_format == "0.0%"
    assert ws["G4"].value.year == 2026 and ws["G5"].value.month == 7
    assert ws.freeze_panes == "A4" and ws.auto_filter.ref == "A3:G6"
    assert infos[0]["totals_row"] == 7 and infos[0]["chart"] == "column"
    assert not [i for i in issues if i.severity == "error"]


def test_text_columns_never_become_formulas_and_bad_numbers_warn():
    spec = WorkbookSpec.model_validate({"title": "Type coercion check", "sheets": [{
        "name": "S", "columns": [{"header": "Note", "type": "text"}, {"header": "Amt", "type": "number"}],
        "rows": [["=not a formula", "n/a"]],
    }]})
    issues = []
    data, _ = xlsx_engine.build_openpyxl(spec, issues)
    ws = load_workbook(io.BytesIO(data))["S"]
    assert ws["A2"].value == "=not a formula" and ws["A2"].data_type == "s"
    assert ws["B2"].value == "n/a"
    assert any("not a number" in i.message for i in issues)


def test_percent_columns_expect_fractions():
    spec = WorkbookSpec.model_validate({"title": "Type coercion check", "sheets": [{
        "name": "S", "columns": [{"header": "Share", "type": "percent"}], "rows": [[45], [0.4]],
    }]})
    issues = []
    xlsx_engine.build_openpyxl(spec, issues)
    assert any("percent columns expect fractions" in i.message for i in issues)


# ---------------------------------------------------------------------------
# Excel: recalculate + verify (LibreOffice)
# ---------------------------------------------------------------------------


@needs_soffice
def test_clean_workbook_is_recalculated_verified_and_delivered_with_values():
    out = xlsx_engine.build_workbook(_budget_spec())
    assert out.report.ok, out.report.issues
    assert out.report.recalculated and out.report.formula_count == 10  # 3x2 column formulas + 3 totals + I4
    assert out.delivered == "recalculated"
    ws = load_workbook(io.BytesIO(out.data), data_only=True)["Budget"]
    assert ws["E4"].value == 4800 and ws["E7"].value == pytest.approx(8146)
    assert ws["F7"].value == pytest.approx(1.0)
    assert ws["I4"].value == pytest.approx(8146)
    # Everything built survived the LibreOffice round trip.
    wb = load_workbook(io.BytesIO(out.data))
    assert wb["Budget"]["E4"].value == "=C4*D4"
    assert len(wb["Budget"].data_validations.dataValidation) == 2
    assert len(list(wb["Budget"].conditional_formatting)) == 1
    assert "Totals" in wb.defined_names
    assert any(n.startswith("xl/charts/chart") for n in zipfile.ZipFile(io.BytesIO(out.data)).namelist())
    assert "recalculated and error-free" in out.summary


@needs_soffice
def test_formula_errors_are_found_and_the_file_is_still_returned():
    spec = _budget_spec()
    spec.sheets[0].cells.append(CellSpec(cell="K2", value="=BADFUNC(1)"))
    spec.sheets[0].cells.append(CellSpec(cell="K3", value="=1/0"))
    out = xlsx_engine.build_workbook(spec)
    assert not out.report.ok
    locations = {i.location for i in out.report.issues if i.severity == "error"}
    assert {"Budget!K2", "Budget!K3"} <= locations
    assert any("#NAME?" in i.message for i in out.report.issues)
    assert any("#DIV/0!" in i.message for i in out.report.issues)
    assert out.delivered == "original"   # never ship a recalculated copy that contains errors
    assert out.data


@needs_soffice
def test_failed_assertions_fail_verification():
    spec = _budget_spec(assertions=[{"cell": "E7", "equals": 1}, {"cell": "A1", "equals": 1}])
    out = xlsx_engine.build_workbook(spec)
    assert not out.report.ok
    msgs = [i.message for i in out.report.issues if i.severity == "error"]
    assert any("expected 1" in m and "got 8146" in m for m in msgs)
    assert any("not numeric" in m for m in msgs)


def test_no_libreoffice_degrades_to_a_warning(monkeypatch):
    monkeypatch.setattr(xlsx_engine, "tool_availability", lambda: type("T", (), {"soffice": False})())
    out = xlsx_engine.build_workbook(_budget_spec())
    assert out.report.ok and not out.report.recalculated
    assert any("not installed" in i.message for i in out.report.issues)
    assert out.delivered == "original"


# ---------------------------------------------------------------------------
# Word: assembly
# ---------------------------------------------------------------------------


def _doc_spec(**overrides) -> DocumentSpec:
    body = (
        "## Executive summary\n\nCosts ran $5-$10 per kg and $1,200 vs $350.\n\n"
        "| Year | Launches |\n|---|---:|\n| 2023 | 96 |\n| 2024 | 134 |\n\n"
        f"![Launches per year](/portal/api/media/{CHART_ID})\n\n"
        "### Details\n\n- Falcon 9 reuse **matters**\n\n"
        "![external](https://example.com/x.png)\n\n"
        "```{=openxml}\n<w:p/>\n```\n"
    )
    base = {
        "title": "SpaceX Launch Economics",
        "kind": "Research Report",
        "subtitle": "Deep research report",
        "sections": [
            {"heading": "Findings", "markdown": body},
            {"heading": "Appendix", "markdown": "More.", "images": [{"file_id": CHART_ID, "caption": "Again", "width_in": 5}], "page_break_before": True},
            {"heading": "Method", "level": 2, "markdown": "Workers."},
        ],
        "sources": [{"title": "Press kit", "url": "https://www.spacex.com/press"}, {"title": "FAA data [2024]"}],
    }
    base.update(overrides)
    return DocumentSpec.model_validate(base)


def test_collect_image_ids_finds_inline_and_explicit_figures():
    assert docx_engine.collect_image_ids(_doc_spec()) == [CHART_ID]


def test_nest_headings_puts_the_body_one_level_under_the_section():
    # The body's top-most heading (here the H1) lands one level under the
    # section; relative depth is preserved; fenced code is untouched.
    body = "## Top\n\ntext\n\n### Sub\n\n```\n# not a heading\n```\n\n# Also top"
    nested = docx_engine._nest_headings(body, 1)
    assert nested.splitlines()[0] == "### Top"
    assert "#### Sub" in nested and "## Also top" in nested
    assert "# not a heading" in nested
    assert docx_engine._nest_headings("# A\n### B", 1) == "## A\n#### B"
    assert docx_engine._nest_headings("## A\n### B", 1) == "## A\n### B"   # already right: unchanged
    assert docx_engine._nest_headings("no headings", 2) == "no headings"


def test_assemble_rewrites_media_images_drops_external_ones_and_strips_raw_xml(tmp_path):
    issues = []
    md, expected = docx_engine.assemble_markdown(_doc_spec(), {CHART_ID: PNG}, tmp_path, issues)
    assert f"![Launches per year]({CHART_ID}.png)" in md
    assert f"![Again]({CHART_ID}.png){{width=5in}}" in md
    assert "*[external]*" in md and "example.com" not in md
    assert "```{=openxml}\n<w:p/>" not in md and "```\n<w:p/>" in md   # body fence neutralised
    assert '```{=openxml}\n<w:p><w:r><w:br w:type="page"/>' in md      # our own page break kept
    assert "**Contents**" in md and "[Findings](#sec-1)" in md and "    - [Method](#sec-3)" in md
    assert "# Findings {#sec-1}" in md and "\n## Executive summary\n" in md and "\n### Details\n" in md
    assert "# Sources {#sources}" in md and "1. Press kit — <https://www.spacex.com/press>" in md
    assert "2. FAA data \\[2024\\]" in md
    assert expected == {"figures": 2, "tables": 1}
    assert [i.message for i in issues] == ["image 'external' could not be embedded (only Busibox media links are embedded)"]
    assert (tmp_path / f"{CHART_ID}.png").read_bytes() == PNG


def test_missing_figure_is_a_warning_not_a_crash(tmp_path):
    issues = []
    md, expected = docx_engine.assemble_markdown(_doc_spec(), {}, tmp_path, issues)
    assert expected["figures"] == 0
    assert "*[Launches per year]*" in md
    assert sum("could not be embedded" in i.message for i in issues) == 3


# ---------------------------------------------------------------------------
# Word: render + verify (pandoc / LibreOffice)
# ---------------------------------------------------------------------------


@needs_pandoc
def test_document_renders_with_headings_tables_figures_and_sources():
    from docx import Document

    out = docx_engine.build_document(_doc_spec(), {CHART_ID: PNG}, thumbnail=TOOLS.thumbnail_ok)
    assert out.report.ok, out.report.issues
    doc = Document(io.BytesIO(out.data))
    styles = [(p.style.name, p.text) for p in doc.paragraphs]
    assert ("Title", "SpaceX Launch Economics") in styles
    headings = [t for s, t in styles if s.lower().startswith("heading")]
    assert headings[:3] == ["Findings", "Executive summary", "Details"]
    assert "Sources" in headings and "Method" in headings
    assert len(doc.tables) == 1 and doc.tables[0].rows[0].cells[0].text == "Year"
    assert len(doc.inline_shapes) == 2
    assert doc.core_properties.title == "SpaceX Launch Economics"
    # pandoc 2.9 leaves tables at zero width; the post-processor fixes that.
    tblw = doc.tables[0]._tbl.tblPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblW")
    assert tblw.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}w") == "5000"
    if TOOLS.soffice:
        assert out.report.pages and out.report.pages >= 2
        if TOOLS.thumbnail_ok:
            assert out.thumbnail and out.thumbnail[:8] == b"\x89PNG\r\n\x1a\n"
    assert "3 section(s), 1 table(s), 2 figure(s)" in out.summary


@needs_pandoc
def test_missing_heading_is_a_structural_error(monkeypatch):
    # Force the verifier to look for a heading pandoc never emitted.
    spec = _doc_spec()
    real_verify = docx_engine._verify_structure

    def verify(path, spec_in, expected, issues, checks):
        spec_in.sections[0].heading = "Never rendered"
        return real_verify(path, spec_in, expected, issues, checks)

    monkeypatch.setattr(docx_engine, "_verify_structure", verify)
    out = docx_engine.build_document(spec, {CHART_ID: PNG}, thumbnail=False)
    assert not out.report.ok
    assert any("Never rendered" in i.message and i.severity == "error" for i in out.report.issues)


def test_pandoc_missing_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr(docx_engine, "tool_availability", lambda: type("T", (), {"pandoc": False, "soffice": False, "thumbnail_ok": False})())
    with pytest.raises(docx_engine.OfficeToolError, match="pandoc"):
        docx_engine.build_document(_doc_spec(), {})


def test_reference_template_is_shipped():
    assert docx_engine.REFERENCE_DOCX.exists()
    with zipfile.ZipFile(docx_engine.REFERENCE_DOCX) as zf:
        styles = zf.read("word/styles.xml").decode("utf-8")
    assert "Calibri" in styles and "tblBorders" in styles and 'w:type="firstRow"' in styles
    assert "word/footer1.xml" in zf.namelist()


# ---------------------------------------------------------------------------
# PowerPoint
# ---------------------------------------------------------------------------

from services.document_engine import pptx as pptx_engine  # noqa: E402
from services.document_engine.specs import PresentationSpec, SlideSpec  # noqa: E402


def _deck_spec(**overrides) -> PresentationSpec:
    base = {
        "title": "SpaceX Launch Economics",
        "kind": "Briefing",
        "subtitle": "What reuse did to the cost of reaching orbit",
        "author": "Busibox AI Chat",
        "slides": [
            {"layout": "section", "title": "Where the money goes"},
            {"layout": "bullets", "title": "Bottom line", "bullets": ["Reuse cut marginal cost ~60%", "- First-stage recovery is the largest saving", "Cadence now drives revenue"], "notes": "Open with the cost curve."},
            {"layout": "two_column", "title": "Expendable vs reusable", "left_heading": "2015", "right_heading": "2024", "bullets": ["$62M list", "12 launches"], "right_bullets": ["$67M list", "134 launches"]},
            {"layout": "chart", "title": "Launches per year", "chart": {"type": "column", "categories": ["2022", "2023", "2024"], "series": [{"name": "Launches", "values": [61, 96, 134]}], "number_format": "#,##0", "source": "SpaceX"}, "bullets": ["Doubled twice in three years"]},
            {"layout": "table", "title": "Cost per kg", "table": {"headers": ["Vehicle", "$/kg"], "rows": [["Falcon 9", 3850], ["Starship (target)", 67]]}},
            {"layout": "image", "title": "Cadence, charted", "image": {"file_id": CHART_ID, "caption": "Launches per year"}},
            {"layout": "chart", "title": "Share of launches", "chart": {"type": "pie", "categories": ["SpaceX", "Others"], "series": [{"name": "Share", "values": [0.52, 0.48]}]}},
        ],
        "sources": [{"title": "SpaceX press kit", "url": "https://www.spacex.com/press"}],
    }
    base.update(overrides)
    return PresentationSpec.model_validate(base)


def test_slide_specs_are_validated():
    with pytest.raises(ValidationError, match="needs a title"):
        SlideSpec(layout="bullets", bullets=["x"])
    with pytest.raises(ValidationError, match="needs 'chart'"):
        SlideSpec(layout="chart", title="t")
    with pytest.raises(ValidationError, match="values but there are"):
        SlideSpec(layout="chart", title="t", chart={"type": "column", "categories": ["a", "b"], "series": [{"name": "s", "values": [1]}]})
    with pytest.raises(ValidationError, match="exactly one series"):
        SlideSpec(layout="chart", title="t", chart={"type": "pie", "categories": ["a"], "series": [{"name": "s", "values": [1]}, {"name": "t", "values": [2]}]})
    with pytest.raises(ValidationError, match="needs 'bullets' and 'right_bullets'"):
        SlideSpec(layout="two_column", title="t", bullets=["x"])
    assert SlideSpec(layout="bullets", title="t", bullets=[" a ", "", "b"]).bullets == ["a", "b"]
    assert _deck_spec().filename == f"SpaceX Launch Economics - Briefing - {datetime.date.today().isoformat()}.pptx"


def test_deck_builds_every_layout_with_a_title_slide_and_sources():
    from pptx import Presentation

    issues = []
    data, expected = pptx_engine.build_pptx(_deck_spec(), {CHART_ID: PNG}, issues)
    assert expected["slides"] == 9  # title + 7 + sources
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) == 9
    assert prs.slide_width == pptx_engine.SLIDE_W and prs.slide_height == pptx_engine.SLIDE_H
    titles = [next(sh.text_frame.text for sh in s.shapes if sh.name == "Title") for s in prs.slides]
    assert titles[0] == "SpaceX Launch Economics" and titles[1] == "Where the money goes" and titles[-1] == "Sources"
    assert prs.slides[2].notes_slide.notes_text_frame.text == "Open with the cost curve."
    kinds = {s.name for s in prs.slides[4].shapes}
    assert "Chart" in kinds and "Body" in kinds       # chart + takeaways
    assert any(sh.has_table for sh in prs.slides[5].shapes)
    assert any(sh.shape_type == 13 for sh in prs.slides[6].shapes)  # PICTURE
    assert "2 / 9" in next(sh.text_frame.text for sh in prs.slides[1].shapes if sh.name == "SlideNumber")
    assert prs.core_properties.title == "SpaceX Launch Economics"
    assert not [i for i in issues if i.severity == "error"]


def test_dense_slides_are_flagged_and_missing_images_warn():
    spec = _deck_spec(slides=[
        {"layout": "bullets", "title": "Wall of text", "bullets": [("word " * 20).strip()] * 10},
        {"layout": "image", "title": "No picture", "image": {"file_id": "22222222-2222-3333-4444-555555555555"}},
    ])
    out = pptx_engine.build_presentation(spec, {}, thumbnail=False)
    msgs = [i.message for i in out.report.issues]
    assert any("dense slide" in m for m in msgs)
    assert any("could not be embedded" in m for m in msgs)
    # The missing image is a warning, not an error: the deck still verifies.
    assert out.report.ok or any(i.severity == "error" and "LibreOffice" in i.message for i in out.report.issues)


@needs_soffice
def test_deck_verifies_and_renders_one_page_per_slide():
    out = pptx_engine.build_presentation(_deck_spec(), {CHART_ID: PNG}, thumbnail=TOOLS.thumbnail_ok)
    assert out.report.ok, out.report.issues
    assert out.report.pages == 9 and out.stats == {"slides": 9, "pictures": 1, "tables": 1, "charts": 2}
    assert "9 slide(s), 2 chart(s), 1 table(s), 1 image(s), 1 source(s)" in out.summary
    if TOOLS.thumbnail_ok:
        assert out.thumbnail and out.thumbnail[:8] == b"\x89PNG\r\n\x1a\n"


def test_structural_check_catches_a_lost_slide(monkeypatch):
    real = pptx_engine.build_pptx

    def lossy(spec, images, issues):
        data, expected = real(spec, images, issues)
        expected["slides"] += 1  # claim one more than we built
        return data, expected

    monkeypatch.setattr(pptx_engine, "build_pptx", lossy)
    monkeypatch.setattr(pptx_engine, "tool_availability", lambda: type("T", (), {"soffice": False})())
    out = pptx_engine.build_presentation(_deck_spec(), {CHART_ID: PNG}, thumbnail=False)
    assert not out.report.ok
    assert any("expected 10 slides, file has 9" in i.message for i in out.report.issues)
