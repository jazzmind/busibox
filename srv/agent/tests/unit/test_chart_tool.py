"""render_chart: numbers in, a markdown image the chat can display out.

Why a server-rendered PNG and not something lighter — verified 2026-09-12
against busibox-frontend, apps/chat/src/components/chat/themes/marine/Messages.tsx:
the chat mounts ReactMarkdown with remark-gfm and a single `a` override. That
renders tables and plain <img> tags, and nothing else. A Mermaid block shows
as raw source; generate_image is a diffusion model that paints a picture *of*
a chart. So: draw it with matplotlib, store it where generate_image stores its
output, return `![title](/portal/api/media/{fileId})`.
"""

import pytest

from app.agents.base_agent import TOOL_CLASSES, TOOL_SCOPES, ToolRegistry
from app.agents.chat_agent import ChatAgent
from app.tools import chart_tool
from app.tools.chart_tool import ChartSeries, _validate, render_chart, render_chart_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,labels,series",
    [
        pytest.param("bar", ["2023", "2024", "2025"],
                     [ChartSeries(name="Revenue", values=[1.2, 1.8, 2.4]),
                      ChartSeries(name="Cost", values=[0.9, 1.1, 1.5])], id="grouped-bar"),
        pytest.param("hbar", ["Boskalis", "Van Oord", "DEME", "Cashman"],
                     [ChartSeries(name="Fleet", values=[80, 60, 55, 12])], id="hbar"),
        pytest.param("line", ["Q1", "Q2", "Q3", "Q4"],
                     [ChartSeries(name="Utilisation %", values=[71, 78, 84, 80])], id="line"),
        pytest.param("pie", ["US", "EU", "Asia"],
                     [ChartSeries(name="Share", values=[45, 30, 25])], id="pie"),
    ],
)
def test_every_kind_renders_a_png(kind, labels, series):
    png = render_chart_png(kind, f"{kind} test", labels, series, "x", "y", "test source")
    assert png[:8] == PNG_MAGIC
    assert len(png) > 5000, "suspiciously small — did it draw anything?"


def test_many_categories_still_render():
    """Up to MAX_POINTS labels; long axes rotate rather than overlap."""
    labels = [f"wk{i}" for i in range(chart_tool.MAX_POINTS)]
    series = [ChartSeries(name="s", values=[float(i % 7) for i in range(chart_tool.MAX_POINTS)])]
    assert render_chart_png("bar", "t", labels, series)[:8] == PNG_MAGIC


# ---------------------------------------------------------------------------
# Validation — the tool never guesses numbers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,labels,series,needle",
    [
        ("bar", [], [ChartSeries(name="a", values=[])], "labels is empty"),
        ("bar", ["a", "b"], [], "series is empty"),
        ("bar", ["a", "b"], [ChartSeries(name="s", values=[1])], "one value per label"),
        ("pie", ["a", "b"], [ChartSeries(name="s", values=[1, 2]), ChartSeries(name="t", values=[1, 2])],
         "exactly one series"),
        ("pie", ["a", "b"], [ChartSeries(name="s", values=[-1, 2])], "non-negative"),
        ("pie", ["a", "b"], [ChartSeries(name="s", values=[0, 0])], "sum to zero"),
        ("radar", ["a"], [ChartSeries(name="s", values=[1])], "Unsupported"),
        ("bar", ["a"] * (chart_tool.MAX_POINTS + 1),
         [ChartSeries(name="s", values=[1.0] * (chart_tool.MAX_POINTS + 1))], "Too many points"),
        ("bar", ["a"], [ChartSeries(name=f"s{i}", values=[1.0]) for i in range(chart_tool.MAX_SERIES + 1)],
         "Too many series"),
    ],
)
def test_bad_input_is_a_clear_error_not_a_guess(kind, labels, series, needle):
    err = _validate(kind, labels, series)
    assert err and needle in err, err


# ---------------------------------------------------------------------------
# The tool: upload path and returned markdown
# ---------------------------------------------------------------------------


class _Client:
    _token = "tok"


class _Deps:
    busibox_client = _Client()


class _Ctx:
    deps = _Deps()


@pytest.mark.asyncio
async def test_success_returns_markdown_image_on_the_media_url(monkeypatch):
    uploaded = {}

    async def fake_upload(token, image_bytes, mime_type, filename):
        uploaded.update(token=token, size=len(image_bytes), mime=mime_type, filename=filename)
        return "file-123"

    monkeypatch.setattr(chart_tool, "_upload_image_via_data_api", fake_upload)

    out = await render_chart(
        _Ctx(), "bar", "Dredging fleet by operator", ["A", "B"],
        [ChartSeries(name="Vessels", values=[10, 4])], source="Filings",
    )

    assert out.success
    assert out.media_url == "/portal/api/media/file-123"
    assert out.markdown == "![Dredging fleet by operator](/portal/api/media/file-123)"
    assert uploaded["mime"] == "image/png" and uploaded["token"] == "tok"
    assert uploaded["filename"].startswith("chart-") and uploaded["filename"].endswith(".png")
    assert uploaded["size"] > 5000


@pytest.mark.asyncio
async def test_validation_failure_never_touches_the_upload_path(monkeypatch):
    async def must_not_run(*a, **kw):
        raise AssertionError("upload called on invalid input")

    monkeypatch.setattr(chart_tool, "_upload_image_via_data_api", must_not_run)
    out = await render_chart(_Ctx(), "pie", "t", ["a", "b"], [ChartSeries(name="s", values=[1])])
    assert out.success is False and "one value per label" in out.error


