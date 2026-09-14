"""create_spreadsheet / create_document: thin clients over the data-api document engine.

No network: httpx.AsyncClient is replaced at the module seam. What is pinned
here is the contract the model relies on — a typed spec in, a download link
and readable issues out, and never a claimed file that does not exist.
"""

import json

import httpx
import pytest

from app.agents.base_agent import DOCUMENT_TOOLS_DIRECTIVE, TOOL_CLASSES, TOOL_SCOPES, ToolRegistry
from app.agents.chat_agent import ChatAgent
from app.tools import document_tools as dt
from busibox_common.document_specs import DocumentSpec, WorkbookSpec


class _Client:
    def token_for(self, audience):
        return "tok"


class _Deps:
    busibox_client = _Client()
    metadata = {"conversation_id": "conv-1"}


class _Ctx:
    deps = _Deps()


class _NoTokenCtx:
    class deps:  # noqa: N801
        busibox_client = None


class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeHttp:
    """Stands in for httpx.AsyncClient; records the request, returns a canned response."""

    calls = []

    def __init__(self, response=None, raise_exc=None):
        self.response, self.raise_exc = response, raise_exc

    def __call__(self, timeout=None):
        self.timeout = timeout
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeHttp.calls.append({"url": url, "headers": headers, "json": json})
        if self.raise_exc:
            raise self.raise_exc
        return self.response


@pytest.fixture(autouse=True)
def _reset_calls():
    _FakeHttp.calls.clear()


def _use(monkeypatch, response=None, raise_exc=None):
    fake = _FakeHttp(response, raise_exc)
    monkeypatch.setattr(dt.httpx, "AsyncClient", fake)
    return fake


WORKBOOK = {
    "filename": "Crew hours",
    "sheets": [{
        "name": "Hours",
        "columns": [{"header": "Crew", "type": "text"}, {"header": "Hours", "type": "number"}, {"header": "Rate", "type": "currency"}, {"header": "Cost", "type": "currency"}],
        "rows": [["A", 40, 55, None], ["B", 32, 60, None]],
        "column_formulas": [{"column": "Cost", "formula": "=B{row}*C{row}"}],
        "totals": [{"column": "Cost"}],
    }],
    "assertions": [{"cell": "D4", "equals": 4120}],
}

DOCUMENT = {
    "filename": "Safety memo",
    "title": "Safety Plan Summary",
    "sections": [{"heading": "Summary", "markdown": "All good.\n\n![chart](/portal/api/media/11111111-2222-3333-4444-555555555555)"}],
    "sources": [{"title": "Plan", "url": "https://x"}],
}

OK_XLSX = {
    "success": True, "filename": "Crew hours.xlsx", "file_id": "f1",
    "download_url": "/portal/api/media/f1?download=1",
    "validation": {"ok": True, "checks": ["recalculated 3 formula(s)"], "issues": [], "formula_count": 3, "recalculated": True, "pages": None},
    "summary": "Crew hours.xlsx: Hours (2 rows × 4 cols, totals). 3 formula(s), recalculated and error-free.",
}

OK_DOCX = {
    "success": True, "filename": "Safety memo.docx", "file_id": "d1",
    "download_url": "/portal/api/media/d1?download=1", "thumbnail_file_id": "t1", "thumbnail_url": "/portal/api/media/t1",
    "validation": {"ok": True, "checks": [], "issues": [], "pages": 3},
    "summary": "Safety memo.docx: 1 section(s), 0 table(s), 1 figure(s), ~2 words, 3 page(s).",
}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_tools_are_registered_scoped_and_slow():
    for name in ("create_spreadsheet", "create_document"):
        assert ToolRegistry.has(name)
        assert TOOL_SCOPES[name] == ["data.write"]
        assert TOOL_CLASSES[name]["class"] == "slow"
        # Outer kill switch must exceed the tool's own HTTP timeout.
        assert TOOL_CLASSES[name]["timeout"] > 180
    assert {"create_spreadsheet", "create_document"} <= set(ChatAgent().config.tools)


def test_loop_prompt_explains_when_to_make_a_file():
    assert "only when the user asks" in DOCUMENT_TOOLS_DIRECTIVE
    assert "Never claim a file exists" in DOCUMENT_TOOLS_DIRECTIVE


def test_tool_schemas_expose_the_typed_spec():
    """pydantic-ai builds the tool schema from the signature; the model must
    see the spec's fields, not an opaque dict."""
    import inspect

    assert inspect.signature(dt.create_spreadsheet).parameters["spec"].annotation is WorkbookSpec
    assert inspect.signature(dt.create_document).parameters["spec"].annotation is DocumentSpec
    schema = WorkbookSpec.model_json_schema()
    assert "column_formulas" in schema["properties"]["sheets"]["items"]["properties"] or "SheetSpec" in schema.get("$defs", {})


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_spreadsheet_posts_the_spec_as_the_user_and_returns_a_link(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_XLSX))
    out = await dt.create_spreadsheet(_Ctx(), WorkbookSpec.model_validate(WORKBOOK))
    assert out.success and out.file_id == "f1"
    assert out.download_url.endswith("?download=1")
    assert out.markdown == "[Download the spreadsheet: Crew hours.xlsx](/portal/api/media/f1?download=1)"
    assert "recalculated and error-free" in out.summary
    assert out.issues == [] and out.error is None
    call = _FakeHttp.calls[0]
    assert call["url"].endswith("/files/generate/xlsx")
    assert call["headers"] == {"Authorization": "Bearer tok"}
    assert call["json"]["spec"]["filename"] == "Crew hours.xlsx"
    assert call["json"]["spec"]["sheets"][0]["column_formulas"][0]["formula"] == "=B{row}*C{row}"
    assert call["json"]["conversation_id"] == "conv-1"


