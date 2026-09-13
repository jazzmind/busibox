"""
render_chart — turn a numeric series into a PNG the chat can display.

Why this exists
---------------
Research answers need charts and the two obvious routes both fail:

- ``generate_image`` is a diffusion model. Ask it for a bar chart and it
  paints a picture *of* a bar chart — wrong numbers, garbled axis text.
- Mermaid ``xychart`` would be dependency-free, but the chat renderer
  (busibox-frontend, apps/chat/.../marine/Messages.tsx, verified 2026-09-12)
  has no code-block override, so a Mermaid block is displayed as raw source.

What does render today is a plain ``<img>``: react-markdown renders
``![alt](/portal/api/media/{fileId})`` and the portal's media proxy serves
it. So this tool draws the chart server-side with matplotlib, uploads the
PNG through the same data-api path ``generate_image`` uses, and hands back a
markdown image line the model can paste into its answer.

The model supplies the numbers. The tool never invents data — an empty or
mismatched series is an error, not a guess.
"""

import asyncio
import io
import logging
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field
from pydantic_ai import RunContext

from app.tools.image_tool import _upload_image_via_data_api

logger = logging.getLogger(__name__)

ChartKind = Literal["bar", "hbar", "line", "pie"]

MAX_SERIES = 6
MAX_POINTS = 60
# Roughly the width of the chat column at 2x; keeps text legible when scaled.
_FIG_W, _FIG_H, _DPI = 9.0, 5.0, 160


class ChartSeries(BaseModel):
    name: str = Field(description="Legend label for this series")
    values: List[float] = Field(description="One value per label, in order")


class ChartOutput(BaseModel):
    """Output schema for render_chart."""

    success: bool = Field(description="Whether the chart was rendered and stored")
    markdown: str = Field(default="", description="Markdown image line to include in the answer")
    media_url: Optional[str] = Field(default=None, description="Portal-relative URL of the PNG")
    file_id: Optional[str] = Field(default=None, description="File ID in the data store")
    error: Optional[str] = Field(default=None, description="Why rendering failed")


def _validate(kind: str, labels: List[str], series: List[ChartSeries]) -> Optional[str]:
    if kind not in ("bar", "hbar", "line", "pie"):
        return f"Unsupported chart kind {kind!r}; use bar, hbar, line or pie."
    if not labels:
        return "labels is empty — a chart needs at least one category or x value."
    if len(labels) > MAX_POINTS:
        return f"Too many points ({len(labels)}); keep it to {MAX_POINTS} or fewer."
    if not series:
        return "series is empty — supply at least one {name, values} series."
    if len(series) > MAX_SERIES:
        return f"Too many series ({len(series)}); keep it to {MAX_SERIES} or fewer."
    for s in series:
        if len(s.values) != len(labels):
            return (
                f"Series {s.name!r} has {len(s.values)} values but there are "
                f"{len(labels)} labels — every series needs one value per label."
            )
    if kind == "pie":
        if len(series) != 1:
            return "A pie chart takes exactly one series."
        if any(v < 0 for v in series[0].values):
            return "Pie chart values must be non-negative."
        if sum(series[0].values) <= 0:
            return "Pie chart values sum to zero; nothing to draw."
    return None