@pytest.mark.asyncio
async def test_missing_token_is_reported():
    class _NoTokenCtx:
        class deps:
            class busibox_client:
                _token = None

    out = await render_chart(_NoTokenCtx(), "bar", "t", ["a"], [ChartSeries(name="s", values=[1])])
    assert out.success is False and "token" in out.error


@pytest.mark.asyncio
async def test_upload_failure_is_reported_not_raised(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("data-api down")

    monkeypatch.setattr(chart_tool, "_upload_image_via_data_api", boom)
    out = await render_chart(_Ctx(), "bar", "t", ["a"], [ChartSeries(name="s", values=[1])])
    assert out.success is False and "data-api down" in out.error


@pytest.mark.asyncio
async def test_missing_matplotlib_tells_the_model_to_use_a_table(monkeypatch):
    """The tool stays registered without matplotlib so the model gets a
    useful refusal rather than a tool that silently does not exist."""
    def no_mpl(*a, **kw):
        raise ImportError("No module named matplotlib")

    monkeypatch.setattr(chart_tool, "render_chart_png", no_mpl)
    out = await render_chart(_Ctx(), "bar", "t", ["a"], [ChartSeries(name="s", values=[1])])
    assert out.success is False and "table" in out.error.lower()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_registered_scoped_and_classified():
    assert ToolRegistry.has("render_chart")
    assert TOOL_SCOPES["render_chart"] == ["data.write"], "same store as generate_image"
    assert TOOL_CLASSES["render_chart"]["class"] == "fast"
    assert "render_chart" in ChatAgent().config.tools


def test_chart_guidance_lives_only_where_the_model_can_call_tools():
    """The plain synthesis pass has no tools and a guard that forbids tool-call
    syntax. Telling it to "call render_chart" would at best be ignored and at
    worst leak a fake call into the answer — so the charts bullet is its own
    directive, appended only on the loop path (where render_chart exists)."""
    from app.agents.base_agent import (
        RESEARCH_CHART_DIRECTIVE, RESEARCH_LOOP_DIRECTIVE, RESEARCH_SYNTHESIS_DIRECTIVE,
    )
    assert "`render_chart`" not in RESEARCH_SYNTHESIS_DIRECTIVE
    assert "`render_chart`" in RESEARCH_CHART_DIRECTIVE
    assert "`render_chart`" in RESEARCH_LOOP_DIRECTIVE
    assert "Never estimate" in RESEARCH_CHART_DIRECTIVE


def test_chart_directive_reaches_a_research_loop_that_has_the_tool():
    from app.agents.base_agent import RESEARCH_CHART_DIRECTIVE, AgentContext
    agent = ChatAgent()
    assert "render_chart" in agent.config.tools
    text = agent._build_enriched_system_prompt(AgentContext(loop_mode="research"))
    assert RESEARCH_CHART_DIRECTIVE in text


def test_plain_synthesis_context_never_asks_for_a_tool_call():
    from app.agents.base_agent import AgentContext
    from app.tools.tavily_tools import DeepResearchOutput
    agent = ChatAgent()
    ctx = AgentContext(tool_results={"deep_research": DeepResearchOutput(
        success=True, status="completed", report="## Findings\nsome text")})
    text = agent._build_synthesis_context("q", ctx)
    assert "render_chart" not in text


@pytest.mark.asyncio
async def test_plan_path_dict_series_is_coerced(monkeypatch):
    """_execute_step passes planner JSON through untouched, so `series`
    arrives as a list of dicts, not ChartSeries."""
    async def fake_upload(token, image_bytes, mime_type, filename):
        return "file-9"

    monkeypatch.setattr(chart_tool, "_upload_image_via_data_api", fake_upload)
    out = await render_chart(
        _Ctx(), "bar", "t", ["a", "b"], [{"name": "s", "values": [1, 2]}],  # type: ignore[list-item]
    )
    assert out.success and out.file_id == "file-9"


@pytest.mark.asyncio
async def test_malformed_series_dict_is_a_clear_error():
    out = await render_chart(_Ctx(), "bar", "t", ["a"], [{"nope": 1}])  # type: ignore[list-item]
    assert out.success is False and "name, values" in out.error


# ---------------------------------------------------------------------------
# The token: BusiboxClient never had a `_token`
# ---------------------------------------------------------------------------


def test_data_api_token_comes_from_the_real_client():
    """First production research turn: every render_chart call failed with
    "No authenticated token available". BusiboxClient holds a default token
    plus per-audience exchanged tokens — no `_token` attribute — so the
    lookup (copied from generate_image, which had the same bug) was always
    None. The helper must prefer the data-api-scoped token, fall back to the
    default, and only then accept a test double's bare `_token`."""
    from app.clients.busibox import BusiboxClient
    from app.tools.image_tool import _data_api_token

    class Scoped:
        busibox_client = BusiboxClient("default", tokens_by_audience={"data-api": "scoped"})

    class DefaultOnly:
        busibox_client = BusiboxClient("default")

    class NoClient:
        busibox_client = None

    assert _data_api_token(Scoped()) == "scoped"
    assert _data_api_token(DefaultOnly()) == "default"
    assert _data_api_token(NoClient()) is None
    assert _data_api_token(_Ctx.deps) == "tok", "test doubles with a bare _token still work"
    assert _data_api_token(None) is None
