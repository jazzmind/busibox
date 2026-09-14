"""
Thin, timeout-guarded wrappers around the office tooling the engine needs:
LibreOffice (recalculate/convert), pandoc (markdown → docx) and poppler
(``pdfinfo``/``pdftoppm`` for page counts and thumbnails).

Every call runs in a fresh temporary directory with an isolated LibreOffice
profile (``-env:UserInstallation``) so concurrent requests never share
state, and every subprocess has a hard timeout — a wedged soffice must not
hold a request open.

All functions are synchronous; callers run them via ``asyncio.to_thread``.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SOFFICE_BIN = os.getenv("DOCGEN_SOFFICE_BIN", "soffice")
PANDOC_BIN = os.getenv("DOCGEN_PANDOC_BIN", "pandoc")
PDFTOPPM_BIN = os.getenv("DOCGEN_PDFTOPPM_BIN", "pdftoppm")
PDFINFO_BIN = os.getenv("DOCGEN_PDFINFO_BIN", "pdfinfo")

DEFAULT_TIMEOUT = int(os.getenv("DOCGEN_TOOL_TIMEOUT_SECONDS", "90"))


class OfficeToolError(RuntimeError):
    """A tool was missing, timed out, or exited non-zero."""


@dataclass
class ToolAvailability:
    soffice: bool
    pandoc: bool
    pdftoppm: bool
    pdfinfo: bool

    @property
    def xlsx_ok(self) -> bool:
        return self.soffice

    @property
    def docx_ok(self) -> bool:
        return self.pandoc

    @property
    def thumbnail_ok(self) -> bool:
        return self.soffice and self.pdftoppm


def tool_availability() -> ToolAvailability:
    return ToolAvailability(
        soffice=shutil.which(SOFFICE_BIN) is not None,
        pandoc=shutil.which(PANDOC_BIN) is not None,
        pdftoppm=shutil.which(PDFTOPPM_BIN) is not None,
        pdfinfo=shutil.which(PDFINFO_BIN) is not None,
    )


def _run(cmd: List[str], timeout: int, env: Optional[dict] = None, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(cwd) if cwd else None,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OfficeToolError(f"{cmd[0]} is not installed on this server") from exc
    except subprocess.TimeoutExpired as exc:
        raise OfficeToolError(f"{cmd[0]} timed out after {timeout}s") from exc


def _soffice_env() -> dict:
    env = os.environ.copy()
    # Headless VCL backend; no display, no GTK.
    env["SAL_USE_VCLPLUGIN"] = "svp"
    env.setdefault("HOME", tempfile.gettempdir())
    return env


def soffice_convert(src: Path, target_ext: str, timeout: int = DEFAULT_TIMEOUT) -> Path:
    """Convert ``src`` to ``target_ext`` ("xlsx", "pdf", …) with LibreOffice.

    Returns the path of the converted file, which lives next to ``src`` in a
    ``converted/`` sub-directory. Converting an ``.xlsx`` to ``.xlsx`` is the
    recalculation step: LibreOffice evaluates every formula that has no
    cached value and writes the results back, exactly as Excel would on open.
    """
    outdir = src.parent / "converted"
    outdir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="lo-profile-", dir=src.parent) as profile:
        cmd = [
            SOFFICE_BIN,
            "--headless",
            "--norestore",
            "--nologo",
            f"-env:UserInstallation={Path(profile).as_uri()}",
            "--convert-to",
            target_ext,
            "--outdir",
            str(outdir),
            str(src),
        ]
        proc = _run(cmd, timeout=timeout, env=_soffice_env(), cwd=src.parent)
    out = outdir / f"{src.stem}.{target_ext}"
    if proc.returncode != 0 or not out.exists():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {proc.returncode}"
        raise OfficeToolError(f"LibreOffice could not convert {src.name} to {target_ext}: {tail}")
    return out


_pandoc_version: Optional[Tuple[int, ...]] = None


def pandoc_version() -> Tuple[int, ...]:
    """Cached ``pandoc --version`` tuple; ``(0,)`` when unavailable."""
    global _pandoc_version
    if _pandoc_version is None:
        try:
            proc = _run([PANDOC_BIN, "--version"], timeout=15)
            first = (proc.stdout or "").splitlines()[0] if proc.stdout else ""
            _pandoc_version = tuple(int(x) for x in first.split()[1].split(".")) if first.startswith("pandoc ") else (0,)
        except (OfficeToolError, ValueError, IndexError):
            _pandoc_version = (0,)
    return _pandoc_version


def pandoc_markdown_to_docx(
    markdown_path: Path,
    out_path: Path,
    reference_doc: Optional[Path] = None,
    resource_path: Optional[Path] = None,
    timeout: int = DEFAULT_TIMEOUT,
    from_format: str = "gfm",
    metadata: Optional[Dict[str, str]] = None,
) -> Path:
    """Render a Markdown file to ``.docx`` with pandoc.

    Images resolve only from ``resource_path``; on pandoc ≥ 2.15 ``--sandbox``
    additionally forbids reading anything else (Ubuntu 22.04 ships 2.9, so
    the caller must not rely on it — the engine rewrites every image link
    itself).
    """
    cmd = [
        PANDOC_BIN,
        str(markdown_path),
        "--from",
        from_format,
        "--to",
        "docx",
        "--output",
        str(out_path),
    ]
    if pandoc_version() >= (2, 15):
        cmd.append("--sandbox")
    if reference_doc is not None:
        cmd += ["--reference-doc", str(reference_doc)]
    if resource_path is not None:
        cmd += ["--resource-path", str(resource_path)]
    for key, value in (metadata or {}).items():
        if value:
            cmd += ["--metadata", f"{key}={value}"]
    proc = _run(cmd, timeout=timeout, cwd=markdown_path.parent)
    if proc.returncode != 0 or not out_path.exists():
        detail = (proc.stderr or "").strip().splitlines()
        tail = detail[-1] if detail else f"exit code {proc.returncode}"
        raise OfficeToolError(f"pandoc failed: {tail}")
    return out_path


def pdf_page_count(pdf: Path, timeout: int = 30) -> Optional[int]:
    """Page count via ``pdfinfo``; ``None`` if the tool is missing or fails."""
    if shutil.which(PDFINFO_BIN) is None:
        return None
    try:
        proc = _run([PDFINFO_BIN, str(pdf)], timeout=timeout)
    except OfficeToolError:
        return None
    for line in proc.stdout.splitlines():
        if line.lower().startswith("pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def pdf_first_page_png(pdf: Path, width_px: int = 640, timeout: int = 30) -> Optional[bytes]:
    """Render page 1 of ``pdf`` to a PNG of ``width_px`` pixels; ``None`` on failure."""
    if shutil.which(PDFTOPPM_BIN) is None:
        return None
    prefix = pdf.parent / f"{pdf.stem}-thumb"
    cmd = [
        PDFTOPPM_BIN,
        "-png",
        "-f", "1", "-l", "1",
        "-scale-to-x", str(width_px),
        "-scale-to-y", "-1",
        "-singlefile",
        str(pdf),
        str(prefix),
    ]
    try:
        proc = _run(cmd, timeout=timeout)
    except OfficeToolError:
        return None
    out = Path(f"{prefix}.png")
    if proc.returncode != 0 or not out.exists():
        return None
    return out.read_bytes()
