"""
Document generation endpoints: build validated Excel and Word files from a
typed spec and store them in the caller's library like any upload.

- POST /files/generate/xlsx — WorkbookSpec → .xlsx (built with openpyxl,
  recalculated with LibreOffice, every formula checked, assertions run)
- POST /files/generate/docx — DocumentSpec → .docx (pandoc + neutral
  template, structure verified, PDF page count and first-page thumbnail)
- POST /files/generate/pptx — PresentationSpec → .pptx (python-pptx, 16:9
  neutral styling, native charts/tables, LibreOffice render check and
  first-slide thumbnail)
- GET  /files/generate/capabilities — which tools this server has

Both POST routes return a ``GenerateResult``: file id, portal download URL,
thumbnail (Word), a validation report and a one-line summary the agent can
relay. A workbook with formula errors is still stored (so the user can
inspect it) but ``success`` is false and the issues list says which cells.

Files are stored through the normal upload path, so they are encrypted,
indexed and visible in the Documents app, and every call carries the
user's own JWT — no service credentials.

Design: docs/developers/chat-document-generation-plan.md
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from typing import Any, Dict, Optional

import structlog
from fastapi import APIRouter, Depends, Request, UploadFile, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from starlette.datastructures import Headers

from api.middleware.jwt_auth import ScopeChecker
from api.routes.files import download_file
from api.routes.upload import upload_file
from services.document_engine.docx import DOCX_MIME, build_document, collect_image_ids
from services.document_engine.office import OfficeToolError, tool_availability
from services.document_engine.pptx import PPTX_MIME, build_presentation, collect_deck_image_ids
from services.document_engine.specs import DocumentSpec, GenerateResult, PresentationSpec, WorkbookSpec
from services.document_engine.xlsx import XLSX_MIME, SpecError, build_workbook

logger = structlog.get_logger()

router = APIRouter()

require_data_write = ScopeChecker("data.write")
require_data_read = ScopeChecker("data.read")

# LibreOffice is memory-hungry; bound how many renders run at once per process.
_MAX_CONCURRENT = int(os.getenv("DOCGEN_MAX_CONCURRENT", "2"))
_GEN_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)
_TOOL_TIMEOUT = int(os.getenv("DOCGEN_TOOL_TIMEOUT_SECONDS", "90"))

PORTAL_MEDIA_PREFIX = os.getenv("DOCGEN_MEDIA_URL_PREFIX", "/portal/api/media")


class WorkbookRequest(BaseModel):
    spec: WorkbookSpec
    library_id: Optional[str] = Field(default=None, description="Target library; default is the user's personal Documents library")
    conversation_id: Optional[str] = Field(default=None, max_length=64, description="Chat conversation that produced the file (stored as metadata)")


class DocumentRequest(BaseModel):
    spec: DocumentSpec
    library_id: Optional[str] = None
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    thumbnail: bool = Field(default=True, description="Also store a first-page PNG and return its URL")


class PresentationRequest(BaseModel):
    spec: PresentationSpec
    library_id: Optional[str] = None
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    thumbnail: bool = Field(default=True, description="Also store a first-slide PNG and return its URL")


def _download_url(file_id: str) -> str:
    return f"{PORTAL_MEDIA_PREFIX}/{file_id}?download=1"


def _media_url(file_id: str) -> str:
    return f"{PORTAL_MEDIA_PREFIX}/{file_id}"


async def _store(request: Request, filename: str, mime_type: str, data: bytes, metadata: Dict[str, Any], library_id: Optional[str]) -> Dict[str, Any]:
    """Store bytes through the regular upload route (encryption, dedup,
    library routing, indexing) and return its JSON body."""
    upload = UploadFile(file=io.BytesIO(data), filename=filename, headers=Headers({"content-type": mime_type}))
    response = await upload_file(
        request=request,
        file=upload,
        metadata=json.dumps(metadata),
        processing_config=None,
        visibility="personal",
        role_ids=None,
        force_reprocess=None,
        library_id=library_id,
        library_id_camel=None,
    )
    body = json.loads(bytes(response.body).decode("utf-8")) if response.body else {}
    if response.status_code != 200 or not body.get("fileId"):
        raise RuntimeError(body.get("error") or f"upload failed with status {response.status_code}")
    return body


async def _fetch_image(request: Request, file_id: str) -> Optional[bytes]:
    """Read a stored image the caller may access, or ``None``."""
    try:
        response = await download_file(file_id, request)
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate: image fetch failed", file_id=file_id, error=str(exc))
        return None
    if not isinstance(response, StreamingResponse):
        return None
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
    data = b"".join(chunks)
    return data or None


def _spec_error(message: str, hint: Optional[str] = None) -> JSONResponse:
    content: Dict[str, Any] = {"error": message}
    if hint:
        content["hint"] = hint
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=content)


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors()[:8]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "; ".join(parts)


@router.get("/generate/capabilities", dependencies=[Depends(require_data_read)])
async def generate_capabilities():
    tools = tool_availability()
    return {
        "xlsx": True,
        "xlsx_recalculation": tools.soffice,
        "docx": tools.pandoc,
        "docx_thumbnail": tools.thumbnail_ok,
        "pptx": True,
        "pptx_render_check": tools.soffice,
        "tools": tools.__dict__,
        "max_concurrent": _MAX_CONCURRENT,
    }


@router.post("/generate/xlsx", dependencies=[Depends(require_data_write)])
async def generate_xlsx(request: Request):
    """Build, recalculate, verify and store an Excel workbook."""
    user_id = request.state.user_id
    try:
        payload = await request.json()
        body = WorkbookRequest.model_validate(payload)
    except ValidationError as exc:
        return _spec_error(f"Invalid workbook spec: {_format_validation_error(exc)}")
    except Exception as exc:  # noqa: BLE001
        return _spec_error(f"Request body must be JSON with a 'spec' object: {exc}")

    spec = body.spec
    try:
        async with _GEN_SEMAPHORE:
            out = await asyncio.to_thread(build_workbook, spec, _TOOL_TIMEOUT)
    except SpecError as exc:
        return _spec_error(str(exc), hint="column references must be a header from 'columns' or a column letter")
    except OfficeToolError as exc:
        logger.error("generate_xlsx: tooling failure", user_id=user_id, error=str(exc))
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_xlsx: build failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Workbook build failed: {exc}"})

    metadata = {
        "source": "generated",
        "generator": "document_engine",
        "kind": "xlsx",
        "title": spec.title or spec.filename,
        "sheets": [s.name for s in spec.sheets],
        "validation_ok": out.report.ok,
        "delivered": out.delivered,
    }
    if body.conversation_id:
        metadata["conversation_id"] = body.conversation_id
    try:
        stored = await _store(request, spec.filename, XLSX_MIME, out.data, metadata, body.library_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_xlsx: store failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Workbook built but could not be stored: {exc}"})

    file_id = stored["fileId"]
    result = GenerateResult(
        success=out.report.ok,
        filename=spec.filename,
        mime_type=XLSX_MIME,
        size_bytes=len(out.data),
        file_id=file_id,
        download_url=_download_url(file_id),
        validation=out.report,
        summary=out.summary,
        error=None if out.report.ok else "The workbook has formula errors or failed assertions; see validation.issues.",
    )
    logger.info("generate_xlsx: stored", user_id=user_id, file_id=file_id, ok=out.report.ok, formulas=out.report.formula_count, delivered=out.delivered)
    return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump())


@router.post("/generate/docx", dependencies=[Depends(require_data_write)])
async def generate_docx(request: Request):
    """Assemble, render, verify and store a Word document."""
    user_id = request.state.user_id
    try:
        payload = await request.json()
        body = DocumentRequest.model_validate(payload)
    except ValidationError as exc:
        return _spec_error(f"Invalid document spec: {_format_validation_error(exc)}")
    except Exception as exc:  # noqa: BLE001
        return _spec_error(f"Request body must be JSON with a 'spec' object: {exc}")

    spec = body.spec
    images: Dict[str, bytes] = {}
    for file_id in collect_image_ids(spec):
        data = await _fetch_image(request, file_id)
        if data:
            images[file_id] = data

    try:
        async with _GEN_SEMAPHORE:
            out = await asyncio.to_thread(build_document, spec, images, _TOOL_TIMEOUT, body.thumbnail)
    except OfficeToolError as exc:
        logger.error("generate_docx: tooling failure", user_id=user_id, error=str(exc))
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_docx: build failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Document build failed: {exc}"})

    metadata = {
        "source": "generated",
        "generator": "document_engine",
        "kind": "docx",
        "title": spec.title,
        "sections": [s.heading for s in spec.sections if s.heading],
        "pages": out.report.pages,
        "validation_ok": out.report.ok,
    }
    if body.conversation_id:
        metadata["conversation_id"] = body.conversation_id
    try:
        stored = await _store(request, spec.filename, DOCX_MIME, out.data, metadata, body.library_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_docx: store failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Document built but could not be stored: {exc}"})
    file_id = stored["fileId"]

    thumb_id: Optional[str] = None
    if out.thumbnail:
        try:
            thumb_meta = {"source": "generated", "generator": "document_engine", "kind": "thumbnail", "of_file_id": file_id}
            thumb_stored = await _store(request, f"{spec.filename.rsplit('.', 1)[0]} - page 1.png", "image/png", out.thumbnail, thumb_meta, None)
            thumb_id = thumb_stored["fileId"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("generate_docx: thumbnail store failed", user_id=user_id, error=str(exc))

    result = GenerateResult(
        success=out.report.ok,
        filename=spec.filename,
        mime_type=DOCX_MIME,
        size_bytes=len(out.data),
        file_id=file_id,
        download_url=_download_url(file_id),
        thumbnail_file_id=thumb_id,
        thumbnail_url=_media_url(thumb_id) if thumb_id else None,
        validation=out.report,
        summary=out.summary,
        error=None if out.report.ok else "The document rendered with structural problems; see validation.issues.",
    )
    logger.info("generate_docx: stored", user_id=user_id, file_id=file_id, ok=out.report.ok, pages=out.report.pages, thumbnail=bool(thumb_id))
    return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump())


@router.post("/generate/pptx", dependencies=[Depends(require_data_write)])
async def generate_pptx(request: Request):
    """Build, verify and store a PowerPoint deck."""
    user_id = request.state.user_id
    try:
        payload = await request.json()
        body = PresentationRequest.model_validate(payload)
    except ValidationError as exc:
        return _spec_error(f"Invalid presentation spec: {_format_validation_error(exc)}")
    except Exception as exc:  # noqa: BLE001
        return _spec_error(f"Request body must be JSON with a 'spec' object: {exc}")

    spec = body.spec
    images: Dict[str, bytes] = {}
    for file_id in collect_deck_image_ids(spec):
        data = await _fetch_image(request, file_id)
        if data:
            images[file_id] = data

    try:
        async with _GEN_SEMAPHORE:
            out = await asyncio.to_thread(build_presentation, spec, images, _TOOL_TIMEOUT, body.thumbnail)
    except OfficeToolError as exc:
        logger.error("generate_pptx: tooling failure", user_id=user_id, error=str(exc))
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_pptx: build failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Presentation build failed: {exc}"})

    metadata = {
        "source": "generated",
        "generator": "document_engine",
        "kind": "pptx",
        "title": spec.title,
        "slides": out.stats.get("slides"),
        "validation_ok": out.report.ok,
    }
    if body.conversation_id:
        metadata["conversation_id"] = body.conversation_id
    try:
        stored = await _store(request, spec.filename, PPTX_MIME, out.data, metadata, body.library_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("generate_pptx: store failed", user_id=user_id)
        return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"error": f"Presentation built but could not be stored: {exc}"})
    file_id = stored["fileId"]

    thumb_id: Optional[str] = None
    if out.thumbnail:
        try:
            thumb_meta = {"source": "generated", "generator": "document_engine", "kind": "thumbnail", "of_file_id": file_id}
            thumb_stored = await _store(request, f"{spec.filename.rsplit('.', 1)[0]} - slide 1.png", "image/png", out.thumbnail, thumb_meta, None)
            thumb_id = thumb_stored["fileId"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("generate_pptx: thumbnail store failed", user_id=user_id, error=str(exc))

    result = GenerateResult(
        success=out.report.ok,
        filename=spec.filename,
        mime_type=PPTX_MIME,
        size_bytes=len(out.data),
        file_id=file_id,
        download_url=_download_url(file_id),
        thumbnail_file_id=thumb_id,
        thumbnail_url=_media_url(thumb_id) if thumb_id else None,
        validation=out.report,
        summary=out.summary,
        error=None if out.report.ok else "The deck was built with structural problems; see validation.issues.",
    )
    logger.info("generate_pptx: stored", user_id=user_id, file_id=file_id, ok=out.report.ok, slides=out.stats.get("slides"), thumbnail=bool(thumb_id))
    return JSONResponse(status_code=status.HTTP_200_OK, content=result.model_dump())