async def test_document_returns_link_and_preview(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_DOCX))
    out = await dt.create_document(_Ctx(), DocumentSpec.model_validate(DOCUMENT))
    assert out.success and out.pages == 3
    assert out.markdown.splitlines()[0] == "[Download the document: Safety memo.docx](/portal/api/media/d1?download=1)"
    assert "![First page of Safety memo.docx](/portal/api/media/t1)" in out.markdown
    assert _FakeHttp.calls[0]["url"].endswith("/files/generate/docx")
    assert _FakeHttp.calls[0]["json"]["thumbnail"] is True


async def test_plan_path_dict_spec_is_coerced(monkeypatch):
    """On the static-plan path the planner's JSON arrives as a dict."""
    _use(monkeypatch, _Resp(200, OK_XLSX))
    out = await dt.create_spreadsheet(_Ctx(), WORKBOOK)
    assert out.success
    out = await dt.create_spreadsheet(_Ctx(), json.dumps(WORKBOOK))
    assert out.success


# ---------------------------------------------------------------------------
# Failure paths — always a readable reason, never a phantom file
# ---------------------------------------------------------------------------


async def test_invalid_spec_never_reaches_the_network(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_XLSX))
    bad = dict(WORKBOOK, sheets=[dict(WORKBOOK["sheets"][0], column_formulas=[{"column": "Cost", "formula": "=B2*C2"}])])
    out = await dt.create_spreadsheet(_Ctx(), bad)
    assert not out.success and out.markdown == ""
    assert "Invalid spec" in out.error and "{row}" in out.error
    assert _FakeHttp.calls == []


async def test_denied_formula_is_rejected_client_side(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_XLSX))
    bad = dict(WORKBOOK, sheets=[dict(WORKBOOK["sheets"][0], cells=[{"cell": "F1", "value": '=WEBSERVICE("http://x")'}])])
    out = await dt.create_spreadsheet(_Ctx(), bad)
    assert not out.success and "WEBSERVICE" in out.error
    assert _FakeHttp.calls == []


async def test_no_token_is_an_error_not_a_request(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_XLSX))
    out = await dt.create_spreadsheet(_NoTokenCtx(), WORKBOOK)
    assert not out.success and "token" in out.error
    assert _FakeHttp.calls == []


async def test_verification_failure_relays_the_cells(monkeypatch):
    payload = dict(OK_XLSX, success=False, error="The workbook has formula errors or failed assertions; see validation.issues.",
                   validation={"ok": False, "checks": [], "issues": [
                       {"severity": "warning", "location": "Hours!B3", "message": "'n/a' is not a number"},
                       {"severity": "error", "location": "Hours!D3", "message": "formula result is #VALUE!"},
                   ], "formula_count": 3, "recalculated": True})
    _use(monkeypatch, _Resp(200, payload))
    out = await dt.create_spreadsheet(_Ctx(), WORKBOOK)
    assert not out.success
    assert out.issues[0].startswith("error at Hours!D3")   # errors first
    assert out.issues[1].startswith("warning at Hours!B3")
    assert "formula errors" in out.error
    assert out.file_id == "f1"  # the file exists for inspection, but success is false


async def test_spec_rejection_by_the_engine_is_relayed_with_hint(monkeypatch):
    _use(monkeypatch, _Resp(400, {"error": "Hours totals: column 'Nope' is not one of the sheet's columns", "hint": "use a header"}))
    out = await dt.create_spreadsheet(_Ctx(), WORKBOOK)
    assert not out.success and "Nope" in out.error and "(use a header)" in out.error


async def test_engine_unavailable_and_timeouts_are_explained(monkeypatch):
    _use(monkeypatch, _Resp(503, {"error": "pandoc is not installed on this server"}))
    out = await dt.create_document(_Ctx(), DOCUMENT)
    assert not out.success and "pandoc" in out.error

    _use(monkeypatch, raise_exc=httpx.ReadTimeout("slow"))
    out = await dt.create_document(_Ctx(), DOCUMENT)
    assert not out.success and "did not finish" in out.error

    _use(monkeypatch, _Resp(500, None, text="boom"))
    out = await dt.create_document(_Ctx(), DOCUMENT)
    assert not out.success and "HTTP 500" in out.error


async def test_export_document_accepts_bare_deps(monkeypatch):
    _use(monkeypatch, _Resp(200, OK_DOCX))
    out = await dt.export_document(_Deps(), DocumentSpec.model_validate(DOCUMENT), thumbnail=False)
    assert out.success
    assert _FakeHttp.calls[0]["json"]["thumbnail"] is False
    assert _FakeHttp.calls[0]["headers"]["Authorization"] == "Bearer tok"
