"""
Document engine — build validated Excel workbooks and Word documents from a
typed spec.

The agent never runs code to produce a file. It describes *what* the file
should contain (``WorkbookSpec`` / ``DocumentSpec``) and this package turns
that into bytes, checks the result the way a user would (LibreOffice
recalculation for Excel, a PDF render for Word) and reports what it found.

Public surface:

- :mod:`services.document_engine.specs` — the Pydantic models the API accepts.
- :func:`services.document_engine.xlsx.build_workbook` — spec → validated
  ``.xlsx`` bytes + report.
- :func:`services.document_engine.docx.build_document` — spec → validated
  ``.docx`` bytes + thumbnail + report.

See ``docs/developers/chat-document-generation-plan.md`` for the design.
"""

from services.document_engine.specs import (  # noqa: F401
    DocumentSpec,
    GenerateResult,
    ValidationReport,
    WorkbookSpec,
)