def render_chart_png(
    kind: str,
    title: str,
    labels: List[str],
    series: List[ChartSeries],
    x_label: str = "",
    y_label: str = "",
    source: str = "",
) -> bytes:
    """Draw the chart and return PNG bytes.

    Uses the object-oriented Figure API rather than pyplot: this runs on a
    worker thread (``asyncio.to_thread``) and pyplot's global state is not
    thread-safe, whereas a Figure with its own Agg canvas is self-contained.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    fig = Figure(figsize=(_FIG_W, _FIG_H), dpi=_DPI)
    FigureCanvasAgg(fig)  # attaches a headless canvas to this figure only
    ax = fig.add_subplot(111)
    try:
        n = len(labels)
        if kind == "pie":
            vals = series[0].values
            ax.pie(vals, labels=labels, autopct="%1.1f%%", startangle=90, counterclock=False)
            ax.axis("equal")
        elif kind == "line":
            for s in series:
                ax.plot(labels, s.values, marker="o", linewidth=2, label=s.name)
            ax.grid(True, alpha=0.3)
        else:
            import numpy as np

            idx = np.arange(n)
            width = 0.8 / max(1, len(series))
            for i, s in enumerate(series):
                offset = (i - (len(series) - 1) / 2) * width
                if kind == "hbar":
                    ax.barh(idx + offset, s.values, height=width, label=s.name)
                else:
                    ax.bar(idx + offset, s.values, width=width, label=s.name)
            if kind == "hbar":
                ax.set_yticks(idx)
                ax.set_yticklabels(labels)
                ax.grid(True, axis="x", alpha=0.3)
            else:
                ax.set_xticks(idx)
                ax.set_xticklabels(labels, rotation=30 if n > 6 else 0, ha="right" if n > 6 else "center")
                ax.grid(True, axis="y", alpha=0.3)

        if title:
            ax.set_title(title, fontsize=13, weight="bold", pad=12)
        if kind != "pie":
            # Labels are applied literally. For hbar the value axis is x, so
            # callers put the unit in x_label and the category in y_label.
            if x_label:
                ax.set_xlabel(x_label)
            if y_label:
                ax.set_ylabel(y_label)
            if len(series) > 1 or kind == "line":
                ax.legend(frameon=False)
            for spine in ("top", "right"):
                ax.spines[spine].set_visible(False)
        if source:
            fig.text(0.01, 0.01, f"Source: {source}", fontsize=8, color="#666666", ha="left", va="bottom")

        fig.tight_layout(rect=(0, 0.03 if source else 0, 1, 1))
        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        return buf.getvalue()
    finally:
        # No pyplot registry to close; dropping the figure releases it.
        fig.clear()


async def render_chart(
    ctx: RunContext[Any],
    kind: ChartKind,
    title: str,
    labels: List[str],
    series: List[ChartSeries],
    x_label: str = "",
    y_label: str = "",
    source: str = "",
) -> ChartOutput:
    """Render a bar, horizontal bar, line, or pie chart from numbers you already have.

    Use this when an answer contains a numeric series worth seeing — figures
    over time, quantities compared across categories, shares of a whole.
    Pass the real values from your sources; do not estimate. The result is a
    markdown image line: put it in the answer where the chart belongs and
    describe the takeaway in a sentence next to it.

    Args:
        kind: "bar" (categories), "hbar" (long category names), "line"
            (a series over time or an ordered x), or "pie" (shares of one whole,
            single series only).
        title: Short chart title.
        labels: Category names or x-axis values, in order. Max 60.
        series: One or more {name, values} series; each needs one value per
            label. Max 6. Pie charts take exactly one.
        x_label: Horizontal axis label. For hbar this is the value axis
            (e.g. "USD millions"). Optional.
        y_label: Vertical axis label. For hbar this is the category axis.
            Optional.
        source: Where the numbers came from; printed under the chart. Optional
            but strongly encouraged for research answers.
    """
    # On the plan path the planner's JSON arrives untouched, so `series` is a
    # list of dicts rather than ChartSeries. Coerce before validating.
    try:
        series = [s if isinstance(s, ChartSeries) else ChartSeries.model_validate(s) for s in (series or [])]
        labels = [str(v) for v in (labels or [])]
    except Exception as exc:  # noqa: BLE001
        return ChartOutput(success=False, error=f"series must be a list of {{name, values}}: {exc}")

    error = _validate(kind, labels, series)
    if error:
        return ChartOutput(success=False, error=error)

    # Check we can store the result before spending CPU drawing it.
    token = getattr(getattr(ctx.deps, "busibox_client", None), "_token", None)
    if not token:
        return ChartOutput(success=False, error="No authenticated token available to store the chart.")

    try:
        # matplotlib is synchronous and the first call in a fresh container
        # also builds its font cache; keep it off the event loop.
        png = await asyncio.to_thread(
            render_chart_png, kind, title, labels, series, x_label, y_label, source
        )
    except ImportError as exc:
        logger.error("render_chart: matplotlib unavailable: %s", exc)
        return ChartOutput(
            success=False,
            error="Chart rendering is not available on this server (matplotlib missing). "
                  "Present the numbers as a markdown table instead.",
        )
    except Exception as exc:  # noqa: BLE001 — a bad chart must not break the turn
        logger.warning("render_chart: draw failed: %s", exc)
        return ChartOutput(success=False, error=f"Could not draw the chart: {exc}")

    try:
        safe_title = "".join(c if c.isalnum() or c in "-_ " else "" for c in title).strip().replace(" ", "-")[:40]
        file_id = await _upload_image_via_data_api(
            token=token,
            image_bytes=png,
            mime_type="image/png",
            filename=f"chart-{safe_title or kind}.png",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("render_chart: upload failed: %s", exc)
        return ChartOutput(success=False, error=f"Chart drawn but could not be stored: {exc}")

    media_url = f"/portal/api/media/{file_id}"
    alt = title or f"{kind} chart"
    logger.info("render_chart: %s chart stored file_id=%s (%d bytes)", kind, file_id, len(png))
    return ChartOutput(
        success=True,
        markdown=f"![{alt}]({media_url})",
        media_url=media_url,
        file_id=file_id,
    )
