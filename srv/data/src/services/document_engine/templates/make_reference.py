"""
Build ``reference.docx`` — the neutral house template pandoc uses for
generated Word documents.

Starts from pandoc's own default reference document (so every style pandoc
emits — Title, Body Text, Compact, Source Code, Image Caption … — exists) and
restyles it: Calibri body, navy headings, US Letter with 1" margins, bordered
tables with a shaded header row, and a "Page X of Y" footer.

Re-run when the look needs to change:

    cd srv/data/src && python -m services.document_engine.templates.make_reference

The output is committed; the data-api never runs this at request time.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

HERE = Path(__file__).resolve().parent
OUT = HERE / "reference.docx"

NAVY = RGBColor(0x1F, 0x38, 0x64)
BLUE = RGBColor(0x2F, 0x54, 0x96)
GREY = RGBColor(0x59, 0x59, 0x59)
BODY_FONT = "Calibri"
HEADING_FONT = "Calibri Light"


def _style(doc: Document, name: str):
    """Look a style up by its stored name (python-docx's ``styles[...]`` maps
    'Heading 1' to Word's internal 'heading 1', which pandoc's file does not use)."""
    for style in doc.styles:
        if style.name == name:
            return style
    return None


def _set_font(style, name: str, size_pt: float | None = None, bold: bool | None = None, color: RGBColor | None = None):
    if style is None:  # pandoc adds some styles (e.g. Source Code) only when a document needs them
        return
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = parse_xml(f'<w:rFonts {nsdecls("w")}/>')
        rpr.append(rfonts)
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
        if rfonts.get(qn(attr)) is not None:
            del rfonts.attrib[qn(attr)]
    if size_pt is not None:
        style.font.size = Pt(size_pt)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color


def _doc_defaults(doc: Document):
    styles_el = doc.styles.element
    rpr = styles_el.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
    rfonts = rpr.find(qn("w:rFonts"))
    for attr in list(rfonts.attrib):
        del rfonts.attrib[attr]
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), BODY_FONT)
    for tag in ("w:sz", "w:szCs"):
        el = rpr.find(qn(tag))
        el.set(qn("w:val"), "22")  # 11 pt
    ppr = styles_el.find(qn("w:docDefaults")).find(qn("w:pPrDefault")).find(qn("w:pPr"))
    spacing = ppr.find(qn("w:spacing"))
    spacing.set(qn("w:after"), "160")
    spacing.set(qn("w:line"), "259")
    spacing.set(qn("w:lineRule"), "auto")


def _table_style(doc: Document):
    table = _style(doc, "Table").element
    tblpr = table.find(qn("w:tblPr"))
    borders = parse_xml(
        f'<w:tblBorders {nsdecls("w")}>'
        '<w:top w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        '<w:left w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        '<w:right w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="BFBFBF"/>'
        "</w:tblBorders>"
    )
    tblpr.insert(1, borders)
    margins = tblpr.find(qn("w:tblCellMar"))
    margins.find(qn("w:top")).set(qn("w:w"), "40")
    margins.find(qn("w:bottom")).set(qn("w:w"), "40")
    # Header row: bold on shaded background. Word applies this via tblLook firstRow.
    first_row = parse_xml(
        f'<w:tblStylePr {nsdecls("w")} w:type="firstRow">'
        "<w:rPr><w:b/><w:bCs/></w:rPr>"
        '<w:tcPr><w:shd w:val="clear" w:color="auto" w:fill="DCE6F1"/></w:tcPr>'
        "</w:tblStylePr>"
    )
    table.append(first_row)
    # Remove semiHidden so Word lists the style.
    for tag in ("w:semiHidden", "w:unhideWhenUsed"):
        el = table.find(qn(tag))
        if el is not None:
            table.remove(el)


def _page_and_footer(doc: Document):
    section = doc.sections[0]
    section.page_width = Inches(8.5)
    section.page_height = Inches(11)
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(1))
    section.footer_distance = Inches(0.5)
    footer = section.footer
    footer.is_linked_to_previous = False
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("Page ")
    run.font.size = Pt(9)
    run.font.color.rgb = GREY
    for field in ("PAGE", "NUMPAGES"):
        for xml in (
            f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>',
            f'<w:instrText {nsdecls("w")} xml:space="preserve"> {field} </w:instrText>',
            f'<w:fldChar {nsdecls("w")} w:fldCharType="separate"/>',
            f'<w:t {nsdecls("w")}>1</w:t>',
            f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>',
        ):
            r = p.add_run()
            r.font.size = Pt(9)
            r.font.color.rgb = GREY
            r._r.append(parse_xml(xml))
        if field == "PAGE":
            mid = p.add_run(" of ")
            mid.font.size = Pt(9)
            mid.font.color.rgb = GREY


def build(out: Path = OUT) -> Path:
    base = out.with_name("pandoc-default-reference.docx")
    with open(base, "wb") as fh:
        subprocess.run(["pandoc", "--print-default-data-file", "reference.docx"], stdout=fh, check=True)
    doc = Document(str(base))
    _doc_defaults(doc)
    _set_font(_style(doc, "Title"), HEADING_FONT, 26, True, NAVY)
    _set_font(_style(doc, "Subtitle"), HEADING_FONT, 14, False, GREY)
    _set_font(_style(doc, "Author"), BODY_FONT, 11, False, GREY)
    _set_font(_style(doc, "Date"), BODY_FONT, 11, False, GREY)
    for name, size, color in (("Heading 1", 16, NAVY), ("Heading 2", 13, BLUE), ("Heading 3", 12, BLUE), ("Heading 4", 11, BLUE)):
        _set_font(_style(doc, name), HEADING_FONT, size, True, color)
        _style(doc, name).paragraph_format.space_before = Pt(18 if name == "Heading 1" else 12)
        _style(doc, name).paragraph_format.space_after = Pt(6)
        _style(doc, name).paragraph_format.keep_with_next = True
    _set_font(_style(doc, "Image Caption"), BODY_FONT, 9, False, GREY)
    _style(doc, "Image Caption").paragraph_format.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(_style(doc, "Source Code"), "Consolas", 9)
    _set_font(_style(doc, "Verbatim Char"), "Consolas", 9)
    _table_style(doc)
    _page_and_footer(doc)
    doc.core_properties.title = ""
    doc.core_properties.author = "Busibox"
    doc.save(str(out))
    base.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    path = build()
    print(f"wrote {path}", file=sys.stderr)
