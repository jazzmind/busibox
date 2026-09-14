---
title: "Chat: Excel and Word files"
category: "platform"
order: 9
description: "Ask AI Chat for a spreadsheet or a Word document and get a working, downloadable file"
published: true
---

# Chat: Excel and Word files

AI Chat can hand you real files, not just text. Ask for a spreadsheet and
you get an `.xlsx` with live formulas; ask for a document and you get a
`.docx` with headings, tables, charts and a sources list. Deep-research
reports are exported to Word automatically.

## Asking for a spreadsheet

Say what you want and where the numbers come from, for example:

- "Make me a spreadsheet of the crew hours by week from the daily reports."
- "Put that comparison in an Excel file with a total row and a chart."
- "Build a budget workbook: items, quantity, unit price, total, and a share column."

The chat gathers the data first (your documents, data tables, or the web),
then builds the workbook. What you get:

- Typed columns with proper number formats — currency, percent, dates,
  integers — and a frozen, filterable header row.
- Real formulas, not pasted results. Per-row formulas and SUM/AVERAGE
  totals are written as Excel formulas so the sheet keeps working when
  you edit it.
- Optional extras when they make sense: a native Excel chart, drop-down
  lists and numeric bounds on columns, conditional highlighting, named
  ranges.

Before the link appears the workbook is opened and recalculated
server-side and every cell is checked for `#REF!`, `#NAME?`, `#DIV/0!` and
friends. If the chat asked for a check it can verify (say, that the total
equals the sum of a column), that is verified too. A workbook that fails
is not presented as finished: the chat tells you what went wrong, fixes
the spec and tries again, or falls back to giving you the table inline.

The link under the answer downloads the file with its real name. The file
is also saved in your personal **Documents** library, indexed like any
upload, so you can find it later.

## Asking for a Word document

- "Write this up as a Word document I can send to the client."
- "Turn that answer into a memo in Word."
- "Give me a docx version of the summary with the chart."

The document uses a clean built-in template: title block, optional
contents list, headings, bulleted and numbered lists, tables with a shaded
header row, embedded charts with captions, a numbered Sources section and
"Page X of Y" footers. Charts the chat rendered earlier in the
conversation are embedded as images.

Each document is rendered to PDF behind the scenes to confirm it opens and
to count pages; the answer shows a preview of the first page next to the
download link.

## Deep research reports

When you run a deep-research pass (the one you confirm with **Yes**), the
finished report is exported to Word automatically once it is on screen —
with its charts, tables and every source the workers found. The link is
appended under the report. If the export fails for any reason the report
itself is unaffected; you can still ask "export that to Word" afterwards.

Administrators can turn the automatic export off with the agent setting
`RESEARCH_EXPORT_DOCX=false`.

## Limits and good to know

- The chat never runs code to make a file. It describes the file in a
  fixed, checkable format and the platform renders it, which is what makes
  "the spreadsheet works" something the system can promise rather than
  hope for.
- Formulas that reach outside the workbook (`WEBSERVICE`, `HYPERLINK`,
  `INDIRECT`, links to other files) are not allowed; everything else in
  Excel's formula language is.
- Percent columns expect fractions (0.125 for 12.5%).
- A workbook can hold up to 12 sheets and 20,000 rows per sheet; a
  document up to 60 sections.
- Only images stored in Busibox (for example charts the chat drew) are
  embedded in documents; images from the open web are replaced by their
  caption.
- Generated files count toward your library like uploads and are subject
  to the same access rules.

## Troubleshooting

| Symptom | What it means |
|---|---|
| "Document generation is not available on this server right now" | LibreOffice or pandoc is missing in the data container. Ask an administrator to redeploy the `data` service. |
| The chat says the workbook had formula errors | A formula referenced the wrong cell or a text value. The chat normally retries with a corrected spec; if not, ask it to try again or to show the table inline. |
| The download opens in the browser instead of saving | Use the link the chat gave you (it ends in `?download=1`); a plain media link displays the file inline. |
| The first-page preview is missing | Thumbnails need `pdftoppm` (poppler) in the data container. The document itself is unaffected. |
